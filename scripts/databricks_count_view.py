"""
Run SELECT COUNT(*) on a Databricks view without ODBC.

Uses the native Databricks SQL connector (HTTPS / SQL warehouse).

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

  python scripts/databricks_count_view.py

  python scripts/databricks_count_view.py --view hub_dev.g_external.v_cognite_material_master_reltio
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone


DEFAULT_VIEW = "hub_dev.g_external.v_cognite_material_master_reltio"


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


def quote_relation(view: str) -> str:
    parts = [p.strip() for p in view.split(".") if p.strip()]
    if not parts:
        raise SystemExit("View name must not be empty.")
    for part in parts:
        if not all(ch.isalnum() or ch == "_" for ch in part):
            raise SystemExit(f"Invalid identifier in view name: {part!r}")
    return ".".join(f"`{p}`" for p in parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SELECT COUNT(*) on a Databricks view (no ODBC)."
    )
    parser.add_argument(
        "--view",
        default=DEFAULT_VIEW,
        help=f"Fully qualified view (default: {DEFAULT_VIEW})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    view = args.view.strip()
    quoted = quote_relation(view)
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

    sql_text = f"SELECT COUNT(*) AS row_count FROM {quoted}"
    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Host:          {host}")
    print(f"HTTP path:     {http_path}")
    print(f"View:          {view}")
    print(f"SQL:           {sql_text}")
    print()

    t0 = time.perf_counter()
    try:
        with sql.connect(
            server_hostname=host,
            http_path=http_path,
            access_token=token,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql_text)
                row = cursor.fetchone()
    except Exception as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - t0
    count = int(row[0]) if row is not None else 0
    print(f"row_count:     {count:,}")
    print(f"elapsed:       {elapsed:.1f}s")
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
