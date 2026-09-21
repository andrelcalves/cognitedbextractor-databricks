"""
Compare today's row volume for one table: Databricks view vs Cognite RAW.

Databricks: COUNT where DATETIMESTAMP is in the calendar day (source watermark).
Cognite:    COUNT where lastUpdatedTime is in the same day (CDF upsert).

Credentials:
  DATABRICKS_HOST / DATABRICKS_HTTP_PATH / DATABRICKS_TOKEN
  COGNITE_TOKEN     JWT (env or --token)

Optional:
  COGNITE_HOST      Default: https://az-phx-001.cognitedata.com
  COGNITE_PROJECT   Default: bdx-dev

Requires:
  pip install databricks-sql-connector cognite-sdk pyyaml

Usage (PowerShell):
  $env:DATABRICKS_HOST = "adb-xxxx.azuredatabricks.net"
  $env:DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/..."
  $env:DATABRICKS_TOKEN = "dapi..."

  # Cognite JWT (access_token from Bruno / Azure). No "Bearer " prefix.
  $env:COGNITE_TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
  python scripts/databricks_cognite_today.py --name MARA

  python scripts/databricks_cognite_today.py --name MARA --token "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."

  python scripts/databricks_cognite_today.py --name F4101 --domain jdena --token "<jwt>"
  python scripts/databricks_cognite_today.py --name MARA --day 2026-09-21 --totals --token "<jwt>"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_HOST = "https://az-phx-001.cognitedata.com"
DEFAULT_PROJECT = "bdx-dev"
DEFAULT_RAW_DATABASE = "db_databricks_glb_raw"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "databricks_incremental_tables.yaml"
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TZ = ZoneInfo("America/Sao_Paulo")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def normalize_token(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


def quote_ident(name: str) -> str:
    name = name.strip()
    if not IDENT_RE.match(name):
        raise SystemExit(f"Invalid identifier: {name!r}")
    return f"`{name}`"


def quote_relation(view: str) -> str:
    parts = [p.strip() for p in view.split(".") if p.strip()]
    if not parts:
        raise SystemExit("Table name must not be empty.")
    return ".".join(quote_ident(p) for p in parts)


def short_name(fqn: str) -> str:
    leaf = fqn.split(".")[-1]
    leaf = re.sub(r"^v_cognite_", "", leaf, flags=re.I)
    leaf = re.sub(r"_(everest|jdeint|jdena)$", "", leaf, flags=re.I)
    return leaf.upper()


def infer_domain(name: str) -> str:
    lower = name.lower()
    if lower.endswith("_everest"):
        return "everest"
    if lower.endswith("_jdeint"):
        return "jdein"
    if lower.endswith("_jdena"):
        return "jdena"
    return "other"


def raw_table_for(short: str, domain: str) -> str:
    if domain == "everest":
        pascal = short[:1].upper() + short[1:].lower()
        return f"tb_{pascal}Everest"
    if domain == "jdein":
        return f"tb_{short}Jdeint"
    if domain == "jdena":
        return f"tb_{short}Jdena"
    raise SystemExit(f"Cannot map RAW table for domain {domain!r}. Pass --raw-table.")


def load_config_tables(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        import yaml
    except ImportError:
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    catalog = str(data.get("catalog") or "").strip()
    schema = str(data.get("schema") or "g_external").strip()
    rows: list[dict[str, str]] = []
    for item in data.get("tables") or []:
        if isinstance(item, str):
            item = {"name": item}
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if "." not in name:
            name = ".".join(p for p in (catalog, schema, name) if p)
        elif name.count(".") == 1 and catalog:
            name = f"{catalog}.{name}"
        rows.append(
            {
                "name": name,
                "domain": str(item.get("domain") or infer_domain(name)),
                "short": short_name(name),
            }
        )
    return rows


def resolve_target(
    name: str,
    domain: str,
    view: str,
    raw_table: str,
    config_path: Path,
) -> tuple[str, str, str]:
    """Return (view, raw_table, domain)."""
    if view.strip() and raw_table.strip():
        return view.strip(), raw_table.strip(), domain or infer_domain(view)

    tables = load_config_tables(config_path)
    wanted = (name or short_name(view or raw_table)).strip().upper()
    if not wanted and not view and not raw_table:
        raise SystemExit("Pass --name (e.g. MARA) or --view and --raw-table.")

    matches = [t for t in tables if t["short"] == wanted]
    if domain:
        matches = [t for t in matches if t["domain"] == domain]
    if not view.strip():
        if not matches:
            raise SystemExit(
                f"Table {wanted!r} not in {config_path}. "
                "Pass --view and --raw-table, or add it to the config."
            )
        if len(matches) > 1:
            domains = ", ".join(t["domain"] for t in matches)
            raise SystemExit(f"{wanted} exists in more than one domain ({domains}). Pass --domain.")
        view = matches[0]["name"]
        domain = matches[0]["domain"]
    else:
        view = view.strip()
        domain = domain or infer_domain(view)

    if not raw_table.strip():
        raw_table = raw_table_for(short_name(view), domain)
    return view, raw_table.strip(), domain


def start_of_day(day: date) -> tuple[str, str, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    nxt = start + timedelta(days=1)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        nxt.strftime("%Y-%m-%d %H:%M:%S"),
        int(start.timestamp() * 1000),
    )


def databricks_today(
    view: str,
    watermark: str,
    day_start: str,
    day_end: str,
    totals: bool,
) -> tuple[int, int | None, float]:
    host = _env("DATABRICKS_HOST").removeprefix("https://").removeprefix("http://").rstrip("/")
    http_path = _env("DATABRICKS_HTTP_PATH")
    token = _env("DATABRICKS_TOKEN")
    missing = [n for n, v in [
        ("DATABRICKS_HOST", host),
        ("DATABRICKS_HTTP_PATH", http_path),
        ("DATABRICKS_TOKEN", token),
    ] if not v]
    if missing:
        raise SystemExit("Missing env vars: " + ", ".join(missing))

    from databricks import sql

    relation = quote_relation(view)
    w = quote_ident(watermark)
    in_day = f"CAST({w} AS STRING) >= '{day_start}' AND CAST({w} AS STRING) < '{day_end}'"
    parts = [f"COUNT(CASE WHEN {in_day} THEN 1 END) AS today_rows"]
    if totals:
        parts.append("COUNT(*) AS total_rows")
    else:
        parts.append("CAST(NULL AS BIGINT) AS total_rows")
    sql_text = f"SELECT {', '.join(parts)} FROM {relation}"
    t0 = time.perf_counter()
    with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql_text)
            row = cursor.fetchone()
    elapsed = time.perf_counter() - t0
    today_n = 0 if row is None or row[0] is None else int(row[0])
    total = None if row is None or row[1] is None else int(row[1])
    return today_n, total, elapsed


def cognite_today(
    database: str,
    table: str,
    min_ms: int,
    totals: bool,
    token: str,
    host: str,
    project: str,
) -> tuple[int, int | None, float]:
    from cognite.client import ClientConfig, CogniteClient
    from cognite.client.config import global_config
    from cognite.client.credentials import Token

    global_config.disable_pypi_version_check = True
    client = CogniteClient(
        ClientConfig(
            client_name="databricks-cognite-today",
            project=project,
            base_url=host,
            credentials=Token(token),
        )
    )
    t0 = time.perf_counter()
    today_n = 0
    for _row in client.raw.rows.list(
        db_name=database,
        table_name=table,
        columns=[],
        min_last_updated_time=min_ms,
        limit=None,
    ):
        today_n += 1
    total: int | None = None
    if totals:
        total = 0
        for _row in client.raw.rows(
            db_name=database,
            table_name=table,
            columns=[],
            chunk_size=10_000,
            limit=None,
        ):
            total += 1
    elapsed = time.perf_counter() - t0
    return today_n, total, elapsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Count today's rows in one Databricks view and the matching Cognite RAW table."
    )
    p.add_argument("--name", default="", help="Short table id, e.g. MARA or F4101.")
    p.add_argument("--domain", default="", help="everest | jdein | jdena (required if the name is shared).")
    p.add_argument("--view", default="", help="Override Databricks view, e.g. g_external.v_cognite_mara_everest.")
    p.add_argument("--raw-table", default="", help="Override Cognite RAW table, e.g. tb_MaraEverest.")
    p.add_argument("--database", default=DEFAULT_RAW_DATABASE, help=f"RAW database (default: {DEFAULT_RAW_DATABASE}).")
    p.add_argument("--day", default="", help="YYYY-MM-DD (default: today America/Sao_Paulo).")
    p.add_argument("--token", default="", help="Cognite JWT. Overrides COGNITE_TOKEN.")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--watermark", default="DATETIMESTAMP")
    p.add_argument("--totals", action="store_true", help="Also COUNT(*) on Databricks and full RAW table (slow).")
    return p.parse_args(argv)


def fmt(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    day = date.fromisoformat(args.day) if args.day.strip() else datetime.now(TZ).date()
    day_start, day_end, min_ms = start_of_day(day)
    view, raw_table, domain = resolve_target(
        name=args.name,
        domain=args.domain.strip().lower(),
        view=args.view,
        raw_table=args.raw_table,
        config_path=Path(args.config),
    )
    cdf_token = normalize_token(args.token or _env("COGNITE_TOKEN"))
    cdf_host = _env("COGNITE_HOST", DEFAULT_HOST)
    cdf_project = _env("COGNITE_PROJECT", DEFAULT_PROJECT)
    if not cdf_token:
        print("Missing Cognite JWT. Set COGNITE_TOKEN or pass --token.", file=sys.stderr)
        return 1

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Day:           {day.isoformat()} (America/Sao_Paulo)")
    print(f"Domain:        {domain}")
    print(f"Databricks:    {view}")
    print(f"Cognite:       {args.database.strip()}.{raw_table}")
    print(f"DB filter:     {args.watermark} >= {day_start!r} AND < {day_end!r}")
    print(f"CDF filter:    lastUpdatedTime >= {min_ms}")
    print()

    try:
        db_today, db_total, db_s = databricks_today(
            view, args.watermark, day_start, day_end, args.totals
        )
    except Exception as exc:
        print(f"Databricks failed: {exc}", file=sys.stderr)
        return 1

    try:
        cdf_today, cdf_total, cdf_s = cognite_today(
            args.database.strip(),
            raw_table,
            min_ms,
            args.totals,
            cdf_token,
            cdf_host,
            cdf_project,
        )
    except Exception as exc:
        print(f"Cognite failed: {exc}", file=sys.stderr)
        return 1

    print(f"{'SIDE':<12} {'TODAY':>14} {'TOTAL':>16} {'ELAPSED':>10}")
    print("-" * 56)
    print(f"{'Databricks':<12} {fmt(db_today):>14} {fmt(db_total):>16} {db_s:9.1f}s")
    print(f"{'Cognite':<12} {fmt(cdf_today):>14} {fmt(cdf_total):>16} {cdf_s:9.1f}s")
    print("-" * 56)
    delta = db_today - cdf_today
    print(f"Delta today (Databricks - Cognite): {delta:+,}")
    print(
        "Databricks TODAY = source DATETIMESTAMP in the day. "
        "Cognite TODAY = RAW lastUpdatedTime in the day (extractor upsert). "
        "They can differ by lag, full-row hash appends, or restamped watermarks."
    )
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
