"""
Daily Cognite RAW ingestion inspector for Everest + JDE IN + JDE NA.

Lists rows with lastUpdatedTime in the selected calendar day, breaks down
DATETIMESTAMP (source) age, and writes an HTML daily report (+ JSONL history).

Credentials (env vars):
  COGNITE_TOKEN     Bearer access token (required)

Optional:
  COGNITE_HOST      Default: https://az-phx-001.cognitedata.com
  COGNITE_PROJECT   Default: bdx-dev

Requires:
  pip install cognite-sdk

Usage (PowerShell) — from repo root or scripts/:
  $env:COGNITE_TOKEN = "<bearer-token>"

  # Default: Everest tiers 1,2,? + JDE IN + JDE NA + HTML report
  python scripts/cognite_inspect_glb_raw_daily.py

  # Include large Everest tiers 3-5
  python scripts/cognite_inspect_glb_raw_daily.py --include-large

  # Only JDE
  python scripts/cognite_inspect_glb_raw_daily.py --domain jdein,jdena

  # Only Everest tier 1
  python scripts/cognite_inspect_glb_raw_daily.py --domain everest --tier 1

  # Console only (no HTML)
  python scripts/cognite_inspect_glb_raw_daily.py --no-report
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_HOST = "https://az-phx-001.cognitedata.com"
DEFAULT_PROJECT = "bdx-dev"
DEFAULT_DATABASE = "db_databricks_glb_raw"
DEFAULT_TIERS = ("1", "2", "?")
LARGE_TIERS = ("3", "4", "5")

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = Path(__file__).resolve().parent / "reports"


def _tier_sort_key(tier: str) -> float:
    """Order tiers 1, 2, ?, 3, 4, 5 — '?' sits between 2 and 3."""
    if tier == "?":
        return 2.5
    try:
        return float(tier)
    except ValueError:
        return 99.0


def _env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, default).strip()


def build_client(token: str, host: str, project: str) -> Any:
    from cognite.client import ClientConfig, CogniteClient
    from cognite.client.config import global_config
    from cognite.client.credentials import Token

    global_config.disable_pypi_version_check = True
    return CogniteClient(
        ClientConfig(
            client_name="cognite-inspect-glb-raw-daily",
            project=project,
            base_url=host,
            credentials=Token(token),
        )
    )


# ---------------------------------------------------------------------------
# Catalogs (parsed from extractor YAML)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    domain: str  # everest | jdein | jdena
    view: str
    table: str
    tier: str  # 1-5, ?, or - for JDE


def _parse_everest(path: Path) -> list[CatalogEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[CatalogEntry] = []
    i = 0
    while i < len(lines):
        m = re.match(
            r"^#\s+([A-Z0-9]+)\s+-.*?\[TIER\s+([12345]|\?)(?:\s|\||\])",
            lines[i],
        )
        if m:
            view, tier = m.group(1), m.group(2)
            table = None
            for j in range(i + 1, min(i + 90, len(lines))):
                tm = re.search(r'table:\s*"([^"]+)"', lines[j])
                if tm:
                    table = tm.group(1)
                    break
                if j > i + 1 and re.match(r"^#\s+[A-Z0-9]+\s+-", lines[j]):
                    break
            if table:
                rows.append(CatalogEntry("everest", view, table, tier))
        i += 1
    return rows


def _parse_jde(path: Path, domain: str) -> list[CatalogEntry]:
    lines = path.read_text(encoding="utf-8").splitlines()
    suffix = "Jdeint" if domain == "jdein" else "Jdena"
    rows: list[CatalogEntry] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^#\s+([A-Z0-9]+)\s+-", lines[i])
        if m:
            view = m.group(1)
            table = None
            for j in range(i + 1, min(i + 90, len(lines))):
                tm = re.search(r'table:\s*"([^"]+)"', lines[j])
                if tm:
                    table = tm.group(1)
                    break
                if j > i + 1 and re.match(r"^#\s+[A-Z0-9]+\s+-", lines[j]):
                    break
            if table and table.endswith(suffix):
                rows.append(CatalogEntry(domain, view, table, "-"))
        i += 1
    return rows


def load_catalog() -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    for p in sorted((REPO_ROOT / "sap").glob("config-extract-*.yaml")):
        entries.extend(_parse_everest(p))
    entries.extend(_parse_jde(REPO_ROOT / "jde-in" / "config.yaml", "jdein"))
    entries.extend(_parse_jde(REPO_ROOT / "jde-na" / "config.yaml", "jdena"))
    return entries


def filter_catalog(
    entries: Iterable[CatalogEntry],
    domains: set[str],
    tiers: set[str],
) -> list[CatalogEntry]:
    out: list[CatalogEntry] = []
    for e in entries:
        if e.domain not in domains:
            continue
        if e.domain == "everest" and e.tier not in tiers:
            continue
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def day_start_ms(*, use_local: bool) -> tuple[int, str, date]:
    if use_local:
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"{start.date().isoformat()} (local {start.tzinfo})"
        return int(start.timestamp() * 1000), label, start.date()
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    label = f"{start.date().isoformat()} (UTC)"
    return int(start.timestamp() * 1000), label, start.date()


def parse_source_day(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text[:26].replace("Z", ""), fmt).date().isoformat()
        except ValueError:
            continue
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


@dataclass
class TableStats:
    domain: str
    view: str
    table: str
    tier: str
    rows_today: int
    unique_keys: int
    source_day_today: int
    source_day_before: int
    source_day_missing: int
    top_source_days: list[tuple[str, int]]
    status: str
    flag: str = ""
    error: str | None = None


def inspect_table(
    client: Any,
    database: str,
    entry: CatalogEntry,
    min_last_updated_time: int,
    today: date,
    sample: int,
) -> TableStats:
    try:
        rows = list(
            client.raw.rows.list(
                db_name=database,
                table_name=entry.table,
                min_last_updated_time=min_last_updated_time,
                limit=None,
            )
        )
    except Exception as exc:
        msg = str(exc).lower()
        status = "MISSING" if ("not found" in msg or "404" in msg) else "ERROR"
        return TableStats(
            domain=entry.domain,
            view=entry.view,
            table=entry.table,
            tier=entry.tier,
            rows_today=0,
            unique_keys=0,
            source_day_today=0,
            source_day_before=0,
            source_day_missing=0,
            top_source_days=[],
            status=status,
            error=str(exc),
        )

    keys = [r.key for r in rows]
    unique_keys = len(set(keys))
    day_counts: Counter[str] = Counter()
    source_today = 0
    source_before = 0
    source_missing = 0
    today_s = today.isoformat()

    for r in rows:
        cols = r.columns or {}
        raw_ts = cols.get("DATETIMESTAMP", cols.get("datetimestamp"))
        day = parse_source_day(raw_ts)
        if day is None:
            source_missing += 1
            day_counts["<missing>"] += 1
        elif day == today_s:
            source_today += 1
            day_counts[day] += 1
        else:
            source_before += 1
            day_counts[day] += 1

    if sample > 0 and rows:
        print(f"\n--- sample {entry.domain}/{entry.view} / {entry.table} ---")
        for r in rows[:sample]:
            cols = r.columns or {}
            print(
                f"  key={str(r.key)[:16]}... "
                f"lastUpdatedTime={r.last_updated_time} "
                f"DATETIMESTAMP={cols.get('DATETIMESTAMP')}"
            )

    flag = ""
    if len(rows) != unique_keys:
        flag = "!DUP_KEYS"
    elif len(rows) > 0 and source_before > source_today:
        flag = "~re-upsert/catch-up"

    return TableStats(
        domain=entry.domain,
        view=entry.view,
        table=entry.table,
        tier=entry.tier,
        rows_today=len(rows),
        unique_keys=unique_keys,
        source_day_today=source_today,
        source_day_before=source_before,
        source_day_missing=source_missing,
        top_source_days=day_counts.most_common(3),
        status="OK",
        flag=flag,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _summary(stats: list[TableStats]) -> dict[str, Any]:
    by_domain: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "tables": 0,
            "ok": 0,
            "missing": 0,
            "errors": 0,
            "upd_today": 0,
            "src_today": 0,
            "src_old": 0,
            "dup_keys": 0,
            "reupsert": 0,
        }
    )
    by_tier: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tables": 0, "upd_today": 0, "src_today": 0, "src_old": 0}
    )
    for s in stats:
        d = by_domain[s.domain]
        d["tables"] += 1
        if s.status == "OK":
            d["ok"] += 1
            d["upd_today"] += s.rows_today
            d["src_today"] += s.source_day_today
            d["src_old"] += s.source_day_before
            if s.flag == "!DUP_KEYS":
                d["dup_keys"] += 1
            if s.flag.startswith("~re-upsert"):
                d["reupsert"] += 1
        elif s.status == "MISSING":
            d["missing"] += 1
        else:
            d["errors"] += 1

        if s.domain == "everest":
            t = by_tier[s.tier]
            t["tables"] += 1
            if s.status == "OK":
                t["upd_today"] += s.rows_today
                t["src_today"] += s.source_day_today
                t["src_old"] += s.source_day_before

    return {"by_domain": dict(by_domain), "by_tier": dict(by_tier)}


def append_history(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_previous_history(path: Path, today: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    prev = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("day") != today:
            prev = obj
    return prev


def write_html_report(
    path: Path,
    *,
    host: str,
    project: str,
    database: str,
    day_label: str,
    day: str,
    stats: list[TableStats],
    summary: dict[str, Any],
    previous: dict[str, Any] | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def esc(x: Any) -> str:
        return html.escape(str(x))

    domain_rows = []
    for domain, d in sorted(summary["by_domain"].items()):
        prev_upd = None
        if previous:
            prev_upd = (previous.get("summary") or {}).get("by_domain", {}).get(domain, {}).get(
                "upd_today"
            )
        delta = ""
        if prev_upd is not None:
            delta = f"{d['upd_today'] - prev_upd:+d}"
        domain_rows.append(
            "<tr>"
            f"<td>{esc(domain)}</td>"
            f"<td>{d['tables']}</td><td>{d['ok']}</td>"
            f"<td>{d['missing']}</td><td>{d['errors']}</td>"
            f"<td>{d['upd_today']}</td><td>{d['src_today']}</td><td>{d['src_old']}</td>"
            f"<td>{d['dup_keys']}</td><td>{d['reupsert']}</td>"
            f"<td>{esc(delta)}</td>"
            "</tr>"
        )

    tier_rows = []
    for tier in ("1", "2", "?", "3", "4", "5"):
        t = summary["by_tier"].get(tier)
        if not t:
            continue
        tier_rows.append(
            "<tr>"
            f"<td>T{esc(tier)}</td><td>{t['tables']}</td>"
            f"<td>{t['upd_today']}</td><td>{t['src_today']}</td><td>{t['src_old']}</td>"
            "</tr>"
        )

    detail_rows = []
    for s in stats:
        top = ", ".join(f"{d}={n}" for d, n in s.top_source_days) or "-"
        cls = ""
        if s.status != "OK":
            cls = ' class="err"'
        elif s.flag == "!DUP_KEYS":
            cls = ' class="warn"'
        elif s.flag.startswith("~re-upsert"):
            cls = ' class="catchup"'
        detail_rows.append(
            f"<tr{cls}>"
            f"<td>{esc(s.domain)}</td><td>{esc(s.tier)}</td>"
            f"<td>{esc(s.view)}</td><td>{esc(s.table)}</td>"
            f"<td>{s.rows_today if s.status == 'OK' else '-'}</td>"
            f"<td>{s.unique_keys if s.status == 'OK' else '-'}</td>"
            f"<td>{s.source_day_today if s.status == 'OK' else '-'}</td>"
            f"<td>{s.source_day_before if s.status == 'OK' else '-'}</td>"
            f"<td>{s.source_day_missing if s.status == 'OK' else '-'}</td>"
            f"<td>{esc(s.status)}</td><td>{esc(s.flag or '-')}</td>"
            f"<td>{esc(top if s.status == 'OK' else (s.error or ''))}</td>"
            "</tr>"
        )

    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>RAW ingestion daily — {esc(day)}</title>
<style>
  :root {{ font-family: Segoe UI, system-ui, sans-serif; color: #1a1a1a; }}
  body {{ margin: 24px; background: #f6f7f9; }}
  h1,h2 {{ margin: 0 0 12px; }}
  .meta {{ color: #555; margin-bottom: 24px; }}
  .card {{ background: #fff; border: 1px solid #dde1e6; border-radius: 8px;
           padding: 16px 18px; margin-bottom: 18px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #eceff3; padding: 6px 8px; text-align: left; }}
  th {{ background: #f0f3f7; position: sticky; top: 0; }}
  tr.err {{ background: #fff1f0; }}
  tr.warn {{ background: #fff7e6; }}
  tr.catchup {{ background: #f0f7ff; }}
  .legend span {{ display: inline-block; padding: 2px 8px; margin-right: 8px;
                  border-radius: 4px; font-size: 12px; }}
  .legend .err {{ background: #fff1f0; }}
  .legend .warn {{ background: #fff7e6; }}
  .legend .catchup {{ background: #f0f7ff; }}
</style>
</head>
<body>
  <h1>Cognite RAW daily ingestion</h1>
  <div class="meta">
    Day: <b>{esc(day_label)}</b><br/>
    Host: {esc(host)} · Project: {esc(project)} · Database: {esc(database)}<br/>
    Generated: {esc(generated)} · Tables inspected: {len(stats)}
  </div>

  <div class="card">
    <h2>Summary by domain</h2>
    <table>
      <thead>
        <tr>
          <th>Domain</th><th>Tables</th><th>OK</th><th>Missing</th><th>Errors</th>
          <th>UPD_TODAY</th><th>SRC_TODAY</th><th>SRC_OLD</th>
          <th>DUP_KEYS</th><th>Re-upsert</th><th>Δ vs prev day</th>
        </tr>
      </thead>
      <tbody>
        {''.join(domain_rows)}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Everest by tier</h2>
    <table>
      <thead>
        <tr><th>Tier</th><th>Tables</th><th>UPD_TODAY</th><th>SRC_TODAY</th><th>SRC_OLD</th></tr>
      </thead>
      <tbody>
        {''.join(tier_rows) if tier_rows else '<tr><td colspan="5">No Everest tables in this run</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Detail</h2>
    <div class="legend" style="margin-bottom:10px">
      <span class="catchup">re-upsert/catch-up</span>
      <span class="warn">duplicate keys</span>
      <span class="err">missing/error</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>Domain</th><th>Tier</th><th>View</th><th>Table</th>
          <th>UPD_TODAY</th><th>UNIQ_KEY</th><th>SRC_TODAY</th><th>SRC_OLD</th>
          <th>MISSING_TS</th><th>Status</th><th>Flag</th><th>Top source days / error</th>
        </tr>
      </thead>
      <tbody>
        {''.join(detail_rows)}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>How to read</h2>
    <ul>
      <li><b>UPD_TODAY</b> — RAW rows with <code>lastUpdatedTime</code> in this calendar day</li>
      <li><b>UNIQ_KEY</b> — distinct RAW keys (should equal UPD_TODAY)</li>
      <li><b>SRC_TODAY / SRC_OLD</b> — those rows whose <code>DATETIMESTAMP</code> is today vs earlier</li>
      <li><b>~re-upsert/catch-up</b> — updates today mostly from old source timestamps (expected during catch-up)</li>
      <li><b>!DUP_KEYS</b> — same key listed more than once (unexpected)</li>
    </ul>
  </div>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Daily Cognite RAW ingestion inspector (Everest + JDE) with HTML report."
    )
    p.add_argument("--database", default=DEFAULT_DATABASE)
    p.add_argument(
        "--domain",
        default="everest,jdein,jdena",
        help="Comma list: everest,jdein,jdena (default: all).",
    )
    p.add_argument(
        "--tier",
        default="",
        help="Everest tiers, comma list (default: 1,2,? unless --include-large).",
    )
    p.add_argument(
        "--include-large",
        action="store_true",
        help="Include Everest tiers 3,4,5 (can be slow / heavy).",
    )
    p.add_argument("--tz", choices=("utc", "local"), default="local")
    p.add_argument("--sample", type=int, default=0)
    p.add_argument("--table", default="", help="Inspect a single RAW table name only.")
    p.add_argument(
        "--report-dir",
        default=str(REPORT_DIR),
        help=f"Directory for HTML/JSONL (default: {REPORT_DIR})",
    )
    p.add_argument("--no-report", action="store_true", help="Skip HTML/JSONL output.")
    return p.parse_args(argv)


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

    try:
        import cognite.client  # noqa: F401
    except ImportError:
        print("cognite-sdk is required. Install with: pip install cognite-sdk", file=sys.stderr)
        return 1

    domains = {d.strip().lower() for d in args.domain.split(",") if d.strip()}
    unknown = domains - {"everest", "jdein", "jdena"}
    if unknown:
        print(f"Unknown domain(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1

    if args.tier.strip():
        tiers = {t.strip() for t in args.tier.split(",") if t.strip()}
    else:
        tiers = set(DEFAULT_TIERS)
        if args.include_large:
            tiers |= set(LARGE_TIERS)

    catalog = load_catalog()
    targets = filter_catalog(catalog, domains, tiers)

    if args.table.strip():
        wanted = args.table.strip()
        targets = [e for e in catalog if e.table == wanted]
        if not targets:
            targets = [CatalogEntry("custom", wanted, wanted, "-")]

    min_ms, day_label, today = day_start_ms(use_local=(args.tz == "local"))
    day_s = today.isoformat()

    print(f"Host:     {host}")
    print(f"Project:  {project}")
    print(f"Database: {database}")
    print(f"Day:      {day_label}")
    print(f"Filter:   lastUpdatedTime >= {min_ms}")
    print(f"Domains:  {', '.join(sorted(domains))}")
    if "everest" in domains:
        print(f"Tiers:    {', '.join(sorted(tiers, key=_tier_sort_key))}")
    print(f"Tables:   {len(targets)}")
    print()
    print(
        f"{'DOMAIN':<8} {'TIER':<4} {'VIEW':<8} {'TABLE':<22} "
        f"{'UPD_TODAY':>10} {'UNIQ_KEY':>10} {'SRC_TODAY':>10} {'SRC_OLD':>10}  FLAG"
    )
    print("-" * 120)

    client = build_client(token, host, project)
    stats: list[TableStats] = []
    errors = 0

    for entry in targets:
        s = inspect_table(client, database, entry, min_ms, today, args.sample)
        stats.append(s)
        if s.status != "OK":
            errors += 1
            print(
                f"{s.domain:<8} {s.tier:<4} {s.view:<8} {s.table:<22} "
                f"{'-':>10} {'-':>10} {'-':>10} {'-':>10}  {s.status}"
            )
            continue
        print(
            f"{s.domain:<8} {s.tier:<4} {s.view:<8} {s.table:<22} "
            f"{s.rows_today:>10} {s.unique_keys:>10} {s.source_day_today:>10} "
            f"{s.source_day_before:>10}  {s.flag or '-'}"
        )

    summary = _summary(stats)
    total_upd = sum(s.rows_today for s in stats if s.status == "OK")
    print("-" * 120)
    print(f"Totals: updated_today={total_upd}  errors={errors}")

    if not args.no_report:
        report_dir = Path(args.report_dir)
        history_path = report_dir / "ingestion_history.jsonl"
        previous = load_previous_history(history_path, day_s)
        html_path = report_dir / f"ingestion_daily_{day_s}.html"

        payload = {
            "day": day_s,
            "day_label": day_label,
            "host": host,
            "project": project,
            "database": database,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "summary": summary,
            "tables": [asdict(s) for s in stats],
        }
        append_history(history_path, payload)
        write_html_report(
            html_path,
            host=host,
            project=project,
            database=database,
            day_label=day_label,
            day=day_s,
            stats=stats,
            summary=summary,
            previous=previous,
        )
        print()
        print(f"HTML report: {html_path}")
        print(f"History:     {history_path}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
