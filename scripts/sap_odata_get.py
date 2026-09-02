"""
Call a SAP OData endpoint (GET) and print the JSON response.

No extra packages. Auth is Basic (user/password) and optional client certificates.

Credentials / connection (env, or CLI):
  SAP_ODATA_URL       Full entity URL, or gateway base (.../sap/opu/odata/sap/SERVICE/)
  SAP_USERNAME        SAP user
  SAP_PASSWORD        SAP password
  SAP_CLIENT          Optional sap-client (e.g. 100)
  SAP_CERT            Optional client public cert PEM (mTLS)
  SAP_KEY             Optional client private key PEM (mTLS)
  SAP_CA              Optional CA bundle PEM (or REQUESTS_CA_BUNDLE)

Usage (PowerShell):
  $env:SAP_ODATA_URL = "https://host/sap/opu/odata/sap/Z_MY_SRV/"
  $env:SAP_USERNAME = "myuser"
  $env:SAP_PASSWORD = "secret"
  $env:SAP_CLIENT = "100"

  python scripts/sap_odata_get.py --entity MaintenanceOrderSet --top 5

  python scripts/sap_odata_get.py --url "https://host/sap/opu/odata/sap/Z_MY_SRV/MaintenanceOrderSet?$top=5"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def build_url(
    url: str,
    entity: str,
    top: int | None,
    skip: int | None,
    filter_expr: str,
    select: str,
    expand: str,
    extra_query: str,
    metadata: bool,
) -> str:
    url = url.strip()
    if not url:
        raise SystemExit("Missing URL. Pass --url or set SAP_ODATA_URL.")

    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    path = parsed.path or "/"

    if metadata:
        if not path.rstrip("/").endswith("$metadata"):
            path = path.rstrip("/") + "/$metadata"
        query.pop("$format", None)
    else:
        if entity:
            path = path.rstrip("/") + "/" + entity.lstrip("/")
        if top is not None:
            query["$top"] = str(top)
        if skip is not None:
            query["$skip"] = str(skip)
        if filter_expr:
            query["$filter"] = filter_expr
        if select:
            query["$select"] = select
        if expand:
            query["$expand"] = expand
        if extra_query:
            extra = dict(urllib.parse.parse_qsl(extra_query, keep_blank_values=True))
            query.update(extra)
        if "$format" not in query:
            query["$format"] = "json"

    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def ssl_context(ca: str, cert: str, key: str, insecure: bool) -> ssl.SSLContext:
    if insecure:
        ctx = ssl._create_unverified_context()
    elif ca:
        ctx = ssl.create_default_context(cafile=ca)
    else:
        ctx = ssl.create_default_context()
    if cert:
        if not key:
            raise SystemExit("Client cert set but no key. Pass --key or SAP_KEY.")
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    elif key:
        raise SystemExit("Client key set but no cert. Pass --cert or SAP_CERT.")
    return ctx


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GET a SAP OData endpoint and print the JSON body."
    )
    parser.add_argument(
        "--url",
        default="",
        help="Full OData URL or service root. Overrides SAP_ODATA_URL.",
    )
    parser.add_argument(
        "--entity",
        default="",
        help="Entity set appended to the service root (e.g. MaintenanceOrderSet).",
    )
    parser.add_argument("--top", type=int, default=5, help="OData $top (default: 5). Use 0 to omit.")
    parser.add_argument("--skip", type=int, default=None, help="OData $skip.")
    parser.add_argument("--filter", default="", dest="odata_filter", help="OData $filter.")
    parser.add_argument("--select", default="", help="OData $select.")
    parser.add_argument("--expand", default="", help="OData $expand.")
    parser.add_argument("--query", default="", help="Extra query string, e.g. sap-language=EN.")
    parser.add_argument(
        "--metadata",
        action="store_true",
        help="GET $metadata instead of an entity set.",
    )
    parser.add_argument("--username", default="", help="Overrides SAP_USERNAME.")
    parser.add_argument("--password", default="", help="Overrides SAP_PASSWORD.")
    parser.add_argument("--sap-client", default="", help="Overrides SAP_CLIENT (header sap-client).")
    parser.add_argument("--cert", default="", help="Client cert PEM. Overrides SAP_CERT.")
    parser.add_argument("--key", default="", help="Client key PEM. Overrides SAP_KEY.")
    parser.add_argument("--ca", default="", help="CA bundle PEM. Overrides SAP_CA / REQUESTS_CA_BUNDLE.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS certificate verification (lab only).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (default: 60).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    url = args.url or _env("SAP_ODATA_URL")
    username = args.username or _env("SAP_USERNAME")
    password = args.password if args.password else _env("SAP_PASSWORD")
    sap_client = args.sap_client or _env("SAP_CLIENT")
    cert = args.cert or _env("SAP_CERT")
    key = args.key or _env("SAP_KEY")
    ca = args.ca or _env("SAP_CA") or _env("REQUESTS_CA_BUNDLE")

    top = None if args.metadata or args.top == 0 else args.top
    final_url = build_url(
        url=url,
        entity=args.entity,
        top=top,
        skip=None if args.metadata else args.skip,
        filter_expr="" if args.metadata else args.odata_filter,
        select="" if args.metadata else args.select,
        expand="" if args.metadata else args.expand,
        extra_query="" if args.metadata else args.query,
        metadata=args.metadata,
    )

    headers = {
        "Accept": "application/xml" if args.metadata else "application/json",
        "User-Agent": "sap-odata-get",
    }
    if sap_client:
        headers["sap-client"] = sap_client
    if username:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    print(f"Started (UTC): {datetime.now(timezone.utc).isoformat()}")
    print(f"GET {final_url}")
    if username:
        print(f"Auth:          Basic ({username})")
    elif cert:
        print("Auth:          client certificate")
    else:
        print("Auth:          none")
    if sap_client:
        print(f"sap-client:    {sap_client}")
    print()

    request = urllib.request.Request(final_url, method="GET", headers=headers)
    ctx = ssl_context(ca=ca, cert=cert, key=key, insecure=args.insecure)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(request, context=ctx, timeout=args.timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - t0
        print(f"HTTP {exc.code}  ({elapsed:.1f}s)", file=sys.stderr)
        print(err_body[:8000], file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.perf_counter() - t0
    text = body.decode("utf-8", errors="replace")
    print(f"HTTP {status}  ({elapsed:.1f}s)  {content_type}")
    print()

    if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            print(text)
        else:
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
            results = None
            if isinstance(parsed, dict):
                d = parsed.get("d")
                if isinstance(d, dict) and isinstance(d.get("results"), list):
                    results = d["results"]
                elif isinstance(parsed.get("value"), list):
                    results = parsed["value"]
            if results is not None:
                print()
                print(f"rows: {len(results)}")
    else:
        print(text)

    print()
    print(f"Finished (UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
