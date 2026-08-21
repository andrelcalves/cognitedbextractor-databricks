"""
Delete a Cognite RAW database after deleting all of its tables.

Credentials (env vars):
  COGNITE_TOKEN     Bearer access token (required)

Optional:
  COGNITE_HOST      Default: https://az-phx-001.cognitedata.com
  COGNITE_PROJECT   Default: bdx-dev

Requires:
  pip install cognite-sdk

Usage (PowerShell):
  $env:COGNITE_TOKEN = "<bearer-token>"
  # optional:
  # $env:COGNITE_HOST = "https://az-phx-001.cognitedata.com"
  # $env:COGNITE_PROJECT = "bdx-dev"

  # Preview (dry-run — lists tables, does not delete)
  python scripts/cognite_delete_raw_database.py

  # Delete
  python scripts/cognite_delete_raw_database.py --yes

  # Other database
  python scripts/cognite_delete_raw_database.py --database other_db --yes
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


DEFAULT_HOST = "https://az-phx-001.cognitedata.com"
DEFAULT_PROJECT = "bdx-dev"
DEFAULT_DATABASE = "db_databricks_everest_raw"
DELETE_BATCH_SIZE = 100


def _env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, default).strip()


def build_client(token: str, host: str, project: str) -> Any:
    from cognite.client import ClientConfig, CogniteClient
    from cognite.client.credentials import Token

    return CogniteClient(
        ClientConfig(
            client_name="cognite-delete-raw-database",
            project=project,
            base_url=host,
            credentials=Token(token),
        )
    )


def list_table_names(client: Any, database: str) -> list[str]:
    tables = client.raw.tables.list(db_name=database, limit=None)
    return sorted(t.name for t in tables)


def delete_tables(client: Any, database: str, table_names: list[str]) -> None:
    for i in range(0, len(table_names), DELETE_BATCH_SIZE):
        batch = table_names[i : i + DELETE_BATCH_SIZE]
        print(f"Deleting tables {i + 1}-{i + len(batch)} of {len(table_names)}...")
        for name in batch:
            print(f"  - {name}")
        client.raw.tables.delete(db_name=database, name=batch)


def delete_database(client: Any, database: str) -> None:
    print(f"Deleting database: {database}")
    client.raw.databases.delete(name=database)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete a Cognite RAW database (tables first, then the database)."
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help=f"RAW database name (default: {DEFAULT_DATABASE})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete. Without this flag the script only lists tables (dry-run).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    token = _env("COGNITE_TOKEN")
    host = _env("COGNITE_HOST", DEFAULT_HOST)
    project = _env("COGNITE_PROJECT", DEFAULT_PROJECT)
    database = args.database.strip()

    if not token:
        print(
            "Missing env var: COGNITE_TOKEN\n"
            "Set it before running, e.g. in PowerShell:\n"
            '  $env:COGNITE_TOKEN = "<bearer-token>"',
            file=sys.stderr,
        )
        return 1

    if not database:
        print("Database name must not be empty.", file=sys.stderr)
        return 1

    try:
        import cognite.client  # noqa: F401
    except ImportError:
        print(
            "cognite-sdk is required. Install with: pip install cognite-sdk",
            file=sys.stderr,
        )
        return 1

    print(f"Host:     {host}")
    print(f"Project:  {project}")
    print(f"Database: {database}")
    print(f"Mode:     {'DELETE' if args.yes else 'DRY-RUN'}")
    print()

    client = build_client(token, host, project)

    try:
        table_names = list_table_names(client, database)
    except Exception as exc:
        # CogniteNotFoundError / CogniteAPIError when DB missing
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            print(f"Database '{database}' does not exist (or is not accessible). Nothing to do.")
            return 0
        print(f"Failed to list tables in '{database}': {exc}", file=sys.stderr)
        return 1

    if not table_names:
        print(f"No tables found in '{database}'.")
    else:
        print(f"Found {len(table_names)} table(s):")
        for name in table_names:
            print(f"  - {name}")

    if not args.yes:
        print()
        print("Dry-run only. Re-run with --yes to delete all tables, then the database.")
        return 0

    print()
    try:
        if table_names:
            delete_tables(client, database, table_names)
        delete_database(client, database)
    except Exception as exc:
        print(f"Delete failed: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Successfully deleted database '{database}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
