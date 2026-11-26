"""trading-research decision memory review.

CLI command implementations, aggregation, and statistics for the
decision-memory substrate: recording decisions/results, rolling review,
recommendation derivation, preflight checks, and self-test. Split out of
trading_memory_core.py (Task 6, skill_optimization_plan_20260705.md). No
broker access, no real trade execution, no external secrets.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_schema import STRATEGY_VERSION, FAILURE_TAGS, require, decision_completeness, bool_int, row_to_decision, row_to_result
from memory_store import now_iso, parse_time, age_decay_weight, make_id, db_path, connect, jdump, read_payload, event, set_state, get_state

RESULT_LOCK_WAIT_SECONDS = 10.0
RESULT_LOCK_ATTEMPT_MS = 250
RESULT_LOCK_RETRY_SECONDS = 0.025
RESULT_SAVEPOINT = "record_result_scope"

def record_claims(conn: sqlite3.Connection, decision_id: str, payload: dict[str, Any]) -> list[str]:
    claims = payload.get("claims") or []
    if not claims:
        symbol = payload.get("symbol", "UNKNOWN")
        factors = payload.get("factors") or {}
        confidence = None
        for key in ("model_score", "estimated_win_rate"):
            if isinstance(factors.get(key), (int, float)):
                confidence = float(factors[key])
                break
        claims = [
            {
                "subject": symbol,
                "relation": "factor_observed_at_decision",
                "object": str(k),
                "polarity": "positive",
                "regime": payload.get("risk_regime") or payload.get("attack_state"),
                "evidence_ids": payload.get("evidence_ids", []),
                "confidence": confidence,
                "valid_from": payload.get("analysis_time"),
            }
            for k, v in factors.items()
            if v not in (None, "", [], {})
        ]
    claim_ids = []
    for item in claims[:80]:
        claim_id = item.get("claim_id") or make_id("CL", payload.get("symbol"))
        record = {
            "claim_id": claim_id,
            "decision_id": decision_id,
            "subject": item.get("subject") or payload.get("symbol") or "UNKNOWN",
            "relation": item.get("relation") or "related_to_decision",
            "object": item.get("object") or "unknown",
            "polarity": item.get("polarity") or "positive",
            "regime": item.get("regime") or payload.get("risk_regime") or payload.get("attack_state"),
            "evidence_ids": item.get("evidence_ids") or payload.get("evidence_ids", []),
            "confidence": item.get("confidence"),
            "valid_from": item.get("valid_from") or payload.get("analysis_time"),
            "valid_to": item.get("valid_to"),
        }
        conn.execute(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["claim_id"],
                record["decision_id"],
                record["subject"],
                record["relation"],
                record["object"],
                record["polarity"],
                record["regime"],
                jdump(record["evidence_ids"]),
                record["confidence"],
                record["valid_from"],
                record["valid_to"],
                jdump(record),
            ),
        )
        claim_ids.append(claim_id)
    return claim_ids


def cmd_record_decision(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    payload = read_payload(args.payload)
    require(payload, ["symbol", "market", "direction", "action_level"])
    valid_for_review = bool(payload.get("valid_for_review", True))
    review_clock = payload.get("review_clock")
    if valid_for_review:
        parsed_review_clock = parse_time(str(review_clock) if review_clock is not None else None)
        if parsed_review_clock is None or parsed_review_clock.utcoffset() is None:
            raise SystemExit("review_clock must be timezone-aware ISO-8601 when valid_for_review=true")
        payload["review_clock"] = parsed_review_clock.astimezone(timezone.utc).isoformat(timespec="seconds")
    elif review_clock:
        parsed_review_clock = parse_time(str(review_clock))
        if parsed_review_clock is None or parsed_review_clock.utcoffset() is None:
            raise SystemExit("review_clock must be timezone-aware ISO-8601 when provided")
        payload["review_clock"] = parsed_review_clock.astimezone(timezone.utc).isoformat(timespec="seconds")
    decision_id = payload.get("decision_id") or make_id("DM", payload.get("symbol"))
    if conn.execute("SELECT 1 FROM decisions WHERE decision_id=?", (decision_id,)).fetchone():
        raise SystemExit(f"decision_id already exists: {decision_id}")
    analysis_time = payload.get("analysis_time") or now_iso()
    payload["decision_id"] = decision_id
    payload["analysis_time"] = analysis_time
    payload.setdefault("strategy_version", STRATEGY_VERSION)
    conn.execute(
        """
        INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            analysis_time,
            payload.get("agent"),
            payload.get("skill_version"),
            payload.get("strategy_version"),
            payload["symbol"].upper(),
            payload["market"].upper(),
            payload.get("longbridge_symbol"),
            payload["direction"],
            payload["action_level"],
            payload.get("action_label"),
            payload.get("position_cap_pct"),
            payload.get("risk_level"),
            payload.get("attack_state"),
            payload.get("manual_aggressive_profile"),
            1 if payload.get("valid_for_review", True) else 0,
            jdump(payload.get("scores", {})),
            jdump(payload.get("factors", {})),
            jdump(payload.get("evidence_ids", [])),
            jdump(payload.get("hypothesis_ids", [])),
            jdump(payload.get("conflict_ids", [])),
            jdump(payload.get("data_gaps", [])),
            jdump(payload.get("reasons", [])),
            payload.get("falsifier"),
            payload.get("review_clock"),
            payload.get("candidate_source"),
            payload.get("confluence_count"),
            payload.get("report_path"),
            jdump(payload),
            now_iso(),
        ),
    )
    claim_ids = record_claims(conn, decision_id, payload)
    event(conn, "record_decision", "decision", decision_id, payload)
    conn.commit()
    completeness = decision_completeness(payload)
    return {
        "ok": True,
        "decision_id": decision_id,
        "claims_recorded": len(claim_ids),
        "completeness": completeness["status"],
        "completeness_missing": completeness["missing"],
        "db": str(db_path(args)),
    }


def infer_outcome(decision: sqlite3.Row, payload: dict[str, Any]) -> str:
    if payload.get("outcome"):
        return payload["outcome"]
    ret = payload.get("return_pct")
    stop = payload.get("stop_hit") or payload.get("falsifier_hit")
    target = payload.get("target_hit")
    exit_type = payload.get("exit_type")
    direction = decision["direction"]
    if exit_type in {"watch_correct", "avoid_correct"}:
        return "success"
    if exit_type in {"watch_missed", "avoid_missed"}:
        return "failure"
    if exit_type in {"stop_loss", "thesis_invalidated"} or stop:
        return "failure"
    if target:
        return "success"
    if direction in {"long", "buy", "build", "add"}:
        if isinstance(ret, (int, float)) and ret < 0:
            return "failure"
        if isinstance(ret, (int, float)) and ret > 0:
            return "success"
    if direction in {"short", "sell", "reduce"}:
        if isinstance(ret, (int, float)) and ret < 0:
            return "success"
        if isinstance(ret, (int, float)) and ret > 0:
            return "failure"
    if direction in {"watch", "avoid"}:
        return "neutral"
    return "mixed"


def record_result_claims(conn: sqlite3.Connection, decision: sqlite3.Row, payload: dict[str, Any], result_id: str) -> int:
    count = 0
    symbol = decision["symbol"]
    evidence = payload.get("review_evidence_ids", [])
    for relation, factors, polarity in [
        ("factor_validated_after_review", payload.get("effective_factors", []), "positive"),
        ("factor_invalidated_after_review", payload.get("ineffective_factors", []), "negative"),
    ]:
        for factor in factors[:40]:
            claim = {
                "claim_id": make_id("CL", symbol),
                "decision_id": decision["decision_id"],
                "subject": symbol,
                "relation": relation,
                "object": str(factor),
                "polarity": polarity,
                "regime": decision["attack_state"],
                "evidence_ids": evidence,
                "confidence": 0.8 if payload.get("outcome") == "success" else 0.6,
                "valid_from": payload.get("validation_time"),
                "result_id": result_id,
            }
            conn.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    claim["claim_id"],
                    claim["decision_id"],
                    claim["subject"],
                    claim["relation"],
                    claim["object"],
                    claim["polarity"],
                    claim["regime"],
                    jdump(claim["evidence_ids"]),
                    claim["confidence"],
                    claim["valid_from"],
                    None,
                    jdump(claim),
                ),
            )
            count += 1
    return count


def already_processed_result(existing: sqlite3.Row, decision_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "recorded": False,
        "already_processed": True,
        "skipped_lock_timeout": False,
        "result_id": existing["result_id"],
        "decision_id": decision_id,
        "outcome": existing["outcome"],
        "claims_recorded": 0,
        "unknown_failure_tags": [],
    }

def lock_timeout_result(decision_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "recorded": False,
        "already_processed": False,
        "skipped_lock_timeout": True,
        "result_id": None,
        "decision_id": decision_id,
        "outcome": None,
        "claims_recorded": 0,
        "unknown_failure_tags": [],
    }

def finish_result_scope(conn: sqlite3.Connection, caller_owned: bool) -> None:
    if caller_owned:
        conn.execute(f"RELEASE SAVEPOINT {RESULT_SAVEPOINT}")
    else:
        conn.commit()

def rollback_result_scope(conn: sqlite3.Connection, caller_owned: bool) -> None:
    if caller_owned:
        conn.execute(f"ROLLBACK TO SAVEPOINT {RESULT_SAVEPOINT}")
        conn.execute(f"RELEASE SAVEPOINT {RESULT_SAVEPOINT}")
    elif conn.in_transaction:
        conn.rollback()

def sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def cmd_record_result(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    payload = read_payload(args.payload)
    caller_owned = conn.in_transaction
    wait_seconds = max(
        0.0, float(getattr(args, "lock_wait_seconds", RESULT_LOCK_WAIT_SECONDS))
    )
    deadline = time.monotonic() + wait_seconds
    original_busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    try:
        while True:
            scope_started = False
            remaining = max(0.0, deadline - time.monotonic())
            attempt_ms = max(1, min(RESULT_LOCK_ATTEMPT_MS, int(remaining * 1000)))
            conn.execute(f"PRAGMA busy_timeout={attempt_ms}")
            try:
                if caller_owned:
                    conn.execute(f"SAVEPOINT {RESULT_SAVEPOINT}")
                else:
                    conn.execute("BEGIN IMMEDIATE")
                scope_started = True
                decision = conn.execute(
                    "SELECT * FROM decisions WHERE decision_id=?", (args.decision_id,)
                ).fetchone()
                if not decision:
                    raise SystemExit(f"decision not found: {args.decision_id}")
                existing = conn.execute(
                    "SELECT result_id, outcome FROM results WHERE decision_id=?",
                    (args.decision_id,),
                ).fetchone()
                if existing:
                    finish_result_scope(conn, caller_owned)
                    return already_processed_result(existing, args.decision_id)

                payload.setdefault("validation_time", now_iso())
                payload["outcome"] = infer_outcome(decision, payload)
                tags = payload.get("failure_tags", []) or []
                unknown = sorted(set(tags) - FAILURE_TAGS)
                result_id = payload.get("result_id") or make_id("RV", decision["symbol"])
                conn.execute(
                    """
                    INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_id, args.decision_id, payload["validation_time"],
                        payload.get("price_at_decision"), payload.get("price_at_validation"),
                        payload.get("return_pct"), payload.get("max_favorable_excursion_pct"),
                        payload.get("max_adverse_excursion_pct"), bool_int(payload.get("stop_hit")),
                        bool_int(payload.get("target_hit")), bool_int(payload.get("falsifier_hit")),
                        bool_int(payload.get("expired_early")), payload.get("exit_type"),
                        payload["outcome"], jdump(tags),
                        jdump(payload.get("effective_factors", [])),
                        jdump(payload.get("ineffective_factors", [])),
                        jdump(payload.get("review_evidence_ids", [])), jdump(payload), now_iso(),
                    ),
                )
                claim_count = record_result_claims(conn, decision, payload, result_id)
                event(conn, "record_result", "result", result_id, {"decision_id": args.decision_id, **payload})
                finish_result_scope(conn, caller_owned)
                return {
                    "ok": True, "recorded": True, "already_processed": False,
                    "skipped_lock_timeout": False, "result_id": result_id,
                    "decision_id": args.decision_id, "outcome": payload["outcome"],
                    "claims_recorded": claim_count, "unknown_failure_tags": unknown,
                }
            except sqlite3.IntegrityError:
                if scope_started:
                    rollback_result_scope(conn, caller_owned)
                existing = conn.execute(
                    "SELECT result_id, outcome FROM results WHERE decision_id=?",
                    (args.decision_id,),
                ).fetchone()
                if existing:
                    return already_processed_result(existing, args.decision_id)
                raise
            except sqlite3.OperationalError as exc:
                if scope_started:
                    rollback_result_scope(conn, caller_owned)
                elif not caller_owned and conn.in_transaction:
                    conn.rollback()
                if not sqlite_lock_error(exc):
                    raise
                try:
                    existing = conn.execute(
                        "SELECT result_id, outcome FROM results WHERE decision_id=?",
                        (args.decision_id,),
                    ).fetchone()
                except sqlite3.OperationalError as recheck_error:
                    if not sqlite_lock_error(recheck_error):
                        raise
                    existing = None
                if existing:
                    return already_processed_result(existing, args.decision_id)
                remaining = deadline - time.monotonic()
                if caller_owned or remaining <= 0:
                    return lock_timeout_result(args.decision_id)
                time.sleep(min(RESULT_LOCK_RETRY_SECONDS, remaining))
            except BaseException:
                if scope_started:
                    rollback_result_scope(conn, caller_owned)
                raise
    finally:
        conn.execute(f"PRAGMA busy_timeout={int(original_busy_timeout)}")


def verified_rows(conn: sqlite3.Connection, window: int, symbol: str | None = None, direction: str | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    where = ["d.valid_for_review=1"]
    params: list[Any] = []
    if symbol:
        where.append("d.symbol=?")
        params.append(symbol.upper())
    if direction:
        where.append("d.direction=?")
        params.append(direction)
    sql = f"""
        SELECT d.*, r.* FROM results r
        JOIN decisions d ON d.decision_id = r.decision_id
        WHERE {' AND '.join(where)}
        ORDER BY r.validation_time DESC
        LIMIT ?
    """
    params.append(window)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        dcols = conn.execute("SELECT * FROM decisions WHERE decision_id=?", (row["decision_id"],)).fetchone()
        rcols = conn.execute("SELECT * FROM results WHERE result_id=?", (row["result_id"],)).fetchone()
        out.append((row_to_decision(dcols), row_to_result(rcols)))
    return out


def group_stats(items: list[tuple[dict[str, Any], dict[str, Any]]], key: str) -> dict[str, Any]:
    buckets: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for d, r in items:
        buckets[str(d.get(key) or r.get(key) or "unknown")].append((d, r))
    summary = {}
    for name, rows in buckets.items():
        returns = [r.get("return_pct") for _, r in rows if isinstance(r.get("return_pct"), (int, float))]
        success = sum(1 for _, r in rows if r.get("outcome") == "success")
        failure = sum(1 for _, r in rows if r.get("outcome") == "failure")
        summary[name] = {
            "count": len(rows),
            "success": success,
            "failure": failure,
            "win_rate": round(success / len(rows), 3) if rows else 0,
            "avg_return_pct": round(statistics.mean(returns), 3) if returns else None,
            "sum_return_pct": round(sum(returns), 3) if returns else None,
        }
    return summary


def row_summary(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    returns = [r.get("return_pct") for _, r in rows if isinstance(r.get("return_pct"), (int, float))]
    success = sum(1 for _, r in rows if r.get("outcome") == "success")
    failure = sum(1 for _, r in rows if r.get("outcome") == "failure")
    return {
        "count": len(rows),
        "success": success,
        "failure": failure,
        "win_rate": round(success / len(rows), 3) if rows else 0,
        "avg_return_pct": round(statistics.mean(returns), 3) if returns else None,
        "sum_return_pct": round(sum(returns), 3) if returns else None,
    }


def action_bucket(decision: dict[str, Any]) -> str:
    direction = decision.get("direction")
    level = decision.get("action_level")
    # Direction dominates action level: an L2 short is still a sell/short call,
    # not a buy/build call.
    if direction in {"short", "sell", "reduce"}:
        return "sell_reduce_short"
    if direction in {"long", "buy", "build", "add"}:
        return "buy_build_add"
    if direction in {"watch", "avoid"}:
        return "watch_avoid"
    if level in {"L1", "L2", "L3"}:
        return "buy_build_add"
    if level == "L4":
        return "sell_reduce_short"
    if level in {"L0", "L5"}:
        return "watch_avoid"
    return "other"


def action_success_rates(items: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    buckets: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for d, r in items:
        buckets[action_bucket(d)].append((d, r))
    return {name: row_summary(rows) for name, rows in buckets.items()}


def biggest_drag_symbols(by_symbol: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    rows = []
    for symbol, stats in by_symbol.items():
        rows.append({
            "symbol": symbol,
            "count": stats.get("count", 0),
            "failure": stats.get("failure", 0),
            "sum_return_pct": stats.get("sum_return_pct"),
            "avg_return_pct": stats.get("avg_return_pct"),
        })
    rows.sort(key=lambda x: ((x.get("sum_return_pct") if x.get("sum_return_pct") is not None else 0), -x.get("failure", 0)))
    return rows[:limit]


def review_stats(items: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    returns = [r.get("return_pct") for _, r in items if isinstance(r.get("return_pct"), (int, float))]
    outcomes = Counter(r.get("outcome", "unknown") for _, r in items)
    failures = Counter(tag for _, r in items for tag in r.get("failure_tags", []))
    exits = Counter(r.get("exit_type") for _, r in items if r.get("exit_type"))
    gaps = Counter(gap for d, _ in items for gap in d.get("data_gaps", []))
    effective = Counter(f for _, r in items for f in r.get("effective_factors", []))
    ineffective = Counter(f for _, r in items for f in r.get("ineffective_factors", []))
    weights = [age_decay_weight(r.get("validation_time")) for _, r in items]
    weight_sum = sum(weights)
    weighted_success = sum(w for w, (_, r) in zip(weights, items) if r.get("outcome") == "success")
    weighted_returns = [float(r.get("return_pct")) * w for w, (_, r) in zip(weights, items) if isinstance(r.get("return_pct"), (int, float))]
    return_weight_sum = sum(w for w, (_, r) in zip(weights, items) if isinstance(r.get("return_pct"), (int, float)))
    sample_count = len(items)
    by_symbol = group_stats(items, "symbol")
    by_direction = group_stats(items, "direction")
    by_action_level = group_stats(items, "action_level")
    return {
        "sample_count": sample_count,
        "outcomes": dict(outcomes),
        "win_rate": round(outcomes.get("success", 0) / sample_count, 3) if sample_count else 0,
        "failure_rate": round(outcomes.get("failure", 0) / sample_count, 3) if sample_count else 0,
        "decayed_win_rate": round(weighted_success / weight_sum, 3) if weight_sum else 0,
        "decayed_avg_return_pct": round(sum(weighted_returns) / return_weight_sum, 3) if return_weight_sum else None,
        "recency_decay_half_life_days": 30,
        "total_return_pct": round(sum(returns), 3) if returns else None,
        "avg_return_pct": round(statistics.mean(returns), 3) if returns else None,
        "by_direction": by_direction,
        "by_symbol": by_symbol,
        "by_action_level": by_action_level,
        "action_success_rates": action_success_rates(items),
        "long_short_performance": {k: v for k, v in by_direction.items() if k in {"long", "short", "buy", "sell", "build", "add", "reduce"}},
        "biggest_drag_symbols": biggest_drag_symbols(by_symbol),
        "failure_tags": dict(failures.most_common(20)),
        "exit_types": dict(exits.most_common(20)),
        "data_gaps": dict(gaps.most_common(20)),
        "stable_factors": dict(effective.most_common(20)),
        "degrading_factors": dict(ineffective.most_common(20)),
        "effective_factors": dict(effective.most_common(20)),
        "ineffective_factors": dict(ineffective.most_common(20)),
    }


def add_reco(recommendations: list[dict[str, Any]], adjustments: list[dict[str, Any]], kind: str, target: str, reason: str, multiplier: float) -> None:
    recommendations.append({"kind": kind, "target": target, "reason": reason, "action": f"apply multiplier {multiplier:.2f}"})
    adjustments.append({"kind": kind, "target": target, "reason": reason, "multiplier": round(multiplier, 3)})


def derive_recommendations(stats: dict[str, Any], min_count: int, min_share: float, max_delta: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recos: list[dict[str, Any]] = []
    adjustments: list[dict[str, Any]] = []
    n = max(stats.get("sample_count", 0), 1)
    delta = min(max_delta, 0.3)
    tag_rules = {
        "low_coverage_loss": ("data_coverage_gate", "global", "低覆盖失败偏多，提高数据覆盖门槛"),
        "quick_loss": ("entry_timing_filter", "global", "快速亏损偏多，加强追价过滤和入场缓冲"),
        "chase_reversal": ("entry_timing_filter", "global", "追高回撤偏多，等待回踩/二次确认"),
        "fake_breakout": ("breakout_confirmation", "global", "假突破偏多，突破信号降权"),
        "timeout_failure": ("holding_clock", "global", "超时失败偏多，弱方向更早退出"),
        "take_profit_too_early": ("take_profit_rule", "global", "止盈过早偏多，优化分批和追踪止盈"),
        "watch_missed_opportunity": ("conservatism_filter", "global", "观望错过机会偏多，复查保守过滤条件"),
    }
    for tag, count in stats.get("failure_tags", {}).items():
        if count >= min_count and count / n >= min_share and tag in tag_rules:
            kind, target, reason = tag_rules[tag]
            add_reco(recos, adjustments, kind, target, f"{reason}（{tag}={count}/{n}）", 1 - delta)
    for symbol, s in stats.get("by_symbol", {}).items():
        if s["count"] >= min_count and s["failure"] >= min_count and (s.get("avg_return_pct") or 0) < 0:
            add_reco(recos, adjustments, "symbol_penalty", symbol, f"{symbol} 持续拖累：failure={s['failure']} avg_return={s.get('avg_return_pct')}", 1 - delta)
    for direction, s in stats.get("by_direction", {}).items():
        if s["count"] >= min_count and s["win_rate"] < 0.4:
            add_reco(recos, adjustments, "direction_penalty", direction, f"{direction} 方向近期胜率低：{s['win_rate']}", 1 - delta)
    for factor, count in stats.get("ineffective_factors", {}).items():
        if count >= min_count and count / n >= min_share:
            add_reco(recos, adjustments, "factor_penalty", factor, f"因子 {factor} 失效频率高：{count}/{n}", 1 - min(delta, 0.15))
    return recos, adjustments


def cooldown_active(conn: sqlite3.Connection, cooldown_minutes: int) -> tuple[bool, str | None]:
    last = get_state(conn, "last_review_time")
    dt = parse_time(last)
    if not dt:
        return False, None
    elapsed = (datetime.now(dt.tzinfo or timezone.utc) - dt).total_seconds() / 60
    if elapsed < cooldown_minutes:
        return True, f"cooldown active: {elapsed:.1f}/{cooldown_minutes} minutes since last review"
    return False, None


def cmd_review(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    items = verified_rows(conn, args.window)
    stats = review_stats(items)
    if len(items) < args.min_samples:
        return {"ok": True, "adjustment_applied": False, "reason": "insufficient_samples", "sample_count": len(items), "min_samples": args.min_samples, "stats": stats}
    active, reason = cooldown_active(conn, args.cooldown_minutes)
    if active and not args.force:
        return {"ok": True, "adjustment_applied": False, "reason": reason, "stats": stats}
    recos, adjustments = derive_recommendations(stats, args.pattern_min_count, args.pattern_min_share, args.max_adjustment_delta)
    review_id = make_id("RR")
    conn.execute(
        "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (review_id, now_iso(), args.window, len(items), args.cooldown_minutes, jdump(stats), jdump(recos), jdump(adjustments), now_iso()),
    )
    set_state(conn, "last_review_time", now_iso())
    set_state(conn, "last_adjustments", adjustments)
    event(conn, "rolling_review", "review", review_id, {"stats": stats, "recommendations": recos, "adjustments": adjustments})
    conn.commit()
    # Flag at generation time which of these adjustments will never be
    # auto-applied by apply_adjustments (holding_clock/take_profit_rule/
    # conservatism_filter and any future unrecognized kind), so the review
    # report doesn't silently imply every recommendation becomes a live
    # adjustment.
    skipped_adjustments = [
        {**adj, "skip_reason": classify_adjustment(adj)}
        for adj in adjustments
        if classify_adjustment(adj) is not None
    ]
    return {
        "ok": True,
        "review_id": review_id,
        "adjustment_applied": bool(adjustments),
        "stats": stats,
        "recommendations": recos,
        "adjustments": adjustments,
        "skipped_adjustments": skipped_adjustments,
    }


def cmd_think(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    """Run a YantrikDB-style cognition loop for trading memory.

    This is an operator-friendly wrapper around pending review detection,
    rolling review, pattern recommendations, and health stats. It never mutates
    raw decisions except through cmd_review's append-only review event.
    """
    pending_due = cmd_pending(conn, argparse.Namespace(limit=50, due_only=True))["pending"]
    review = cmd_review(conn, args)
    stats = cmd_stats(conn, argparse.Namespace())
    triggers: list[dict[str, Any]] = []
    if pending_due:
        triggers.append({
            "trigger_type": "pending_decision_review",
            "urgency": 0.8,
            "reason": f"{len(pending_due)} 条旧 AI 决策已到 review clock，需要先验证再继续提高进攻权重",
            "suggested_action": "record-result for due decisions before relying on memory feedback",
            "decision_ids": [p["decision_id"] for p in pending_due[:10]],
        })
    for reco in review.get("recommendations", [])[:10]:
        triggers.append({
            "trigger_type": "pattern_recommendation",
            "urgency": 0.6,
            "reason": reco.get("reason"),
            "suggested_action": reco.get("action"),
            "kind": reco.get("kind"),
            "target": reco.get("target"),
        })
    return {
        "ok": True,
        "cognition_cycle": "pending_reviews -> rolling_review -> pattern_triggers -> stats",
        "pending_due_reviews": pending_due,
        "review": review,
        "triggers": triggers,
        "stats": stats,
    }


def decision_has_result(conn: sqlite3.Connection, decision_id: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM results WHERE decision_id=? LIMIT 1", (decision_id,)).fetchone())


def cmd_pending(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM decisions ORDER BY analysis_time DESC LIMIT ?", (args.limit,)).fetchall()
    now = datetime.now(timezone.utc).astimezone()
    pending = []
    for row in rows:
        if decision_has_result(conn, row["decision_id"]):
            continue
        review_at = parse_time(row["review_clock"])
        due = bool(review_at and review_at <= now)
        if args.due_only and not due:
            continue
        pending.append({
            "decision_id": row["decision_id"],
            "symbol": row["symbol"],
            "market": row["market"],
            "direction": row["direction"],
            "action_level": row["action_level"],
            "analysis_time": row["analysis_time"],
            "review_clock": row["review_clock"],
            "due": due,
            "falsifier": row["falsifier"],
        })
    return {"ok": True, "count": len(pending), "pending": pending}


# kind -> which apply_adjustments branch consumes it. Anything not in this set
# has no preflight-multiplier handler and must surface in skipped_adjustments
# instead of being silently dropped (audit finding
# memory-review-inert-adjustment-kinds).
MULTIPLIER_ADJUSTMENT_KINDS = {
    "symbol_penalty",
    "direction_penalty",
    "data_coverage_gate",
    "entry_timing_filter",
    "breakout_confirmation",
    "factor_penalty",
}

# tag_rules in derive_recommendations also emits these three kinds. They are
# intentionally never auto-applied here: apply_adjustments only scales the
# preflight position-sizing multipliers (base dict), and none of the three
# describe a sizing adjustment — they describe exit-time behavior (holding
# duration, take-profit management) or a threshold *loosening* that no
# consumer reads yet. Auto-loosening a gate or reaching into the exit engine
# from the decision-time preflight check would be an unverified behavior
# change, not a safe multiplier scale. Surface them explicitly instead so the
# review report shows "recommended but not auto-applied" rather than nothing.
UNAPPLIABLE_ADJUSTMENT_REASONS = {
    "holding_clock": "作用于持仓时长/退出时机，非 preflight 仓位乘数；exit 引擎尚无消费端，需人工在 SKILL.md 复盘流程中手动应用",
    "take_profit_rule": "作用于止盈/追踪止盈行为，非 preflight 仓位乘数；exit 引擎尚无消费端，需人工在 SKILL.md 复盘流程中手动应用",
    "conservatism_filter": "建议放松（而非收紧）观望/回避阈值，自动放松门槛存在风险，需人工复核后再手动应用",
}


def classify_adjustment(adj: dict[str, Any]) -> str | None:
    """Return a skip reason for adjustment kinds apply_adjustments cannot consume, or None if it's applicable."""
    kind = adj.get("kind")
    if kind in MULTIPLIER_ADJUSTMENT_KINDS:
        return None
    if kind in UNAPPLIABLE_ADJUSTMENT_REASONS:
        return UNAPPLIABLE_ADJUSTMENT_REASONS[kind]
    return f"unrecognized adjustment kind {kind!r}; no handler in apply_adjustments"


def apply_adjustments(base: dict[str, float], adjustments: list[dict[str, Any]], symbol: str, direction: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applied = []
    skipped = []
    for adj in adjustments or []:
        kind = adj.get("kind")
        target = str(adj.get("target", ""))
        mult = float(adj.get("multiplier", 1.0))
        if kind == "symbol_penalty":
            if target.upper() == symbol.upper():
                base["symbol_multiplier"] *= mult
                applied.append(adj)
            # else: penalty targets a different symbol — not applicable to
            # this preflight call, not a dropped/unhandled kind.
        elif kind == "direction_penalty":
            if target == direction:
                base["direction_multiplier"] *= mult
                applied.append(adj)
        elif kind in {"data_coverage_gate", "entry_timing_filter", "breakout_confirmation"}:
            base["data_gap_multiplier"] *= mult if kind == "data_coverage_gate" else 1.0
            base["factor_multiplier"] *= mult if kind != "data_coverage_gate" else 1.0
            applied.append(adj)
        elif kind == "factor_penalty":
            base["factor_multiplier"] *= mult
            applied.append(adj)
        else:
            reason = classify_adjustment(adj)
            skipped.append({**adj, "skip_reason": reason})
    return applied, skipped


def recent_failure_streak(items: list[tuple[dict[str, Any], dict[str, Any]]], streak_n: int = 3) -> bool:
    return len(items) >= streak_n and all(r.get("outcome") == "failure" for _, r in items[:streak_n])


def cmd_preflight(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    symbol_items = verified_rows(conn, args.window, symbol=args.symbol)
    direction_items = verified_rows(conn, args.window, direction=args.direction)
    all_recent = verified_rows(conn, args.window)
    symbol_stats = review_stats(symbol_items)
    direction_stats = review_stats(direction_items)
    multipliers = {
        "symbol_multiplier": 1.0,
        "direction_multiplier": 1.0,
        "data_gap_multiplier": 1.0,
        "factor_multiplier": 1.0,
        "defensive_state_multiplier": 1.0,
    }
    warnings: list[str] = []
    if symbol_stats["sample_count"] >= 3:
        symbol_avg = symbol_stats.get("decayed_avg_return_pct")
        if symbol_avg is None:
            symbol_avg = symbol_stats.get("avg_return_pct")
        if symbol_avg is not None and symbol_avg < 0:
            multipliers["symbol_multiplier"] = 0.85
            warnings.append(f"{args.symbol} 最近衰减加权验证收益为负，候选权重降级")
    if direction_stats["sample_count"] >= 5 and direction_stats.get("decayed_win_rate", direction_stats["win_rate"]) < 0.4:
        multipliers["direction_multiplier"] = 0.8
        warnings.append(f"{args.direction} 方向近期衰减加权胜率低，方向权重降级")
    if recent_failure_streak(all_recent):
        multipliers["defensive_state_multiplier"] = 0.7
        warnings.append("最近连续 3 次验证失败，进入 defensive state；默认禁止 Level 3")
    applied, skipped_adjustments = apply_adjustments(multipliers, get_state(conn, "last_adjustments", []), args.symbol, args.direction)
    memory_multiplier = 1.0
    for v in multipliers.values():
        memory_multiplier *= v
    pending = cmd_pending(conn, argparse.Namespace(limit=20, due_only=True))["pending"]
    failure_pressure = Counter(tag for _, r in (symbol_items + direction_items) for tag in r.get("failure_tags", []))
    return {
        "ok": True,
        "memory_status": "pass" if (symbol_items or direction_items or applied) else "no_prior",
        "symbol": args.symbol.upper(),
        "market": args.market.upper(),
        "direction": args.direction,
        "symbol_memory": symbol_stats,
        "direction_memory": direction_stats,
        "failure_tag_pressure": dict(failure_pressure.most_common(10)),
        "multipliers": {k: round(v, 3) for k, v in multipliers.items()},
        "memory_multiplier": round(memory_multiplier, 3),
        "applied_adjustments": applied,
        "skipped_adjustments": skipped_adjustments,
        "warnings": warnings,
        "pending_due_reviews": pending[:10],
        "decision_impact": "feed memory_multiplier into Position Cap and cite this preflight in Decision Memory section",
    }


def cmd_stats(conn: sqlite3.Connection, _args: argparse.Namespace) -> dict[str, Any]:
    counts = {}
    for table in ["decisions", "results", "reviews", "claims", "memory_events"]:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    last_review = conn.execute("SELECT review_id, review_time, sample_count FROM reviews ORDER BY review_time DESC LIMIT 1").fetchone()
    return {"ok": True, "counts": counts, "last_review": dict(last_review) if last_review else None}


def cmd_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "memory.sqlite"
        conn = connect(path)
        decision = {
            "symbol": "TEST",
            "market": "US",
            "direction": "long",
            "action_level": "L2",
            "action_label": "建仓",
            "position_cap_pct": 10,
            "scores": {"total": 3.2, "data_coverage": 0.8},
            "factors": {"model_score": 0.6, "entry_timing": 0.4},
            "evidence_ids": ["E1", "E2", "E3"],
            "reasons": ["self-test"],
            "review_clock": now_iso(),
        }
        payload_path = Path(td) / "decision.json"
        payload_path.write_text(jdump(decision), encoding="utf-8")
        out1 = cmd_record_decision(conn, argparse.Namespace(payload=str(payload_path), db=str(path)))
        result = {
            "return_pct": -5.0,
            "max_favorable_excursion_pct": 1.0,
            "max_adverse_excursion_pct": -6.0,
            "stop_hit": True,
            "exit_type": "stop_loss",
            "outcome": "failure",
            "failure_tags": ["quick_loss", "entry_too_early"],
            "ineffective_factors": ["entry_timing"],
        }
        result_path = Path(td) / "result.json"
        result_path.write_text(jdump(result), encoding="utf-8")
        out2 = cmd_record_result(conn, argparse.Namespace(payload=str(result_path), decision_id=out1["decision_id"], db=str(path)))
        review = cmd_review(conn, argparse.Namespace(window=36, min_samples=1, cooldown_minutes=0, force=True, pattern_min_count=1, pattern_min_share=0.1, max_adjustment_delta=0.15))
        preflight = cmd_preflight(conn, argparse.Namespace(symbol="TEST", market="US", direction="long", window=36))
        stats = cmd_stats(conn, argparse.Namespace())
        conn.close()
        assert stats["counts"]["decisions"] == 1
        assert stats["counts"]["results"] == 1
        assert review["adjustment_applied"] is True
        assert "action_success_rates" in review["stats"]
        assert review["stats"]["action_success_rates"].get("buy_build_add", {}).get("failure") == 1
        assert preflight["memory_multiplier"] < 1.0
        return {"ok": True, "record_decision": out1, "record_result": out2, "review_id": review["review_id"], "memory_multiplier": preflight["memory_multiplier"]}


def print_result(obj: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(obj, ensure_ascii=False))
