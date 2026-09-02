"""
Count rows in Cognite RAW tables (JWT auth). Targets come from JSON.

Cognite RAW has no SQL COUNT(*); this script pages through row keys and counts them.

Credentials:
  COGNITE_TOKEN     JWT / bearer access token (env, or --token)

Optional:
  COGNITE_HOST      Default: https://az-phx-001.cognitedata.com
  COGNITE_PROJECT   Default: bdx-dev

Requires:
  pip install cognite-sdk

JSON shapes accepted:
  {"database": "db_databricks_glb_raw", "table": "tb_F4111Jdeint"}
  {"database": "db_databricks_glb_raw", "tables": ["tb_F4111Jdeint", "tb_F0010Jdeint"]}
  [{"database": "db_databricks_glb_raw", "table": "tb_F4111Jdeint"}]
  {"items": [{"database": "...", "table": "..."}]}

Usage (PowerShell):
  $env:COGNITE_TOKEN = "<jwt>"

  python scripts/cognite_count_raw.py --json tables.json

  python scripts/cognite_count_raw.py --token "<jwt>" --json '{
    "database": "db_databricks_glb_raw",
    "table": "tb_F4111Jdeint"
  }'

  Get-Content tables.json | python scripts/cognite_count_raw.py --json -
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any


DEFAULT_HOST = "https://az-phx-001.cognitedata.com"
DEFAULT_PROJECT = "bdx-dev"
DEFAULT_DATABASE = "db_databricks_glb_raw"
PAGE_SIZE = 10_000


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def normalize_token(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


def load_json_spec(raw: str) -> Any:
    text = raw.strip()
    if text == "-":
        text = sys.stdin.read()
    else:
        path_candidate = text.strip('"')
        if os.path.isfile(path_candidate):
            text = open(path_candidate, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {exc}") from exc


def parse_targets(spec: Any, default_database: str) -> list[tuple[str, str]]:
    """Return unique (database, table) pairs from flexible JSON."""
    items: list[dict[str, Any]]
    if isinstance(spec, dict) and "items" in spec:
        items = list(spec["items"])
    elif isinstance(spec, list):
        items = list(spec)
    elif isinstance(spec, dict):
        items = [spec]
    else:
        raise SystemExit("JSON must be an object, a list, or {\"items\": [...]}.")

    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in items:
        if isinstance(item, str):
            database, table = default_database, item
        elif isinstance(item, dict):
            database = str(
                item.get("database")
                or item.get("dbName")
                or item.get("db")
                or default_database
            ).strip()
            tables = item.get("tables")
            table = item.get("table") or item.get("tableName") or item.get("name")
            if tables is not None:
                if not isinstance(tables, list):
                    raise SystemExit("'tables' must be a JSON array of strings.")
                for name in tables:
                    pair = (database, str(name).strip())
                    if pair[1] and pair not in seen:
                        seen.add(pair)
                        targets.append(pair)
                continue
            if table is None:
                raise SystemExit(
                    "Each JSON object needs 'table' / 'tableName' or 'tables'."
                )
            database, table = database, str(table).strip()
        else:
            raise SystemExit(f"Unsupported JSON item: {item!r}")

        if not database or not table:
            raise SystemExit("database and table must be non-empty.")
        pair = (database, table)
        if pair not in seen:
            seen.add(pair)
            targets.append(pair)

    if not targets:
        raise SystemExit("JSON did not contain any tables to count.")
    return targets


def build_client(token: str, host: str, project: str) -> Any:
    from cognite.client import ClientConfig, CogniteClient
    from cognite.client.config import global_config
    from cognite.client.credentials import Token

    global_config.disable_pypi_version_check = True
    return CogniteClient(
        ClientConfig(
            client_name="cognite-count-raw",
            project=project,
            base_url=host,
            credentials=Token(token),
        )
    )


def count_table(client: Any, database: str, table: str) -> int:
    # columns=[] retrieves keys only. Chunk to avoid loading the whole table in memory.
    total = 0
    for chunk in client.raw.rows(
        db_name=database,
        table_name=table,
        columns=[],
        chunk_size=PAGE_SIZE,
        limit=None,
    ):
        total += len(chunk)
    return total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count Cognite RAW rows from a JSON list of tables (JWT auth)."
    )
    parser.add_argument(
        "--json",
        required=True,
        help="JSON string, path to a .json file, or '-' to read stdin.",
    )
    parser.add_argument(
        "--token",
        default="",
        help="JWT access token. Overrides COGNITE_TOKEN.",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help=f"Default RAW database when omitted in JSON (default: {DEFAULT_DATABASE}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    token = normalize_token(args.token or _env("COGNITE_TOKEN"))
    host = _env("COGNITE_HOST", DEFAULT_HOST)
    project = _env("COGNITE_PROJECT", DEFAULT_PROJECT)

    if not token:
        print(
            "Missing JWT. Pass --token or set COGNITE_TOKEN, e.g. in PowerShell:\n"
            '  $env:COGNITE_TOKEN = "<jwt>"\n'
            '  python scripts/cognite_count_raw.py --json tables.json',
            file=sys.stderr,
        )
        return 1

    try:
        import cognite.client  # noqa: F401
    except ImportError:
        print(
            "cognite-sdk is required. Install with: pip install cognite-sdk",
            file=sys.stderr,
        )
        return 1

    targets = parse_targets(load_json_spec(args.json), args.database.strip())

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Host:          {host}")
    print(f"Project:       {project}")
    print(f"Tables:        {len(targets)}")
    print()
    print(f"{'DATABASE':<28} {'TABLE':<28} {'ROW_COUNT':>18} {'ELAPSED':>10}  STATUS")
    print("-" * 96)

    client = build_client(token, host, project)
    errors = 0
    grand_total = 0

    for database, table in targets:
        t0 = time.perf_counter()
        try:
            n = count_table(client, database, table)
            elapsed = time.perf_counter() - t0
            grand_total += n
            print(f"{database:<28} {table:<28} {n:>18,} {elapsed:9.1f}s  OK")
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            errors += 1
            msg = str(exc).split("\n", 1)[0]
            print(f"{database:<28} {table:<28} {'ERROR':>18} {elapsed:9.1f}s  {msg}")

    print("-" * 96)
    print(f"sum of counted rows: {grand_total:,}  |  OK: {len(targets) - errors}/{len(targets)}")
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
