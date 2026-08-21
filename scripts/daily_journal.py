#!/usr/bin/env python3
"""Daily Trading Journal composer for trading-research.

CLI compatibility and public re-export shim. Data reads remain isolated in
``daily_journal_data``; markdown generation and redaction live in
``daily_journal_render``. The module is read-only against System A and never
submits orders or changes action levels.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from daily_journal_data import *  # noqa: F401,F403 - compatibility re-export
from daily_journal_render import *  # noqa: F401,F403 - compatibility re-export

def compose(dates: list[str], system_a_root: Path, journal_root: Path) -> dict[str, Any]:
    date_set = set(dates)
    dmin = date.fromisoformat(min(dates)) - timedelta(days=2)
    dmax = date.fromisoformat(max(dates)) + timedelta(days=2)
    start = datetime.combine(dmin, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(dmax, datetime.min.time(), tzinfo=timezone.utc)

    buckets: dict[str, dict[str, Any]] = defaultdict(new_day_bucket)
    for d in dates:
        buckets[d]  # ensure present even if empty

    gather_proposals(date_set, system_a_root, buckets, start, end)
    gather_exit_proposals(date_set, system_a_root, buckets, start, end)
    gather_decision_packets(date_set, system_a_root, buckets, start, end)
    gather_orders(date_set, system_a_root, buckets)
    gather_outcomes(date_set, system_a_root, buckets)
    gather_snapshots(date_set, system_a_root, buckets)
    gather_hypothesis_changes(date_set, buckets)
    calibration_available = gather_calibration(date_set, buckets)

    index_path = journal_root / "index.jsonl"
    rows_by_date = load_index(index_path)
    written_pages = []
    for d in dates:
        bucket = buckets[d]
        md = render_day_markdown(d, bucket, calibration_available)
        page_path = journal_root / "daily" / f"{d}.md"
        atomic_write(page_path, md)
        written_pages.append(str(page_path))
        rows_by_date[d] = build_index_row(d, bucket)

    fill_equity_change(rows_by_date)
    write_index(index_path, rows_by_date)
    readme_path = journal_root / "README.md"
    atomic_write(readme_path, render_readme(rows_by_date))

    return {
        "ok": True,
        "dates": dates,
        "pages_written": written_pages,
        "index_path": str(index_path),
        "readme_path": str(readme_path),
        "calibration_available": calibration_available,
    }


def ensure_git_repo(journal_root: Path) -> None:
    if (journal_root / ".git").exists():
        return
    journal_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(journal_root), check=True, capture_output=True)
    gitignore = journal_root / ".gitignore"
    if not gitignore.exists():
        atomic_write(gitignore, "")


def publish(journal_root: Path, dates: list[str], push: bool) -> dict[str, Any]:
    ensure_git_repo(journal_root)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(journal_root), check=True, capture_output=True, text=True
    )
    if not status.stdout.strip():
        return {"committed": False, "reason": "no_changes"}
    subprocess.run(["git", "add", "-A"], cwd=str(journal_root), check=True, capture_output=True)
    message = f"journal: {dates[0]}" if len(dates) == 1 else f"journal: {dates[0]}..{dates[-1]}"
    subprocess.run(["git", "commit", "-m", message], cwd=str(journal_root), check=True, capture_output=True)
    result: dict[str, Any] = {"committed": True, "message": message}
    if push:
        push_result = subprocess.run(["git", "push"], cwd=str(journal_root), capture_output=True, text=True)
        result["pushed"] = push_result.returncode == 0
        result["push_stderr"] = push_result.stderr
    return result


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "system_a"
        journal = Path(td) / "journal"
        (root / "proposals").mkdir(parents=True)
        (root / "decision_packets").mkdir(parents=True)
        (root / "journal").mkdir(parents=True)

        day = "2026-07-06"
        gen_ts = "20260706T143000Z"

        # Fake entry proposal with a token_path leak to prove redaction works.
        proposal = {
            "generated_at_utc": "2026-07-06T14:30:00Z",
            "gate": {
                "paper_account_gate": "pass",
                "token_path": "${HOME}/.hermes/longbridge-paper-home/.longbridge/openapi/cli-auth",
            },
            "orders": [{
                "symbol": "XLF.US",
                "side": "buy",
                "quantity": 100,
                "limit_price": "50.00",
                "thesis": "test thesis above sma20",
                "invalidation": "close below stop",
                "metadata": {"decision_fusion": {"win_rate_proxy": 0.6, "composite_score": 70.0, "confidence": "medium"}},
            }],
        }
        (root / "proposals" / f"generated_{gen_ts}.json").write_text(json.dumps(proposal), encoding="utf-8")

        exit_proposal = {
            "generated_at_utc": "2026-07-06T14:31:00Z",
            "orders": [],
            "diagnostics": [{"symbol": "XLF.US", "status": "no_exit", "checks": {"regime_profile": "defensive"}}],
        }
        (root / "proposals" / f"exit_generated_{gen_ts}.json").write_text(json.dumps(exit_proposal), encoding="utf-8")

        packet = {
            "generated_at_utc": "2026-07-06T14:30:27Z",
            "policy_summary": {"universe": ["XLF.US", "SPY.US"], "universe_core": ["XLF.US"], "max_positions": 5,
                                "max_notional_per_order_pct": 0.05, "daily_new_orders_limit": 5},
        }
        (root / "decision_packets" / f"paper_decision_packet_{gen_ts}.json").write_text(json.dumps(packet), encoding="utf-8")

        orders_jsonl = [
            {"event_type": "paper_order_submit_result", "timestamp_utc": "2026-07-06T14:31:07Z",
             "proposal_id": "p1", "status": "submitted",
             "result": {"returncode": 0, "stdout": "Submitting Buy order: 100 XLF.US @ 50.00\n{}"}},
            {"event_type": "paper_order_validation", "timestamp_utc": "2026-07-06T14:32:00Z", "valid": False,
             "reasons": ["daily_new_orders_limit_reached"],
             "proposal": {"symbol": "SPY.US", "side": "buy", "quantity": 10}},
            # Same symbol/side/reason-set retried 3x with a slightly different
            # limit price and quantity each scan cycle (real System A behavior)

            # -- must aggregate into one row with an attempt count + ranges.
            {"event_type": "paper_order_validation", "timestamp_utc": "2026-07-06T14:33:00Z", "valid": False,
             "reasons": ["max_positions_reached", "cash_max_qty_exceeded_or_unavailable"],
             "proposal": {"symbol": "MU.US", "side": "sell", "quantity": 100, "limit_price": "20.10"}},
            {"event_type": "paper_order_validation", "timestamp_utc": "2026-07-06T15:03:00Z", "valid": False,
             "reasons": ["cash_max_qty_exceeded_or_unavailable", "max_positions_reached"],
             "proposal": {"symbol": "MU.US", "side": "sell", "quantity": 105, "limit_price": "20.15"}},
            {"event_type": "paper_order_validation", "timestamp_utc": "2026-07-06T15:33:00Z", "valid": False,
             "reasons": ["max_positions_reached", "cash_max_qty_exceeded_or_unavailable"],
             "proposal": {"symbol": "MU.US", "side": "sell", "quantity": 110, "limit_price": "20.20"}},
        ]
        with (root / "journal" / "paper_orders.jsonl").open("w", encoding="utf-8") as fh:
            for r in orders_jsonl:
                fh.write(json.dumps(r) + "\n")

        outcomes_jsonl = [
            {"event_type": "paper_trade_outcome", "status": "open", "symbol": "XLF.US", "side": "Buy",
             "quantity": 100, "entry_price": "50.00", "timestamp_utc": "2026-07-06T14:31:10Z"},
            {"event_type": "paper_trade_outcome", "status": "closed", "symbol": "XLF.US", "side": "Sell",
             "quantity": 100, "exit_price": "52.00", "exit_reason": "hard_stop", "r_multiple": "1.0",
             "timestamp_utc": "2026-07-06T15:00:00Z"},
        ]
        with (root / "journal" / "paper_outcomes.jsonl").open("w", encoding="utf-8") as fh:
            for r in outcomes_jsonl:
                fh.write(json.dumps(r) + "\n")

        snapshots_jsonl = [
            {"event_type": "paper_position_snapshot", "timestamp_utc": "2026-07-06T20:00:00Z",
             "gate": {"token_path": "${HOME}/.hermes/longbridge-paper-home/.longbridge/openapi/cli-auth"},
             "portfolio": {"assets": [{"net_assets": "100000.00", "total_cash": "50000.00"}],
                           "market_value": "50000.00", "unrealized_pnl": "500.00",
                           "unrealized_pnl_pct": "1.0", "position_count": 1}},
        ]
        with (root / "journal" / "paper_position_snapshots.jsonl").open("w", encoding="utf-8") as fh:
            for r in snapshots_jsonl:
                fh.write(json.dumps(r) + "\n")

        result = compose([day], root, journal)
        assert result["ok"], result
        page = (journal / "daily" / f"{day}.md").read_text(encoding="utf-8")
        assert "token_path" not in page and "cli-auth" not in page, "redaction failed: raw token_path leaked"
        assert "cli-auth" not in page, "credential path leaked"
        assert "XLF.US" in page
        assert "hard_stop" in page
        assert page.count("MU.US sell") == 1, "rejected retries with same reason-set must collapse to one row"
        assert "×3 attempts" in page, "rejected-order aggregation must report the attempt count"
        assert "qty 100-110" in page, "rejected-order aggregation must report the quantity range"
        assert "limit 20.1-20.2" in page, "rejected-order aggregation must report the limit-price range"
        assert "cash_max_qty_exceeded_or_unavailable, max_positions_reached" in page, "reasons must render sorted and deduped per group"

        index_rows = load_index(journal / "index.jsonl")
        assert day in index_rows
        row = index_rows[day]
        assert row["proposals"] == 1
        assert row["closed_trades"] == 1
        assert row["realized_pnl"] == 200.0, row["realized_pnl"]  # (52-50)*100
        assert row["error_labels"] == ["hard_stop"]

        # Idempotent recompose: run again, page count for that date stays 1 in index.
        compose([day], root, journal)
        index_rows_2 = load_index(journal / "index.jsonl")
        assert len(index_rows_2) == 1, "recompose duplicated index rows instead of upserting"

        readme = (journal / "README.md").read_text(encoding="utf-8")
        assert "token" not in readme.lower() or "[redacted]" in readme

        return {"ok": True, "self_test": "passed", "realized_pnl": row["realized_pnl"]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Daily Trading Journal composer (System A read-only)")
    ap.add_argument("--compose", action="store_true", help="recompose the most recent --days trading days")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--date", help="recompose a single YYYY-MM-DD trading day")
    ap.add_argument("--publish", action="store_true", help="local git commit after composing (never pushes by itself)")
    ap.add_argument("--push", action="store_true", help="also git push after --publish; requires a configured remote")
    ap.add_argument("--system-a-root", default=str(DEFAULT_SYSTEM_A_ROOT))
    ap.add_argument("--journal-root", default=str(DEFAULT_JOURNAL_ROOT))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    if not args.compose and not args.date:
        ap.error("specify --compose or --date")

    dates = [args.date] if args.date else recent_trading_dates(args.days)
    system_a_root = Path(args.system_a_root).expanduser()
    journal_root = Path(args.journal_root).expanduser()

    result = compose(dates, system_a_root, journal_root)
    if args.publish:
        result["publish"] = publish(journal_root, dates, args.push)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
