#!/usr/bin/env python3
"""US short-cycle market-structure overlay for trading-research.

The overlay preserves the upstream direction decision, emits only existing
Decision Compiler constraints, and never submits orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from short_cycle_review import eod_review
from short_cycle_signals import continuity_read, finite_number, gamma_range_read, option_execution_read

SCHEMA_VERSION = "short_cycle_structure.v1"
ACTION_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
DEFAULT_POLICY = {
    "min_relative_volume": 0.8,
    "continuity_pass_score": 0.70,
    "continuity_fail_score": 0.40,
    "max_option_quote_age_seconds": 5.0,
    "max_option_spread_pct_mid": 0.10,
    "max_live_gamma_age_seconds": 180.0,
    "max_delayed_gamma_age_seconds": 1200.0,
    "min_reweight_samples": 12,
    "max_weight_delta": 0.15,
}
TERMINAL_VERDICTS = {"no_trade", "inapplicable", "exit_at_open", "review_only"}


def normalize_action_cap(value: Any) -> str:
    return str(value) if value in ACTION_ORDER else "L0"


def tighter_cap(candidate: str, upstream: str) -> str:
    return candidate if ACTION_ORDER[candidate] <= ACTION_ORDER[upstream] else upstream


def module_signal(cap: str, multiplier: float, reason: str) -> dict[str, Any]:
    return {
        "module": "execution_window",
        "sub_framework": "short_cycle_structure",
        "max_action_level": cap,
        "position_multiplier": max(0.0, min(1.0, multiplier)),
        "hard_veto": False,
        "reason": reason,
        "repair_signal": "new intraday/swing Decision Compiler run after 09:40 ET",
    }


def _base_verdict(market_scope: str, checkpoint: str, upstream: dict[str, Any], continuity: dict[str, Any]) -> tuple[str, str, float, str]:
    upstream_cap = normalize_action_cap(upstream.get("action_cap"))
    pre_open = str(upstream.get("pre_open_status") or "unavailable")
    if checkpoint == "eod_review":
        return "review_only", upstream_cap, 1.0, "eod_review_only_no_compiler_effect"
    if market_scope != "US_only":
        return "inapplicable", "L0", 0.0, "non_us_scope"
    if upstream_cap == "L0" or not bool(upstream.get("thesis_valid", True)):
        return "no_trade", "L0", 0.0, "upstream_not_executable"
    if pre_open == "weak":
        return "exit_at_open", "L0", 0.0, "pre_open_read_weak"
    if checkpoint != "open_0940":
        return "eligible", upstream_cap, 1.0, "checkpoint_eligible"
    if continuity["status"] == "pass":
        return "recompile_intraday", upstream_cap, 1.0, "0940_continuity_pass_recompile_required"
    if continuity["status"] == "fail":
        return "exit_or_reduce_by_0940", "L0", 0.0, "0940_continuity_failed"
    cap = "L1" if upstream_cap != "L0" else "L0"
    return "data_gap", cap, 0.5, "0940_continuity_mixed_or_incomplete"


def _gamma_constraint(gamma: dict[str, Any], upstream: dict[str, Any], checkpoint: str, verdict: str) -> tuple[dict[str, Any] | None, str]:
    upstream_cap = normalize_action_cap(upstream.get("action_cap"))
    direction = str(upstream.get("direction") or "long")
    risky = gamma["risk_state"] in {"breakdown_amplification", "negative_gamma_below_flip"}
    if risky and direction == "long":
        live = gamma["status"] == "confirmed_live"
        cap = tighter_cap("L0" if live else "L1", upstream_cap)
        signal = {
            "module": "gamma", "sub_framework": "short_cycle_structure",
            "max_action_level": cap, "position_multiplier": 0.0 if cap == "L0" else 0.5,
            "hard_veto": live and gamma["zone"] == "below_put_wall",
            "reason": f"{gamma['risk_state']}|{gamma['status']}|{gamma['zone']}",
            "repair_signal": "reclaim put wall/gamma flip with fresh same-expiry RTH data",
        }
        if verdict not in TERMINAL_VERDICTS:
            verdict = "exit_or_reduce_by_0940" if live else "data_gap" if checkpoint == "open_0940" else verdict
        return signal, verdict
    if gamma["status"] not in {"invalid", "stale_for_checkpoint"}:
        return None, verdict
    cap = tighter_cap("L1", upstream_cap)
    signal = {
        "module": "data_quality", "sub_framework": "spx_gamma_freshness",
        "max_action_level": cap, "position_multiplier": 0.0 if cap == "L0" else 0.5,
        "hard_veto": False, "reason": "spx_gamma_stale_or_invalid_cannot_relax",
        "repair_signal": "refresh same-expiry RTH SPX gamma range",
    }
    if checkpoint == "open_0940" and verdict not in TERMINAL_VERDICTS:
        verdict = "data_gap"
    return signal, verdict


def _option_constraint(quality: dict[str, Any], upstream_cap: str) -> dict[str, Any] | None:
    if quality["status"] not in {"no_trade", "limit_only"}:
        return None
    cap = tighter_cap("L0" if quality["status"] == "no_trade" else "L1", upstream_cap)
    return {
        "module": "data_quality", "sub_framework": "option_execution",
        "max_action_level": cap, "position_multiplier": 0.0 if cap == "L0" else 0.5,
        "hard_veto": False, "reason": "|".join(quality["reasons"]),
        "repair_signal": "fresh BBO, sufficient top size, positive cost edge, use non-marketable limit order",
    }


def _output(payload: dict[str, Any], upstream: dict[str, Any], verdict: str, continuity: dict[str, Any], gamma: dict[str, Any], quality: dict[str, Any], review: dict[str, Any], underlying: list[dict[str, Any]], option: list[dict[str, Any]]) -> dict[str, Any]:
    checkpoint = str(payload.get("checkpoint") or "")
    return {
        "ok": True, "schema_version": SCHEMA_VERSION,
        "market_scope": str(payload.get("market_scope") or ""), "checkpoint": checkpoint,
        "upstream_unchanged": True, "upstream_action_cap": normalize_action_cap(upstream.get("action_cap")),
        "upstream_position_multiplier": finite_number(upstream.get("position_multiplier")),
        "checkpoint_verdict": verdict, "continuity": continuity, "gamma_range": gamma,
        "option_execution_quality": quality,
        "instrument_permissions": {"underlying": "upstream_only", "option_contract": quality["status"]},
        "underlying_module_signals": underlying, "option_module_signals": option,
        "must_recompile_after_0940": checkpoint == "open_0940", "eod_review": review,
        "no_order_execution": True,
    }


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    policy = {**DEFAULT_POLICY, **(payload.get("policy") or {})}
    upstream = dict(payload.get("upstream") or {})
    upstream["pre_open_status"] = str((payload.get("pre_open") or {}).get("status") or "unavailable")
    checkpoint, market_scope = str(payload.get("checkpoint") or ""), str(payload.get("market_scope") or "")
    continuity = continuity_read(payload, policy) if checkpoint == "open_0940" else {
        "status": "not_evaluated", "score": None, "components": {}, "missing": []
    }
    gamma, quality = gamma_range_read(payload, policy), option_execution_read(payload, policy)
    review = eod_review(payload, policy) if checkpoint == "eod_review" else {
        "status": "not_evaluated", "weight_update": {"applied": False}
    }
    verdict, cap, multiplier, reason = _base_verdict(market_scope, checkpoint, upstream, continuity)
    underlying = [module_signal(cap, multiplier, reason)]
    gamma_signal, verdict = _gamma_constraint(gamma, upstream, checkpoint, verdict)
    if gamma_signal is not None:
        underlying = [*underlying, gamma_signal]
    option = list(underlying)
    option_signal = _option_constraint(quality, normalize_action_cap(upstream.get("action_cap")))
    if option_signal is not None:
        option.append(option_signal)
    return _output(payload, upstream, verdict, continuity, gamma, quality, review, underlying, option)


def load_payload(path: str | None) -> dict[str, Any]:
    text = open(path, encoding="utf-8").read() if path else sys.stdin.read()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    if parsed.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="JSON payload path; stdin if omitted")
    args = parser.parse_args()
    try:
        result = evaluate(load_payload(args.path))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
