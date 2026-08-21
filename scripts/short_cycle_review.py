#!/usr/bin/env python3
"""EOD attribution and bounded advisory reweighting for short-cycle overlay."""
from __future__ import annotations

from typing import Any

from short_cycle_signals import finite_number


def signed_return_bps(start: float | None, end: float | None, direction: str) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    raw = (end / start - 1.0) * 10000
    return round(-raw if direction == "short" else raw, 4)


def _normalized_weights(row: dict[str, Any]) -> dict[str, float]:
    source = row.get("current_weights") or {}
    if not isinstance(source, dict):
        return {}
    weights = {str(key): value for key, raw in source.items() if (value := finite_number(raw)) is not None and value >= 0}
    total = sum(weights.values())
    return {} if total <= 0 else {key: value / total for key, value in weights.items()}


def _component_briers(samples: list[Any], components: list[str]) -> tuple[dict[str, float], dict[str, int]]:
    briers, counts = {}, {}
    for component in components:
        errors: list[float] = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            prediction = finite_number((sample.get("predictions") or {}).get(component))
            outcome = finite_number(sample.get("outcome"))
            if prediction is None or outcome not in {0.0, 1.0} or not 0 <= prediction <= 1:
                continue
            errors.append((prediction - outcome) ** 2)
        counts[component] = len(errors)
        if errors:
            briers[component] = round(sum(errors) / len(errors), 6)
    return briers, counts


def _bounded_suggestion(current: dict[str, float], briers: dict[str, float], max_delta: float) -> dict[str, float]:
    quality = {key: max(0.05, 1.0 - briers[key]) for key in current}
    quality_total = sum(quality.values())
    target = {key: quality[key] / quality_total for key in current}
    max_raw_change = max(abs(target[key] - current[key]) for key in current)
    alpha = min(1.0, max_delta / max_raw_change) if max_raw_change > 0 else 1.0
    return {key: round(current[key] + alpha * (target[key] - current[key]), 6) for key in current}


def bounded_weight_review(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    samples = row.get("calibration_samples") or []
    samples = samples if isinstance(samples, list) else []
    min_samples = int(finite_number(policy.get("min_reweight_samples")) or 12)
    max_delta = finite_number(policy.get("max_weight_delta")) or 0.15
    current = _normalized_weights(row)
    if not current:
        return {"applied": False, "recommended": False, "sample_count": len(samples), "reason": "current_weights_missing"}
    briers, counts = _component_briers(samples, list(current))
    if len(samples) < min_samples or any(counts.get(key, 0) < min_samples for key in current):
        return {
            "applied": False, "recommended": False, "sample_count": len(samples),
            "minimum_samples": min_samples, "reason": "insufficient_samples",
            "component_sample_counts": counts, "brier_by_component": briers,
            "current_weights": current,
        }
    suggested = _bounded_suggestion(current, briers, max_delta)
    return {
        "applied": False, "recommended": True, "sample_count": len(samples),
        "minimum_samples": min_samples, "reason": "bounded_advisory_reweight",
        "component_sample_counts": counts, "brier_by_component": briers,
        "current_weights": current, "suggested_weights": suggested,
        "max_absolute_delta": round(max(abs(suggested[key] - current[key]) for key in current), 6),
    }


def _review_metrics(row: dict[str, Any], direction: str) -> dict[str, float | None]:
    entry, open_price = finite_number(row.get("entry_price")), finite_number(row.get("open_price"))
    price_0940, close_price = finite_number(row.get("price_0940")), finite_number(row.get("close_price"))
    actual_exit = finite_number(row.get("actual_exit_price"))
    estimated_cost = finite_number(row.get("estimated_cost_bps")) or 0.0
    realized_cost = finite_number(row.get("realized_cost_bps")) or 0.0
    close_open = signed_return_bps(entry, open_price, direction)
    actual = signed_return_bps(entry, actual_exit, direction)
    counterfactual = signed_return_bps(entry, price_0940, direction)
    return {
        "close_to_open_net_bps": None if close_open is None else round(close_open - realized_cost, 4),
        "open_to_0940_bps": signed_return_bps(open_price, price_0940, direction),
        "0940_to_close_bps": signed_return_bps(price_0940, close_price, direction),
        "actual_exit_net_bps": None if actual is None else round(actual - realized_cost, 4),
        "exit_0940_counterfactual_net_bps": None if counterfactual is None else round(counterfactual - realized_cost, 4),
        "execution_cost_error_bps": round(realized_cost - estimated_cost, 4),
    }


def eod_review(payload: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    row = payload.get("eod_review") or {}
    direction = str((payload.get("upstream") or {}).get("direction") or "long")
    weights, metrics = bounded_weight_review(row, policy), _review_metrics(row, direction)
    labels: list[str] = []
    if metrics["execution_cost_error_bps"] is not None and metrics["execution_cost_error_bps"] > 0:
        labels.append("execution_cost_underestimated")
    actual, counterfactual = metrics["actual_exit_net_bps"], metrics["exit_0940_counterfactual_net_bps"]
    if actual is not None and counterfactual is not None and actual < counterfactual:
        labels.append("late_exit_vs_0940")
    status = "insufficient_samples" if weights.get("reason") == "insufficient_samples" else "reviewed"
    if status == "reviewed" and any(value is None for key, value in metrics.items() if key != "execution_cost_error_bps"):
        status = "partial_data"
    return {"status": status, "metrics": metrics, "attribution_labels": labels, "weight_update": weights}
