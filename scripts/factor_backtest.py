#!/usr/bin/env python3
"""Research-only quantile backtest and minimal cross-sectional attribution."""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import factor_engine as engine  # noqa: E402
import factor_panel  # noqa: E402
import factor_verdict as verdict_engine  # noqa: E402

DEFAULT_RUN_DIR = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-backtests"
CONDITION_CHOICES = ("vol_20_quintile", "risk_regime")
RISK_REGIME_BINS = (
    "normal", "stress_building", "deleveraging_watch",
    "active_deleveraging", "forced_liquidation",
)
CONDITION_BIN_COUNT = 5


def envelope(schema: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": schema, **fields, "no_order_execution": True}


def validate_config(
    *, bins: int, rebalance_days: int, fac_shift: int, fee_bps: float,
    slippage_bps: float, stamp_bps: float, stamp_direction: str,
) -> None:
    if bins < 3:
        raise ValueError("bins must be >=3")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be >=1")
    if fac_shift < 1:
        raise ValueError("fac_shift must be >=1")
    for name, value in (("fee_bps", fee_bps), ("slippage_bps", slippage_bps), ("stamp_bps", stamp_bps)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be a non-negative finite float")
    if stamp_direction not in ("sell", "both", "none"):
        raise ValueError("stamp_direction must be sell|both|none")


def assign_bins(rows: list[dict[str, Any]], bins: int, min_cross_section: int) -> list[list[dict[str, Any]]] | None:
    """Filter null first, then equal-count rank bins."""
    valid = [row for row in rows if engine.finite(row.get("factor"))]
    if len(valid) < max(min_cross_section, bins):
        return None
    ordered = sorted(valid, key=lambda row: (float(row["factor"]), str(row.get("symbol"))))
    result: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for index, row in enumerate(ordered):
        bucket = min(bins - 1, index * bins // len(ordered))
        result[bucket].append(row)
    return result


def drift_weights(weights: dict[str, float], returns: dict[str, float]) -> dict[str, float]:
    values = {symbol: weight * (1.0 + returns[symbol]) for symbol, weight in weights.items() if symbol in returns}
    total = sum(values.values())
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in values.items()}


def portfolio_turnover(old_weights: dict[str, float], new_weights: dict[str, float]) -> float:
    symbols = set(old_weights) | set(new_weights)
    return sum(abs(new_weights.get(symbol, 0.0) - old_weights.get(symbol, 0.0)) for symbol in symbols)


def turnover_sides(old_weights: dict[str, float], new_weights: dict[str, float]) -> tuple[float, float]:
    symbols = set(old_weights) | set(new_weights)
    buys = sum(max(new_weights.get(symbol, 0.0) - old_weights.get(symbol, 0.0), 0.0) for symbol in symbols)
    sells = sum(max(old_weights.get(symbol, 0.0) - new_weights.get(symbol, 0.0), 0.0) for symbol in symbols)
    return buys, sells


def _time_split(returns: list[float], train_frac: float) -> tuple[list[float], list[float]]:
    split = max(1, min(len(returns) - 1, int(len(returns) * train_frac))) if len(returns) > 1 else len(returns)
    return returns[:split], returns[split:]


def epoch_return(
    rows: list[dict[str, Any]], entry_date: str, exit_date: str,
) -> tuple[float | None, bool, str | None]:
    """Settle at last available price; report interruption instead of filling zeros."""
    prices = {str(row.get("date")): float(row["close"]) for row in rows
              if engine.finite(row.get("close")) and float(row["close"]) > 0}
    if entry_date not in prices:
        return None, False, None
    candidates = sorted(trade_date for trade_date in prices if entry_date <= trade_date <= exit_date)
    if not candidates:
        return None, False, None
    last_date = candidates[-1]
    interrupted = last_date != exit_date
    return prices[last_date] / prices[entry_date] - 1.0, interrupted, last_date


def _metrics(returns: list[float], periods_per_year: float) -> dict[str, float | None]:
    if not returns:
        return {"total_return": None, "annualized_return": None, "annualized_volatility": None,
                "sharpe": None, "max_drawdown": None, "periods": 0}
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        nav *= 1.0 + value
        peak = max(peak, nav)
        if peak > 0:
            max_dd = min(max_dd, nav / peak - 1.0)
    annualized = nav ** (periods_per_year / len(returns)) - 1.0 if nav > 0 else None
    volatility = statistics.stdev(returns) * math.sqrt(periods_per_year) if len(returns) > 1 else None
    mean_annual = statistics.fmean(returns) * periods_per_year
    sharpe = mean_annual / volatility if volatility not in (None, 0) else None
    return {"total_return": nav - 1.0, "annualized_return": annualized,
            "annualized_volatility": volatility, "sharpe": sharpe,
            "max_drawdown": max_dd, "periods": len(returns)}


def _solve_linear3(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    augmented = [list(row) + [value] for row, value in zip(matrix, vector)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [left - factor * right for left, right in zip(augmented[row], augmented[column])]
    return [augmented[row][3] for row in range(3)]


def cross_section_attribution(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    valid = [row for row in rows if all(engine.finite(row.get(field)) for field in ("factor", "size", "fwd_return"))]
    if len(valid) < 4:
        return {"alpha": None, "beta_size": None, "beta_factor": None, "n": len(valid)}
    factors = engine._standardize([float(row["factor"]) for row in valid], "zscore")
    sizes = engine._standardize([float(row["size"]) for row in valid], "zscore")
    returns = [float(row["fwd_return"]) for row in valid]
    design = [[1.0, size, factor] for size, factor in zip(sizes, factors)]
    matrix = [[sum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)]
    vector = [sum(row[i] * value for row, value in zip(design, returns)) for i in range(3)]
    solved = _solve_linear3(matrix, vector)
    if solved is None:
        return {"alpha": None, "beta_size": None, "beta_factor": None, "n": len(valid)}
    return {"alpha": solved[0], "beta_size": solved[1], "beta_factor": solved[2], "n": len(valid)}


def build_attribution(
    panels: dict[str, list[dict[str, Any]]], factor_values: dict[str, dict[str, float | None]],
    formation_dates: list[str], horizon: int,
) -> dict[str, Any]:
    """Uses factor_engine.forward_return, the same function as IC evaluation."""
    size_values = {symbol: engine.size_proxy(rows) for symbol, rows in panels.items()}
    forward_values = {symbol: engine.forward_return(rows, horizon) for symbol, rows in panels.items()}
    series: list[dict[str, Any]] = []
    for formation_date in formation_dates:
        rows = [{"symbol": symbol, "factor": factor_values[symbol].get(formation_date),
                 "size": size_values[symbol].get(formation_date),
                 "fwd_return": forward_values[symbol].get(formation_date)}
                for symbol in panels]
        result = cross_section_attribution(rows)
        series.append({"date": formation_date, **result})
    valid = [row for row in series if row["beta_factor"] is not None]
    return {
        "status": "ok" if valid else "insufficient_data",
        "alpha_series_summary": statistics.fmean(row["alpha"] for row in valid) if valid else None,
        "beta_size": statistics.fmean(row["beta_size"] for row in valid) if valid else None,
        "beta_factor": statistics.fmean(row["beta_factor"] for row in valid) if valid else None,
        "sections": len(valid), "series": series,
    }


def _panel_dates(panels: dict[str, list[dict[str, Any]]]) -> list[str]:
    return sorted({str(row.get("date")) for rows in panels.values() for row in rows if row.get("date")})


def _lagged_condition_assignments(
    panels: dict[str, list[dict[str, Any]]], condition_by: str,
) -> tuple[dict[str, str], dict[str, Any], list[dict[str, Any]]]:
    """Build date-level condition bins from information available one session earlier."""
    if condition_by not in CONDITION_CHOICES:
        raise ValueError(f"unsupported condition_by: {condition_by}")
    dates = _panel_dates(panels)
    assignments: dict[str, str] = {}
    gaps: list[dict[str, Any]] = []

    if condition_by == "vol_20_quintile":
        vol_by_symbol = {symbol: engine.compute_factor(rows, "vol_20") for symbol, rows in panels.items()}
        daily_median: dict[str, float] = {}
        for trade_date in dates:
            values = [
                float(vol_by_symbol[symbol][trade_date])
                for symbol in panels
                if engine.finite(vol_by_symbol[symbol].get(trade_date))
            ]
            if values:
                daily_median[trade_date] = statistics.median(values)
        # Expanding boundaries are recomputed using information known by t-1.
        # Full-sample quantiles would leak future volatility into earlier bins.
        history: list[float] = []
        boundaries_by_date: dict[str, list[float]] = {}
        condition_values_by_date: dict[str, float] = {}
        for previous, current in zip(dates, dates[1:]):
            if previous not in daily_median:
                continue
            value = daily_median[previous]
            history.append(value)
            if len(history) < CONDITION_BIN_COUNT:
                continue
            boundaries = [
                engine._quantile(history, probability)
                for probability in (0.2, 0.4, 0.6, 0.8)
            ]
            assignments[current] = f"Q{bisect.bisect_right(boundaries, value) + 1}"
            boundaries_by_date[current] = boundaries
            condition_values_by_date[current] = value
        binning = {
            "kind": "lagged_expanding_daily_cross_section_median_quintiles",
            "source_factor": "vol_20",
            "lag_sessions": 1,
            "quantiles": [0.2, 0.4, 0.6, 0.8],
            "boundary_history_min_observations": CONDITION_BIN_COUNT,
            "boundaries_by_date": boundaries_by_date,
            "condition_values_by_date": condition_values_by_date,
            "bins": [f"Q{index}" for index in range(1, CONDITION_BIN_COUNT + 1)],
            "date_assignments": assignments,
        }
    else:
        daily_regime: dict[str, str] = {}
        conflicting_dates: list[str] = []
        for trade_date in dates:
            observed = {
                str(row.get("risk_regime"))
                for rows in panels.values()
                for row in rows
                if str(row.get("date")) == trade_date and row.get("risk_regime") in RISK_REGIME_BINS
            }
            if len(observed) == 1:
                daily_regime[trade_date] = next(iter(observed))
            elif len(observed) > 1:
                conflicting_dates.append(trade_date)
        assignments = {
            current: daily_regime[previous]
            for previous, current in zip(dates, dates[1:])
            if previous in daily_regime
        }
        binning = {
            "kind": "lagged_risk_regime_categories",
            "source_schema": "risk_regime_snapshot.v1",
            "lag_sessions": 1,
            "boundaries": list(RISK_REGIME_BINS),
            "bins": list(RISK_REGIME_BINS),
            "date_assignments": assignments,
        }
        if conflicting_dates:
            gaps.append({
                "reason_code": "conflicting_condition_values",
                "gap": "multiple_risk_regimes_on_same_date",
                "dates": conflicting_dates,
            })

    missing_dates = [trade_date for trade_date in dates if trade_date not in assignments]
    if missing_dates:
        gaps.append({
            "reason_code": "missing_lagged_condition",
            "gap": "condition_value_unavailable_one_session_earlier",
            "missing_dates": len(missing_dates),
            "total_dates": len(dates),
        })
    return assignments, binning, gaps


def _factor_observations(
    panels: dict[str, list[dict[str, Any]]], factor_name: str, horizon: int,
) -> list[dict[str, Any]]:
    factor_values = {symbol: engine.compute_factor(rows, factor_name) for symbol, rows in panels.items()}
    observations: list[dict[str, Any]] = []
    for symbol, rows in panels.items():
        forwards = engine.forward_return(rows, horizon)
        null_eligible = not any(row.get("null_eligible") is False for row in rows)
        for row in rows:
            trade_date = str(row.get("date"))
            observations.append({
                "date": trade_date,
                "symbol": symbol,
                "factor": factor_values[symbol].get(trade_date),
                "fwd_return": forwards.get(trade_date),
                "null_eligible": null_eligible,
            })
    return observations


def apply_condition_state_ceiling(global_state: str | None, raw_bin_state: str | None) -> str | None:
    """A condition can invalidate confirmed alpha, never promote a failed global state."""
    if global_state is None or raw_bin_state is None:
        return None
    if global_state == "confirmed_alive":
        return "confirmed_alive" if raw_bin_state == "confirmed_alive" else "train_only"
    return global_state


def _condition_hypothesis_payload(
    *, market: str = "UNKNOWN", factor_name: str, condition_by: str, bin_id: str, horizon: int,
    raw_state: str, conditioned_state: str, seed: int = 42,
    null_kind: str = engine.DEFAULT_NULL_KIND,
) -> dict[str, Any]:
    return {
        "statement": (
            f"market={market};factor={factor_name};condition_by={condition_by};bin={bin_id};"
            f"horizon={horizon};conditional_validity_under_fixed_T=3.5"
        ),
        "status": "train_only",
        "source_module": "quant_robustness",
        "tags": [market, factor_name, condition_by, bin_id, "conditional_validation"],
        "evidence_ids": [],
        "falsifiers": ["下一轮滚动 OOS 中该分箱 `alpha_t < 3.5` 即降级。"],
        "record_type": "factor_condition_hypothesis",
        "note": (
            "conditional factor validation; initial status train_only; no promotion authority; "
            f"raw_state={raw_state}; conditioned_state={conditioned_state}; "
            f"null_kind={null_kind}; seed={seed}"
        ),
    }


def conditional_factor_validation(
    panels: dict[str, list[dict[str, Any]]], factor_name: str, *,
    horizon: int, condition_by: str, market: str = "UNKNOWN",
    null_trials: int = 100, seed: int = 42,
    null_kind: str = engine.DEFAULT_NULL_KIND, min_cross_section: int = 30,
    min_dates: int = 40, train_frac: float = 0.7,
    family_factor_count: int = 1, family_horizon_count: int = 1,
) -> dict[str, Any]:
    if factor_name not in engine.FACTOR_METHODS:
        raise ValueError(f"unknown factor: {factor_name}")
    if horizon < 1:
        raise ValueError("horizon must be >=1")
    assignments, binning, condition_gaps = _lagged_condition_assignments(panels, condition_by)
    observations = _factor_observations(panels, factor_name, horizon)
    if family_factor_count < 1 or family_horizon_count < 1:
        raise ValueError("condition family counts must be positive")
    scan_count = family_factor_count * family_horizon_count * CONDITION_BIN_COUNT
    common_global = {
        "null_trials": null_trials, "seed": seed, "null_kind": null_kind,
        "min_cross_section": min_cross_section, "min_dates": min_dates,
        "train_frac": train_frac, "section_stride": horizon,
    }
    raw_global_sections, _excluded, _total = engine._prepared_sections(
        observations, min_cross_section
    )
    global_section_dates = [
        section[0] for section in raw_global_sections[::horizon]
    ]
    global_section_date_set = set(global_section_dates)
    global_stats = engine.evaluate_observations(observations, **common_global)
    global_stats["n_factors_scanned"] = scan_count
    global_stats["section_dates"] = global_section_dates
    direction = str(engine.FACTOR_METHODS[factor_name]["direction_hypothesis"])
    global_state = (
        verdict_engine.classify(global_stats, direction)
        if global_stats.get("status") == "ok" else None
    )
    bins: list[dict[str, Any]] = []
    degradations: list[dict[str, Any]] = []
    for bin_id in binning["bins"]:
        allowed_dates = {
            trade_date for trade_date, label in assignments.items()
            if label == bin_id and trade_date in global_section_date_set
        }
        bin_observations = [row for row in observations if row["date"] in allowed_dates]
        stats = engine.evaluate_observations(
            bin_observations,
            null_trials=null_trials,
            seed=seed,
            null_kind=null_kind,
            min_cross_section=min_cross_section,
            min_dates=min_dates,
            train_frac=train_frac,
            section_stride=1,
        )
        stats["n_factors_scanned"] = scan_count
        stats["preselected_section_stride"] = horizon
        stats["section_dates"] = sorted(allowed_dates)
        effective_sections = int(stats.get("effective_sections") or 0)
        if stats.get("status") != "ok" or effective_sections < min_dates:
            bins.append({
                "bin": bin_id,
                "status": "insufficient_sample",
                "effective_sections": effective_sections,
                "min_dates": min_dates,
                "verdict": None,
                "hypothesis_payload": None,
                "factor_validation": stats,
            })
            continue
        raw_state = verdict_engine.classify(stats, direction)
        conditioned_state = apply_condition_state_ceiling(global_state, raw_state)
        if raw_state is None or conditioned_state is None:
            bins.append({
                "bin": bin_id,
                "status": "unjudgeable",
                "effective_sections": effective_sections,
                "min_dates": min_dates,
                "verdict": None,
                "hypothesis_payload": None,
                "factor_validation": stats,
            })
            continue
        verdict = {
            "raw_state": raw_state,
            "state": conditioned_state,
            "global_state_ceiling": global_state,
            "threshold": verdict_engine.THRESHOLD,
            "position_multiplier": 0.0 if conditioned_state != "confirmed_alive" else None,
            "no_promotion": conditioned_state == global_state and global_state != "confirmed_alive",
        }
        payload = _condition_hypothesis_payload(
            market=market, factor_name=factor_name, condition_by=condition_by, bin_id=bin_id,
            horizon=horizon, raw_state=raw_state, conditioned_state=conditioned_state,
            seed=seed, null_kind=null_kind,
        )
        degraded = global_state == "confirmed_alive" and conditioned_state == "train_only"
        if degraded:
            degradations.append({
                "factor": factor_name, "horizon": horizon, "condition_by": condition_by,
                "bin": bin_id, "before": global_state, "after": conditioned_state,
                "raw_bin_state": raw_state,
            })
        bins.append({
            "bin": bin_id,
            "status": "ok",
            "effective_sections": effective_sections,
            "min_dates": min_dates,
            "verdict": verdict,
            "hypothesis_payload": payload,
            "factor_validation": stats,
        })
    judgeable_bins = [row for row in bins if row["status"] == "ok"]
    unjudgeable_bins = [row for row in bins if row["status"] == "unjudgeable"]
    if degradations:
        pair_status = "degradation_observed"
    elif len(judgeable_bins) == CONDITION_BIN_COUNT:
        pair_status = "no_degradation"
    elif unjudgeable_bins:
        pair_status = "pending_unjudgeable"
    else:
        pair_status = "pending_insufficient_sample"
    return envelope(
        "factor_condition_validation.v1", status="ok" if judgeable_bins else "insufficient_sample",
        market=market, factor=factor_name, horizon=horizon, condition_by=condition_by,
        lag_sessions=1, k=CONDITION_BIN_COUNT,
        n_factors_scanned=scan_count,
        family_factor_count=family_factor_count,
        family_horizon_count=family_horizon_count,
        n_factors_scanned_formula=(
            f"{family_factor_count} factors * {family_horizon_count} horizons * "
            f"{CONDITION_BIN_COUNT} condition bins"
        ),
        threshold=verdict_engine.THRESHOLD,
        threshold_policy="fixed_3.5_not_relaxed_for_condition_bins",
        null_kind=null_kind, seed=seed, null_trials=null_trials,
        binning=binning,
        global_validation=global_stats, global_state=global_state,
        bins=bins, degradation_count=len(degradations), degradations=degradations,
        factor_level_degradation_count=len(degradations),
        judgeable_bin_count=len(judgeable_bins),
        insufficient_bin_count=sum(row["status"] == "insufficient_sample" for row in bins),
        unjudgeable_bin_count=len(unjudgeable_bins),
        pair_status=pair_status,
        dimension_retained=False,
        dimension_status="pending_family_aggregation",
        data_gaps=condition_gaps,
    )


def conditional_family_validation(
    panels: dict[str, list[dict[str, Any]]], factor_names: list[str], horizons: list[int], *,
    condition_by: str, market: str = "UNKNOWN", null_trials: int = 100, seed: int = 42,
    null_kind: str = engine.DEFAULT_NULL_KIND, min_cross_section: int = 30,
    min_dates: int = 40, train_frac: float = 0.7,
    family_complete: bool = False,
) -> dict[str, Any]:
    """Aggregate a predeclared factor×horizon family before judging a dimension."""
    factors = list(dict.fromkeys(factor_names))
    declared_horizons = list(dict.fromkeys(horizons))
    if not factors or any(name not in engine.FACTOR_METHODS for name in factors):
        raise ValueError("condition family requires registered factors")
    if not declared_horizons or any(value < 1 for value in declared_horizons):
        raise ValueError("condition family requires positive horizons")
    scan_count = len(factors) * len(declared_horizons) * CONDITION_BIN_COUNT
    results = [
        conditional_factor_validation(
            panels, factor_name, horizon=horizon, condition_by=condition_by,
            market=market,
            null_trials=null_trials, seed=seed, null_kind=null_kind,
            min_cross_section=min_cross_section, min_dates=min_dates,
            train_frac=train_frac,
            family_factor_count=len(factors),
            family_horizon_count=len(declared_horizons),
        )
        for factor_name in factors
        for horizon in declared_horizons
    ]
    degradations = [
        row
        for result in results
        for row in result.get("degradations") or []
    ]
    all_judgeable = bool(results) and all(
        result.get("judgeable_bin_count") == CONDITION_BIN_COUNT
        for result in results
    )
    if not family_complete:
        dimension_status = "pending_family_aggregation"
        dimension_retained = False
    elif degradations:
        dimension_status = "retained_degradation_observed"
        dimension_retained = True
    elif family_complete and all_judgeable:
        dimension_status = "rejected_no_degradation"
        dimension_retained = False
    elif any(result.get("unjudgeable_bin_count") for result in results):
        dimension_status = "pending_unjudgeable"
        dimension_retained = False
    elif any(result.get("insufficient_bin_count") for result in results):
        dimension_status = "pending_insufficient_sample"
        dimension_retained = False
    else:
        dimension_status = "pending_dimension_evidence"
        dimension_retained = False
    return envelope(
        "factor_condition_family_validation.v1",
        status="ok" if any(result.get("judgeable_bin_count") for result in results) else "insufficient_sample",
        market=market,
        condition_by=condition_by,
        factors=factors,
        horizons=declared_horizons,
        family_complete=family_complete,
        k=CONDITION_BIN_COUNT,
        n_factors_scanned=scan_count,
        n_factors_scanned_formula="factors * horizons * k condition bins",
        threshold=verdict_engine.THRESHOLD,
        threshold_policy="fixed_3.5_not_relaxed_for_condition_bins",
        null_kind=null_kind,
        seed=seed,
        null_trials=null_trials,
        results=results,
        degradation_count=len(degradations),
        degradations=degradations,
        all_bins_judgeable=all_judgeable,
        dimension_retained=dimension_retained,
        dimension_status=dimension_status,
        binning=results[0].get("binning") if results else None,
        data_gaps=[gap for result in results for gap in result.get("data_gaps") or []],
    )


def register_condition_hypotheses(condition_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Search before create/update; only judgeable bins from a retained dimension write."""
    if not condition_result.get("dimension_retained"):
        return []
    registrations: list[dict[str, Any]] = []
    env = dict(os.environ)
    pair_results = condition_result.get("results")
    if not isinstance(pair_results, list):
        pair_results = [condition_result]
    rows = [
        row
        for pair_result in pair_results
        if isinstance(pair_result, dict)
        for row in pair_result.get("bins") or []
    ]
    for row in rows:
        payload = row.get("hypothesis_payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            continue
        statement = str(payload["statement"])
        search = subprocess.run(
            [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "search", "--query", statement],
            env=env, capture_output=True, text=True, timeout=10, check=False,
        )
        if search.returncode != 0:
            raise RuntimeError(search.stderr.strip() or search.stdout.strip())
        search_result = json.loads(search.stdout)
        exact = [item for item in search_result.get("hypotheses") or [] if item.get("statement") == statement]
        if exact:
            hypothesis_id = str(exact[0]["hypothesis_id"])
            command = [
                sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "update",
                "--id", hypothesis_id, "--status", "train_only",
                "--note", str(payload["note"]), "--record-type", str(payload["record_type"]),
            ]
            for tag in payload.get("tags") or []:
                command.extend(["--tag", str(tag)])
            for falsifier in payload.get("falsifiers") or []:
                command.extend(["--falsifier", str(falsifier)])
            action = "updated"
        else:
            fd, name = tempfile.mkstemp(prefix="factor-condition-", suffix=".json")
            os.close(fd)
            payload_path = Path(name)
            try:
                payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                command = [
                    sys.executable, str(SCRIPTS / "hypothesis_registry.py"),
                    "create", "--payload", str(payload_path),
                ]
                completed = subprocess.run(
                    command, env=env, capture_output=True, text=True, timeout=10, check=False,
                )
            finally:
                payload_path.unlink(missing_ok=True)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
            registrations.append({"action": "created", **json.loads(completed.stdout)})
            continue
        completed = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=10, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        registrations.append({"action": action, **json.loads(completed.stdout)})
    return registrations


def summarize_panel_provenance(
    panels: dict[str, list[dict[str, Any]]], market: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Aggregate cached panel provenance and fail closed on illegal market/source pairs."""
    market = market.upper()
    symbol_rows: dict[str, Any] = {}
    gaps: list[dict[str, Any]] = []
    sources_used: set[str] = set()
    adjusts_used: set[str] = set()
    bases_used: set[str] = set()
    caveats: set[str] = set()
    expected_adjust = {
        "akshare": "qfq",
        "akshare_sina": "sina_qfq",
        "longbridge": "forward",
    }
    expected_caveat = {
        "akshare": "qfq_rewrites_history",
        "akshare_sina": "qfq_rewrites_history",
        "longbridge": "forward_adjust_rewrites_history",
    }
    for symbol, rows in panels.items():
        sources = sorted({
            str(row.get("panel_source"))
            for row in rows
            if isinstance(row.get("panel_source"), str) and row.get("panel_source")
        })
        adjusts = sorted({
            str(row.get("panel_adjust"))
            for row in rows
            if isinstance(row.get("panel_adjust"), str) and row.get("panel_adjust")
        })
        bases = sorted({
            str(row.get("panel_adjust_basis"))
            for row in rows
            if isinstance(row.get("panel_adjust_basis"), str) and row.get("panel_adjust_basis")
        })
        symbol_caveats = sorted({
            str(value)
            for row in rows
            for value in (row.get("panel_pit_caveats") or [])
            if isinstance(value, str) and value
        })
        sources_used.update(sources)
        adjusts_used.update(adjusts)
        bases_used.update(bases)
        caveats.update(symbol_caveats)
        for row_index, row in enumerate(rows):
            source = row.get("panel_source")
            adjust = row.get("panel_adjust")
            adjust_basis = row.get("panel_adjust_basis")
            row_caveats = (
                row.get("panel_pit_caveats")
                if isinstance(row.get("panel_pit_caveats"), list)
                else []
            )
            if not isinstance(source, str) or not source:
                gaps.append({
                    "symbol": symbol, "row_index": row_index,
                    "reason_code": "missing_panel_provenance",
                    "gap": "panel_source_missing",
                })
            elif source not in expected_adjust:
                gaps.append({
                    "symbol": symbol, "row_index": row_index,
                    "reason_code": "illegal_panel_source",
                    "gap": "panel_source_not_registered",
                    "source": source,
                })
            if source in expected_adjust and adjust != expected_adjust[source]:
                gaps.append({
                    "symbol": symbol, "row_index": row_index,
                    "reason_code": "panel_adjust_mismatch",
                    "gap": f"{source}_requires_{expected_adjust[source]}",
                    "adjust": adjust,
                })
            required_caveat = expected_caveat.get(str(source))
            if required_caveat and required_caveat not in row_caveats:
                gaps.append({
                    "symbol": symbol, "row_index": row_index,
                    "reason_code": "missing_panel_provenance",
                    "gap": f"{source}_requires_{required_caveat}",
                })
            if not isinstance(adjust_basis, str) or not adjust_basis.strip():
                gaps.append({
                    "symbol": symbol, "row_index": row_index,
                    "reason_code": "missing_panel_provenance",
                    "gap": "adjust_basis_missing",
                })
        if market == "A" and any(
            source not in {"akshare", "akshare_sina"} for source in sources
        ):
            gaps.append({
                "symbol": symbol, "reason_code": "illegal_panel_source",
                "gap": "A_HK_panel_requires_akshare_family",
                "sources": sources,
            })
        if market == "HK" and any(
            source not in {"akshare", "akshare_sina", "longbridge"} for source in sources
        ):
            gaps.append({
                "symbol": symbol, "reason_code": "illegal_panel_source",
                "gap": "HK_panel_source_not_registered",
                "sources": sources,
            })
        if market == "US" and any(
            source not in {"akshare", "longbridge"} for source in sources
        ):
            gaps.append({
                "symbol": symbol, "reason_code": "illegal_panel_source",
                "gap": "US_panel_source_not_registered",
                "sources": sources,
            })
        for source in sources:
            required = expected_adjust.get(source)
            if required and required not in adjusts:
                gaps.append({
                    "symbol": symbol, "reason_code": "panel_adjust_mismatch",
                    "gap": f"{source}_requires_{required}",
                    "adjusts": adjusts,
                })
        symbol_rows[symbol] = {
            "sources": sources,
            "adjusts": adjusts,
            "adjust_bases": bases,
            "pit_caveats": symbol_caveats,
            "null_eligible": not any(row.get("null_eligible") is False for row in rows),
        }
    if len(bases_used) > 1:
        gaps.append({
            "reason_code": "mixed_adjust_basis",
            "gap": "cross_symbol_adjust_basis_mismatch",
            "bases": sorted(bases_used),
        })
    summary = envelope(
        "factor_backtest_panel_provenance.v1",
        market=market,
        sources_used=sorted(sources_used),
        adjusts_used=sorted(adjusts_used),
        adjust_bases_used=sorted(bases_used),
        pit_caveats=sorted(caveats),
        symbols=symbol_rows,
    )
    return summary, gaps


def run_backtest(
    market: str, panels: dict[str, list[dict[str, Any]]], factor_name: str, *,
    bins: int = 5, rebalance_days: int = 5, fac_shift: int = 1,
    fee_bps: float = 3.0, slippage_bps: float = 5.0, stamp_bps: float = 5.0,
    stamp_direction: str = "sell", min_cross_section: int = 30,
    train_frac: float = 0.7, attribution: bool = False,
    condition_by: str | None = None, null_trials: int = 100, seed: int = 42,
    null_kind: str = engine.DEFAULT_NULL_KIND, min_dates: int = 40,
    condition_factors: list[str] | None = None,
    condition_horizons: list[int] | None = None,
    condition_family_complete: bool = False,
    panel_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_config(bins=bins, rebalance_days=rebalance_days, fac_shift=fac_shift,
                    fee_bps=fee_bps, slippage_bps=slippage_bps,
                    stamp_bps=stamp_bps, stamp_direction=stamp_direction)
    if not 0 < train_frac < 1:
        raise ValueError("train_frac must be between 0 and 1")
    if factor_name not in engine.FACTOR_METHODS:
        raise ValueError(f"unknown factor: {factor_name}")
    if condition_by is not None and condition_by not in CONDITION_CHOICES:
        raise ValueError(f"unsupported condition_by: {condition_by}")
    condition_result = (
        conditional_family_validation(
            panels, condition_factors or [factor_name],
            condition_horizons or [fac_shift + rebalance_days],
            condition_by=condition_by, market=market,
            null_trials=null_trials, seed=seed,
            null_kind=null_kind, min_cross_section=min_cross_section,
            min_dates=min_dates, train_frac=train_frac,
            family_complete=condition_family_complete,
        )
        if condition_by else None
    )
    factor_values = {symbol: engine.compute_factor(rows, factor_name) for symbol, rows in panels.items()}
    dates = sorted({str(row.get("date")) for rows in panels.values() for row in rows if row.get("date")})
    price_by_symbol = {symbol: {str(row.get("date")): row for row in rows} for symbol, rows in panels.items()}
    epochs: list[dict[str, Any]] = []
    excluded_epochs = 0
    interrupted_count = 0
    limit_move_days = 0
    previous_weights: list[dict[str, float]] = [{} for _ in range(bins)]
    formation_dates: list[str] = []
    start = fac_shift
    for entry_index in range(start, max(start, len(dates) - 1), rebalance_days):
        exit_index = min(entry_index + rebalance_days, len(dates) - 1)
        if exit_index <= entry_index:
            continue
        formation_date, entry_date, exit_date = dates[entry_index - fac_shift], dates[entry_index], dates[exit_index]
        candidates: list[dict[str, Any]] = []
        for symbol in panels:
            factor = factor_values[symbol].get(formation_date)
            entry_row = price_by_symbol[symbol].get(entry_date)
            if engine.finite(factor) and entry_row and engine.finite(entry_row.get("close")):
                candidates.append({"symbol": symbol, "factor": factor})
        buckets = assign_bins(candidates, bins, min_cross_section)
        if buckets is None:
            excluded_epochs += 1
            continue
        formation_dates.append(formation_date)
        bin_records: list[dict[str, Any]] = []
        for bucket_index, bucket in enumerate(buckets):
            symbols = [str(row["symbol"]) for row in bucket]
            new_weights = {symbol: 1.0 / len(symbols) for symbol in symbols}
            turnover = portfolio_turnover(previous_weights[bucket_index], new_weights)
            buy_turnover, sell_turnover = turnover_sides(previous_weights[bucket_index], new_weights)
            symbol_returns: dict[str, float] = {}
            last_dates: dict[str, str | None] = {}
            for symbol in symbols:
                value, interrupted, last_date = epoch_return(panels[symbol], entry_date, exit_date)
                if value is None:
                    continue
                symbol_returns[symbol] = value
                last_dates[symbol] = last_date
                if interrupted:
                    interrupted_count += 1
                entry_row = price_by_symbol[symbol][entry_date]
                if market == "A" and engine.finite(entry_row.get("pct_change")) and abs(float(entry_row["pct_change"])) >= 9.5:
                    limit_move_days += 1
            gross = statistics.fmean(symbol_returns.values()) if symbol_returns else None
            trading_cost = turnover * (float(fee_bps) + float(slippage_bps)) / 10000.0
            stamp_base = sell_turnover if stamp_direction == "sell" else (turnover if stamp_direction == "both" else 0.0)
            stamp_cost = stamp_base * float(stamp_bps) / 10000.0
            cost = trading_cost + stamp_cost
            net = gross - cost if gross is not None else None
            previous_weights[bucket_index] = drift_weights(new_weights, symbol_returns) if symbol_returns else {}
            bin_records.append({"bin": bucket_index + 1, "symbols": symbols, "gross_return": gross,
                                "net_return": net, "turnover": turnover,
                                "buy_turnover": buy_turnover, "sell_turnover": sell_turnover,
                                "trading_cost": trading_cost, "stamp_cost": stamp_cost, "cost": cost,
                                "ending_weights": previous_weights[bucket_index], "last_dates": last_dates})
        epochs.append({"formation_date": formation_date, "entry_date": entry_date,
                       "exit_date": exit_date, "bins": bin_records})
    total_epoch_slots = len(epochs) + excluded_epochs
    excluded_share = excluded_epochs / total_epoch_slots if total_epoch_slots else 1.0
    if excluded_share > 0.30 or not epochs:
        result = envelope("factor_backtest_run.v1", status="insufficient_data", market=market,
                          factor=factor_name, bins=None, ls_net=None, monotonicity_spearman=None,
                          turnover=None, costs_included="yes", train=None, test=None, attribution=None,
                          failure_modes=[{"type": "insufficient_cross_section", "excluded_epochs": excluded_epochs}],
                          robustness_checks=["no_lookahead_fac_shift>=1"], data_gaps=[],
                          panel_provenance=panel_provenance)
        if condition_result is not None:
            result["condition_validation"] = condition_result
        return result
    per_bin_returns = [[epoch["bins"][index]["net_return"] for epoch in epochs
                        if epoch["bins"][index]["net_return"] is not None] for index in range(bins)]
    periods_per_year = 252.0 / rebalance_days
    bin_metrics = [{"bin": index + 1, **_metrics(per_bin_returns[index], periods_per_year)} for index in range(bins)]
    annuals = [row["annualized_return"] for row in bin_metrics]
    monotonicity = engine.spearman(list(range(1, bins + 1)), [float(value) for value in annuals]) if all(value is not None for value in annuals) else None
    ls_returns = [0.5 * (epoch["bins"][-1]["net_return"] - epoch["bins"][0]["net_return"])
                  for epoch in epochs if epoch["bins"][-1]["net_return"] is not None and epoch["bins"][0]["net_return"] is not None]
    train_ls, test_ls = _time_split(ls_returns, train_frac)
    all_turnover = [record["turnover"] for epoch in epochs for record in epoch["bins"]]
    attribution_result = build_attribution(panels, factor_values, formation_dates, fac_shift + rebalance_days) if attribution else None
    failure_modes = [
        {"type": "suspended_or_delisted_count", "count": interrupted_count,
         "settlement": "last_available_price_no_zero_fill"},
        {"type": "limit_move_entry_days", "count": limit_move_days},
        {"type": "excluded_formation_epochs", "count": excluded_epochs},
    ]
    robustness = [f"limit_move_entry_days={limit_move_days}", "no_lookahead_fac_shift>=1",
                  "quantile_bins_break_factor_ties_by_symbol_order",
                  "low_cardinality_factor_spread_may_be_tie_driven"]
    if panel_provenance:
        robustness.extend(panel_provenance.get("pit_caveats") or [])
    if market == "A":
        robustness.extend([
            "short_leg_feasibility=not_available_in_A_share",
            "a_share_limit_detection_uniform_9_5pct_not_board_or_st_aware",
            "limit_move_days_counts_symbol_epoch_occurrences",
        ])
    result = envelope("factor_backtest_run.v1", status="ok", market=market, factor=factor_name,
                      fac_shift=fac_shift, rebalance_days=rebalance_days, bins=bin_metrics,
                      ls_net=_metrics(ls_returns, periods_per_year), monotonicity_spearman=monotonicity,
                      turnover=statistics.fmean(all_turnover) if all_turnover else None,
                      costs_included="yes", train=_metrics(train_ls, periods_per_year),
                      test=_metrics(test_ls, periods_per_year), attribution=attribution_result,
                      failure_modes=failure_modes, robustness_checks=robustness,
                      data_gaps=[], epochs=epochs, panel_provenance=panel_provenance)
    if condition_result is not None:
        result["condition_validation"] = condition_result
    return result


def _atomic_save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def self_test() -> None:
    validate_config(bins=3, rebalance_days=2, fac_shift=1, fee_bps=3.0,
                    slippage_bps=5.0, stamp_bps=5.0, stamp_direction="sell")
    with tempfile.TemporaryDirectory() as tmp:
        rows = [{"date": "2026-01-01", "close": 10.0},
                {"date": "2026-01-02", "close": 11.0}]
        value, interrupted, last = epoch_return(rows, "2026-01-01", "2026-01-03")
        assert abs(float(value) - 0.1) < 1e-12 and interrupted and last == "2026-01-02"
        result = envelope("factor_backtest_self_test.v1", ok=True)
        _atomic_save(Path(tmp) / "result.json", result)
        assert json.loads((Path(tmp) / "result.json").read_text())["ok"] is True
    assert apply_condition_state_ceiling("train_only", "confirmed_alive") == "train_only"
    assert apply_condition_state_ceiling("confirmed_alive", "noise") == "train_only"


def _symbols(args: argparse.Namespace) -> list[str]:
    values = list(args.symbols or [])
    if args.symbols_file:
        values.extend(line.strip().split()[0] for line in Path(args.symbols_file).read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError("at least one symbol is required")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--market", required=True, choices=sorted(factor_panel.MARKETS))
    run.add_argument("--symbols", nargs="*", default=[])
    run.add_argument("--symbols-file")
    run.add_argument("--factor", required=True, choices=sorted(engine.FACTOR_METHODS))
    run.add_argument("--bins", type=int, default=5)
    run.add_argument("--rebalance-days", type=int, default=5)
    run.add_argument("--fac-shift", type=int, default=1)
    run.add_argument("--fee-bps", type=float, default=3.0)
    run.add_argument("--slippage-bps", type=float, default=5.0)
    run.add_argument("--stamp-bps", type=float, default=5.0)
    run.add_argument("--stamp-direction", choices=["sell", "both", "none"], default="sell")
    run.add_argument("--min-cross-section", type=int, default=30)
    run.add_argument("--train-frac", type=float, default=0.7)
    run.add_argument("--attribution", action="store_true")
    run.add_argument("--condition-by", choices=CONDITION_CHOICES)
    run.add_argument(
        "--condition-factors", nargs="+", choices=sorted(engine.FACTOR_METHODS),
        help="predeclared factor family; requires --condition-horizons",
    )
    run.add_argument(
        "--condition-horizons", nargs="+", type=int,
        help="predeclared horizon family; requires --condition-factors",
    )
    run.add_argument(
        "--null", choices=engine.NULL_KINDS, default=engine.DEFAULT_NULL_KIND,
        help="null for conditional validation; incumbent default remains until real comparison passes",
    )
    run.add_argument("--null-trials", type=int, default=100)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--min-dates", type=int, default=40)
    run.add_argument("--register-conditions", action="store_true")
    run.add_argument("--save", action="store_true")
    run.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed"}))
        return 0
    args = build_parser().parse_args(args_list)
    try:
        symbols = _symbols(args)
        if args.register_conditions and not args.condition_by:
            raise ValueError("--register-conditions requires --condition-by")
        if (args.condition_factors or args.condition_horizons) and not args.condition_by:
            raise ValueError("condition family arguments require --condition-by")
        if bool(args.condition_factors) != bool(args.condition_horizons):
            raise ValueError("--condition-factors and --condition-horizons must be supplied together")
        panels, gaps, _basis = engine.load_panels(args.market, symbols)
        panel_provenance, provenance_gaps = summarize_panel_provenance(panels, args.market)
        gaps.extend(provenance_gaps)
        if gaps or not panels:
            result = envelope("factor_backtest_run.v1", status="insufficient_data", market=args.market,
                              factor=args.factor, bins=None, ls_net=None, monotonicity_spearman=None,
                              turnover=None, costs_included="yes", train=None, test=None, attribution=None,
                              failure_modes=[], robustness_checks=["no_lookahead_fac_shift>=1"],
                              data_gaps=gaps, panel_provenance=panel_provenance)
        else:
            result = run_backtest(args.market, panels, args.factor, bins=args.bins,
                                  rebalance_days=args.rebalance_days, fac_shift=args.fac_shift,
                                  fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                                  stamp_bps=args.stamp_bps, stamp_direction=args.stamp_direction,
                                  min_cross_section=args.min_cross_section, train_frac=args.train_frac,
                                  attribution=args.attribution, condition_by=args.condition_by,
                                  null_trials=args.null_trials, seed=args.seed, null_kind=args.null,
                                  min_dates=args.min_dates,
                                  condition_factors=args.condition_factors,
                                  condition_horizons=args.condition_horizons,
                                  condition_family_complete=bool(
                                      args.condition_factors and args.condition_horizons
                                  ),
                                  panel_provenance=panel_provenance)
        if args.register_conditions:
            condition_result = result.get("condition_validation")
            if not isinstance(condition_result, dict):
                raise ValueError("condition validation result missing")
            result["condition_registrations"] = register_condition_hypotheses(condition_result)
        if args.save:
            root = Path(os.environ.get("FACTOR_BACKTEST_DIR", str(DEFAULT_RUN_DIR))).expanduser()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = root / f"fb_{stamp}_{args.factor}.json"
            _atomic_save(path, result)
            result["saved_path"] = str(path)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps(envelope("factor_backtest_error.v1", ok=False, status="error",
                                  data_gaps=[{"reason_code": "error", "gap": str(exc)}]), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, allow_nan=False))
    return 0 if result.get("status") != "insufficient_data" else 1


if __name__ == "__main__":
    raise SystemExit(main())
