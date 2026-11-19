"""
Count rows for JDE NA views in Databricks via ODBC, or group one view by plant.

Credentials (env vars, same shape as jde-na/config.yaml ODBC block):
  DATABRICKS_HOST
  DATABRICKS_HTTP_PATH
  DATABRICKS_TOKEN

Optional:
  DATABRICKS_ODBC_DRIVER  (default: Databricks ODBC Driver)

Usage:
  python sap/databricks_tables_rowcount_jde_na.py

  python sap/databricks_tables_rowcount_jde_na.py --by-plant
  python sap/databricks_tables_rowcount_jde_na.py --by-plant --view hub_prd.g_external.v_cognite_resb_everest
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

# Same 30 views as jde-na/config.yaml (spreadsheet order).
_TABLE_IDS = [
    "F42019", "F42119", "F4108", "F3002", "F4111", "F41021", "F4101", "F0006", "F0010", "F4102",
    "F0101", "F03012", "F4105", "F30026", "F4801", "F3111", "F3112", "F3003", "F30008", "F0911",
    "F3411", "F4301", "F4311", "F43199", "F43092", "F43121", "F4201", "F4211", "F41002", "F40072",
]
TABLES: list[tuple[str, str]] = [
    (t, f"hub_dev.g_external.v_cognite_{t}_jdena") for t in _TABLE_IDS
]
DEFAULT_PLANT_VIEW = "hub_prd.g_external.v_cognite_resb_everest"
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def connection_string() -> str:
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    http_path = os.environ.get("DATABRICKS_HTTP_PATH", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    driver = os.environ.get("DATABRICKS_ODBC_DRIVER", "Databricks ODBC Driver").strip()

    missing = [n for n, v in [
        ("DATABRICKS_HOST", host),
        ("DATABRICKS_HTTP_PATH", http_path),
        ("DATABRICKS_TOKEN", token),
    ] if not v]
    if missing:
        raise SystemExit(
            "Missing env vars: " + ", ".join(missing) + "\n"
            "Set them before running, e.g. in PowerShell:\n"
            '  $env:DATABRICKS_HOST = "adb-xxxx.azuredatabricks.net"\n'
            '  $env:DATABRICKS_HTTP_PATH = "/sql/1.0/warehouses/..."\n'
            '  $env:DATABRICKS_TOKEN = "dapi..."'
        )

    return (
        f"Driver={{{driver}}};"
        f"Host={host};"
        "Port=443;"
        f"HTTPPath={http_path};"
        "SSL=1;"
        "ThriftTransport=2;"
        "AuthMech=3;"
        "UID=token;"
        f"PWD={token};"
    )


def count_rows(cursor: Any, view: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {view}")
    row = cursor.fetchone()
    return int(row[0])


def quote_ident(name: str) -> str:
    name = name.strip()
    if not IDENT_RE.match(name):
        raise SystemExit(f"Invalid identifier: {name!r}")
    return f"`{name}`"


def quote_relation(name: str) -> str:
    parts = [part.strip() for part in name.split(".") if part.strip()]
    if not parts:
        raise SystemExit("View name must not be empty.")
    return ".".join(quote_ident(part) for part in parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count JDE NA views, or volume of one view by plant."
    )
    parser.add_argument(
        "--by-plant",
        action="store_true",
        help="Group one view by plant instead of counting the JDE NA list.",
    )
    parser.add_argument("--view", default=DEFAULT_PLANT_VIEW, help=f"View for --by-plant (default: {DEFAULT_PLANT_VIEW})")
    parser.add_argument("--plant-column", default="WERKS", help="Plant column (default: WERKS)")
    parser.add_argument("--timestamp-column", default="DATETIMESTAMP", help="Extraction timestamp (default: DATETIMESTAMP)")
    return parser.parse_args(argv)


def volume_by_plant(cursor: Any, view: str, plant_column: str, timestamp_column: str) -> int:
    relation = quote_relation(view)
    plant = quote_ident(plant_column)
    watermark = quote_ident(timestamp_column)
    sql_text = f"""
SELECT
    {plant} AS plant,
    COUNT(*) AS total_records,
    MIN({watermark}) AS first_extraction,
    MAX({watermark}) AS last_extraction
FROM {relation}
GROUP BY {plant}
ORDER BY total_records DESC
"""
    print(f"View:      {view}")
    print(f"Plant:     {plant_column}")
    print(f"Timestamp: {timestamp_column}")
    print()
    t0 = time.perf_counter()
    cursor.execute(sql_text)
    rows = cursor.fetchall() or []
    elapsed = time.perf_counter() - t0

    header = f"{'PLANT':<12} {'TOTAL':>16}  {'FIRST_EXTRACTION':<26}  LAST_EXTRACTION"
    print(header)
    print("-" * len(header))
    total = 0
    for row in rows:
        plant_value = "(blank)" if row[0] in (None, "") else str(row[0]).strip() or "(blank)"
        count = int(row[1] or 0)
        total += count
        first = "-" if row[2] is None else str(row[2])
        last = "-" if row[3] is None else str(row[3])
        print(f"{plant_value:<12} {count:>16,}  {first:<26}  {last}")
    print("-" * len(header))
    print(f"Plants: {len(rows)}  total rows: {total:,}  elapsed: {elapsed:.1f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        import pyodbc
    except ImportError:
        print("pyodbc is required. Install with: pip install pyodbc", file=sys.stderr)
        return 1

    args = parse_args(argv)
    started = datetime.now(timezone.utc)
    print(f"Started (UTC): {started.isoformat()}")
    if not args.by_plant:
        print(f"Tables: {len(TABLES)}")
        print("-" * 72)

    conn = pyodbc.connect(connection_string(), autocommit=True)
    try:
        cursor = conn.cursor()
        if args.by_plant:
            return volume_by_plant(cursor, args.view, args.plant_column, args.timestamp_column)

        results: list[tuple[str, str, int | None, str, float]] = []

        for label, view in TABLES:
            t0 = time.perf_counter()
            try:
                n = count_rows(cursor, view)
                elapsed = time.perf_counter() - t0
                results.append((label, view, n, "OK", elapsed))
                print(f"{label:8}  {n:>18,}  {elapsed:7.1f}s  OK  {view}")
            except Exception as exc:  # noqa: BLE001 - surface ODBC errors per table
                elapsed = time.perf_counter() - t0
                msg = str(exc).split("\n", 1)[0]
                results.append((label, view, None, msg, elapsed))
                print(f"{label:8}  {'ERROR':>18}  {elapsed:7.1f}s  {msg}")

        print("-" * 72)
        ok = [r for r in results if r[2] is not None]
        if ok:
            total = sum(r[2] for r in ok)  # type: ignore[misc]
            print(f"OK: {len(ok)}/{len(results)}  |  sum of counted rows: {total:,}")
        else:
            print(f"OK: 0/{len(results)}")

        failed = [r for r in results if r[2] is None]
        if failed:
            print("Failed:", ", ".join(r[0] for r in failed))
            return 1
        return 0
    finally:
        conn.close()
        print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    sys.exit(main())
