#!/usr/bin/env python3
"""Append-only, queryable ledger for trading-research daily self-optimization.

Each daily run appends one structured row so trends and silent failures
(e.g. GitHub refresh failing for several days) are visible with a single
query instead of grepping multi-KB transcripts.

This script never edits the skill, never trades, and stores runtime data
outside the skill directory.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
from datetime import datetime, timezone
from typing import Any

DEFAULT_LEDGER = (
    pathlib.Path.home() / ".hermes" / "work" / "trading-research-autoevolve" / "ledger.jsonl"
)


def ledger_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("SELF_OPT_LEDGER", str(DEFAULT_LEDGER)))


def read_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def derive_from_check(doc: dict[str, Any]) -> dict[str, Any]:
    """Pull deterministic fields from a self_optimization_check.py JSON document."""
    eval_suite = doc.get("eval_suite") or {}
    eval_cmp = doc.get("eval_comparison") or {}
    perf = doc.get("performance_snapshot") or {}
    gh = doc.get("github_reference_projects") or {}
    grok_auth = doc.get("grok_auth_presence") or {}
    grok_health = doc.get("grok_health") or {}
    local = doc.get("local_files") or {}

    if gh.get("skipped"):
        github = "skipped"
    elif gh.get("ok"):
        github = "refreshed"
    elif gh.get("repos"):
        github = "partial"
    else:
        github = "skipped"

    if grok_health and not grok_health.get("skipped"):
        grok = "available" if grok_health.get("ok") else "unavailable"
    elif grok_auth.get("present"):
        grok = "available"
    else:
        grok = "unavailable"

    return {
        "performance_materiality": perf.get("performance_materiality"),
        "eval_pass_count": eval_suite.get("passed"),
        "eval_total": eval_suite.get("total"),
        "eval_regressions": eval_cmp.get("regressions") or [],
        "sources_checked": {
            "github": github,
            "grok": grok,
            "learning_packet": perf.get("learning_packet_status", "unknown"),
            "trading_memory": perf.get("trading_memory_status", "unknown"),
        },
        "version": local.get("version"),
    }


def parse_changes(raw_changes: list[str], changes_json: str | None) -> list[dict[str, str]]:
    if changes_json:
        parsed = json.loads(changes_json)
        if not isinstance(parsed, list):
            raise ValueError("--changes-json must be a JSON array")
        return parsed
    changes: list[dict[str, str]] = []
    for item in raw_changes:
        file_part, _, reason = item.partition("::")
        changes.append({"file": file_part.strip(), "reason": reason.strip()})
    return changes


def build_row(args: argparse.Namespace) -> dict[str, Any]:
    derived: dict[str, Any] = {}
    if args.from_check:
        doc = json.loads(pathlib.Path(args.from_check).read_text(encoding="utf-8"))
        derived = derive_from_check(doc)
    now = datetime.now(timezone.utc)
    return {
        "date": args.date or now.date().isoformat(),
        "generated_at_utc": now.isoformat(),
        "status": args.status or "unknown",
        "materiality": args.materiality or "none",
        "performance_materiality": derived.get("performance_materiality"),
        "eval_pass_count": derived.get("eval_pass_count"),
        "eval_total": derived.get("eval_total"),
        "eval_regressions": derived.get("eval_regressions") or [],
        "sources_checked": derived.get("sources_checked")
        or {"github": "unknown", "grok": "unknown", "learning_packet": "unknown", "trading_memory": "unknown"},
        "changes": parse_changes(args.change or [], args.changes_json),
        "version": derived.get("version"),
        "next_watch": args.next_watch or [],
    }


def cmd_append(args: argparse.Namespace) -> int:
    row = build_row(args)
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"ok": True, "appended": row, "ledger": str(path)}, ensure_ascii=False, indent=2))
    return 0


def _tail_streak(rows: list[dict[str, Any]], predicate) -> int:
    streak = 0
    for row in reversed(rows):
        if predicate(row):
            streak += 1
        else:
            break
    return streak


def health_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    def src(row: dict[str, Any], key: str) -> str:
        return (row.get("sources_checked") or {}).get(key, "unknown")

    # Daily self-optimization intentionally runs self_optimization_check.py with
    # --skip-network; only Monday performs the external reference refresh. Do not
    # count expected daily "skipped" rows as a stalled GitHub refresh, otherwise
    # the ledger becomes false-positive noisy every week. A repeated "partial"
    # status still means an attempted refresh is failing and should surface.
    gh_partial_streak = _tail_streak(rows, lambda r: src(r, "github") == "partial")
    if gh_partial_streak >= 3:
        issues.append({"type": "github_refresh_partial_stalled", "consecutive_runs": gh_partial_streak})

    grok_streak = _tail_streak(rows, lambda r: src(r, "grok") == "unavailable")
    if grok_streak >= 3:
        issues.append({"type": "grok_unavailable_stalled", "consecutive_runs": grok_streak})

    blocked_streak = _tail_streak(rows, lambda r: r.get("status") == "blocked")
    if blocked_streak >= 2:
        issues.append({"type": "blocked_streak", "consecutive_runs": blocked_streak})

    counts = [r.get("eval_pass_count") for r in rows if isinstance(r.get("eval_pass_count"), int)]
    if len(counts) >= 2 and counts[-1] < counts[-2]:
        issues.append({"type": "eval_pass_count_drop", "from": counts[-2], "to": counts[-1]})

    return {"ok": not issues, "issues": issues, "rows_scanned": len(rows)}


def cmd_query(args: argparse.Namespace) -> int:
    rows = read_rows(ledger_path())
    if args.status:
        rows = [r for r in rows if r.get("status") == args.status]
    if args.health:
        print(json.dumps(health_report(rows), ensure_ascii=False, indent=2))
        return 0
    if args.last:
        rows = rows[-args.last :]
    print(json.dumps({"ok": True, "count": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Self-optimization structured ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("append", help="append one daily decision row")
    p.add_argument("--from", dest="from_check", help="self_optimization_check.py JSON path")
    p.add_argument("--status", choices=["upgraded", "no_necessary_upgrade", "blocked", "unknown"])
    p.add_argument("--materiality", choices=["high", "medium", "low", "none"])
    p.add_argument("--change", action="append", help="'path::reason'; repeatable")
    p.add_argument("--changes-json", help="JSON array of {file, reason}")
    p.add_argument("--next-watch", action="append", dest="next_watch", help="repeatable watch note")
    p.add_argument("--date", help="override ISO date (default: today UTC)")
    p.set_defaults(func=cmd_append)

    q = sub.add_parser("query", help="query the ledger")
    q.add_argument("--last", type=int, help="show last N rows")
    q.add_argument("--status", choices=["upgraded", "no_necessary_upgrade", "blocked", "unknown"])
    q.add_argument("--health", action="store_true", help="report silent-failure signals")
    q.set_defaults(func=cmd_query)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
