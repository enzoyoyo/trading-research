#!/usr/bin/env python3
"""Stdlib-only cross-sectional factor engine with random-control IC tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import factor_panel  # noqa: E402

FACTOR_METHODS: dict[str, dict[str, Any]] = {
    "mom_20_1": {"formula": "close[t-1]/close[t-21] - 1", "direction_hypothesis": "+", "min_history_days": 22, "cross_section_only": True},
    "mom_60_5": {"formula": "close[t-5]/close[t-65] - 1", "direction_hypothesis": "+", "min_history_days": 66, "cross_section_only": True},
    "rev_5": {"formula": "-(close[t]/close[t-5] - 1)", "direction_hypothesis": "+", "min_history_days": 6, "cross_section_only": True},
    "vol_20": {"formula": "stdev(daily_ret,20)", "direction_hypothesis": "-", "min_history_days": 21, "cross_section_only": True},
    "turn_20": {"formula": "mean(turnover_rate,20)", "direction_hypothesis": "-", "min_history_days": 20, "cross_section_only": True},
    "turn_ratio_5_60": {"formula": "mean(turnover_rate,5)/mean(turnover_rate,60)", "direction_hypothesis": "-", "min_history_days": 60, "cross_section_only": True},
    "range_pos_252": {"formula": "(close-min(low,252))/(max(high,252)-min(low,252))", "direction_hypothesis": "+", "min_history_days": 252, "cross_section_only": True, "default_enabled": False, "window_policy": "explicit_long_window_only"},
    "amihud_20": {"formula": "mean(abs(daily_ret)/amount,20)", "direction_hypothesis": "research", "min_history_days": 21, "cross_section_only": True},
}
DEFAULT_FACTORS = tuple(name for name, method in FACTOR_METHODS.items() if method.get("default_enabled", True))
FACTORS_HELP = (
    "all selects default-enabled factors and excludes explicit long-window factors such as "
    "range_pos_252; comma-separated names for a custom set; omit or all for defaults"
)
PREPROCESS_METHODS = {
    "winsorize": ["mad", "iqr", "quantile"],
    "standardize": ["zscore", "robust_zscore", "rank"],
    "neutralize": ["ols_residual_size", "ols_residual_size_sector"],
}
NEUTRALIZE_ALIASES = {
    "size": "size",
    "size_sector": "size_sector",
    "ols_residual_size": "size",
    "ols_residual_size_sector": "size_sector",
}
DEFAULT_RUN_DIR = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-runs"
NULL_KINDS = ("cross_section_shuffle", "circular_rotation")
# A live comparison needs a deployable cross-section (>=30 symbols). The local
# cache currently cannot clear that gate, so the default remains the incumbent
# until compare-nulls produces at least one strict four-state downgrade.
DEFAULT_NULL_KIND = "cross_section_shuffle"
MIN_CIRCULAR_SERIES = 4


def envelope(schema: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": schema, **fields, "no_order_execution": True}


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def mean_or_none(values: Iterable[float]) -> float | None:
    seq = list(values)
    return statistics.fmean(seq) if seq else None


def ranks(values: list[float]) -> list[float]:
    """Average ranks for ties, ascending, 1-based."""
    order = sorted(range(len(values)), key=values.__getitem__)
    output = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for pos in order[i:j]:
            output[pos] = rank
        i = j
    return output


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    lx = statistics.fmean(left)
    rx = statistics.fmean(right)
    a = [value - lx for value in left]
    b = [value - rx for value in right]
    denom = math.sqrt(sum(value * value for value in a) * sum(value * value for value in b))
    return sum(x * y for x, y in zip(a, b)) / denom if denom else None


def spearman(left: list[float], right: list[float]) -> float | None:
    return pearson(ranks(left), ranks(right))


def forward_return(rows: list[dict[str, Any]], horizon: int) -> dict[str, float | None]:
    """Return at date t is close[t+h]/close[t]-1 within one symbol only."""
    if horizon < 1:
        raise ValueError("horizon must be >=1")
    ordered = sorted(rows, key=lambda row: str(row.get("date", "")))
    result: dict[str, float | None] = {}
    for index, row in enumerate(ordered):
        key = str(row.get("date"))
        if index + horizon >= len(ordered):
            result[key] = None
            continue
        current, future = row.get("close"), ordered[index + horizon].get("close")
        if not finite(current) or not finite(future) or float(current) <= 0:
            result[key] = None
        else:
            result[key] = float(future) / float(current) - 1.0
    return result


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _winsor(values: list[float], method: str) -> list[float]:
    if method == "mad":
        center = statistics.median(values)
        mad = statistics.median(abs(value - center) for value in values)
        low, high = center - 5 * mad, center + 5 * mad
    elif method == "iqr":
        q1, q3 = _quantile(values, 0.25), _quantile(values, 0.75)
        low, high = q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)
    elif method == "quantile":
        low, high = _quantile(values, 0.01), _quantile(values, 0.99)
    else:
        raise ValueError(f"unsupported winsorize method: {method}")
    return [min(high, max(low, value)) for value in values]


def _standardize(values: list[float], method: str) -> list[float]:
    if method == "rank":
        ranked = ranks(values)
        return [(value - (len(values) + 1) / 2) / max(len(values), 1) for value in ranked]
    if method == "robust_zscore":
        center = statistics.median(values)
        scale = statistics.median(abs(value - center) for value in values) * 1.4826
    elif method == "zscore":
        center = statistics.fmean(values)
        scale = statistics.pstdev(values)
    else:
        raise ValueError(f"unsupported standardize method: {method}")
    return [(value - center) / scale for value in values] if scale else [0.0] * len(values)


def _neutralize(values: list[float], sizes: list[float], sectors: list[str] | None = None) -> list[float]:
    target = list(values)
    if sectors:
        groups: dict[str, list[int]] = {}
        for index, sector in enumerate(sectors):
            groups.setdefault(sector, []).append(index)
        for indexes in groups.values():
            group_mean = statistics.fmean(target[index] for index in indexes)
            for index in indexes:
                target[index] -= group_mean
    sx, sy = statistics.fmean(sizes), statistics.fmean(target)
    variance = sum((value - sx) ** 2 for value in sizes)
    beta = sum((x - sx) * (y - sy) for x, y in zip(sizes, target)) / variance if variance else 0.0
    intercept = sy - beta * sx
    return [y - (intercept + beta * x) for x, y in zip(sizes, target)]


def preprocess_cross_sections(
    rows: list[dict[str, Any]], *, winsorize: str | None = None,
    standardize: str | None = None, neutralize: str | None = None,
) -> list[dict[str, Any]]:
    """All statistics are independently estimated per trade_date."""
    normalized_neutralize = NEUTRALIZE_ALIASES.get(neutralize) if neutralize else None
    if neutralize and normalized_neutralize is None:
        raise ValueError(f"unsupported neutralize method: {neutralize}")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["date"]), []).append(dict(row))
    output: list[dict[str, Any]] = []
    for trade_date in sorted(grouped):
        group = grouped[trade_date]
        valid = [row for row in group if finite(row.get("factor"))]
        values = [float(row["factor"]) for row in valid]
        if winsorize and values:
            values = _winsor(values, winsorize)
        if normalized_neutralize and values:
            sizes = [float(row.get("size_proxy", 0.0)) if finite(row.get("size_proxy")) else 0.0 for row in valid]
            sectors = [str(row.get("sector", "unknown")) for row in valid] if normalized_neutralize == "size_sector" else None
            values = _neutralize(values, sizes, sectors)
        if standardize and values:
            values = _standardize(values, standardize)
        by_identity = {id(row): value for row, value in zip(valid, values)}
        for row in group:
            if id(row) in by_identity:
                row["factor"] = by_identity[id(row)]
            output.append(row)
    return output


def _prepared_sections(observations: list[dict[str, Any]], min_cross_section: int) -> tuple[list[tuple[str, list[float], list[float], list[str]]], int, int]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        grouped.setdefault(str(row["date"]), []).append(row)
    sections: list[tuple[str, list[float], list[float], list[str]]] = []
    excluded = 0
    for trade_date in sorted(grouped):
        valid = [row for row in grouped[trade_date] if finite(row.get("factor")) and finite(row.get("fwd_return"))]
        if len(valid) < min_cross_section:
            excluded += 1
            continue
        factor_ranks = ranks([float(row["factor"]) for row in valid])
        return_ranks = ranks([float(row["fwd_return"]) for row in valid])
        sections.append((trade_date, factor_ranks, return_ranks, [str(row.get("symbol")) for row in valid]))
    return sections, excluded, len(grouped)


def _filter_null_sections(
    sections: list[tuple[str, list[float], list[float], list[str]]],
    excluded_symbols: set[str],
) -> list[tuple[str, list[float], list[float], list[str]]]:
    filtered: list[tuple[str, list[float], list[float], list[str]]] = []
    for trade_date, factor_ranks, return_ranks, symbols in sections:
        indexes = [index for index, symbol in enumerate(symbols) if symbol not in excluded_symbols]
        filtered.append((
            trade_date,
            ranks([factor_ranks[index] for index in indexes]),
            ranks([return_ranks[index] for index in indexes]),
            [symbols[index] for index in indexes],
        ))
    return filtered


def _circular_offsets(length: int) -> range:
    """Exclude k=0, one-step near-zero and k=T-1 near-T rotations."""
    return range(2, length - 1)


def _null_summary(
    sections: list[tuple[str, list[float], list[float], list[str]]],
    trials: int,
    seed: int,
    *,
    null_kind: str,
    excluded_symbols: set[str] | None = None,
) -> dict[str, Any]:
    if null_kind not in NULL_KINDS:
        raise ValueError(f"unsupported null kind: {null_kind}")
    real = [pearson(factor_ranks, return_ranks) for _, factor_ranks, return_ranks, _ in sections]
    real_values = [value for value in real if value is not None]
    null_sections = _filter_null_sections(sections, excluded_symbols or set())
    rng = random.Random(seed)
    trial_means: list[float] = []
    offset_symbols: list[str] = []
    offset_bounds: dict[str, list[int]] = {}
    offsets_by_trial: list[list[int]] = []

    if null_kind == "cross_section_shuffle":
        for _ in range(trials):
            trial_ics: list[float] = []
            for _, factor_ranks, return_ranks, _symbols in null_sections:
                shuffled = list(factor_ranks)
                rng.shuffle(shuffled)
                value = pearson(shuffled, return_ranks)
                if value is not None:
                    trial_ics.append(value)
            if trial_ics:
                trial_means.append(statistics.fmean(trial_ics))
    else:
        series: dict[str, list[tuple[int, float]]] = {}
        for section_index, (_trade_date, factor_ranks, _return_ranks, symbols) in enumerate(null_sections):
            for symbol, factor_rank in zip(symbols, factor_ranks):
                series.setdefault(symbol, []).append((section_index, factor_rank))
        offset_symbols = sorted(series)
        offset_bounds = {
            symbol: [2, len(series[symbol]) - 2]
            for symbol in offset_symbols
        }
        positions = {
            (section_index, symbol): position
            for symbol, values in series.items()
            for position, (section_index, _factor_rank) in enumerate(values)
        }
        values_by_symbol = {
            symbol: [factor_rank for _section_index, factor_rank in values]
            for symbol, values in series.items()
        }
        for _ in range(trials):
            offsets = {
                symbol: rng.choice(list(_circular_offsets(len(values_by_symbol[symbol]))))
                for symbol in offset_symbols
            }
            offsets_by_trial.append([offsets[symbol] for symbol in offset_symbols])
            trial_ics = []
            for section_index, (_trade_date, _factor_ranks, return_ranks, symbols) in enumerate(null_sections):
                rotated_values = []
                aligned_returns = []
                for symbol, return_rank in zip(symbols, return_ranks):
                    values = values_by_symbol[symbol]
                    position = positions[(section_index, symbol)]
                    rotated_values.append(values[(position - offsets[symbol]) % len(values)])
                    aligned_returns.append(return_rank)
                value = pearson(ranks(rotated_values), ranks(aligned_returns))
                if value is not None:
                    trial_ics.append(value)
            if trial_ics:
                trial_means.append(statistics.fmean(trial_ics))

    random_mean = mean_or_none(trial_means)
    mean_real = mean_or_none(real_values)
    spread = statistics.stdev(trial_means) if len(trial_means) > 1 else None
    alpha_t = ((mean_real - random_mean) / spread
               if mean_real is not None and random_mean is not None and spread not in (None, 0) else None)
    return {
        "real_values": real_values,
        "random_ic_mean": random_mean,
        "random_ic_std": spread,
        "alpha_t": alpha_t,
        "null_audit": {
            "null_kind": null_kind,
            "seed": seed,
            "trials": trials,
            "offset_policy": (
                "not_applicable"
                if null_kind == "cross_section_shuffle"
                else "per_symbol_independent_k;2<=k<=T-2;exclude_0_1_and_T-1"
            ),
            "offset_symbols": offset_symbols,
            "offset_bounds": offset_bounds,
            "offsets_by_trial": offsets_by_trial,
            "excluded_symbols": sorted(excluded_symbols or set()),
        },
    }


def _autocorrelation(observations: list[dict[str, Any]], min_cross_section: int) -> float | None:
    grouped: dict[str, dict[str, float]] = {}
    for row in observations:
        if finite(row.get("factor")):
            grouped.setdefault(str(row["date"]), {})[str(row["symbol"])] = float(row["factor"])
    dates = sorted(grouped)
    correlations: list[float] = []
    for previous, current in zip(dates, dates[1:]):
        common = sorted(set(grouped[previous]) & set(grouped[current]))
        if len(common) < min_cross_section:
            continue
        value = spearman([grouped[previous][symbol] for symbol in common], [grouped[current][symbol] for symbol in common])
        if value is not None:
            correlations.append(value)
    return mean_or_none(correlations)


def _stats_for_sections(
    sections: list[tuple[str, list[float], list[float], list[str]]],
    trials: int,
    seed: int,
    *,
    null_kind: str,
    excluded_symbols: set[str],
) -> dict[str, Any]:
    summary = _null_summary(
        sections, trials, seed, null_kind=null_kind, excluded_symbols=excluded_symbols
    )
    real_ics = summary["real_values"]
    random_mean = summary["random_ic_mean"]
    alpha_t = summary["alpha_t"]
    mean_ic = mean_or_none(real_ics)
    ic_std = statistics.stdev(real_ics) if len(real_ics) > 1 else None
    t_stat = (mean_ic / (ic_std / math.sqrt(len(real_ics)))) if mean_ic is not None and ic_std not in (None, 0) else None
    p_value = math.erfc(abs(t_stat) / math.sqrt(2)) if t_stat is not None else None
    return {"mean_ic": mean_ic, "ic_std": ic_std, "icir": mean_ic / ic_std if mean_ic is not None and ic_std not in (None, 0) else None,
            "t_stat": t_stat, "p_value": p_value, "random_ic_mean": random_mean,
            "random_ic_std": summary["random_ic_std"], "alpha_t": alpha_t,
            "positive_ic_share": sum(value > 0 for value in real_ics) / len(real_ics) if real_ics else None,
            "section_count": len(real_ics), "null_audit": summary["null_audit"]}


def _null_exclusions_for_sections(
    sections: list[tuple[str, list[float], list[float], list[str]]],
    explicit_exclusions: set[str],
    null_kind: str,
) -> tuple[set[str], set[str]]:
    if null_kind != "circular_rotation":
        return set(explicit_exclusions), set()
    counts: dict[str, int] = {}
    for _trade_date, _factor_ranks, _return_ranks, symbols in sections:
        for symbol in symbols:
            if symbol not in explicit_exclusions:
                counts[symbol] = counts.get(symbol, 0) + 1
    short = {symbol for symbol, count in counts.items() if count < MIN_CIRCULAR_SERIES}
    return set(explicit_exclusions) | short, short


def _null_cross_section_valid(
    sections: list[tuple[str, list[float], list[float], list[str]]],
    excluded_symbols: set[str],
    min_cross_section: int,
) -> bool:
    return all(
        sum(symbol not in excluded_symbols for symbol in symbols) >= min_cross_section
        for _trade_date, _factor_ranks, _return_ranks, symbols in sections
    )


def evaluate_observations(
    observations: list[dict[str, Any]], *, null_trials: int = 100, seed: int = 42,
    min_cross_section: int = 30, min_dates: int = 40, train_frac: float = 0.7,
    section_stride: int = 1, null_kind: str = DEFAULT_NULL_KIND,
) -> dict[str, Any]:
    if (null_trials < 2 or min_cross_section < 2 or min_dates < 1 or section_stride < 1
            or not 0 < train_frac < 1 or null_kind not in NULL_KINDS):
        raise ValueError("invalid evaluation parameters")
    raw_sections, excluded, total = _prepared_sections(observations, min_cross_section)
    sections = raw_sections[::section_stride]
    exclusion_share = excluded / total if total else 1.0
    if exclusion_share > 0.30 or len(sections) < min_dates:
        null_stats: dict[str, Any] = {key: None for key in ("mean_ic", "ic_std", "icir", "t_stat", "p_value", "random_ic_mean", "random_ic_std", "alpha_t", "positive_ic_share", "rank_autocorr_1", "coverage", "monthly_ic")}
        null_stats.update({"section_count": len(sections), "effective_sections": len(sections),
                           "raw_section_count": len(raw_sections), "section_stride": section_stride,
                           "excluded_dates": excluded, "total_dates": total,
                           "null_kind": null_kind, "null_seed": seed, "null_trials": null_trials,
                           "null_excluded_symbols": [], "null_audit": None,
                           "train": {"mean_ic": None, "alpha_t": None}, "test": {"mean_ic": None, "alpha_t": None}})
        gaps: list[dict[str, Any]] = []
        if len(sections) < min_dates:
            gaps.append({"reason_code": "insufficient_effective_sections",
                         "gap": "effective_sections_below_min_dates",
                         "available": len(sections), "required": min_dates})
        if exclusion_share > 0.30:
            gaps.append({"reason_code": "excessive_cross_section_exclusions",
                         "gap": "excluded_dates_above_30_percent",
                         "excluded_dates": excluded, "total_dates": total})
        return {"status": "insufficient_data", **null_stats, "data_gaps": gaps}
    split = max(1, min(len(sections) - 1, int(len(sections) * train_frac)))
    explicit_exclusions = {
        str(row.get("symbol"))
        for row in observations
        if row.get("null_eligible") is False and row.get("symbol") is not None
    }
    subsets = {"overall": sections, "train": sections[:split], "test": sections[split:]}
    exclusions: dict[str, set[str]] = {}
    short_by_subset: dict[str, set[str]] = {}
    invalid_subsets: list[str] = []
    for name, subset in subsets.items():
        excluded_for_subset, short = _null_exclusions_for_sections(
            subset, explicit_exclusions, null_kind
        )
        exclusions[name] = excluded_for_subset
        short_by_subset[name] = short
        if not _null_cross_section_valid(subset, excluded_for_subset, min_cross_section):
            invalid_subsets.append(name)
    if invalid_subsets:
        null_stats = {key: None for key in ("mean_ic", "ic_std", "icir", "t_stat", "p_value", "random_ic_mean", "random_ic_std", "alpha_t", "positive_ic_share", "rank_autocorr_1", "coverage", "monthly_ic")}
        null_stats.update({
            "section_count": len(sections), "effective_sections": len(sections),
            "raw_section_count": len(raw_sections), "section_stride": section_stride,
            "excluded_dates": excluded, "total_dates": total,
            "null_kind": null_kind, "null_seed": seed, "null_trials": null_trials,
            "null_excluded_symbols": sorted(exclusions["overall"]), "null_audit": None,
            "train": {"mean_ic": None, "alpha_t": None}, "test": {"mean_ic": None, "alpha_t": None},
        })
        gaps = [{
            "reason_code": "insufficient_null_cross_section",
            "gap": "null_cross_section_below_min_after_symbol_exclusions",
            "invalid_subsets": invalid_subsets,
            "excluded_symbols": sorted(set().union(*exclusions.values())),
            "required": min_cross_section,
        }]
        if explicit_exclusions:
            gaps.append({
                "reason_code": "cross_source_panel_excluded_from_null",
                "gap": "cross_source_symbol_history_excluded_from_random_control",
                "symbols": sorted(explicit_exclusions),
            })
        if any(short_by_subset.values()):
            gaps.append({
                "reason_code": "circular_series_too_short",
                "gap": "symbol_series_too_short_for_non_degenerate_rotation",
                "symbols_by_subset": {key: sorted(value) for key, value in short_by_subset.items() if value},
            })
        return {"status": "insufficient_data", **null_stats, "data_gaps": gaps}

    overall = _stats_for_sections(
        sections, null_trials, seed, null_kind=null_kind, excluded_symbols=exclusions["overall"]
    )
    train = _stats_for_sections(
        sections[:split], null_trials, seed + 1,
        null_kind=null_kind, excluded_symbols=exclusions["train"],
    )
    test = _stats_for_sections(
        sections[split:], null_trials, seed + 2,
        null_kind=null_kind, excluded_symbols=exclusions["test"],
    )
    null_audit = {
        "overall": overall.pop("null_audit"),
        "train": train.pop("null_audit"),
        "test": test.pop("null_audit"),
    }
    monthly: dict[str, list[float]] = {}
    for trade_date, factor_ranks, return_ranks, _ in sections:
        value = pearson(factor_ranks, return_ranks)
        if value is not None:
            monthly.setdefault(trade_date[:7], []).append(value)
    valid_pairs = sum(len(section[1]) for section in sections)
    potential = sum(1 for row in observations if str(row.get("date")) in {section[0] for section in sections})
    data_gaps: list[dict[str, Any]] = []
    if explicit_exclusions:
        data_gaps.append({
            "reason_code": "cross_source_panel_excluded_from_null",
            "gap": "cross_source_symbol_history_excluded_from_random_control",
            "symbols": sorted(explicit_exclusions),
        })
    return {"status": "ok", **overall, "effective_sections": len(sections),
            "raw_section_count": len(raw_sections), "section_stride": section_stride,
            "excluded_dates": excluded, "total_dates": total,
            "null_kind": null_kind, "null_seed": seed, "null_trials": null_trials,
            "null_excluded_symbols": sorted(exclusions["overall"]), "null_audit": null_audit,
            "train": {"mean_ic": train["mean_ic"], "alpha_t": train["alpha_t"]},
            "test": {"mean_ic": test["mean_ic"], "alpha_t": test["alpha_t"]},
            "rank_autocorr_1": _autocorrelation(observations, min_cross_section),
            "coverage": valid_pairs / potential if potential else None,
            "monthly_ic": {month: statistics.fmean(values) for month, values in monthly.items()},
            "data_gaps": data_gaps}


def _rolling_mean(values: list[Any], end: int, length: int) -> float | None:
    window = values[end - length + 1:end + 1]
    return statistics.fmean(float(value) for value in window) if len(window) == length and all(finite(value) for value in window) else None


def compute_factor(rows: list[dict[str, Any]], name: str) -> dict[str, float | None]:
    if name not in FACTOR_METHODS:
        raise ValueError(f"unknown factor: {name}")
    ordered = sorted(rows, key=lambda row: str(row.get("date", "")))
    closes = [row.get("close") for row in ordered]
    lows = [row.get("low") for row in ordered]
    highs = [row.get("high") for row in ordered]
    turns = [row.get("turnover_rate") for row in ordered]
    amounts = [row.get("amount") for row in ordered]
    output: dict[str, float | None] = {}
    daily_returns: list[float | None] = [None]
    for index in range(1, len(closes)):
        daily_returns.append(float(closes[index]) / float(closes[index - 1]) - 1 if finite(closes[index]) and finite(closes[index - 1]) and float(closes[index - 1]) else None)
    for index, row in enumerate(ordered):
        value: float | None = None
        try:
            if name == "mom_20_1" and index >= 21 and finite(closes[index - 1]) and finite(closes[index - 21]):
                value = float(closes[index - 1]) / float(closes[index - 21]) - 1
            elif name == "mom_60_5" and index >= 65 and finite(closes[index - 5]) and finite(closes[index - 65]):
                value = float(closes[index - 5]) / float(closes[index - 65]) - 1
            elif name == "rev_5" and index >= 5 and finite(closes[index]) and finite(closes[index - 5]):
                value = -(float(closes[index]) / float(closes[index - 5]) - 1)
            elif name == "vol_20" and index >= 20:
                window = daily_returns[index - 19:index + 1]
                value = statistics.stdev(float(item) for item in window) if all(finite(item) for item in window) else None
            elif name == "turn_20":
                value = _rolling_mean(turns, index, 20)
            elif name == "turn_ratio_5_60":
                short, long = _rolling_mean(turns, index, 5), _rolling_mean(turns, index, 60)
                value = short / long if short is not None and long not in (None, 0) else None
            elif name == "range_pos_252" and index >= 251:
                low_window, high_window = lows[index - 251:index + 1], highs[index - 251:index + 1]
                if all(finite(item) for item in low_window + high_window) and finite(closes[index]):
                    low, high = min(map(float, low_window)), max(map(float, high_window))
                    value = (float(closes[index]) - low) / (high - low) if high != low else None
            elif name == "amihud_20" and index >= 20:
                ratios = [abs(float(daily_returns[pos])) / float(amounts[pos]) for pos in range(index - 19, index + 1)
                          if finite(daily_returns[pos]) and finite(amounts[pos]) and float(amounts[pos]) > 0]
                value = statistics.fmean(ratios) if len(ratios) == 20 else None
        except (ArithmeticError, statistics.StatisticsError, ValueError):
            value = None
        output[str(row.get("date"))] = value if value is None or math.isfinite(value) else None
    return output


def size_proxy(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    amounts = [row.get("amount") for row in sorted(rows, key=lambda row: str(row.get("date", "")))]
    ordered = sorted(rows, key=lambda row: str(row.get("date", "")))
    result: dict[str, float | None] = {}
    for index, row in enumerate(ordered):
        average = _rolling_mean(amounts, index, 20)
        result[str(row.get("date"))] = math.log(average) if average is not None and average > 0 else None
    return result


def load_panels(market: str, symbols: list[str], root: Path | None = None) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]], str | None]:
    target = root or factor_panel.cache_root()
    panels: dict[str, list[dict[str, Any]]] = {}
    gaps: list[dict[str, str]] = []
    adjust_basis: str | None = None
    for symbol in symbols:
        path = target / market / f"{symbol}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("rows")
            if not isinstance(rows, list) or not rows:
                raise ValueError("empty rows")
            declared_sources: set[str] = set()
            top_sources = payload.get("panel_sources")
            if isinstance(top_sources, list):
                declared_sources.update(
                    str(value).strip() for value in top_sources
                    if isinstance(value, str) and value.strip()
                )
            top_source = payload.get("panel_source") or payload.get("source")
            if isinstance(top_source, str) and top_source.strip():
                declared_sources.add(top_source.strip())
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("panel_source"), str) and row["panel_source"].strip():
                    declared_sources.add(row["panel_source"].strip())
            cross_source = len(declared_sources) > 1
            sole_source = next(iter(declared_sources)) if len(declared_sources) == 1 else None
            basis = payload.get("adjust_basis")
            panel_adjust = payload.get("adjust")
            panel_caveats = payload.get("pit_caveats")
            panels[symbol] = [
                {
                    **row,
                    "panel_source": row.get("panel_source") or sole_source,
                    "panel_adjust": row.get("panel_adjust") or panel_adjust,
                    "panel_adjust_basis": row.get("panel_adjust_basis") or basis,
                    "panel_pit_caveats": (
                        row.get("panel_pit_caveats")
                        if isinstance(row.get("panel_pit_caveats"), list)
                        else (panel_caveats if isinstance(panel_caveats, list) else [])
                    ),
                    "null_eligible": not cross_source,
                }
                for row in rows if isinstance(row, dict)
            ]
            adjust_basis = str(basis) if basis else adjust_basis
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            gaps.append({"symbol": symbol, "reason_code": "missing", "gap": f"panel_unavailable:{exc}"})
    return panels, gaps, adjust_basis


def run_engine(
    market: str, symbols: list[str], factors: list[str], horizons: list[int], *,
    null_trials: int = 100, seed: int = 42, train_frac: float = 0.7,
    min_cross_section: int = 30, min_dates: int = 40, panel_root: Path | None = None,
    winsorize: str | None = None, standardize: str | None = None,
    neutralize: str | None = None, null_kind: str = DEFAULT_NULL_KIND,
) -> dict[str, Any]:
    panels, gaps, adjust_basis = load_panels(market, symbols, panel_root)
    cross_source_symbols = sorted(
        symbol for symbol, rows in panels.items()
        if any(row.get("null_eligible") is False for row in rows)
    )
    null_eligible_by_symbol = {
        symbol: symbol not in set(cross_source_symbols) for symbol in panels
    }
    if cross_source_symbols:
        gaps.append({
            "reason_code": "cross_source_panel_excluded_from_null",
            "gap": "cross_source_symbol_history_excluded_from_random_control",
            "symbols": cross_source_symbols,
        })
    factor_output: dict[str, Any] = {}
    overall_status = "ok"
    all_dates = sorted({str(row.get("date")) for rows in panels.values() for row in rows})
    for factor_name in factors:
        values_by_symbol = {symbol: compute_factor(rows, factor_name) for symbol, rows in panels.items()}
        sizes_by_symbol = {symbol: size_proxy(rows) for symbol, rows in panels.items()}
        horizons_output: dict[str, Any] = {}
        for horizon in horizons:
            observations: list[dict[str, Any]] = []
            for symbol, rows in panels.items():
                forwards = forward_return(rows, horizon)
                for trade_date in {str(row.get("date")) for row in rows}:
                    observations.append({"date": trade_date, "symbol": symbol,
                                         "factor": values_by_symbol[symbol].get(trade_date),
                                         "size_proxy": sizes_by_symbol[symbol].get(trade_date),
                                         "fwd_return": forwards.get(trade_date),
                                         "null_eligible": null_eligible_by_symbol[symbol]})
            processed = preprocess_cross_sections(observations, winsorize=winsorize,
                                                  standardize=standardize, neutralize=neutralize)
            result = evaluate_observations(processed, null_trials=null_trials, seed=seed,
                                           min_cross_section=min_cross_section, min_dates=min_dates,
                                           train_frac=train_frac, section_stride=horizon,
                                           null_kind=null_kind)
            row_counts = {symbol: len(rows) for symbol, rows in panels.items()}
            ordered_counts = sorted(row_counts.values())
            available_rows_min = ordered_counts[0] if ordered_counts else 0
            available_rows_median = statistics.median(ordered_counts) if ordered_counts else 0
            shortest_symbols = sorted(symbol for symbol, count in row_counts.items()
                                      if count == available_rows_min)
            min_history_days = int(FACTOR_METHODS[factor_name]["min_history_days"])
            required_rows = max(
                min_history_days + horizon * (min_dates + 1),
                math.ceil((min_history_days + horizon) / 0.30),
            )
            if result["status"] != "ok" and available_rows_min < required_rows:
                result.setdefault("data_gaps", []).append({
                    "reason_code": "insufficient_history_window",
                    "gap": "panel_window_too_short_for_factor",
                    "factor": factor_name,
                    "horizon": horizon,
                    "available_rows": available_rows_min,
                    "available_rows_min": available_rows_min,
                    "available_rows_median": available_rows_median,
                    "shortest_symbols": shortest_symbols,
                    "required_rows": required_rows,
                })
            if result["status"] != "ok":
                overall_status = "insufficient_data"
            horizons_output[str(horizon)] = result
        factor_output[factor_name] = {"horizons": horizons_output,
                                      "direction_hypothesis": FACTOR_METHODS[factor_name]["direction_hypothesis"],
                                      "calculation_ref": FACTOR_METHODS[factor_name]["formula"]}
    run_material = json.dumps({"market": market, "symbols": symbols, "factors": factors,
                               "horizons": horizons, "seed": seed, "null_kind": null_kind}, sort_keys=True)
    hash8 = hashlib.sha256(run_material.encode()).hexdigest()[:8]
    run_id = f"fr_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{hash8}"
    return envelope("factor_engine_run.v1", run_id=run_id, status=overall_status, market=market,
                    universe={"symbols": len(panels), "basis": "user_watchlist_survivorship_biased"},
                    panel={"adjust_basis": adjust_basis, "date_range": [all_dates[0], all_dates[-1]] if all_dates else [],
                           "excluded_symbols": len(gaps)},
                    n_factors_scanned=len(factors) * len(horizons),
                    multiple_testing_policy="fixed_alpha_t_threshold_3.5_disclosure_only_not_family_adjusted",
                    seed=seed, null_trials=null_trials, null_kind=null_kind,
                    factors=factor_output, data_gaps=gaps,
                    pit_caveats=["qfq_rewrites_history", "survivorship_current_constituents"])


def _factor_state(stats: dict[str, Any], direction: str) -> str | None:
    import factor_verdict
    return factor_verdict.classify(stats, direction)


def compare_null_evaluations(
    observations: list[dict[str, Any]], *, direction: str = "+", **kwargs: Any
) -> dict[str, Any]:
    """Run both nulls on one observation set and expose all three P1 gates."""
    common = dict(kwargs)
    common.pop("null_kind", None)
    shuffle = evaluate_observations(
        observations, null_kind="cross_section_shuffle", **common
    )
    circular = evaluate_observations(
        observations, null_kind="circular_rotation", **common
    )
    return _null_comparison_entry(shuffle, circular, direction=direction)


def _null_comparison_entry(
    shuffle: dict[str, Any], circular: dict[str, Any], *, direction: str,
) -> dict[str, Any]:
    comparable = shuffle.get("status") == "ok" and circular.get("status") == "ok"
    shuffle_std, circular_std = shuffle.get("random_ic_std"), circular.get("random_ic_std")
    shuffle_alpha, circular_alpha = shuffle.get("alpha_t"), circular.get("alpha_t")
    std_strictly_wider = bool(
        comparable and finite(shuffle_std) and finite(circular_std)
        and float(circular_std) > float(shuffle_std)
    )
    alpha_non_increase = bool(
        comparable and finite(shuffle_alpha) and finite(circular_alpha)
        and float(circular_alpha) <= float(shuffle_alpha) + 1e-12
    )
    old_state = _factor_state(shuffle, direction) if comparable else None
    new_state = _factor_state(circular, direction) if comparable else None
    return {
        "comparable": comparable,
        "cross_section_shuffle": {
            "random_ic_mean": shuffle.get("random_ic_mean"),
            "random_ic_std": shuffle_std,
            "alpha_t": shuffle_alpha,
            "state": old_state,
            "status": shuffle.get("status"),
        },
        "circular_rotation": {
            "random_ic_mean": circular.get("random_ic_mean"),
            "random_ic_std": circular_std,
            "alpha_t": circular_alpha,
            "state": new_state,
            "status": circular.get("status"),
        },
        "null_std_strictly_wider": std_strictly_wider,
        "alpha_t_non_increase": alpha_non_increase,
        "state_changed": comparable and old_state != new_state,
        "downgraded_from_confirmed_alive": (
            comparable and old_state == "confirmed_alive" and new_state != "confirmed_alive"
        ),
        "data_gaps": list(shuffle.get("data_gaps") or []) + list(circular.get("data_gaps") or []),
    }


def compare_engine_nulls(
    market: str, symbols: list[str], factors: list[str], horizons: list[int], **kwargs: Any
) -> dict[str, Any]:
    """Compare both registered nulls and recommend a default only after all gates."""
    common = dict(kwargs)
    common.pop("null_kind", None)
    shuffle = run_engine(
        market, symbols, factors, horizons, null_kind="cross_section_shuffle", **common
    )
    circular = run_engine(
        market, symbols, factors, horizons, null_kind="circular_rotation", **common
    )
    comparisons: list[dict[str, Any]] = []
    state_changes: list[dict[str, Any]] = []
    downgrades: list[dict[str, Any]] = []
    alpha_decreases: list[float] = []
    for factor_name in factors:
        direction = str(FACTOR_METHODS[factor_name]["direction_hypothesis"])
        for horizon in horizons:
            left = shuffle["factors"][factor_name]["horizons"][str(horizon)]
            right = circular["factors"][factor_name]["horizons"][str(horizon)]
            item = {
                "factor": factor_name,
                "horizon": horizon,
                **_null_comparison_entry(left, right, direction=direction),
            }
            comparisons.append(item)
            if item["comparable"] and finite(item["cross_section_shuffle"]["alpha_t"]) and finite(item["circular_rotation"]["alpha_t"]):
                alpha_decreases.append(
                    float(item["cross_section_shuffle"]["alpha_t"])
                    - float(item["circular_rotation"]["alpha_t"])
                )
            if item["state_changed"]:
                change = {
                    "factor": factor_name,
                    "horizon": horizon,
                    "before": item["cross_section_shuffle"]["state"],
                    "after": item["circular_rotation"]["state"],
                }
                state_changes.append(change)
                if item["downgraded_from_confirmed_alive"]:
                    downgrades.append(change)
    comparable = [item for item in comparisons if item["comparable"]]
    invariants_pass = bool(comparable) and all(
        item["null_std_strictly_wider"] and item["alpha_t_non_increase"]
        for item in comparable
    )
    median_alpha_decrease = statistics.median(alpha_decreases) if alpha_decreases else None
    falsifier_triggered = bool(
        comparable and not state_changes and median_alpha_decrease is not None
        and median_alpha_decrease < 0.3
    )
    supported = invariants_pass and bool(downgrades)
    if not comparable:
        status = "insufficient_data"
    elif not invariants_pass:
        status = "failed_invariants"
    else:
        status = "ok"
    comparison_material = json.dumps({
        "market": market, "symbols": symbols, "factors": factors, "horizons": horizons,
        "seed": common.get("seed", 42), "null_trials": common.get("null_trials", 100),
    }, sort_keys=True)
    comparison_id = (
        f"fnc_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_"
        f"{hashlib.sha256(comparison_material.encode()).hexdigest()[:8]}"
    )
    comparison_gaps: list[dict[str, Any]] = []
    required_cross_section = int(common.get("min_cross_section", 30))
    if len(symbols) < required_cross_section:
        comparison_gaps.append({
            "reason_code": "insufficient_universe",
            "gap": "cached_panel_universe_below_min_cross_section",
            "available_symbols": len(symbols),
            "required_symbols": required_cross_section,
        })
    if not comparable and not comparison_gaps:
        comparison_gaps.append({
            "reason_code": "no_comparable_factor_horizon",
            "gap": "both_nulls_must_produce_judgeable_statistics",
        })
    return envelope(
        "factor_null_comparison.v1", comparison_id=comparison_id, status=status,
        market=market, symbols=symbols, factors=factors, horizons=horizons,
        seed=common.get("seed", 42), null_trials=common.get("null_trials", 100),
        compared_count=len(comparable), comparison_count=len(comparisons),
        invariants={
            "circular_random_ic_std_strictly_wider_for_every_comparable_pair": (
                bool(comparable) and all(item["null_std_strictly_wider"] for item in comparable)
            ),
            "circular_alpha_t_never_increases": (
                bool(comparable) and all(item["alpha_t_non_increase"] for item in comparable)
            ),
        },
        comparisons=comparisons,
        state_changes=state_changes,
        downgrade_count=len(downgrades), downgrades=downgrades,
        median_alpha_t_decrease=median_alpha_decrease,
        real_comparison_success=bool(comparable),
        hypothesis_result=(
            "supported" if supported
            else ("falsified" if falsifier_triggered else ("pending_evidence" if not comparable else "not_supported"))
        ),
        falsifier_triggered=falsifier_triggered,
        default_null_before=DEFAULT_NULL_KIND,
        default_null_recommendation=("circular_rotation" if supported else "cross_section_shuffle"),
        data_gaps=comparison_gaps + list(shuffle.get("data_gaps") or []) + list(circular.get("data_gaps") or []),
    )


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
    observations: list[dict[str, Any]] = []
    rng = random.Random(17)
    for day in range(12):
        for symbol in range(10):
            value = symbol + rng.random() * 0.01
            observations.append({"date": f"2026-08-{day + 1:02d}", "symbol": str(symbol),
                                 "factor": value, "fwd_return": value + rng.random() * 0.01})
    result = evaluate_observations(observations, null_trials=60, seed=42,
                                   min_cross_section=5, min_dates=5)
    assert result["status"] == "ok" and result["mean_ic"] > 0.9 and result["alpha_t"] > 3.5
    circular = evaluate_observations(
        observations, null_trials=20, seed=42, min_cross_section=5,
        min_dates=5, null_kind="circular_rotation",
    )
    assert circular["status"] == "ok" and circular["null_audit"]["overall"]["offsets_by_trial"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "result.json"
        _atomic_save(path, envelope("factor_engine_self_test.v1", ok=True))
        assert json.loads(path.read_text())["ok"] is True


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
    parser = argparse.ArgumentParser(description=__doc__, epilog=f"--factors: {FACTORS_HELP}")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list-methods")
    listing.add_argument("--json", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("--market", required=True, choices=sorted(factor_panel.MARKETS))
    run.add_argument("--symbols", nargs="*", default=[])
    run.add_argument("--symbols-file")
    run.add_argument("--factors", default="all", help=FACTORS_HELP)
    run.add_argument("--horizons", default="1,5,10")
    run.add_argument("--null-trials", type=int, default=100)
    run.add_argument(
        "--null", choices=NULL_KINDS, default=DEFAULT_NULL_KIND,
        help="random-control null; default stays cross_section_shuffle until a real >=30-symbol comparison records a strict downgrade",
    )
    run.add_argument("--compare-nulls", action="store_true", help="run both nulls and emit invariant/state-change comparison")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--winsorize", choices=PREPROCESS_METHODS["winsorize"])
    run.add_argument("--standardize", choices=PREPROCESS_METHODS["standardize"])
    run.add_argument("--neutralize", choices=sorted(NEUTRALIZE_ALIASES))
    run.add_argument("--train-frac", type=float, default=0.7)
    run.add_argument("--min-cross-section", type=int, default=30)
    run.add_argument("--min-dates", type=int, default=40)
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
        if args.command == "list-methods":
            result = envelope("factor_engine_methods.v1", factors=FACTOR_METHODS,
                              preprocess=PREPROCESS_METHODS,
                              neutralize_aliases=NEUTRALIZE_ALIASES,
                              null_kinds=list(NULL_KINDS), default_null_kind=DEFAULT_NULL_KIND)
        else:
            factors = list(DEFAULT_FACTORS) if args.factors == "all" else [item for item in args.factors.split(",") if item]
            unknown = sorted(set(factors) - set(FACTOR_METHODS))
            if unknown:
                raise ValueError(f"unknown factors: {unknown}")
            horizons = [int(item) for item in args.horizons.split(",")]
            if any(value < 1 for value in horizons):
                raise ValueError("horizons must be >=1")
            run_kwargs = {
                "null_trials": args.null_trials, "seed": args.seed,
                "train_frac": args.train_frac, "min_cross_section": args.min_cross_section,
                "min_dates": args.min_dates, "winsorize": args.winsorize,
                "standardize": args.standardize, "neutralize": args.neutralize,
            }
            selected_symbols = _symbols(args)
            if args.compare_nulls:
                result = compare_engine_nulls(
                    args.market, selected_symbols, factors, horizons, **run_kwargs
                )
            else:
                result = run_engine(
                    args.market, selected_symbols, factors, horizons,
                    null_kind=args.null, **run_kwargs,
                )
            if args.save:
                root = Path(os.environ.get("FACTOR_RUN_DIR", str(DEFAULT_RUN_DIR))).expanduser()
                artifact_id = result.get("run_id") or result.get("comparison_id")
                if not artifact_id:
                    raise ValueError("factor output missing artifact id")
                path = root / f"{artifact_id}.json"
                _atomic_save(path, result)
                result["saved_path"] = str(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps(envelope("factor_engine_error.v1", ok=False, status="error",
                                  data_gaps=[{"reason_code": "error", "gap": str(exc)}]), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if getattr(args, "json", False) else None, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
