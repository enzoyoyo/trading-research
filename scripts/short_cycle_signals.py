#!/usr/bin/env python3
"""Pure signal evaluators for the US short-cycle structure overlay."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any


def finite_number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def directional_check(direction: str, actual: float | None, reference: float | None) -> float | None:
    if actual is None or reference is None:
        return None
    if direction == "short":
        return 1.0 if actual <= reference else 0.0
    return 1.0 if actual >= reference else 0.0


def _gamma_geometry(row: dict[str, Any]) -> dict[str, Any]:
    values = {key: finite_number(row.get(key)) for key in ("spot", "put_wall", "gamma_flip", "call_wall")}
    spot, put_wall = values["spot"], values["put_wall"]
    gamma_flip, call_wall = values["gamma_flip"], values["call_wall"]
    valid = put_wall is not None and gamma_flip is not None and call_wall is not None
    reasons: list[str] = []
    if valid:
        assert put_wall is not None and gamma_flip is not None and call_wall is not None
        if not put_wall <= gamma_flip <= call_wall:
            reasons.append("wall_order_invalid")
            valid = False
    zone = "unavailable"
    if valid and spot is not None:
        assert put_wall is not None and gamma_flip is not None and call_wall is not None
        if spot < put_wall:
            zone = "below_put_wall"
        elif spot < gamma_flip:
            zone = "put_to_flip"
        elif spot <= call_wall:
            zone = "flip_to_call"
        else:
            zone = "above_call_wall"
    return {**values, "valid_walls": valid, "zone": zone, "reasons": reasons}


def _gamma_freshness(payload: dict[str, Any], row: dict[str, Any], policy: dict[str, Any], geometry: dict[str, Any]) -> tuple[str, float | None]:
    observed = parse_time(payload.get("observed_at"))
    gamma_time = parse_time(row.get("observed_at"))
    delay = finite_number(row.get("source_delay_minutes")) or 0.0
    age = None if observed is None or gamma_time is None else max(0.0, (observed - gamma_time).total_seconds()) + delay * 60
    live_age = finite_number(policy.get("max_live_gamma_age_seconds")) or 180.0
    delayed_age = finite_number(policy.get("max_delayed_gamma_age_seconds")) or 1200.0
    session = str(row.get("option_session") or "unknown")
    expiry = str(row.get("expiry_scope") or "unknown")
    if not geometry["valid_walls"] or geometry["spot"] is None:
        return "invalid", age
    if session == "RTH" and expiry in {"0DTE", "near"} and age is not None and age <= live_age:
        return "confirmed_live", age
    if age is not None and age <= delayed_age:
        return "delayed_reference", age
    return "stale_for_checkpoint", age


def gamma_range_read(payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    row = payload.get("spx_gamma") or {}
    if not row:
        return {
            "status": "unavailable", "zone": "unavailable", "regime": "unknown",
            "risk_state": "neutral", "reasons": ["gamma_missing"],
        }
    geometry = _gamma_geometry(row)
    status, age = _gamma_freshness(payload, row, policy, geometry)
    net_gex = finite_number(row.get("net_gex"))
    vrp = finite_number(row.get("vrp"))
    regime = "negative" if net_gex is not None and net_gex < 0 else "positive" if net_gex is not None and net_gex > 0 else "unknown"
    risk_state = "neutral"
    if regime == "negative" and geometry["zone"] in {"below_put_wall", "put_to_flip"}:
        risk_state = "breakdown_amplification" if vrp is not None and vrp < 0 else "negative_gamma_below_flip"
    elif regime == "positive" and geometry["zone"] == "flip_to_call" and vrp is not None and vrp >= 0:
        risk_state = "pin_possible_not_directional"
    reasons = list(geometry["reasons"])
    if status in {"invalid", "stale_for_checkpoint"}:
        reasons.append("gamma_not_fresh_enough_for_checkpoint")
    return {
        "status": status, "zone": geometry["zone"], "regime": regime, "risk_state": risk_state,
        "age_seconds_effective": age, "option_session": str(row.get("option_session") or "unknown"),
        "expiry_scope": str(row.get("expiry_scope") or "unknown"), "spot": geometry["spot"],
        "put_wall": geometry["put_wall"], "gamma_flip": geometry["gamma_flip"],
        "call_wall": geometry["call_wall"], "net_gex": net_gex, "vrp": vrp,
        "comparable_to_prior": bool(row.get("comparable_to_prior")), "reasons": reasons,
    }


def _option_metrics(row: dict[str, Any]) -> dict[str, float | str | None]:
    bid, ask = finite_number(row.get("bid")), finite_number(row.get("ask"))
    side, order_type = str(row.get("side") or "buy"), str(row.get("order_type") or "")
    midpoint = spread_pct = None
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        midpoint = (bid + ask) / 2
        spread_pct = (ask - bid) / midpoint
    spread_bps = None if spread_pct is None else spread_pct * 10000
    executable_size = finite_number(row.get("ask_size" if side == "buy" else "bid_size"))
    crossing = 1.0 if order_type == "market" else 0.5
    slippage, fees = finite_number(row.get("estimated_slippage_bps")) or 0.0, finite_number(row.get("fees_bps")) or 0.0
    hurdle = (spread_bps or 0.0) * crossing + slippage + fees
    return {
        "bid": bid, "ask": ask, "side": side, "order_type": order_type, "midpoint": midpoint,
        "spread_pct_mid": spread_pct, "spread_bps_mid": spread_bps,
        "quote_age_seconds": finite_number(row.get("quote_age_seconds")),
        "contracts": finite_number(row.get("contracts")), "executable_top_size": executable_size,
        "expected_edge_bps": finite_number(row.get("expected_edge_bps")),
        "estimated_hurdle_bps": round(hurdle, 4), "volume": finite_number(row.get("volume")),
        "open_interest": finite_number(row.get("open_interest")),
    }


def _option_failures(metrics: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if metrics["order_type"] not in {"market", "limit_mid", "limit"}:
        reasons.append("unknown_order_type")
    if metrics["side"] not in {"buy", "sell"}:
        reasons.append("unknown_order_side")
    if metrics["midpoint"] is None:
        reasons.append("invalid_or_missing_bbo")
    max_age = finite_number(policy.get("max_option_quote_age_seconds")) or 5.0
    if metrics["quote_age_seconds"] is None or metrics["quote_age_seconds"] > max_age:
        reasons.append("stale_option_quote")
    contracts, size = metrics["contracts"], metrics["executable_top_size"]
    if contracts is None or contracts <= 0 or size is None or size < contracts:
        reasons.append("insufficient_top_of_book_size")
    max_spread = finite_number(policy.get("max_option_spread_pct_mid")) or 0.10
    if metrics["spread_pct_mid"] is None or metrics["spread_pct_mid"] > max_spread:
        reasons.append("spread_too_wide")
    edge = metrics["expected_edge_bps"]
    if edge is None:
        reasons.append("cost_edge_unknown")
    elif edge <= metrics["estimated_hurdle_bps"]:
        reasons.append("cost_edge_negative_after_friction")
    return reasons


def option_execution_read(payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    row = payload.get("option_execution") or {}
    if not bool(row.get("requested")):
        return {"status": "not_requested", "reasons": [], "metrics": {}}
    metrics = _option_metrics(row)
    reasons = _option_failures(metrics, policy)
    if reasons:
        status = "no_trade"
    elif metrics["order_type"] == "market" or metrics["volume"] in (None, 0):
        status = "limit_only"
        reasons = ["prefer_non_marketable_limit_order"]
    else:
        status = "eligible"
    public_metrics = {key: value for key, value in metrics.items() if key not in {"bid", "ask", "side", "order_type", "expected_edge_bps"}}
    return {"status": status, "reasons": reasons, "metrics": public_metrics}


def continuity_read(payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    row, upstream = payload.get("open_0940") or {}, payload.get("upstream") or {}
    direction = str(upstream.get("direction") or "long")
    last, previous_close = finite_number(row.get("last")), finite_number(row.get("previous_close"))
    range_high, range_low = finite_number(row.get("opening_range_high")), finite_number(row.get("opening_range_low"))
    range_location = None
    if last is not None and range_high is not None and range_low is not None and range_high > range_low:
        normalized = (last - range_low) / (range_high - range_low)
        range_location = float((1.0 - normalized if direction == "short" else normalized) >= 0.5)
    breadth, sector = row.get("breadth_confirmed"), row.get("sector_confirmed")
    breadth_sector = None if breadth is None or sector is None else float(bool(breadth) and bool(sector))
    relative_volume = finite_number(row.get("relative_volume"))
    min_rvol = finite_number(policy.get("min_relative_volume")) or 0.8
    components = {
        "gap_hold": directional_check(direction, last, previous_close),
        "vwap_acceptance": directional_check(direction, last, finite_number(row.get("vwap"))),
        "opening_range_location": range_location,
        "relative_volume": None if relative_volume is None else float(relative_volume >= min_rvol),
        "breadth_sector": breadth_sector,
    }
    observed = [value for value in components.values() if value is not None]
    missing = [name for name, value in components.items() if value is None]
    score = round(sum(observed) / len(observed), 4) if observed else None
    pass_score = finite_number(policy.get("continuity_pass_score")) or 0.70
    fail_score = finite_number(policy.get("continuity_fail_score")) or 0.40
    if len(missing) >= 2 or score is None:
        status = "data_gap"
    elif score >= pass_score:
        status = "pass"
    elif score < fail_score:
        status = "fail"
    else:
        status = "mixed"
    return {"status": status, "score": score, "components": components, "missing": missing}
