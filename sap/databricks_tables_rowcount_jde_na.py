"""
Count rows for SAP Everest views in Databricks via ODBC.

Credentials (env vars, same shape as sap/config.yaml ODBC block):
  DATABRICKS_HOST
  DATABRICKS_HTTP_PATH
  DATABRICKS_TOKEN

Optional:
  DATABRICKS_ODBC_DRIVER  (default: Databricks ODBC Driver)

Usage:
  python sap/databricks_tables_rowcount_jde_na.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

try:
    import pyodbc
except ImportError:
    print("pyodbc is required. Install with: pip install pyodbc", file=sys.stderr)
    sys.exit(1)

# Initial set: the three tables that still failed PK resolution on 2026-07-29.
# Add more entries as (label, fully_qualified_view) when needed.
TABLES: list[tuple[str, str]] = [
    ("MHIO", "hub_dev.g_external.v_cognite_mhio_everest"),
    ("PMCO", "hub_dev.g_external.v_cognite_pmco_everest"),
    ("QAMR", "hub_dev.g_external.v_cognite_qamr_everest"),
]


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


def count_rows(cursor: pyodbc.Cursor, view: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {view}")
    row = cursor.fetchone()
    return int(row[0])


def main() -> int:
    started = datetime.now(timezone.utc)
    print(f"Started (UTC): {started.isoformat()}")
    print(f"Tables: {len(TABLES)}")
    print("-" * 72)

    conn = pyodbc.connect(connection_string(), autocommit=True)
    try:
        cursor = conn.cursor()
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
