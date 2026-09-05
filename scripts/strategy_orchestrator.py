#!/usr/bin/env python3
"""Plan strategy-specific evidence collection, never manufacture trade permission.

No network, orders, probability estimates, or filtering of Compiler inputs.
The campaign is a research mandate, not a second trading engine.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

HORIZONS = {"intraday", "overnight_cto", "swing_days", "position_months", "theme_years"}
REGIMES = {"normal", "stress_building", "deleveraging_watch", "active_deleveraging", "forced_liquidation"}
GLOBAL_GATES = ["risk_regime", "portfolio_risk_budget", "data_quality"]
LANES = {
    "trend_following": {
        "horizons": ["swing_days", "position_months"],
        "questions": ["trend_and_relative_strength", "fundamental_trend", "trend_invalidation"],
        "references": ["prosperity-davis-double-framework.md", "cycle-position-three-clocks.md"],
    },
    "momentum_breakout": {
        "horizons": ["swing_days"],
        "questions": ["breakout_holds", "volume_and_breadth", "crowding_and_false_breakout"],
        "references": ["social-technical-entry-gate.md", "leverage-crowding-dispersion-playbook.md"],
    },
    "oversold_reclaim": {
        "horizons": ["intraday", "swing_days"],
        "questions": ["observable_reclaim_not_just_cheap", "selling_pressure_abates", "nearby_falsifier_and_time_stop"],
        "references": ["short-cycle-market-structure-overlay.md", "participant-flow-motivation.md"],
    },
    "event_followthrough": {
        "horizons": ["swing_days"],
        "questions": ["primary_event_evidence", "price_reaction_vs_expectations", "catalyst_half_life"],
        "references": ["earnings-call-interpretation.md", "event-reaction-memory.md"],
    },
    "defensive_relative_strength": {
        "horizons": ["swing_days", "position_months"],
        "questions": ["relative_strength_in_weak_market", "cashflow_liquidity", "compare_with_existing_holdings_and_cash"],
        "references": ["etf-selection-rotation.md", "dividend-quality-framework.md"],
    },
    "overnight_cto": {
        "horizons": ["overnight_cto"],
        "questions": ["same_session_catalyst", "cost_liquidity_binary_event", "next_session_exit_clock"],
        "references": ["us-close-to-open-execution-overlay.md", "overnight-ensemble-ranker.md"],
    },
}
DEFENSIVE_ORDER = ["defensive_relative_strength", "event_followthrough", "oversold_reclaim", "trend_following", "momentum_breakout", "overnight_cto"]


def build_strategy_mandate(*, market: str = "US", horizon_id: str | None = None,
                           instrument: str = "equity", risk_regime: str | None = None) -> dict[str, Any]:
    """Return questions and applicability only; a supplied regime is not verified."""
    gaps: list[str] = []
    if market != "US":
        gaps.append("us_campaign_not_applicable_use_market_router")
    if horizon_id not in HORIZONS:
        gaps.append("horizon_required_before_action")
    if instrument not in {"equity", "etf", "option"}:
        gaps.append("instrument_identity_required")
    regime = risk_regime if risk_regime in REGIMES else "unknown"
    if regime == "unknown":
        gaps.append("fresh_risk_snapshot_required")
    order = DEFENSIVE_ORDER if regime != "normal" else list(LANES)
    lanes = []
    for name in order:
        lane = LANES[name]
        if market != "US" or (horizon_id in HORIZONS and horizon_id not in lane["horizons"]):
            continue
        lanes.append({"strategy_id": name, **copy.deepcopy(lane),
                      "strategy_qualification": "research_path_only", "entry_permission": None,
                      "requires_fresh_compilation": True})
    return {
        "schema_version": "strategy_mandate.v1", "market": market,
        "horizon_id": horizon_id, "instrument": instrument, "risk_context": regime,
        "risk_context_verified": False, "scan_continues_during_defence": True,
        "global_gates": list(GLOBAL_GATES), "lanes": lanes,
        "instrument_questions": (["option_identity", "underlying_thesis", "bid_ask_size_freshness", "cost_edge_and_iv_crush"]
                                 if instrument == "option" else []),
        "data_gaps": gaps, "status": "research_plan_only", "probability": None,
        "position_multiplier": None, "final_authority": "Decision Compiler",
        "discovery_must_not_be_executable_signals": True,
        "must_not_filter_submitted_risk_constraints": True,
        "cannot_activate_strategy": True, "no_order_execution": True,
    }


def classify_blocker(compilation: dict[str, Any]) -> str:
    """Explain an existing Compiler result; never change it."""
    if compilation.get("contract_status") != "strict_pass" or not compilation.get("ok"):
        return "contract_gap"
    if compilation.get("epistemic_veto"):
        return "data_gap"
    modules = set(compilation.get("hard_veto_modules") or [])
    if modules & {"account", "portfolio_risk_budget", "brokerage_portfolio_margin"}:
        return "portfolio_or_permission"
    if compilation.get("hard_veto"):
        return "market_risk"
    if compilation.get("entry_permission") in {"TEST", "BUILD", "ADD"}:
        size = compilation.get("final_position_multiplier")
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size):
            return "contract_gap"
        if size > 0:
            return "ready"
    if compilation.get("unresolved_conflicts"):
        return "unresolved_conflict"
    return "no_edge_or_non_open_intent"


def compile_research_candidates(candidates: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Replay complete requests, including every submitted signal, for audit only.

    Each candidate is its own strict request. This helper emits no executor
    envelope and never mixes signals across symbols, horizons or instruments.
    """
    from decision_compiler import compile_payload
    rows = []
    for item in candidates:
        if not isinstance(item, dict):
            rows.append({"blocker_class": "contract_gap", "errors": ["candidate_object_required"]})
            continue
        request = item.get("decision_request")
        strategy = item.get("strategy_id")
        horizon = item.get("horizon_id")
        errors = []
        if strategy not in LANES or horizon not in LANES.get(strategy, {}).get("horizons", []):
            errors.append("strategy_horizon_mismatch")
        context = request.get("decision_context", {}) if isinstance(request, dict) else {}
        if not isinstance(context, dict):
            context = {}
        for key in ("symbol", "instrument"):
            if not item.get(key) or context.get(key) != item.get(key):
                errors.append(f"candidate_{key}_binding_required")
        context_horizon = context.get('horizon_id', context.get('horizon'))
        if context_horizon != horizon or (context.get('horizon_id') is not None and context.get('horizon') is not None and context['horizon_id'] != context['horizon']):
            errors.append('candidate_horizon_id_binding_required')
        if not isinstance(request, dict) or request.get("schema_version") != "decision_request.v2":
            errors.append("strict_decision_request_required")
        if errors:
            rows.append({"symbol": item.get("symbol"), "strategy_id": strategy,
                         "blocker_class": "contract_gap", "errors": errors})
            continue
        # No signal selection, removal, reweighting or evidence fabrication here.
        result = compile_payload(copy.deepcopy(request), now=now)
        rows.append({"symbol": item["symbol"], "strategy_id": strategy, "horizon_id": horizon,
                     "instrument": item["instrument"], "blocker_class": classify_blocker(result),
                     "compilation": result})
    return {"schema_version": "strategy_campaign_review.v1", "candidates": rows,
            "blocker_counts": dict(Counter(row["blocker_class"] for row in rows)),
            "scan_continues_during_defence": True, "execution_authorization": False,
            "no_order_execution": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="US")
    parser.add_argument("--horizon", choices=sorted(HORIZONS))
    parser.add_argument("--instrument", choices=["equity", "etf", "option"], default="equity")
    parser.add_argument("--risk-regime", choices=sorted(REGIMES))
    parser.add_argument("--review-json", help="Replay complete strict candidate requests; does not authorize orders")
    args = parser.parse_args()
    if args.review_json:
        from pathlib import Path
        payload = json.loads(Path(args.review_json).read_text())
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            parser.error("object with candidates list required")
        result = compile_research_candidates(payload["candidates"])
    else:
        result = build_strategy_mandate(market=args.market, horizon_id=args.horizon,
                                        instrument=args.instrument, risk_regime=args.risk_regime)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
