"""
Volume of LIKP (delivery header) and LIPS (delivery item) by plant and ERDAT window.

SAP relationship:
  LIKP is one row per delivery (VBELN). It has ERDAT and no plant.
  LIPS is one row per delivery item (VBELN + POSNR). WERKS and ERDAT are on the item.
  A header belongs to a plant when at least one of its items has that WERKS.
  The same VBELN can have items in more than one plant.

This script keeps the two tables separate, which matches a table-by-table copy into
Cognite RAW. It answers:
  1. LIPS rows you keep if you filter the item table by WERKS and ERDAT.
  2. LIKP rows you keep if you filter the header table by ERDAT only.
  3. LIKP deliveries you keep if the header query also requires an item in these plants.
  4. How many of those deliveries span more than one of the listed plants.

Periods are inclusive of the as-of date and look back 1 month, 6 months, 1 year and 3 years
on ERDAT. ERDAT is accepted as yyyyMMdd or yyyy-MM-dd. 00000000 and blank dates are counted
as unparsed and are excluded from the windows.

Credentials (env vars):
  DATABRICKS_HOST       e.g. adb-xxxx.azuredatabricks.net  (no https://)
  DATABRICKS_HTTP_PATH  e.g. /sql/1.0/warehouses/<id>
  DATABRICKS_TOKEN      personal access token (dapi...)

Requires:
  pip install databricks-sql-connector

Usage (PowerShell):
  $env:DATABRICKS_HOST = "adb-xxxx.azuredatabricks.net"
  $env:DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/..."
  $env:DATABRICKS_TOKEN = "dapi..."

  python scripts/databricks_likp_lips_volume.py
  python scripts/databricks_likp_lips_volume.py --catalog hub_prd
  python scripts/databricks_likp_lips_volume.py --pair vbak --catalog hub_dev

VBAK/VBAP uses the same rule as LIKP/LIPS. VBAP is the item (WERKS + ERDAT).
VBAK is the header (ERDAT). A header belongs to a plant when one of its items
has that WERKS. Pass --pair vbak.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "scripts" / "reports"
TZ_NAME = "America/Sao_Paulo"
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PAIRS = {
    "likp": {
        "header": "LIKP",
        "item": "LIPS",
        "header_view": "g_external.v_cognite_likp_everest",
        "item_view": "g_external.v_cognite_lips_everest",
    },
    "vbak": {
        "header": "VBAK",
        "item": "VBAP",
        "header_view": "g_external.v_cognite_vbak_everest",
        "item_view": "g_external.v_cognite_vbap_everest",
    },
}
DEFAULT_PLANTS = (
    "1024",
    "1325",
    "1100",
    "1326",
    "1215",
    "1327",
    "1441",
    "1442",
    "1324",
)


@dataclass
class Window:
    key: str
    label: str
    start: date


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def load_credentials() -> tuple[str, str, str]:
    host = _env("DATABRICKS_HOST").removeprefix("https://").removeprefix("http://").rstrip("/")
    http_path = _env("DATABRICKS_HTTP_PATH")
    token = _env("DATABRICKS_TOKEN")
    missing = [
        name
        for name, value in (
            ("DATABRICKS_HOST", host),
            ("DATABRICKS_HTTP_PATH", http_path),
            ("DATABRICKS_TOKEN", token),
        )
        if not value
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


def quote_relation(name: str) -> str:
    parts = [part.strip() for part in name.split(".") if part.strip()]
    if not parts:
        raise SystemExit("Relation name must not be empty.")
    return ".".join(quote_ident(part) for part in parts)


def qualify(catalog: str, relation: str) -> str:
    rel = relation.strip()
    if catalog and rel.count(".") < 2:
        rel = f"{catalog}.{rel}"
    return quote_relation(rel)


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def parse_plants(raw: str) -> tuple[str, ...]:
    plants = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not plants:
        raise SystemExit("--plants must contain at least one plant code.")
    for plant in plants:
        if not plant.isdigit():
            raise SystemExit(f"Invalid plant code: {plant!r}")
    return plants


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Header/item volume by plant (WERKS) and ERDAT window. LIKP/LIPS or VBAK/VBAP."
    )
    parser.add_argument(
        "--pair",
        choices=tuple(PAIRS),
        default="likp",
        help="likp = LIKP header + LIPS item. vbak = VBAK header + VBAP item.",
    )
    parser.add_argument("--catalog", default="", help="Unity Catalog. Default: warehouse session catalog.")
    parser.add_argument("--header", default="", help="Header view. Default comes from --pair.")
    parser.add_argument("--item", default="", help="Item view. Default comes from --pair.")
    parser.add_argument(
        "--plants",
        default=",".join(DEFAULT_PLANTS),
        help="Comma-separated WERKS codes",
    )
    parser.add_argument("--as-of", default="", help="Window end date YYYY-MM-DD (default: today in America/Sao_Paulo)")
    parser.add_argument("--json-out", default="", help="JSON report path")
    return parser.parse_args(argv)


def describe_columns(cursor, relation: str) -> dict[str, str]:
    cursor.execute(f"DESCRIBE TABLE {relation}")
    found: dict[str, str] = {}
    for row in cursor.fetchall() or []:
        col = str(row[0]).strip() if row and row[0] is not None else ""
        if col and not col.startswith("#") and IDENT_RE.match(col):
            found.setdefault(col.upper(), col)
    return found


def require_column(columns: dict[str, str], name: str, relation: str) -> str:
    actual = columns.get(name.upper())
    if not actual:
        raise SystemExit(f"{relation} has no {name} column. Columns include: {', '.join(sorted(columns)[:40])}")
    return actual


def erdat_expr(column: str) -> str:
    quoted = quote_ident(column)
    text = f"CAST({quoted} AS STRING)"
    return (
        "CASE "
        f"WHEN {text} RLIKE '^[0-9]{{8}}$' AND {text} NOT LIKE '0000%' THEN to_date({text}, 'yyyyMMdd') "
        f"WHEN {text} RLIKE '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' THEN to_date(substr({text}, 1, 10)) "
        "ELSE NULL END"
    )


def windows(as_of: date) -> list[Window]:
    return [
        Window("1m", "1 month", add_months(as_of, -1)),
        Window("6m", "6 months", add_months(as_of, -6)),
        Window("1y", "1 year", add_months(as_of, -12)),
        Window("3y", "3 years", add_months(as_of, -36)),
    ]


def window_sums(alias: str, periods: list[Window]) -> str:
    parts = []
    for period in periods:
        parts.append(
            f"SUM(CASE WHEN {alias} >= DATE '{period.start.isoformat()}' THEN 1 ELSE 0 END) AS rows_{period.key}"
        )
    return ",\n  ".join(parts)


def fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def print_table(headers: list[str], rows: list[list]) -> None:
    cells = [[fmt(value) for value in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print("  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    print()


def fetchone(cursor, sql_text: str, label: str) -> tuple:
    print(f"--- {label} ---")
    t0 = time.perf_counter()
    cursor.execute(sql_text)
    row = cursor.fetchone()
    print(f"elapsed: {time.perf_counter() - t0:.1f}s\n")
    if row is None:
        raise SystemExit(f"{label} returned no row.")
    return tuple(row)


def fetchall(cursor, sql_text: str, label: str) -> list[tuple]:
    print(f"--- {label} ---")
    t0 = time.perf_counter()
    cursor.execute(sql_text)
    rows = [tuple(row) for row in (cursor.fetchall() or [])]
    print(f"elapsed: {time.perf_counter() - t0:.1f}s  groups: {len(rows)}\n")
    return rows


def pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "-"
    return f"{(100.0 * part / whole):.2f}%"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pair = PAIRS[args.pair]
    header_label = pair["header"]
    item_label = pair["item"]
    header_view = args.header.strip() or pair["header_view"]
    item_view = args.item.strip() or pair["item_view"]
    plants = parse_plants(args.plants)
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(ZoneInfo(TZ_NAME)).date()
    periods = windows(as_of)
    plant_sql = ", ".join(f"'{plant}'" for plant in plants)
    host, http_path, token = load_credentials()

    try:
        from databricks import sql
    except ImportError:
        print(
            "databricks-sql-connector is required. Install with:\n"
            "  pip install databricks-sql-connector",
            file=sys.stderr,
        )
        return 1

    likp = qualify(args.catalog, header_view)
    lips = qualify(args.catalog, item_view)

    with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_catalog(), current_database()")
            session = cursor.fetchone()
            session_catalog = str(session[0] or "") if session else ""
            session_schema = str(session[1] or "") if session else ""

            print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
            print(f"Host:          {host}")
            print(f"HTTP path:     {http_path}")
            print(f"Catalog:       {session_catalog or '-'}")
            print(f"Schema:        {session_schema or '-'}")
            print(f"Pair:          {args.pair} ({header_label} header, {item_label} item)")
            print(f"{header_label}:          {likp}")
            print(f"{item_label}:          {lips}")
            print(f"Plants:        {', '.join(plants)}")
            print(f"As of:         {as_of.isoformat()} ({TZ_NAME})")
            for period in periods:
                print(f"Window {period.label:<8} ERDAT >= {period.start.isoformat()}")
            print()

            likp_columns = describe_columns(cursor, likp)
            lips_columns = describe_columns(cursor, lips)
            likp_vbeln = require_column(likp_columns, "VBELN", header_label)
            likp_erdat = require_column(likp_columns, "ERDAT", header_label)
            lips_vbeln = require_column(lips_columns, "VBELN", item_label)
            lips_erdat = require_column(lips_columns, "ERDAT", item_label)
            lips_werks = require_column(lips_columns, "WERKS", item_label)
            likp_werks = likp_columns.get("WERKS")
            print(
                f"{header_label}.WERKS:    "
                + (likp_werks or f"absent — plant filter for the header has to come from {item_label}.VBELN")
            )
            print()

            lips_erdat_sql = erdat_expr(lips_erdat)
            likp_erdat_sql = erdat_expr(likp_erdat)
            lips_werks_sql = f"TRIM(CAST({quote_ident(lips_werks)} AS STRING))"
            lips_sql = f"""
SELECT
  CASE
    WHEN werks_norm IN ({plant_sql}) THEN werks_norm
    WHEN werks_norm IS NULL OR werks_norm = '' THEN '(blank)'
    ELSE '(other)'
  END AS werks,
  COUNT(*) AS rows_all,
  {window_sums('erdat', periods)},
  SUM(CASE WHEN erdat IS NULL THEN 1 ELSE 0 END) AS erdat_unparsed,
  COUNT(DISTINCT vbeln) AS deliveries
FROM (
  SELECT
    {lips_werks_sql} AS werks_norm,
    {lips_erdat_sql} AS erdat,
    CAST({quote_ident(lips_vbeln)} AS STRING) AS vbeln
  FROM {lips}
) src
GROUP BY
  CASE
    WHEN werks_norm IN ({plant_sql}) THEN werks_norm
    WHEN werks_norm IS NULL OR werks_norm = '' THEN '(blank)'
    ELSE '(other)'
  END
"""
            lips_rows = fetchall(cursor, lips_sql, f"{item_label} by plant (item ERDAT, no join)")
            by_werks = {str(row[0]): row for row in lips_rows}

            ordered_keys = list(plants) + ["(blank)", "(other)"]
            lips_table: list[list] = []
            lips_payload: list[dict] = []
            lips_value_count = 1 + len(periods) + 2
            for key in ordered_keys:
                row = by_werks.get(key)
                values = [int(value or 0) for value in row[1:]] if row else [0] * lips_value_count
                lips_table.append([key, *values])
                entry = {"werks": key}
                names = [f"rows_{period.key}" for period in periods]
                entry["rows_all"] = int(values[0] or 0)
                for offset, name in enumerate(names):
                    entry[name] = int(values[offset + 1] or 0)
                entry["erdat_unparsed"] = int(values[len(periods) + 1] or 0)
                entry["deliveries"] = int(values[len(periods) + 2] or 0)
                lips_payload.append(entry)

            plant_entries = [entry for entry in lips_payload if entry["werks"] in plants]
            all_rows = sum(entry["rows_all"] for entry in lips_payload)
            selected_rows = sum(entry["rows_all"] for entry in plant_entries)
            all_entry = {
                "werks": "(selected plants)",
                "rows_all": selected_rows,
                "erdat_unparsed": sum(entry["erdat_unparsed"] for entry in plant_entries),
                "deliveries": None,
            }
            for period in periods:
                all_entry[f"rows_{period.key}"] = sum(entry[f"rows_{period.key}"] for entry in plant_entries)
            lips_table.append(
                [
                    "(selected plants)",
                    all_entry["rows_all"],
                    *[all_entry[f"rows_{period.key}"] for period in periods],
                    all_entry["erdat_unparsed"],
                    "-",
                ]
            )
            lips_payload.append(all_entry)

            print(f"{item_label} — filter the item table alone (WERKS + ERDAT)")
            print(f"Selected plants are {pct(selected_rows, all_rows)} of all {item_label} rows.")
            print("Deliveries on (selected plants) is omitted: summing plants double-counts a VBELN that spans plants.")
            print_table(
                ["WERKS", "ALL_ROWS", *[period.key.upper() for period in periods], "ERDAT_BAD", "DELIVERIES"],
                lips_table,
            )

            likp_sql = f"""
SELECT
  COUNT(*) AS rows_all,
  {window_sums('erdat', periods)},
  SUM(CASE WHEN erdat IS NULL THEN 1 ELSE 0 END) AS erdat_unparsed,
  COUNT(DISTINCT vbeln) AS deliveries
FROM (
  SELECT
    {likp_erdat_sql} AS erdat,
    CAST({quote_ident(likp_vbeln)} AS STRING) AS vbeln
  FROM {likp}
) src
"""
            likp_row = fetchone(cursor, likp_sql, f"{header_label} by header ERDAT only (no plant filter)")
            likp_names = ["rows_all", *[f"rows_{period.key}" for period in periods], "erdat_unparsed", "deliveries"]
            likp_payload = {
                name: int(likp_row[index] or 0) for index, name in enumerate(likp_names)
            }
            print(f"{header_label} — date filter only. This is the header volume if the copy does not look at {item_label}.")
            print_table(
                ["SCOPE", "ALL_ROWS", *[period.key.upper() for period in periods], "ERDAT_BAD", "DELIVERIES"],
                [["(all plants)", *[likp_payload[name] for name in likp_names]]],
            )

            matched_sql = f"""
WITH items AS (
  SELECT
    {lips_werks_sql} AS werks,
    CAST({quote_ident(lips_vbeln)} AS STRING) AS vbeln
  FROM {lips}
  WHERE {lips_werks_sql} IN ({plant_sql})
),
headers AS (
  SELECT
    CAST({quote_ident(likp_vbeln)} AS STRING) AS vbeln,
    {likp_erdat_sql} AS erdat
  FROM {likp}
)
SELECT
  CASE WHEN GROUPING(items.werks) = 1 THEN '(selected plants)' ELSE items.werks END AS werks,
  COUNT(DISTINCT items.vbeln) AS item_deliveries,
  COUNT(DISTINCT headers.vbeln) AS headers_matched,
  {", ".join(
      f"COUNT(DISTINCT CASE WHEN headers.erdat >= DATE '{period.start.isoformat()}' THEN headers.vbeln END) AS headers_{period.key}"
      for period in periods
  )},
  COUNT(DISTINCT CASE WHEN headers.vbeln IS NULL THEN items.vbeln END) AS deliveries_without_header
FROM items
LEFT JOIN headers ON items.vbeln = headers.vbeln
GROUP BY GROUPING SETS ((items.werks), ())
"""
            matched_rows = fetchall(
                cursor,
                matched_sql,
                f"{header_label} headers that have an item in the selected plants",
            )
            matched_by_werks = {str(row[0]): row for row in matched_rows}
            matched_order = list(plants) + ["(selected plants)"]
            matched_table = []
            matched_payload = []
            matched_fields = [
                "item_deliveries",
                "headers_matched",
                *[f"headers_{period.key}" for period in periods],
                "deliveries_without_header",
            ]
            for key in matched_order:
                row = matched_by_werks.get(key)
                values = [int(value or 0) for value in row[1:]] if row else [0] * len(matched_fields)
                matched_table.append([key, *values])
                entry = {"werks": key}
                for index, name in enumerate(matched_fields):
                    entry[name] = int(values[index] or 0)
                matched_payload.append(entry)

            print(f"{header_label} — header kept only when VBELN exists on {item_label} for that plant.")
            print("A delivery with items in two plants is counted in each plant and once in (selected plants).")
            print_table(
                ["WERKS", f"{item_label}_VBELN", f"{header_label}_MATCHED", *[f"H_{period.key.upper()}" for period in periods], "NO_HEADER"],
                matched_table,
            )

            multi_sql = f"""
SELECT COUNT(*) AS multi_plant_deliveries
FROM (
  SELECT CAST({quote_ident(lips_vbeln)} AS STRING) AS vbeln
  FROM {lips}
  WHERE {lips_werks_sql} IN ({plant_sql})
  GROUP BY CAST({quote_ident(lips_vbeln)} AS STRING)
  HAVING COUNT(DISTINCT {lips_werks_sql}) > 1
) src
"""
            multi_row = fetchone(cursor, multi_sql, "Deliveries spanning more than one selected plant")
            multi_plant = int(multi_row[0] or 0)
            print(f"Deliveries with items in more than one selected plant: {multi_plant:,}")
            print()

            likp_by_werks_payload = None
            if likp_werks:
                likp_werks_sql = f"TRIM(CAST({quote_ident(likp_werks)} AS STRING))"
                likp_plant_sql = f"""
SELECT
  CASE
    WHEN werks_norm IN ({plant_sql}) THEN werks_norm
    WHEN werks_norm IS NULL OR werks_norm = '' THEN '(blank)'
    ELSE '(other)'
  END AS werks,
  COUNT(*) AS rows_all,
  {window_sums('erdat', periods)}
FROM (
  SELECT
    {likp_werks_sql} AS werks_norm,
    {likp_erdat_sql} AS erdat
  FROM {likp}
) src
GROUP BY
  CASE
    WHEN werks_norm IN ({plant_sql}) THEN werks_norm
    WHEN werks_norm IS NULL OR werks_norm = '' THEN '(blank)'
    ELSE '(other)'
  END
"""
                direct_rows = fetchall(cursor, likp_plant_sql, f"{header_label} has WERKS; volume by header plant")
                print(f"{header_label}.WERKS is present, so the header can also be filtered without joining {item_label}.")
                print_table(
                    ["WERKS", "ALL_ROWS", *[period.key.upper() for period in periods]],
                    [[row[0], *[int(value or 0) for value in row[1:]]] for row in direct_rows],
                )
                likp_by_werks_payload = [
                    {"werks": str(row[0]), "rows_all": int(row[1] or 0)} for row in direct_rows
                ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    default_json = REPORT_DIR / f"{header_label.lower()}_{item_label.lower()}_volume_{as_of.isoformat()}.json"
    out = Path(args.json_out) if args.json_out else default_json
    payload = {
        "as_of": as_of.isoformat(),
        "timezone": TZ_NAME,
        "host": host,
        "http_path": http_path,
        "catalog": session_catalog,
        "schema": session_schema,
        "pair": args.pair,
        "header": header_label,
        "item": item_label,
        "header_view": header_view,
        "item_view": item_view,
        "plants": list(plants),
        "windows": [{"key": period.key, "label": period.label, "start": period.start.isoformat()} for period in periods],
        "likp_has_werks": bool(likp_werks),
        "lips_by_werks": lips_payload,
        "lips_selected_share": pct(selected_rows, all_rows),
        "likp_by_erdat": likp_payload,
        "likp_matched_by_werks": matched_payload,
        "multi_plant_deliveries": multi_plant,
        "likp_werks_direct": likp_by_werks_payload,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"JSON: {out}")
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
