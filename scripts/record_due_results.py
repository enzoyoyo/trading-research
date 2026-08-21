#!/usr/bin/env python3
"""Auto-verify due trading-research decisions (self-evolution result-recorder).

Closes the automatable half of the decision-memory loop. Decision recording
captures *what the skill predicted* (direction, action level, and an honest
``factors.estimated_win_rate``). This script captures *what actually happened*:
at each decision's ``review_clock`` it fetches the current price (read-only via
``longbridge_query.py``), compares it against the entry price captured at
decision time (``price_at_decision``), infers the binary outcome, and appends a
result through the same append-only memory that powers preflight and the
calibration scorecard.

That is what turns accumulating decisions into the predicted-vs-realised pairs
calibration needs — without fabricating anything. A decision that never recorded
an entry price is skipped (surfaced for manual review), never guessed.

Read-only on market data. No broker access, no order execution, no external
secrets. Watch/avoid calls are left ``neutral`` (a price move alone cannot prove
a "stay out" call right), so they never inflate the win-rate calibration.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from trading_memory_core import (
    cmd_record_result,
    connect,
    db_path,
    now_iso,
    parse_time,
)
from prediction_ledger import settle_due_predictions

SCRIPTS = Path(__file__).resolve().parent
# Within this band the move is treated as a wash: honest "neutral", not a forced
# win or loss. Calibration excludes neutral outcomes from the Brier score.
DEFAULT_DEADBAND_PCT = 0.5
QuoteFn = Callable[[str], Optional[Union[float, dict[str, Any], tuple[float, str]]]]

LONG_DIRECTIONS = {"long", "buy", "build", "add"}
SHORT_DIRECTIONS = {"short", "sell", "reduce"}
# A price move alone cannot validate these; leave them for a human/result call.
NEUTRAL_DIRECTIONS = {"watch", "avoid"}


def longbridge_quote(symbol: str) -> dict[str, Any] | None:
    """Default read-only price fetch: shell out to longbridge_query.py.

    Returns price plus an exact resolved symbol, or None when unavailable.
    Never raises into the caller — missing or mismatched identity means
    "skip and report", not crash.
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "longbridge_query.py"), "quote", symbol, "--json"],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    items = data if isinstance(data, list) else [data]
    for item in items:
        price = item.get("price") if isinstance(item, dict) else None
        resolved_symbol = item.get("symbol") if isinstance(item, dict) else None
        if (
            isinstance(price, (int, float))
            and price > 0
            and isinstance(resolved_symbol, str)
            and resolved_symbol.upper() == symbol.upper()
        ):
            return {
                "price": float(price),
                "quote_source": "longbridge",
                "symbol": resolved_symbol.upper(),
            }
    return None


def is_a_share_symbol(symbol: str) -> bool:
    """Return True for A-share quote symbols accepted by a_stock_data_bridge."""
    s = str(symbol or "").strip().upper()
    return bool(re.fullmatch(r"\d{6}\.(SZ|SH|SS|SSE|SZSE|BJ|BSE)", s) or re.fullmatch(r"\d{6}", s))


def a_stock_quote(symbol: str) -> dict[str, Any] | None:
    """Read-only A-share fallback via a_stock_data_bridge.py.

    The bridge returns JSON shaped as {ok, items:[{price,...}]}. Missing/empty
    quotes preserve the existing skip semantics: never guess, never raise.
    """
    if not is_a_share_symbol(symbol):
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "a_stock_data_bridge.py"), "quote", symbol, "--json"],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode not in (0, 2) or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    for item in items:
        price = item.get("price") if isinstance(item, dict) else None
        resolved_symbol = item.get("canonical_symbol") if isinstance(item, dict) else None
        if (
            isinstance(price, (int, float))
            and price > 0
            and isinstance(resolved_symbol, str)
            and resolved_symbol.upper() == symbol.upper()
        ):
            return {
                "price": float(price),
                "quote_source": "a_stock_bridge",
                "symbol": resolved_symbol.upper(),
            }
    return None


def quote_with_fallback(
    symbol: str,
    primary: Callable[[str], Any] = longbridge_quote,
    a_fallback: Callable[[str], Any] = a_stock_quote,
) -> dict[str, Any] | None:
    """LongBridge first; A-share bridge only when LongBridge has no quote."""
    primary_value = primary(symbol)
    price, source = normalize_quote(primary_value)
    if price is not None and price > 0:
        resolved = primary_value.get("symbol") if isinstance(primary_value, dict) else symbol
        return {
            "price": float(price),
            "quote_source": source or "longbridge",
            "symbol": str(resolved).upper(),
        }
    if is_a_share_symbol(symbol):
        fallback_value = a_fallback(symbol)
        price, source = normalize_quote(fallback_value)
        if price is not None and price > 0:
            resolved = fallback_value.get("symbol") if isinstance(fallback_value, dict) else symbol
            return {
                "price": float(price),
                "quote_source": (
                    source
                    if isinstance(fallback_value, dict) and source
                    else "a_stock_bridge"
                ),
                "symbol": str(resolved).upper(),
            }
    return None


def normalize_quote(value: float | dict[str, Any] | tuple[float, str] | None) -> tuple[float | None, str | None]:
    if isinstance(value, dict):
        price = value.get("price")
        source = value.get("quote_source") or value.get("source")
        if isinstance(price, (int, float)) and price > 0:
            return float(price), str(source or "unknown")
        return None, str(source) if source else None
    if isinstance(value, tuple) and len(value) >= 2:
        price, source = value[0], value[1]
        if isinstance(price, (int, float)) and price > 0:
            return float(price), str(source)
        return None, str(source)
    if isinstance(value, (int, float)) and value > 0:
        return float(value), "stub"
    return None, None


def quote_symbol(decision: dict[str, Any]) -> str | None:
    """Prefer the explicit LongBridge symbol; fall back to symbol.market."""
    lb = decision.get("longbridge_symbol")
    if lb:
        return str(lb)
    symbol = decision.get("symbol")
    market = (decision.get("market") or "").upper()
    if not symbol:
        return None
    suffix = {"US": "US", "HK": "HK", "A": "SH", "SH": "SH", "SZ": "SZ"}.get(market)
    return f"{symbol}.{suffix}" if suffix else str(symbol)


def entry_price(payload: dict[str, Any]) -> float | None:
    """Entry/reference price captured at decision time. Never invented."""
    for source in (payload, payload.get("factors") or {}):
        value = source.get("price_at_decision")
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def directional_return_pct(direction: str, entry: float, current: float) -> float:
    """Return in the *direction of the thesis*: a short that fell is positive."""
    raw = (current - entry) / entry * 100.0
    if direction in SHORT_DIRECTIONS:
        return -raw
    return raw


def outcome_for(direction: str, thesis_return_pct: float, deadband_pct: float) -> str:
    if abs(thesis_return_pct) < deadband_pct:
        return "neutral"
    return "success" if thesis_return_pct > 0 else "failure"


def classify_due_decisions(conn, now: datetime, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return due decisions and legacy rows whose review_clock is invalid."""
    rows = conn.execute(
        "SELECT decision_id, symbol, market, longbridge_symbol, direction, "
        "review_clock, valid_for_review, payload_json "
        "FROM decisions ORDER BY analysis_time ASC"
    ).fetchall()
    due: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for row in rows:
        if conn.execute(
            "SELECT 1 FROM results WHERE decision_id=? LIMIT 1", (row["decision_id"],)
        ).fetchone():
            continue
        if not bool(row["valid_for_review"]):
            continue
        review_at = parse_time(row["review_clock"])
        if review_at is None or review_at.utcoffset() is None:
            invalid.append(
                {
                    "decision_id": row["decision_id"],
                    "symbol": row["symbol"],
                    "review_clock": row["review_clock"],
                }
            )
            continue
        if review_at > now:
            continue
        payload = {}
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except json.JSONDecodeError:
            payload = {}
        if len(due) >= limit:
            continue
        due.append(
            {
                "decision_id": row["decision_id"],
                "symbol": row["symbol"],
                "market": row["market"],
                "longbridge_symbol": row["longbridge_symbol"],
                "direction": row["direction"],
                "valid_for_review": row["valid_for_review"],
                "payload": payload,
            }
        )
    return due, invalid


def due_decisions(conn, now: datetime, limit: int) -> list[dict[str, Any]]:
    """Backward-compatible due-only view; run() also exposes invalid clocks."""
    due, _invalid = classify_due_decisions(conn, now, limit)
    return due


def record_one(
    conn, decision: dict[str, Any], entry: float, current: float, deadband_pct: float, validation_time: str, quote_source: str
) -> dict[str, Any]:
    direction = decision["direction"]
    thesis_return = round(directional_return_pct(direction, entry, current), 4)
    raw_return = round((current - entry) / entry * 100.0, 4)
    outcome = "neutral" if direction in NEUTRAL_DIRECTIONS else outcome_for(direction, thesis_return, deadband_pct)
    result_payload = {
        "validation_time": validation_time,
        "price_at_decision": entry,
        "price_at_validation": current,
        "return_pct": raw_return,
        "exit_type": "timeout_exit",
        "expired_early": False,
        "outcome": outcome,
        "quote_source": quote_source,
        "review_evidence_ids": [f"auto:review_clock:{validation_time}"],
        "notes": f"auto-verified at review_clock by record_due_results.py (read-only price via {quote_source}; no trade)",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(result_payload, fh, ensure_ascii=False)
        tmp = fh.name
    try:
        out = cmd_record_result(
            conn,
            argparse.Namespace(payload=tmp, decision_id=decision["decision_id"], db=None),
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {
        "decision_id": decision["decision_id"],
        "symbol": decision["symbol"],
        "direction": direction,
        "entry": entry,
        "current": current,
        "return_pct": raw_return,
        "thesis_return_pct": thesis_return,
        "quote_source": quote_source,
        "outcome": out["outcome"],
        "already_processed": out.get("already_processed", False),
        "skipped_lock_timeout": out.get("skipped_lock_timeout", False),
    }


def run(
    conn,
    now: datetime,
    quote_fn: QuoteFn,
    deadband_pct: float,
    limit: int,
    dry_run: bool,
) -> dict[str, Any]:
    validation_time = now_iso()
    recorded: list[dict[str, Any]] = []
    skipped_no_price: list[str] = []
    skipped_no_quote: list[str] = []
    skipped_no_symbol: list[str] = []
    skipped_already_processed: list[str] = []
    skipped_lock_timeout: list[str] = []
    due, invalid_review_clocks = classify_due_decisions(conn, now, limit)
    for decision in due:
        entry = entry_price(decision["payload"])
        if entry is None:
            skipped_no_price.append(decision["decision_id"])
            continue
        symbol = quote_symbol(decision)
        if not symbol:
            skipped_no_symbol.append(decision["decision_id"])
            continue
        current, quote_source = normalize_quote(quote_fn(symbol))
        if current is None or current <= 0:
            skipped_no_quote.append(decision["decision_id"])
            continue
        if dry_run:
            recorded.append(
                {"decision_id": decision["decision_id"], "symbol": decision["symbol"], "dry_run": True, "quote_source": quote_source}
            )
            continue
        result = record_one(
            conn,
            decision,
            entry,
            current,
            deadband_pct,
            validation_time,
            quote_source or "unknown",
        )
        if result["skipped_lock_timeout"]:
            skipped_lock_timeout.append(decision["decision_id"])
        elif result["already_processed"]:
            skipped_already_processed.append(decision["decision_id"])
        else:
            recorded.append(result)
    return {
        "ok": True,
        "status": (
            "partial" if recorded and (skipped_no_quote or skipped_no_price or invalid_review_clocks)
            else "blocked" if due and not recorded and (skipped_no_quote or skipped_no_price or invalid_review_clocks)
            else "recorded" if recorded
            else "no_due"
        ),
        "due_count": len(due),
        "validation_time": validation_time,
        "dry_run": dry_run,
        "recorded_count": len(recorded),
        "recorded": recorded,
        "skipped_no_entry_price": skipped_no_price,
        "skipped_no_symbol": skipped_no_symbol,
        "skipped_no_quote": skipped_no_quote,
        "skipped_already_processed": skipped_already_processed,
        "skipped_lock_timeout": skipped_lock_timeout,
        "invalid_review_clocks": invalid_review_clocks,
        "manual_review_hint": (
            "decisions with invalid_review_clocks require an audited ISO-8601 migration; "
            "decisions skipped for a missing entry price need a human record-result."
            if invalid_review_clocks or skipped_no_price
            else None
        ),
        "no_order_execution": True,
    }


def self_test() -> dict[str, Any]:
    """Seed due decisions with known entry prices and a stub quote; assert the
    recorder produces the right outcomes, stays idempotent, and never fabricates
    a result for a decision missing its entry price."""
    import argparse as _argparse

    from trading_memory_core import cmd_record_decision

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "memory.sqlite"
        conn = connect(path)
        past = "2020-01-01T00:00:00+00:00"
        seeds = [
            # (symbol, direction, entry, expects)
            ("WINL", "long", 100.0, "success"),   # long, price rises -> win
            ("LOSL", "long", 100.0, "failure"),   # long, price falls -> loss
            ("WINS", "short", 100.0, "success"),  # short, price falls -> win
            ("WACH", "watch", 100.0, "neutral"),  # watch -> never auto-judged
            ("000725", "long", 10.0, "success"),  # A-share fallback when LongBridge has no quote
        ]
        for symbol, direction, entry, _ in seeds:
            market = "SZ" if symbol == "000725" else "US"
            decision = {
                "symbol": symbol,
                "market": market,
                "longbridge_symbol": f"{symbol}.SZ" if market == "SZ" else f"{symbol}.US",
                "direction": direction,
                "action_level": "L2",
                "price_at_decision": entry,
                "factors": {"estimated_win_rate": 0.6},
                "review_clock": past,
            }
            dpath = Path(td) / f"{symbol}.json"
            dpath.write_text(json.dumps(decision), encoding="utf-8")
            cmd_record_decision(conn, _argparse.Namespace(payload=str(dpath), db=str(path)))
        # A due decision with NO entry price must be skipped, never guessed.
        nop = {"symbol": "NOPX", "market": "US", "direction": "long", "action_level": "L2", "review_clock": past}
        npath = Path(td) / "NOPX.json"
        npath.write_text(json.dumps(nop), encoding="utf-8")
        cmd_record_decision(conn, _argparse.Namespace(payload=str(npath), db=str(path)))

        prices = {"WINL.US": 112.0, "LOSL.US": 88.0, "WINS.US": 90.0, "WACH.US": 130.0, "NOPX.US": 105.0}

        def stub_quote(symbol: str) -> dict[str, Any] | None:
            return quote_with_fallback(
                symbol,
                primary=lambda s: prices.get(s),
                a_fallback=lambda s: 11.0 if s == "000725.SZ" else None,
            )

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        out = run(conn, now, quote_fn=stub_quote, deadband_pct=DEFAULT_DEADBAND_PCT, limit=50, dry_run=False)

        got = {r["symbol"]: r["outcome"] for r in out["recorded"]}
        for symbol, _direction, _entry, expected in seeds:
            assert got.get(symbol) == expected, f"{symbol}: expected {expected}, got {got.get(symbol)} :: {out}"
        a_row = next((r for r in out["recorded"] if r["symbol"] == "000725"), None)
        assert a_row and a_row["quote_source"] == "a_stock_bridge", out
        assert out["skipped_no_entry_price"] == [
            r["decision_id"] for r in conn.execute("SELECT decision_id FROM decisions WHERE symbol='NOPX'").fetchall()
        ] or "NOPX" not in got, out
        assert len(out["skipped_no_entry_price"]) == 1, out
        # Idempotent: a second pass finds nothing due (all now have results).
        again = run(conn, now, quote_fn=lambda s: prices.get(s), deadband_pct=DEFAULT_DEADBAND_PCT, limit=50, dry_run=False)
        assert again["recorded_count"] == 0, again
        conn.close()
        return {"ok": True, "self_test": "passed", "recorded": out["recorded_count"], "outcomes": got}


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-verify due trading-research decisions at their review_clock")
    ap.add_argument("--db", help="SQLite DB path; default TRADING_MEMORY_DB or core default")
    ap.add_argument("--deadband-pct", type=float, default=DEFAULT_DEADBAND_PCT,
                    help="moves smaller than this (abs %%) record as neutral")
    ap.add_argument("--limit", type=int, default=200, help="max decisions to verify in one run")
    ap.add_argument("--dry-run", action="store_true", help="report what would be recorded without writing")
    ap.add_argument("--now", help="override 'now' (ISO-8601); for testing only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc).astimezone()
    if now is None or now.utcoffset() is None:
        print(json.dumps({"ok": False, "error": "invalid_or_timezone_naive_--now"}, ensure_ascii=False))
        return 1

    conn = connect(db_path(args))
    try:
        out = run(conn, now, quote_with_fallback, args.deadband_pct, args.limit, args.dry_run)
        out["prediction_ledger"] = settle_due_predictions(
            conn,
            now,
            quote_fn=quote_with_fallback,
            normalize_quote_fn=normalize_quote,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.json else None))
    decision_fail_closed = bool(
        out.get("skipped_no_quote")
        or out.get("invalid_review_clocks")
        or out.get("skipped_lock_timeout")
    )
    return 2 if decision_fail_closed or out["prediction_ledger"].get("fail_closed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
