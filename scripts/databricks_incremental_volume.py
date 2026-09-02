"""
Daily incremental volume on Databricks views (no ODBC).

For each table in a YAML/JSON config:
  - total row count (optional, expensive on large tables)
  - rows the extractor would pick up that calendar day (watermark in [day, next day))
  - new vs updated, when a created column exists (ERDAT or config override)

Credentials:
  DATABRICKS_HOST
  DATABRICKS_HTTP_PATH
  DATABRICKS_TOKEN

Requires:
  pip install databricks-sql-connector
  pip install pyyaml   (only for .yaml / .yml configs)

Usage (PowerShell):
  $env:DATABRICKS_HOST = "adb-xxxx.azuredatabricks.net"
  $env:DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/..."
  $env:DATABRICKS_TOKEN = "dapi..."

  python scripts/databricks_incremental_volume.py

  python scripts/databricks_incremental_volume.py --day 2026-09-02 --domain everest
  python scripts/databricks_incremental_volume.py --no-total --name AFFW,MARA
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "databricks_incremental_tables.yaml"
REPORT_DIR = Path(__file__).resolve().parent / "reports"
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_credentials() -> tuple[str, str, str]:
    host = _env("DATABRICKS_HOST").removeprefix("https://").removeprefix("http://").rstrip("/")
    http_path = _env("DATABRICKS_HTTP_PATH")
    token = _env("DATABRICKS_TOKEN")
    missing = [
        n
        for n, v in [
            ("DATABRICKS_HOST", host),
            ("DATABRICKS_HTTP_PATH", http_path),
            ("DATABRICKS_TOKEN", token),
        ]
        if not v
    ]
    if missing:
        raise SystemExit(
            "Missing env vars: "
            + ", ".join(missing)
            + "\nSet them before running, e.g. in PowerShell:\n"
            '  $env:DATABRICKS_HOST = "adb-xxxx.azuredatabricks.net"\n'
            '  $env:DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/..."\n'
            '  $env:DATABRICKS_TOKEN = "dapi..."'
        )
    return host, http_path, token


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


def load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError:
            raise SystemExit("pyyaml is required for YAML configs. Install with: pip install pyyaml")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("Config root must be an object.")
    return data


def infer_domain(name: str) -> str:
    lower = name.lower()
    if lower.endswith("_everest"):
        return "everest"
    if lower.endswith("_jdeint"):
        return "jdein"
    if lower.endswith("_jdena"):
        return "jdena"
    return "other"


def short_name(fqn: str) -> str:
    leaf = fqn.split(".")[-1]
    leaf = re.sub(r"^v_cognite_", "", leaf, flags=re.I)
    leaf = re.sub(r"_(everest|jdeint|jdena)$", "", leaf, flags=re.I)
    return leaf.upper()


def resolve_table(item: Any, catalog: str, schema: str) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict) or not item.get("name"):
        raise SystemExit(f"Each table needs a name: {item!r}")
    name = str(item["name"]).strip()
    if "." not in name:
        parts = [p for p in (catalog, schema, name) if p]
        name = ".".join(parts)
    return {
        "name": name,
        "domain": str(item.get("domain") or infer_domain(name)),
        "watermark_column": item.get("watermark_column"),
        "created_column": item.get("created_column", "__default__"),
        "count_total": item.get("count_total"),
    }


def parse_day(raw: str, tz_name: str) -> date:
    if raw.strip():
        return date.fromisoformat(raw.strip())
    return datetime.now(ZoneInfo(tz_name)).date()


def describe_columns(cursor: Any, relation: str) -> set[str]:
    cursor.execute(f"DESCRIBE TABLE {relation}")
    names: set[str] = set()
    for row in cursor.fetchall() or []:
        col = str(row[0]).strip() if row and row[0] is not None else ""
        if col and not col.startswith("#") and IDENT_RE.match(col):
            names.add(col.upper())
    return names


def pick_created_column(
    configured: Any,
    default_created: str,
    columns: set[str],
) -> str:
    if configured is None or configured == "":
        return ""
    if configured != "__default__":
        name = str(configured).strip()
        return name if name.upper() in columns else ""
    if default_created and default_created.upper() in columns:
        return default_created
    for candidate in ("ERDAT", "ERDATUM", "CREATEDATE", "CREATE_DATE", "CRTDATE"):
        if candidate in columns:
            return candidate
    return ""


def build_sql(
    relation: str,
    watermark: str,
    created: str,
    day_start: str,
    day_end: str,
    created_start: str,
    created_end: str,
    count_total: bool,
) -> str:
    w = quote_ident(watermark)
    in_day = f"CAST({w} AS STRING) >= '{day_start}' AND CAST({w} AS STRING) < '{day_end}'"
    parts = [
        f"COUNT(CASE WHEN {in_day} THEN 1 END) AS incremental_rows",
    ]
    if count_total:
        parts.insert(0, "COUNT(*) AS total_rows")
    else:
        parts.insert(0, "CAST(NULL AS BIGINT) AS total_rows")
    if created:
        c = quote_ident(created)
        created_in_day = (
            f"CAST({c} AS STRING) >= '{created_start}' AND CAST({c} AS STRING) < '{created_end}' "
            f"AND CAST({c} AS STRING) NOT LIKE '0000%'"
        )
        updated = (
            f"{in_day} AND (CAST({c} AS STRING) < '{created_start}' "
            f"OR CAST({c} AS STRING) LIKE '0000%' OR {c} IS NULL)"
        )
        parts.append(f"COUNT(CASE WHEN {created_in_day} THEN 1 END) AS new_rows")
        parts.append(f"COUNT(CASE WHEN {updated} THEN 1 END) AS updated_rows")
    else:
        parts.append("CAST(NULL AS BIGINT) AS new_rows")
        parts.append("CAST(NULL AS BIGINT) AS updated_rows")
    return f"SELECT {', '.join(parts)} FROM {relation}"


@dataclass
class TableStat:
    domain: str
    table: str
    view: str
    watermark_column: str
    created_column: str
    total_rows: int | None
    incremental_rows: int
    new_rows: int | None
    updated_rows: int | None
    elapsed_s: float
    status: str
    error: str = ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count Databricks rows and daily incremental new vs updated volume."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"YAML/JSON table list (default: {DEFAULT_CONFIG}).",
    )
    parser.add_argument("--day", default="", help="Calendar day YYYY-MM-DD (default: today in config timezone).")
    parser.add_argument("--domain", default="", help="Comma list: everest,jdein,jdena.")
    parser.add_argument("--name", default="", help="Comma list of short names, e.g. AFFW,F4111.")
    parser.add_argument(
        "--no-total",
        action="store_true",
        help="Skip COUNT(*) even when the config asks for it.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Write JSON report path (default: scripts/reports/incremental_YYYY-MM-DD.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config)
    if not config_path.is_file():
        raise SystemExit(f"Config not found: {config_path}")

    cfg = load_config(config_path)
    catalog = str(cfg.get("catalog") or "hub_dev").strip()
    schema = str(cfg.get("schema") or "g_external").strip()
    tz_name = str(cfg.get("timezone") or "America/Sao_Paulo").strip()
    default_watermark = str(cfg.get("watermark_column") or "DATETIMESTAMP").strip()
    default_created = str(cfg.get("created_column") or "").strip()
    default_count_total = bool(cfg.get("count_total", False))
    day = parse_day(args.day, tz_name)
    next_day = day + timedelta(days=1)
    day_start = f"{day.isoformat()} 00:00:00"
    day_end = f"{next_day.isoformat()} 00:00:00"
    created_start = day.isoformat()
    created_end = next_day.isoformat()

    tables = [resolve_table(t, catalog, schema) for t in (cfg.get("tables") or [])]
    if not tables:
        raise SystemExit("Config has no tables.")

    domains = {d.strip().lower() for d in args.domain.split(",") if d.strip()}
    names = {n.strip().upper() for n in args.name.split(",") if n.strip()}
    if domains:
        tables = [t for t in tables if t["domain"].lower() in domains]
    if names:
        tables = [t for t in tables if short_name(t["name"]) in names]
    if not tables:
        raise SystemExit("No tables left after --domain / --name filters.")

    try:
        from databricks import sql
    except ImportError:
        print(
            "databricks-sql-connector is required. Install with:\n"
            "  pip install databricks-sql-connector",
            file=sys.stderr,
        )
        return 1

    host, http_path, token = load_credentials()
    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Host:          {host}")
    print(f"Day:           {day.isoformat()} ({tz_name})")
    print(f"Watermark:     {default_watermark} >= {day_start!r} AND < {day_end!r}")
    print(f"Config:        {config_path}")
    print(f"Tables:        {len(tables)}")
    print()
    header = (
        f"{'DOMAIN':<10} {'TABLE':<12} {'TOTAL':>16} {'INCREMENTAL':>14} "
        f"{'NEW':>12} {'UPDATED':>12} {'ELAPSED':>9}  STATUS"
    )
    print(header)
    print("-" * len(header))

    stats: list[TableStat] = []
    errors = 0

    with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
        with conn.cursor() as cursor:
            for item in tables:
                view = item["name"]
                relation = quote_relation(view)
                table = short_name(view)
                t0 = time.perf_counter()
                try:
                    columns = describe_columns(cursor, relation)
                    watermark = str(item["watermark_column"] or default_watermark)
                    if watermark.upper() not in columns:
                        raise RuntimeError(f"missing watermark column {watermark}")
                    created = pick_created_column(item["created_column"], default_created, columns)
                    count_total = False if args.no_total else (
                        default_count_total if item["count_total"] is None else bool(item["count_total"])
                    )
                    sql_text = build_sql(
                        relation=relation,
                        watermark=watermark,
                        created=created,
                        day_start=day_start,
                        day_end=day_end,
                        created_start=created_start,
                        created_end=created_end,
                        count_total=count_total,
                    )
                    cursor.execute(sql_text)
                    row = cursor.fetchone()
                    total = None if row is None or row[0] is None else int(row[0])
                    incremental = 0 if row is None or row[1] is None else int(row[1])
                    new_rows = None if row is None or row[2] is None else int(row[2])
                    updated_rows = None if row is None or row[3] is None else int(row[3])
                    elapsed = time.perf_counter() - t0
                    stat = TableStat(
                        domain=item["domain"],
                        table=table,
                        view=view,
                        watermark_column=watermark,
                        created_column=created,
                        total_rows=total,
                        incremental_rows=incremental,
                        new_rows=new_rows,
                        updated_rows=updated_rows,
                        elapsed_s=elapsed,
                        status="OK",
                    )
                except Exception as exc:
                    errors += 1
                    elapsed = time.perf_counter() - t0
                    msg = str(exc).split("\n", 1)[0]
                    stat = TableStat(
                        domain=item["domain"],
                        table=table,
                        view=view,
                        watermark_column=str(item["watermark_column"] or default_watermark),
                        created_column="",
                        total_rows=None,
                        incremental_rows=0,
                        new_rows=None,
                        updated_rows=None,
                        elapsed_s=elapsed,
                        status="ERROR",
                        error=msg,
                    )

                stats.append(stat)
                total_s = f"{stat.total_rows:,}" if stat.total_rows is not None else "-"
                new_s = f"{stat.new_rows:,}" if stat.new_rows is not None else "-"
                upd_s = f"{stat.updated_rows:,}" if stat.updated_rows is not None else "-"
                note = stat.status if stat.status == "OK" else stat.error[:40]
                print(
                    f"{stat.domain:<10} {stat.table:<12} {total_s:>16} "
                    f"{stat.incremental_rows:>14,} {new_s:>12} {upd_s:>12} "
                    f"{stat.elapsed_s:8.1f}s  {note}"
                )

    ok = [s for s in stats if s.status == "OK"]
    inc_sum = sum(s.incremental_rows for s in ok)
    new_sum = sum(s.new_rows or 0 for s in ok)
    upd_sum = sum(s.updated_rows or 0 for s in ok)
    print("-" * len(header))
    print(
        f"OK {len(ok)}/{len(stats)}  incremental={inc_sum:,}  "
        f"new={new_sum:,}  updated={upd_sum:,}"
    )
    print(
        "new/updated: created-column in day vs watermark in day with older create date. "
        "'-' means the view has no created column (all incremental rows still need to load)."
    )
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.json_out) if args.json_out else REPORT_DIR / f"incremental_{day.isoformat()}.json"
    payload = {
        "day": day.isoformat(),
        "timezone": tz_name,
        "host": host,
        "watermark_window": [day_start, day_end],
        "tables": [asdict(s) for s in stats],
        "sum_incremental": inc_sum,
        "sum_new": new_sum,
        "sum_updated": upd_sum,
        "ok": len(ok),
        "errors": errors,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"JSON: {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
