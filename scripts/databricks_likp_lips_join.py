"""
Check LIKP vs LIPS delivery numbers on Databricks hub_dev.

The inner join only returns VBELN present in both views. It will miss cases like:
  6015794205  in LIKP, not in LIPS
  6017985082  in LIPS, not in LIKP

This script:
  1. Probes those (or --vbeln) values in each view
  2. Counts distinct VBELN: inner / LIKP-only / LIPS-only
  3. Lists a sample of inner-join delivery numbers

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

  python scripts/databricks_likp_lips_join.py
  python scripts/databricks_likp_lips_join.py --limit 20
  python scripts/databricks_likp_lips_join.py --vbeln 6015794205,6017985082 --no-counts
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone


DEFAULT_CATALOG = "hub_dev"
DEFAULT_LIKP = "g_external.v_cognite_likp_everest"
DEFAULT_LIPS = "g_external.v_cognite_lips_everest"
DEFAULT_VBELN = ("6015794205", "6017985082")


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
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise SystemExit(f"Invalid identifier: {name!r}")
    return f"`{name}`"


def quote_relation(name: str) -> str:
    parts = [p.strip() for p in name.split(".") if p.strip()]
    if not parts:
        raise SystemExit("Relation name must not be empty.")
    return ".".join(quote_ident(p) for p in parts)


def qualify(catalog: str, relation: str) -> str:
    rel = relation.strip()
    if catalog and not rel.startswith(f"{catalog}."):
        rel = f"{catalog}.{rel}"
    return quote_relation(rel)


def parse_vbeln(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not values:
        raise SystemExit("--vbeln must contain at least one delivery number.")
    for value in values:
        if "'" in value or ";" in value:
            raise SystemExit(f"Invalid VBELN: {value!r}")
    return values


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare LIKP/LIPS VBELN on Databricks hub_dev (inner join + orphans)."
    )
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, help=f"Unity Catalog (default: {DEFAULT_CATALOG})")
    parser.add_argument("--likp", default=DEFAULT_LIKP, help=f"LIKP view without catalog (default: {DEFAULT_LIKP})")
    parser.add_argument("--lips", default=DEFAULT_LIPS, help=f"LIPS view without catalog (default: {DEFAULT_LIPS})")
    parser.add_argument(
        "--vbeln",
        default=",".join(DEFAULT_VBELN),
        help="Comma-separated delivery numbers to probe",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max inner-join delivery numbers to print (default: 20)",
    )
    parser.add_argument(
        "--no-counts",
        action="store_true",
        help="Skip the full distinct-VBELN inner/anti-join counts (faster)",
    )
    parser.add_argument(
        "--no-sample",
        action="store_true",
        help="Skip listing inner-join delivery numbers",
    )
    return parser.parse_args(argv)


def fetchall(cursor, sql_text: str, params: tuple | None = None) -> list[tuple]:
    t0 = time.perf_counter()
    print(f"SQL:\n{sql_text.strip()}\n")
    if params:
        cursor.execute(sql_text, params)
    else:
        cursor.execute(sql_text)
    rows = cursor.fetchall() or []
    print(f"elapsed: {time.perf_counter() - t0:.1f}s  rows: {len(rows)}\n")
    return [tuple(row) for row in rows]


def print_table(headers: list[str], rows: list[tuple]) -> None:
    if not rows:
        print("(no rows)\n")
        return
    widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        cells = ["" if v is None else str(v) for v in row]
        str_rows.append(cells)
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for cells in str_rows:
        print("  ".join(cells[i].ljust(widths[i]) for i in range(len(headers))))
    print()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vbelns = parse_vbeln(args.vbeln)
    likp = qualify(args.catalog, args.likp)
    lips = qualify(args.catalog, args.lips)
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

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Host:          {host}")
    print(f"HTTP path:     {http_path}")
    print(f"LIKP:          {likp}")
    print(f"LIPS:          {lips}")
    print(f"VBELN probe:   {', '.join(vbelns)}")
    print()

    placeholders = ", ".join(["?"] * len(vbelns))
    probe_sql = f"""
SELECT
  src.source AS source,
  CAST(src.VBELN AS STRING) AS VBELN,
  COUNT(*) AS row_count
FROM (
  SELECT 'LIKP' AS source, VBELN
  FROM {likp}
  WHERE CAST(VBELN AS STRING) IN ({placeholders})
  UNION ALL
  SELECT 'LIPS' AS source, VBELN
  FROM {lips}
  WHERE CAST(VBELN AS STRING) IN ({placeholders})
) src
GROUP BY src.source, CAST(src.VBELN AS STRING)
ORDER BY VBELN, source
"""

    counts_sql = f"""
WITH likp_vbeln AS (
  SELECT DISTINCT CAST(VBELN AS STRING) AS VBELN
  FROM {likp}
),
lips_vbeln AS (
  SELECT DISTINCT CAST(VBELN AS STRING) AS VBELN
  FROM {lips}
)
SELECT
  (SELECT COUNT(*) FROM likp_vbeln) AS likp_distinct_vbeln,
  (SELECT COUNT(*) FROM lips_vbeln) AS lips_distinct_vbeln,
  (
    SELECT COUNT(*)
    FROM likp_vbeln Likp
    INNER JOIN lips_vbeln Lips ON Likp.VBELN = Lips.VBELN
  ) AS inner_join_vbeln,
  (
    SELECT COUNT(*)
    FROM likp_vbeln Likp
    LEFT ANTI JOIN lips_vbeln Lips ON Likp.VBELN = Lips.VBELN
  ) AS likp_only_vbeln,
  (
    SELECT COUNT(*)
    FROM lips_vbeln Lips
    LEFT ANTI JOIN likp_vbeln Likp ON Lips.VBELN = Likp.VBELN
  ) AS lips_only_vbeln
"""

    sample_sql = f"""
SELECT DISTINCT CAST(Likp.VBELN AS STRING) AS Delivery_number
FROM {likp} Likp
INNER JOIN {lips} Lips
  ON CAST(Likp.VBELN AS STRING) = CAST(Lips.VBELN AS STRING)
LIMIT {max(0, args.limit)}
"""

    try:
        with sql.connect(
            server_hostname=host,
            http_path=http_path,
            access_token=token,
        ) as conn:
            with conn.cursor() as cursor:
                print("=== Probe specific VBELN ===")
                probe_rows = fetchall(cursor, probe_sql, vbelns + vbelns)
                print_table(["source", "VBELN", "row_count"], probe_rows)

                expected = {(src, v) for v in vbelns for src in ("LIKP", "LIPS")}
                found = {(str(r[0]), str(r[1])) for r in probe_rows}
                missing = sorted(expected - found)
                if missing:
                    print("Missing from Databricks view:")
                    for src, vbeln in missing:
                        print(f"  {src}: {vbeln}")
                    print()

                if not args.no_counts:
                    print("=== Distinct VBELN counts (inner join + orphans) ===")
                    count_rows = fetchall(cursor, counts_sql)
                    if count_rows:
                        headers = [
                            "likp_distinct_vbeln",
                            "lips_distinct_vbeln",
                            "inner_join_vbeln",
                            "likp_only_vbeln",
                            "lips_only_vbeln",
                        ]
                        print_table(headers, count_rows)

                if not args.no_sample and args.limit > 0:
                    print(f"=== Inner join sample (LIMIT {args.limit}) ===")
                    sample_rows = fetchall(cursor, sample_sql)
                    print_table(["Delivery_number"], sample_rows)
    except Exception as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        return 1

    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
