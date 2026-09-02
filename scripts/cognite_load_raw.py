"""
Load a JSON document into Cognite RAW (JWT auth).

Database and table are CLI arguments (created if missing). The document is
stored as one RAW row: columns = the JSON as received (no flattening).

If the file is already a Cognite insert body ({"items":[{"key","columns"}]}),
it is POSTed unchanged.

Credentials:
  COGNITE_TOKEN     JWT / bearer access token (env, or --token)

Optional:
  COGNITE_HOST      Default: https://az-phx-001.cognitedata.com
  COGNITE_PROJECT   Default: bdx-dev

Usage (PowerShell):
  python scripts/cognite_load_raw.py --token "<jwt>" --json scripts/payloads/maintenance_order.json

  python scripts/cognite_load_raw.py --token "<jwt>" `
    --database db_mulesoft_glb_raw --table MaintenanceOrder `
    --json scripts/payloads/maintenance_order.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


DEFAULT_HOST = "https://az-phx-001.cognitedata.com"
DEFAULT_PROJECT = "bdx-dev"
DEFAULT_DATABASE = "db_mulesoft_glb_raw"
DEFAULT_TABLE = "MaintenanceOrder"
DEFAULT_KEY_FIELD = "aufnr"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def normalize_token(token: str) -> str:
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


def is_raw_insert_body(payload: object) -> bool:
    if not isinstance(payload, dict) or "items" not in payload:
        return False
    items = payload["items"]
    if not isinstance(items, list) or not items:
        return False
    first = items[0]
    return isinstance(first, dict) and "key" in first and "columns" in first


def row_key(document: dict, key: str, key_field: str) -> str:
    if key:
        return key
    value = document.get(key_field)
    if value is None or str(value).strip() == "":
        raise SystemExit(
            f"Could not find key field {key_field!r} in the JSON. "
            "Pass --key or --key-field."
        )
    return str(value)


def to_insert_body(payload: object, key: str, key_field: str) -> dict:
    if is_raw_insert_body(payload):
        return payload  # type: ignore[return-value]
    documents = payload if isinstance(payload, list) else [payload]
    items = []
    for doc in documents:
        if not isinstance(doc, dict):
            raise SystemExit("JSON must be an object, a list of objects, or a RAW insert body.")
        items.append({"key": row_key(doc, key, key_field), "columns": doc})
    return {"items": items}


def read_json_text(raw: str) -> str:
    text = raw.strip()
    if text == "-":
        return sys.stdin.read()
    path_candidate = text.strip('"')
    if os.path.isfile(path_candidate):
        return open(path_candidate, encoding="utf-8").read()
    return raw


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POST JSON as-is into a Cognite RAW table (JWT auth)."
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help=f"RAW database name (default: {DEFAULT_DATABASE}).",
    )
    parser.add_argument(
        "--table",
        default=DEFAULT_TABLE,
        help=f"RAW table name (default: {DEFAULT_TABLE}).",
    )
    parser.add_argument(
        "--json",
        required=True,
        help="JSON string, path to a .json file, or '-' to read stdin.",
    )
    parser.add_argument("--token", default="", help="JWT access token. Overrides COGNITE_TOKEN.")
    parser.add_argument("--key", default="", help="RAW row key. Overrides --key-field.")
    parser.add_argument(
        "--key-field",
        default=DEFAULT_KEY_FIELD,
        help=f"JSON field used as RAW key (default: {DEFAULT_KEY_FIELD}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = normalize_token(args.token or _env("COGNITE_TOKEN"))
    host = _env("COGNITE_HOST", DEFAULT_HOST).rstrip("/")
    project = _env("COGNITE_PROJECT", DEFAULT_PROJECT)
    database = args.database.strip()
    table = args.table.strip()

    if not token:
        print(
            "Missing JWT. Pass --token or set COGNITE_TOKEN, e.g. in PowerShell:\n"
            '  python scripts/cognite_load_raw.py --token "<jwt>" '
            "--json scripts/payloads/maintenance_order.json",
            file=sys.stderr,
        )
        return 1
    if not database or not table:
        print("Both --database and --table are required.", file=sys.stderr)
        return 1

    raw_text = read_json_text(args.json)
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return 1

    insert_body = to_insert_body(payload, args.key.strip(), args.key_field.strip())
    keys = [str(item.get("key")) for item in insert_body["items"]]
    body = json.dumps(insert_body, ensure_ascii=False).encode("utf-8")
    db_q = urllib.parse.quote(database, safe="")
    table_q = urllib.parse.quote(table, safe="")
    url = (
        f"{host}/api/v1/projects/{urllib.parse.quote(project, safe='')}"
        f"/raw/dbs/{db_q}/tables/{table_q}/rows?ensureParent=true"
    )

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Host:          {host}")
    print(f"Project:       {project}")
    print(f"Database:      {database}")
    print(f"Table:         {table}")
    print(f"Row keys:      {', '.join(keys)}")
    print(f"Bytes:         {len(body):,}")
    print()

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - t0
        print(f"Load failed ({exc.code}) in {elapsed:.1f}s", file=sys.stderr)
        print(detail, file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - t0
    print(f"status:        {status}")
    print(f"elapsed:       {elapsed:.1f}s")
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
