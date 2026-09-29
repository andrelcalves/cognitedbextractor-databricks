"""
Publish a JSON file to an Azure Event Hub (Shared Access Policy).

Send uses the hub name, the namespace endpoint, and a policy with Send rights.
The consumer group is only for readers; this script does not use it.

Credentials (env, or CLI). Do not commit the primary key.
  EVENTHUB_NAME          Event Hub name
  EVENTHUB_ENDPOINT      sb://<namespace>.servicebus.windows.net/  or https://...
  EVENTHUB_POLICY_NAME   Shared Access Policy name
  EVENTHUB_POLICY_KEY    Shared Access Policy primary key

Optional:
  EVENTHUB_CONSUMER_GROUP   Recorded in the log only. Not used to send.

No extra packages.

Usage (PowerShell):
  $env:EVENTHUB_NAME = "my-hub"
  $env:EVENTHUB_ENDPOINT = "sb://mynamespace.servicebus.windows.net/"
  $env:EVENTHUB_POLICY_NAME = "send"
  $env:EVENTHUB_POLICY_KEY = "<primary-key>"

  python scripts/eventhub_send.py --json scripts/payloads/maintenance_orders_eventhub.json

  python scripts/eventhub_send.py --json scripts/payloads/maintenance_orders_eventhub.json --per-item
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def namespace_host(endpoint: str) -> str:
    raw = endpoint.strip().rstrip("/")
    for prefix in ("sb://", "https://", "http://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
            break
    host = raw.split("/")[0]
    if not host:
        raise SystemExit("EVENTHUB_ENDPOINT is empty or not a Service Bus host.")
    return host


def sas_token(resource_uri: str, key_name: str, key: str, ttl_seconds: int = 3600) -> str:
    expiry = int(time.time()) + ttl_seconds
    encoded = urllib.parse.quote_plus(resource_uri)
    to_sign = f"{encoded}\n{expiry}".encode("utf-8")
    digest = hmac.new(key.encode("utf-8"), to_sign, hashlib.sha256).digest()
    signature = urllib.parse.quote_plus(base64.b64encode(digest))
    return (
        f"SharedAccessSignature sr={encoded}&sig={signature}"
        f"&se={expiry}&skn={urllib.parse.quote_plus(key_name)}"
    )


def load_json(path: str) -> object:
    text = path.strip()
    if text == "-":
        text = sys.stdin.read()
    else:
        text = open(text.strip('"'), encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON: {exc}") from exc


def messages_from(payload: object, per_item: bool) -> list[tuple[str, bytes]]:
    """Return (partition_key, body) pairs. One message, or one per items[] row."""
    if per_item and isinstance(payload, dict) and isinstance(payload.get("items"), list):
        out: list[tuple[str, bytes]] = []
        for item in payload["items"]:
            if not isinstance(item, dict):
                raise SystemExit("Each items[] entry must be an object.")
            key = str(item.get("key") or "").strip()
            body = json.dumps(item, ensure_ascii=False).encode("utf-8")
            out.append((key, body))
        if not out:
            raise SystemExit("JSON items[] is empty.")
        return out
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return [("", body)]


def send_one(
    url: str,
    token: str,
    body: bytes,
    partition_key: str,
    timeout: float,
) -> int:
    headers = {
        "Authorization": token,
        "Content-Type": "application/json; charset=utf-8",
    }
    if partition_key:
        headers["BrokerProperties"] = json.dumps({"PartitionKey": partition_key})
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return int(getattr(resp, "status", None) or resp.getcode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise SystemExit(f"HTTP {exc.code} {exc.reason}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Request failed: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="POST a JSON file to Azure Event Hub.")
    parser.add_argument(
        "--json",
        default="scripts/payloads/maintenance_orders_eventhub.json",
        help="Path to the JSON file, or '-' for stdin.",
    )
    parser.add_argument("--name", default="", help="Event Hub name. Overrides EVENTHUB_NAME.")
    parser.add_argument("--endpoint", default="", help="Namespace endpoint. Overrides EVENTHUB_ENDPOINT.")
    parser.add_argument("--policy-name", default="", help="Shared Access Policy name. Overrides EVENTHUB_POLICY_NAME.")
    parser.add_argument("--policy-key", default="", help="Policy primary key. Overrides EVENTHUB_POLICY_KEY.")
    parser.add_argument(
        "--consumer-group",
        default="",
        help="Ignored for send. Overrides EVENTHUB_CONSUMER_GROUP (logged only).",
    )
    parser.add_argument(
        "--per-item",
        action="store_true",
        help="Send each items[] object as its own event. Partition key is the row key.",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.name or _env("EVENTHUB_NAME")
    endpoint = args.endpoint or _env("EVENTHUB_ENDPOINT")
    policy_name = args.policy_name or _env("EVENTHUB_POLICY_NAME")
    policy_key = args.policy_key or _env("EVENTHUB_POLICY_KEY")
    consumer_group = args.consumer_group or _env("EVENTHUB_CONSUMER_GROUP")

    missing = [
        label
        for label, value in [
            ("EVENTHUB_NAME", name),
            ("EVENTHUB_ENDPOINT", endpoint),
            ("EVENTHUB_POLICY_NAME", policy_name),
            ("EVENTHUB_POLICY_KEY", policy_key),
        ]
        if not value
    ]
    if missing:
        print(
            "Missing: "
            + ", ".join(missing)
            + "\nSet them in PowerShell before running, for example:\n"
            '  $env:EVENTHUB_NAME = "my-hub"\n'
            '  $env:EVENTHUB_ENDPOINT = "sb://mynamespace.servicebus.windows.net/"\n'
            '  $env:EVENTHUB_POLICY_NAME = "send"\n'
            '  $env:EVENTHUB_POLICY_KEY = "<primary-key>"',
            file=sys.stderr,
        )
        return 1

    host = namespace_host(endpoint)
    resource = f"https://{host}/{name}"
    url = f"{resource}/messages"
    token = sas_token(resource, policy_name, policy_key)
    messages = messages_from(load_json(args.json), args.per_item)

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"Endpoint:      https://{host}")
    print(f"Event Hub:     {name}")
    print(f"Policy:        {policy_name}")
    if consumer_group:
        print(f"Consumer grp:  {consumer_group} (not used to send)")
    print(f"Messages:      {len(messages)}")
    print(f"POST           {url}")
    print()

    for index, (partition_key, body) in enumerate(messages, start=1):
        status = send_one(url, token, body, partition_key, args.timeout)
        label = partition_key or "(whole file)"
        print(f"{index:>4}  HTTP {status}  {len(body):>8} bytes  {label}")

    print()
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
