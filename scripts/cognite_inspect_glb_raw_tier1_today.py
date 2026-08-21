"""
Compatibility wrapper — prefer cognite_inspect_glb_raw_daily.py.

Runs the daily inspector limited to Everest Tier 1 (same COGNITE_* env vars).
"""

from __future__ import annotations

import sys

from cognite_inspect_glb_raw_daily import main as daily_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Force Everest Tier 1 unless user already passed domain/tier.
    if not any(a.startswith("--domain") for a in args):
        args = ["--domain", "everest", *args]
    if not any(a.startswith("--tier") for a in args) and "--include-large" not in args:
        args = ["--tier", "1", *args]
    return daily_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
