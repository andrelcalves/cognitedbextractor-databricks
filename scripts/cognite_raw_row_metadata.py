"""
Count Cognite RAW rows updated (lastUpdatedTime) and show row metadata.

Prints UPDATED_COUNT and keeps polling until Ctrl+C (ingest can take 10+ min).

Defaults:
  database  db_mulesoft_glb_raw
  table     tb_EquipmentMaster
  window    lastUpdatedTime >= today 00:00 America/Sao_Paulo

Credentials:
  COGNITE_TOKEN     JWT / bearer access token (env, or --token)

Optional:
  COGNITE_HOST      Default: https://az-phx-001.cognitedata.com
  COGNITE_PROJECT   Default: bdx-dev

Requires:
  pip install cognite-sdk

Usage (PowerShell):
  $env:COGNITE_TOKEN = "<jwt>"

  python scripts/cognite_raw_row_metadata.py

  python scripts/cognite_raw_row_metadata.py --min-updated 2026-09-04
  python scripts/cognite_raw_row_metadata.py --limit 10

  python scripts/cognite_raw_row_metadata.py --once
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_HOST = "https://az-phx-001.cognitedata.com"
DEFAULT_PROJECT = "bdx-dev"
DEFAULT_DATABASE = "db_mulesoft_glb_raw"
DEFAULT_TABLE = "tb_EquipmentMaster"
DEFAULT_WATCH_SECONDS = 30
TZ = ZoneInfo("America/Sao_Paulo")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def normalize_token(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


def ms_to_text(ms: int | None) -> str:
    if ms is None:
        return "-"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def start_of_day_ms(raw: str) -> tuple[str, int]:
    if raw.strip():
        day = datetime.fromisoformat(raw.strip())
        if day.tzinfo is None:
            day = day.replace(tzinfo=TZ)
    else:
        now = datetime.now(TZ)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day.isoformat(), int(day.timestamp() * 1000)


def build_client(token: str, host: str, project: str) -> Any:
    from cognite.client import ClientConfig, CogniteClient
    from cognite.client.config import global_config
    from cognite.client.credentials import Token

    global_config.disable_pypi_version_check = True
    return CogniteClient(
        ClientConfig(
            client_name="cognite-raw-row-metadata",
            project=project,
            base_url=host,
            credentials=Token(token),
        )
    )


def source_timestamp(columns: dict[str, Any] | None) -> str:
    cols = columns or {}
    for name in ("DATETIMESTAMP", "datetimestamp", "lastchangedatetime", "lastChangeDateTime"):
        if cols.get(name) not in (None, ""):
            return str(cols[name])
    return "-"


def count_updated(client: Any, database: str, table: str, min_ms: int) -> int:
    """Count rows with lastUpdatedTime >= min_ms (keys only)."""
    total = 0
    for _row in client.raw.rows.list(
        db_name=database,
        table_name=table,
        columns=[],
        min_last_updated_time=min_ms,
        limit=None,
    ):
        total += 1
    return total


def print_sample(
    client: Any,
    database: str,
    table: str,
    min_ms: int,
    limit: int,
    show_columns: bool,
) -> None:
    rows = list(
        client.raw.rows.list(
            db_name=database,
            table_name=table,
            min_last_updated_time=min_ms,
            limit=limit,
        )
    )
    header = f"{'KEY':<42} {'LAST_UPDATED':<24} {'LAST_UPDATED_MS':>16}  SOURCE_TS"
    print()
    print(header)
    print("-" * len(header))
    if not rows:
        print("(no sample rows)")
        return
    updated_ms: list[int] = []
    for row in rows:
        key = str(row.key)
        display_key = key if len(key) <= 42 else key[:39] + "..."
        print(
            f"{display_key:<42} {ms_to_text(row.last_updated_time):<24} "
            f"{row.last_updated_time or 0:>16}  {source_timestamp(row.columns)}"
        )
        if show_columns:
            names = sorted((row.columns or {}).keys())
            print(f"    columns ({len(names)}): {', '.join(names)}")
        if row.last_updated_time:
            updated_ms.append(int(row.last_updated_time))
    print("-" * len(header))
    if updated_ms:
        print(f"sample lastUpdatedTime min: {ms_to_text(min(updated_ms))}")
        print(f"sample lastUpdatedTime max: {ms_to_text(max(updated_ms))}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count Cognite RAW rows updated today and show lastUpdatedTime."
    )
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--token", default="", help="JWT access token. Overrides COGNITE_TOKEN.")
    parser.add_argument(
        "--min-updated",
        default="",
        help="Count rows with lastUpdatedTime >= this ISO day. Default: today.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional sample of updated rows after the count. Default: 0 (count only).",
    )
    parser.add_argument("--columns", action="store_true", help="Print column names on the sample.")
    parser.add_argument(
        "--watch",
        type=int,
        default=DEFAULT_WATCH_SECONDS,
        metavar="SECONDS",
        help=f"Recount every N seconds until Ctrl+C (default: {DEFAULT_WATCH_SECONDS}). Use 0 with --once.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Count once and exit (do not keep polling).",
    )
    parser.add_argument("--key", default="", help="Inspect a single row by key (skips the count).")
    return parser.parse_args(argv)


def run_once(
    client: Any,
    database: str,
    table: str,
    min_ms: int,
    day_label: str,
    limit: int,
    show_columns: bool,
) -> int:
    t0 = time.perf_counter()
    updated = count_updated(client, database, table, min_ms)
    elapsed = time.perf_counter() - t0
    print(f"UPDATED_COUNT: {updated:,}")
    print(f"Elapsed:       {elapsed:.1f}s")
    if limit > 0 and updated > 0:
        print_sample(client, database, table, min_ms, limit, show_columns)
    return updated


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = normalize_token(args.token or _env("COGNITE_TOKEN"))
    host = _env("COGNITE_HOST", DEFAULT_HOST)
    project = _env("COGNITE_PROJECT", DEFAULT_PROJECT)
    database = args.database.strip()
    table = args.table.strip()

    if not token:
        print(
            "Missing JWT. Pass --token or set COGNITE_TOKEN, e.g. in PowerShell:\n"
            '  $env:COGNITE_TOKEN = "<jwt>"\n'
            "  python scripts/cognite_raw_row_metadata.py",
            file=sys.stderr,
        )
        return 1

    try:
        import cognite.client  # noqa: F401
    except ImportError:
        print("cognite-sdk is required. Install with: pip install cognite-sdk", file=sys.stderr)
        return 1

    day_label, min_ms = start_of_day_ms(args.min_updated)
    client = build_client(token, host, project)

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Host:          {host}")
    print(f"Project:       {project}")
    print(f"Database:      {database}")
    print(f"Table:         {table}")
    print(f"Window:        lastUpdatedTime >= {day_label}")
    print(flush=True)

    try:
        if args.key.strip():
            row = client.raw.rows.retrieve(
                db_name=database,
                table_name=table,
                key=args.key.strip(),
            )
            if row is None:
                print("Row not found.")
                return 1
            print(
                f"key={row.key}\n"
                f"lastUpdatedTime={ms_to_text(row.last_updated_time)} "
                f"({row.last_updated_time})\n"
                f"source={source_timestamp(row.columns)}"
            )
            if args.columns:
                print("columns:", ", ".join(sorted((row.columns or {}).keys())))
            return 0

        once = args.once or args.watch <= 0
        if once:
            run_once(client, database, table, min_ms, day_label, args.limit, args.columns)
            print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
            return 0

        print(f"Polling every {args.watch}s until Ctrl+C (window stays today)\n", flush=True)
        previous: int | None = None
        while True:
            print(f"--- {datetime.now(TZ).strftime('%H:%M:%S')} ---")
            updated = run_once(
                client, database, table, min_ms, day_label, args.limit, args.columns
            )
            if previous is None:
                print("Delta:         (first count)")
            else:
                delta = updated - previous
                sign = "+" if delta >= 0 else ""
                print(f"Delta:         {sign}{delta:,} since last poll")
            previous = updated
            print(f"Next count in {args.watch}s\n", flush=True)
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except Exception as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
