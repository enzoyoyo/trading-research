#!/usr/bin/env python3
"""Deterministic, stdlib-only microstructure signal candidates."""
from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal, TypedDict


InputFidelity = Literal["tick", "snapshot_diff"]


class EvidenceOutput(TypedDict):
    tick_refs: list[str]
    bar_refs: list[str]


class SignalOutput(TypedDict):
    fired: bool
    strength: float
    evidence: EvidenceOutput
    input_fidelity: InputFidelity


def _raw_template(fidelity: InputFidelity) -> SignalOutput:
    return {
        "fired": False,
        "strength": 0.0,
        "evidence": {"tick_refs": [], "bar_refs": []},
        "input_fidelity": fidelity,
    }


SIGNAL_SPECS: dict[str, dict[str, Any]] = {
    "order_wall": {
        "signal_id": "order_wall",
        "name_cn": "主力托压单",
        "name_en": "OrderWall",
        "category": "order_book_depth",
        "inputs": ["level2_5tick"],
        "computation_ref": "references/formulas/order_wall.md#v1",
        "params": [
            {"name": "side", "default": "bid", "range": ["bid", "ask"], "unit": "enum"},
            {"name": "net_add_threshold", "default": 2.0, "range": [1.0, 10.0], "unit": "x_median"},
            {"name": "lookback_snapshots", "default": 5, "range": [2, 60], "unit": "snapshots"},
            {"name": "min_abs_volume", "default": 10_000.0, "range": [0.0, 1_000_000_000.0], "unit": "shares"},
            {"name": "min_notional", "default": 1_000_000.0, "range": [0.0, 1_000_000_000_000.0], "unit": "CNY"},
            {"name": "opening_buffer_seconds", "default": 180.0, "range": [0.0, 3_600.0], "unit": "seconds"},
            {"name": "price_tick", "default": 0.01, "range": [0.0001, 1.0], "unit": "CNY"},
        ],
        "raw_output": _raw_template("tick"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
    "order_imbalance": {
        "signal_id": "order_imbalance",
        "name_cn": "买卖盘失衡",
        "name_en": "OrderImbalance",
        "category": "order_book_depth",
        "inputs": ["level2_5tick"],
        "computation_ref": "references/formulas/order_imbalance.md#v1",
        "params": [
            {"name": "side", "default": "bid", "range": ["bid", "ask"], "unit": "enum"},
            {"name": "ratio_threshold", "default": 3.0, "range": [1.0, 100.0], "unit": "ratio"},
            {"name": "min_total_volume", "default": 10_000.0, "range": [0.0, 1_000_000_000.0], "unit": "shares"},
        ],
        "raw_output": _raw_template("tick"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
    "ignition": {
        "signal_id": "ignition",
        "name_cn": "大单点火",
        "name_en": "Ignition",
        "category": "order_book_depth",
        "inputs": ["tick_by_tick"],
        "computation_ref": "references/formulas/ignition.md#v1",
        "params": [
            {"name": "volume_multiplier", "default": 3.0, "range": [1.0, 100.0], "unit": "x_median"},
            {"name": "lookback_intervals", "default": 5, "range": [2, 60], "unit": "intervals"},
            {"name": "min_trade_volume", "default": 10_000.0, "range": [0.0, 1_000_000_000.0], "unit": "shares"},
            {"name": "min_price_move_ticks", "default": 1, "range": [1, 100], "unit": "ticks"},
            {"name": "max_gap_ms", "default": 6_000.0, "range": [1.0, 60_000.0], "unit": "milliseconds"},
            {"name": "price_tick", "default": 0.01, "range": [0.0001, 1.0], "unit": "CNY"},
        ],
        "raw_output": _raw_template("snapshot_diff"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
    "spoofing": {
        "signal_id": "spoofing",
        "name_cn": "虚假撤单",
        "name_en": "Spoofing",
        "category": "order_book_depth",
        "inputs": ["tick_by_tick"],
        "computation_ref": "references/formulas/spoofing.md#v1",
        "params": [
            {"name": "side", "default": "bid", "range": ["bid", "ask"], "unit": "enum"},
            {"name": "large_order_multiplier", "default": 3.0, "range": [1.0, 100.0], "unit": "x_median"},
            {"name": "lookback_snapshots", "default": 5, "range": [1, 60], "unit": "snapshots"},
            {"name": "min_order_volume", "default": 10_000.0, "range": [0.0, 1_000_000_000.0], "unit": "shares"},
            {"name": "max_lifetime_snapshots", "default": 2, "range": [1, 20], "unit": "snapshots"},
            {"name": "cancel_fraction", "default": 0.8, "range": [0.5, 1.0], "unit": "fraction"},
            {"name": "max_trade_match_fraction", "default": 0.2, "range": [0.0, 0.5], "unit": "fraction"},
            {"name": "max_gap_ms", "default": 6_000.0, "range": [1.0, 60_000.0], "unit": "milliseconds"},
            {"name": "price_tick", "default": 0.01, "range": [0.0001, 1.0], "unit": "CNY"},
        ],
        "raw_output": _raw_template("snapshot_diff"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
    "wall_breaker": {
        "signal_id": "wall_breaker",
        "name_cn": "压单破墙",
        "name_en": "WallBreaker",
        "category": "order_book_depth",
        "inputs": ["tick_by_tick"],
        "computation_ref": "references/formulas/wall_breaker.md#v1",
        "params": [
            {"name": "wall_multiplier", "default": 3.0, "range": [1.0, 100.0], "unit": "x_median"},
            {"name": "lookback_snapshots", "default": 5, "range": [1, 60], "unit": "snapshots"},
            {"name": "min_wall_volume", "default": 10_000.0, "range": [0.0, 1_000_000_000.0], "unit": "shares"},
            {"name": "max_sweep_snapshots", "default": 3, "range": [2, 20], "unit": "snapshots"},
            {"name": "min_sweep_steps", "default": 2, "range": [1, 20], "unit": "transitions"},
            {"name": "break_fraction", "default": 0.8, "range": [0.5, 1.0], "unit": "fraction"},
            {"name": "min_execution_fraction", "default": 0.5, "range": [0.1, 1.0], "unit": "fraction"},
            {"name": "max_gap_ms", "default": 6_000.0, "range": [1.0, 60_000.0], "unit": "milliseconds"},
            {"name": "price_tick", "default": 0.01, "range": [0.0001, 1.0], "unit": "CNY"},
        ],
        "raw_output": _raw_template("snapshot_diff"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
    "limit_leak": {
        "signal_id": "limit_leak",
        "name_cn": "涨停开板漏水",
        "name_en": "LimitLeak",
        "category": "limit_up_game",
        "inputs": ["level2_5tick"],
        "computation_ref": "references/formulas/limit_leak.md#v1",
        "params": [
            {"name": "seal_drop_fraction", "default": 0.5, "range": [0.1, 1.0], "unit": "fraction"},
            {"name": "min_seal_volume", "default": 10_000.0, "range": [0.0, 1_000_000_000.0], "unit": "shares"},
            {"name": "max_gap_ms", "default": 6_000.0, "range": [1.0, 60_000.0], "unit": "milliseconds"},
            {"name": "price_tick", "default": 0.01, "range": [0.0001, 1.0], "unit": "CNY"},
        ],
        "raw_output": _raw_template("snapshot_diff"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
    "td_sequential": {
        "signal_id": "td_sequential",
        "name_cn": "九转序列",
        "name_en": "TDSequential",
        "category": "indicator",
        "inputs": ["ohlcv_1m"],
        "computation_ref": "references/formulas/td_sequential.md#v1",
        "params": [
            {"name": "direction", "default": "buy", "range": ["buy", "sell"], "unit": "enum"},
            {"name": "phase", "default": "setup", "range": ["setup", "countdown"], "unit": "enum"},
        ],
        "raw_output": _raw_template("tick"),
        "validation_posture": "train_only",
        "hypothesis_id": None,
    },
}


DETECTORS: dict[str, Any] = {}


def signal_schema(signal_id: str) -> dict[str, Any]:
    if signal_id not in SIGNAL_SPECS:
        raise ValueError(f"unknown signal_id: {signal_id}")
    return copy.deepcopy(SIGNAL_SPECS[signal_id])


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _field_number(record: Mapping[str, Any], field: str) -> float:
    if field not in record:
        raise ValueError(f"missing required field: {field}")
    return _finite_number(record[field], field)


def _optional_number(record: Mapping[str, Any], field: str) -> float | None:
    return _finite_number(record[field], field) if field in record and record[field] is not None else None


def _records(book_or_ticks: Any, containers: tuple[str, ...]) -> list[dict[str, Any]]:
    value = book_or_ticks
    if isinstance(value, Mapping):
        selected = False
        for key in containers:
            if key in value:
                value = value[key]
                selected = True
                break
        if not selected:
            value = [value]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("input must be a record sequence or a supported container")
    records: list[dict[str, Any]] = []
    for index, record in enumerate(value):
        if not isinstance(record, Mapping):
            raise ValueError(f"record {index} must be a mapping")
        records.append(dict(record))
    return records


def _record_ref(record: Mapping[str, Any], index: int, kind: Literal["tick", "bar"]) -> str:
    fields = (f"{kind}_ref", "ref")
    for field in fields:
        if field in record:
            value = record[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
            value = value.strip()
            return value
    raise ValueError(f"record {index} missing required {kind}_ref/ref")


def _ordered_refs(records: Sequence[Mapping[str, Any]], indexes: Sequence[int], kind: Literal["tick", "bar"]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for index in indexes:
        value = _record_ref(records[index], index, kind)
        if value in seen:
            raise ValueError(f"duplicate evidence ref: {value}")
        seen.add(value)
        output.append(value)
    return output


def _levels(record: Mapping[str, Any], side: Literal["bid", "ask"]) -> list[tuple[float, float]]:
    plural = f"{side}s"
    raw = record.get(plural)
    if raw is None:
        prices = record.get(f"{side}_prices")
        volumes = record.get(f"{side}_volumes")
        if prices is None and volumes is None:
            raise ValueError(f"missing required field: {plural}")
        if not isinstance(prices, Sequence) or isinstance(prices, (str, bytes)):
            raise ValueError(f"{side}_prices must be a sequence")
        if not isinstance(volumes, Sequence) or isinstance(volumes, (str, bytes)):
            raise ValueError(f"{side}_volumes must be a sequence")
        if len(prices) != len(volumes):
            raise ValueError(f"{side}_prices and {side}_volumes length mismatch")
        raw = list(zip(prices, volumes))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError(f"{plural} must be a sequence")
    if len(raw) > 5:
        raise ValueError(f"{plural} must contain at most five levels")
    output: list[tuple[float, float]] = []
    for index, level in enumerate(raw):
        if isinstance(level, Mapping):
            if "price" not in level:
                raise ValueError(f"{plural}[{index}] missing price")
            volume_field = next((key for key in ("volume", "qty", "size") if key in level), None)
            if volume_field is None:
                raise ValueError(f"{plural}[{index}] missing volume")
            price_value, volume_value = level["price"], level[volume_field]
        elif isinstance(level, Sequence) and not isinstance(level, (str, bytes)) and len(level) == 2:
            price_value, volume_value = level
        else:
            raise ValueError(f"{plural}[{index}] must be a price-volume pair")
        price = _finite_number(price_value, f"{plural}[{index}].price")
        volume = _finite_number(volume_value, f"{plural}[{index}].volume")
        if price <= 0 or volume < 0:
            raise ValueError(f"{plural}[{index}] requires price>0 and volume>=0")
        output.append((price, volume))
    prices_only = [price for price, _volume in output]
    if len(set(prices_only)) != len(prices_only):
        raise ValueError(f"{plural} contains duplicate prices")
    expected = sorted(prices_only, reverse=side == "bid")
    if prices_only != expected:
        raise ValueError(f"{plural} prices are not in book order")
    return output


def _price_ticks(value: Any, tick_size: Any) -> int:
    price = _finite_number(value, "price")
    tick = _finite_number(tick_size, "price_tick")
    if price <= 0 or tick <= 0:
        raise ValueError("price and price_tick must be positive")
    try:
        scaled = Decimal(str(price)) / Decimal(str(tick))
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError("invalid price or price_tick") from exc
    nearest = scaled.to_integral_value(rounding=ROUND_HALF_UP)
    if abs(scaled - nearest) > Decimal("1e-9"):
        raise ValueError("price is off the configured tick grid")
    return int(nearest)


def _level_map(record: Mapping[str, Any], side: Literal["bid", "ask"], tick_size: float) -> dict[int, tuple[float, float]]:
    output: dict[int, tuple[float, float]] = {}
    for price, volume in _levels(record, side):
        price_key = _price_ticks(price, tick_size)
        if price_key in output:
            previous_price, previous_volume = output[price_key]
            output[price_key] = (previous_price, previous_volume + volume)
        else:
            output[price_key] = (price, volume)
    return output


def _median_depth(records: Sequence[Mapping[str, Any]], side: Literal["bid", "ask"]) -> float:
    values = [volume for record in records for _price, volume in _levels(record, side) if volume > 0]
    return float(statistics.median(values)) if values else 0.0


def _validated_params(signal_id: str, overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    if overrides is not None and not isinstance(overrides, Mapping):
        raise ValueError("params must be a mapping")
    definitions = SIGNAL_SPECS[signal_id]["params"]
    allowed = {definition["name"] for definition in definitions}
    supplied = dict(overrides or {})
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise ValueError(f"unknown params for {signal_id}: {unknown}")
    output: dict[str, Any] = {}
    for definition in definitions:
        name = definition["name"]
        default = definition["default"]
        value = supplied.get(name, default)
        bounds = definition["range"]
        if isinstance(default, str):
            if not isinstance(value, str) or value not in bounds:
                raise ValueError(f"{name} must be one of {bounds}")
        elif isinstance(default, int) and not isinstance(default, bool):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < bounds[0] or value > bounds[1]:
                raise ValueError(f"{name} outside range {bounds}")
        else:
            number = _finite_number(value, name)
            if number < float(bounds[0]) or number > float(bounds[1]):
                raise ValueError(f"{name} outside range {bounds}")
            value = number
        output[name] = value
    return output


def _output(
    fired: bool,
    strength: float,
    fidelity: InputFidelity,
    *,
    tick_refs: Sequence[str] = (),
    bar_refs: Sequence[str] = (),
) -> SignalOutput:
    bounded = min(1.0, max(0.0, _finite_number(strength, "strength")))
    output: SignalOutput = {
        "fired": bool(fired),
        "strength": round(bounded, 12),
        "evidence": {"tick_refs": list(tick_refs), "bar_refs": list(bar_refs)},
        "input_fidelity": fidelity,
    }
    validate_signal_output(output)
    return output


def validate_signal_output(output: Mapping[str, Any]) -> bool:
    expected = {"fired", "strength", "evidence", "input_fidelity"}
    if set(output) != expected:
        raise ValueError(f"SignalOutput keys must be exactly {sorted(expected)}")
    if not isinstance(output["fired"], bool):
        raise ValueError("fired must be boolean")
    strength = _finite_number(output["strength"], "strength")
    if strength < 0 or strength > 1:
        raise ValueError("strength must be in [0,1]")
    evidence = output["evidence"]
    if not isinstance(evidence, Mapping) or set(evidence) != {"tick_refs", "bar_refs"}:
        raise ValueError("evidence must contain exactly tick_refs and bar_refs")
    for field in ("tick_refs", "bar_refs"):
        refs = evidence[field]
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError(f"{field} must be a list of non-empty strings")
    if output["input_fidelity"] not in ("tick", "snapshot_diff"):
        raise ValueError("invalid input_fidelity")
    if output["fired"] and not (evidence["tick_refs"] or evidence["bar_refs"]):
        raise ValueError("fired=true requires at least one evidence ref")
    return True


def _limit_locked(snapshot: Mapping[str, Any], tick_size: float) -> bool:
    if "is_limit_locked" in snapshot:
        value = snapshot["is_limit_locked"]
        if not isinstance(value, bool):
            raise ValueError("is_limit_locked must be boolean")
        return value
    last = _optional_number(snapshot, "last_price")
    if last is None:
        raise ValueError("missing required field: last_price")
    last_tick = _price_ticks(last, tick_size)
    upper = _optional_number(snapshot, "limit_up_price")
    lower = _optional_number(snapshot, "limit_down_price")
    if upper is None and lower is None:
        raise ValueError("limit filter requires is_limit_locked or a limit price")
    if upper is not None:
        upper_tick = _price_ticks(upper, tick_size)
        upper_queue = _level_map(snapshot, "bid", tick_size).get(upper_tick, (upper, 0.0))[1]
        if last_tick == upper_tick and upper_queue > 0:
            return True
    if lower is not None:
        lower_tick = _price_ticks(lower, tick_size)
        lower_queue = _level_map(snapshot, "ask", tick_size).get(lower_tick, (lower, 0.0))[1]
        if last_tick == lower_tick and lower_queue > 0:
            return True
    return False


def _cumulative_delta(start: Mapping[str, Any], end: Mapping[str, Any]) -> float:
    first = _field_number(start, "cumulative_volume")
    last = _field_number(end, "cumulative_volume")
    if last < first:
        raise ValueError("cumulative_volume must be non-decreasing")
    return last - first


def _validate_snapshot_context(records: Sequence[Mapping[str, Any]], max_gap_ms: float | None) -> None:
    if not records:
        return
    first_symbol = records[0].get("symbol")
    first_session = records[0].get("session_id")
    if not isinstance(first_symbol, str) or not first_symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    if not isinstance(first_session, str) or not first_session.strip():
        raise ValueError("session_id must be a non-empty string")
    previous_ts: float | None = None
    for index, record in enumerate(records):
        if record.get("symbol") != first_symbol:
            raise ValueError("symbol changed within snapshot sequence")
        if record.get("session_id") != first_session:
            raise ValueError("session_id changed within snapshot sequence")
        timestamp = _field_number(record, "ts_ms")
        if previous_ts is not None:
            gap = timestamp - previous_ts
            if gap <= 0:
                raise ValueError("ts_ms must be strictly increasing")
            if max_gap_ms is not None and gap > max_gap_ms:
                raise ValueError(f"snapshot gap exceeds max_gap_ms at record {index}")
        previous_ts = timestamp


def _validate_cumulative_series(records: Sequence[Mapping[str, Any]]) -> None:
    for index in range(1, len(records)):
        _cumulative_delta(records[index - 1], records[index])


def detect_order_wall(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("order_wall", params)
    records = _records(book_or_ticks, ("snapshots", "ticks"))
    lookback = config["lookback_snapshots"]
    if len(records) < lookback + 1:
        return _output(False, 0.0, "tick")
    _validate_snapshot_context(records, None)
    current_index = len(records) - 1
    previous_index = current_index - 1
    current = records[current_index]
    session_offset = _field_number(current, "session_offset_seconds")
    if session_offset < config["opening_buffer_seconds"] or _limit_locked(current, config["price_tick"]):
        return _output(False, 0.0, "tick")
    side = config["side"]
    history = records[current_index - lookback:current_index]
    baseline = _median_depth(history, side)
    previous_levels = _level_map(records[previous_index], side, config["price_tick"])
    current_levels = _level_map(current, side, config["price_tick"])
    best: tuple[float, float, int] | None = None
    for price_key, (price, volume) in current_levels.items():
        previous_volume = previous_levels.get(price_key, (price, 0.0))[1]
        net_add = volume - previous_volume
        median_gate = config["net_add_threshold"] * baseline
        if not (net_add > median_gate and net_add >= config["min_abs_volume"] and
                net_add * price >= config["min_notional"]):
            continue
        effective_gate = max(median_gate, config["min_abs_volume"], config["min_notional"] / price)
        strength = 1.0 if effective_gate == 0 else min(1.0, net_add / (2.0 * effective_gate))
        candidate = (strength, net_add, price_key)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        return _output(False, 0.0, "tick")
    refs = _ordered_refs(records, [previous_index, current_index], "tick")
    return _output(True, best[0], "tick", tick_refs=refs)


def detect_order_imbalance(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("order_imbalance", params)
    records = _records(book_or_ticks, ("snapshots", "ticks"))
    if not records:
        return _output(False, 0.0, "tick")
    current = records[-1]
    bid_total = sum(volume for _price, volume in _levels(current, "bid"))
    ask_total = sum(volume for _price, volume in _levels(current, "ask"))
    if bid_total <= 0 or ask_total <= 0 or bid_total + ask_total < config["min_total_volume"]:
        return _output(False, 0.0, "tick")
    numerator, denominator = (bid_total, ask_total) if config["side"] == "bid" else (ask_total, bid_total)
    ratio = numerator / denominator
    fired = ratio > config["ratio_threshold"]
    strength = min(1.0, ratio / (2.0 * config["ratio_threshold"]))
    refs = _ordered_refs(records, [len(records) - 1], "tick") if fired else []
    return _output(fired, strength, "tick", tick_refs=refs)


def detect_ignition(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("ignition", params)
    records = _records(book_or_ticks, ("snapshots", "ticks"))
    lookback = config["lookback_intervals"]
    if len(records) < lookback + 2:
        return _output(False, 0.0, "snapshot_diff")
    _validate_snapshot_context(records, config["max_gap_ms"])
    deltas = [_cumulative_delta(records[index - 1], records[index]) for index in range(1, len(records))]
    baseline = float(statistics.median(deltas[-(lookback + 1):-1]))
    current_delta = deltas[-1]
    threshold = max(config["min_trade_volume"], config["volume_multiplier"] * baseline)
    previous, current = records[-2], records[-1]
    previous_ticks = _price_ticks(_field_number(previous, "last_price"), config["price_tick"])
    current_ticks = _price_ticks(_field_number(current, "last_price"), config["price_tick"])
    asks = _level_map(previous, "ask", config["price_tick"])
    if not asks:
        return _output(False, 0.0, "snapshot_diff")
    previous_best_ask = min(asks)
    move_ticks = current_ticks - previous_ticks
    fired = (current_delta > threshold and move_ticks >= config["min_price_move_ticks"] and
             current_ticks >= previous_best_ask)
    strength = min(1.0, current_delta / (2.0 * threshold)) if threshold > 0 else (1.0 if fired else 0.0)
    refs = _ordered_refs(records, [len(records) - 2, len(records) - 1], "tick") if fired else []
    return _output(fired, strength, "snapshot_diff", tick_refs=refs)


def detect_spoofing(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("spoofing", params)
    records = _records(book_or_ticks, ("snapshots", "ticks"))
    if len(records) < 3:
        return _output(False, 0.0, "snapshot_diff")
    _validate_snapshot_context(records, config["max_gap_ms"])
    _validate_cumulative_series(records)
    side = config["side"]
    best: tuple[float, int, int, int] | None = None
    for appearance_index in range(1, len(records) - 1):
        baseline_start = max(0, appearance_index - config["lookback_snapshots"])
        baseline_records = records[baseline_start:appearance_index]
        if len(baseline_records) < config["lookback_snapshots"]:
            continue
        baseline = _median_depth(baseline_records, side)
        threshold = max(config["min_order_volume"], config["large_order_multiplier"] * baseline)
        previous_levels = _level_map(records[appearance_index - 1], side, config["price_tick"])
        appeared_levels = _level_map(records[appearance_index], side, config["price_tick"])
        for price_key, (price, appeared_volume) in appeared_levels.items():
            previous_volume = previous_levels.get(price_key, (price, 0.0))[1]
            added = appeared_volume - previous_volume
            if added <= threshold:
                continue
            last_index = min(len(records) - 1, appearance_index + config["max_lifetime_snapshots"])
            for removal_index in range(appearance_index + 1, last_index + 1):
                if removal_index != len(records) - 1:
                    continue
                removed_levels = _level_map(records[removal_index], side, config["price_tick"])
                remaining = removed_levels.get(price_key, (price, 0.0))[1]
                prior_levels = _level_map(records[removal_index - 1], side, config["price_tick"])
                prior_remaining = prior_levels.get(price_key, (price, 0.0))[1]
                if remaining >= prior_remaining:
                    continue
                removed = max(0.0, appeared_volume - remaining)
                cancelled = min(added, removed)
                if cancelled <= 0:
                    continue
                cancel_ratio = cancelled / added if added > 0 else 0.0
                traded = _cumulative_delta(records[appearance_index], records[removal_index])
                match_ratio = traded / cancelled
                current_qualified = (cancel_ratio >= config["cancel_fraction"] and
                                     match_ratio <= config["max_trade_match_fraction"])
                prior_removed = max(0.0, appeared_volume - prior_remaining)
                prior_cancelled = min(added, prior_removed)
                prior_traded = _cumulative_delta(records[appearance_index], records[removal_index - 1])
                prior_qualified = (prior_cancelled > 0 and
                                   prior_cancelled / added >= config["cancel_fraction"] and
                                   prior_traded / prior_cancelled <= config["max_trade_match_fraction"])
                if not current_qualified or prior_qualified:
                    continue
                large_strength = 1.0 if threshold == 0 else min(1.0, added / (2.0 * threshold))
                strength = min(1.0, (large_strength + cancel_ratio + (1.0 - match_ratio)) / 3.0)
                candidate = (strength, -appearance_index, -removal_index, price_key)
                if best is None or candidate > best:
                    best = candidate
    if best is None:
        return _output(False, 0.0, "snapshot_diff")
    appearance_index, removal_index = -best[1], -best[2]
    refs = _ordered_refs(records, [appearance_index - 1, appearance_index, removal_index], "tick")
    return _output(True, best[0], "snapshot_diff", tick_refs=refs)


def detect_wall_breaker(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("wall_breaker", params)
    if config["min_sweep_steps"] > config["max_sweep_snapshots"]:
        raise ValueError("min_sweep_steps cannot exceed max_sweep_snapshots")
    records = _records(book_or_ticks, ("snapshots", "ticks"))
    if len(records) < config["min_sweep_steps"] + 2:
        return _output(False, 0.0, "snapshot_diff")
    _validate_snapshot_context(records, config["max_gap_ms"])
    _validate_cumulative_series(records)
    best: tuple[float, int, int, int] | None = None
    for wall_index in range(1, len(records) - config["min_sweep_steps"]):
        baseline_start = max(0, wall_index - config["lookback_snapshots"])
        baseline_records = records[baseline_start:wall_index]
        if len(baseline_records) < config["lookback_snapshots"]:
            continue
        baseline = _median_depth(baseline_records, "ask")
        threshold = max(config["min_wall_volume"], config["wall_multiplier"] * baseline)
        wall_levels = _level_map(records[wall_index], "ask", config["price_tick"])
        for price_key, (_price, wall_volume) in wall_levels.items():
            if wall_volume <= threshold:
                continue
            last_index = min(len(records) - 1, wall_index + config["max_sweep_snapshots"])
            first_end = wall_index + config["min_sweep_steps"]
            for end_index in range(first_end, last_index + 1):
                if end_index != len(records) - 1:
                    continue
                quantities = [
                    _level_map(records[index], "ask", config["price_tick"]).get(price_key, (0.0, 0.0))[1]
                    for index in range(wall_index, end_index + 1)
                ]
                prices = [_price_ticks(_field_number(records[index], "last_price"), config["price_tick"])
                          for index in range(wall_index, end_index + 1)]
                interval_trades = [_cumulative_delta(records[index - 1], records[index])
                                   for index in range(wall_index + 1, end_index + 1)]
                removed_fraction = (wall_volume - quantities[-1]) / wall_volume
                executed_fraction = sum(interval_trades) / wall_volume
                current_qualified = (
                    not any(current > previous for previous, current in zip(quantities, quantities[1:])) and
                    not any(current < previous for previous, current in zip(prices, prices[1:])) and
                    sum(delta > 0 for delta in interval_trades) >= config["min_sweep_steps"] and
                    removed_fraction >= config["break_fraction"] and
                    executed_fraction >= config["min_execution_fraction"] and
                    prices[-1] > price_key
                )
                prior_quantities = quantities[:-1]
                prior_prices = prices[:-1]
                prior_trades = interval_trades[:-1]
                prior_removed_fraction = (wall_volume - prior_quantities[-1]) / wall_volume
                prior_executed_fraction = sum(prior_trades) / wall_volume
                prior_qualified = (
                    len(prior_trades) >= config["min_sweep_steps"] and
                    not any(current > previous for previous, current in zip(prior_quantities, prior_quantities[1:])) and
                    not any(current < previous for previous, current in zip(prior_prices, prior_prices[1:])) and
                    sum(delta > 0 for delta in prior_trades) >= config["min_sweep_steps"] and
                    prior_removed_fraction >= config["break_fraction"] and
                    prior_executed_fraction >= config["min_execution_fraction"] and
                    prior_prices[-1] > price_key
                )
                if not current_qualified or prior_qualified:
                    continue
                execution_strength = min(1.0, executed_fraction / (2.0 * config["min_execution_fraction"]))
                strength = min(1.0, (removed_fraction + execution_strength) / 2.0)
                candidate = (strength, -wall_index, -end_index, price_key)
                if best is None or candidate > best:
                    best = candidate
    if best is None:
        return _output(False, 0.0, "snapshot_diff")
    wall_index, end_index = -best[1], -best[2]
    refs = _ordered_refs(records, list(range(wall_index, end_index + 1)), "tick")
    return _output(True, best[0], "snapshot_diff", tick_refs=refs)


def _seal_volume(snapshot: Mapping[str, Any], limit_tick: int, tick_size: float) -> float:
    return sum(volume for price_tick, (_price, volume) in _level_map(snapshot, "bid", tick_size).items()
               if price_tick == limit_tick)


def detect_limit_leak(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("limit_leak", params)
    records = _records(book_or_ticks, ("snapshots", "ticks"))
    if len(records) < 2:
        return _output(False, 0.0, "snapshot_diff")
    _validate_snapshot_context(records, config["max_gap_ms"])
    previous, current = records[-2], records[-1]
    limit = _field_number(previous, "limit_up_price")
    current_limit = _optional_number(current, "limit_up_price")
    if current_limit is not None and _price_ticks(current_limit, config["price_tick"]) != _price_ticks(limit, config["price_tick"]):
        raise ValueError("limit_up_price changed between adjacent snapshots")
    limit_tick = _price_ticks(limit, config["price_tick"])
    previous_last = _price_ticks(_field_number(previous, "last_price"), config["price_tick"])
    current_last = _price_ticks(_field_number(current, "last_price"), config["price_tick"])
    previous_seal = _seal_volume(previous, limit_tick, config["price_tick"])
    if "is_limit_locked" in previous:
        if not isinstance(previous["is_limit_locked"], bool):
            raise ValueError("is_limit_locked must be boolean")
        was_locked = previous["is_limit_locked"]
    else:
        was_locked = previous_last == limit_tick and previous_seal > 0 and not _levels(previous, "ask")
    if not was_locked or previous_seal <= 0 or previous_seal < config["min_seal_volume"]:
        return _output(False, 0.0, "snapshot_diff")
    current_seal = _seal_volume(current, limit_tick, config["price_tick"])
    drop_fraction = max(0.0, previous_seal - current_seal) / previous_seal
    opened = current_last < limit_tick
    fired = opened or drop_fraction >= config["seal_drop_fraction"]
    strength = max(drop_fraction, 1.0 if opened else 0.0)
    refs = _ordered_refs(records, [len(records) - 2, len(records) - 1], "tick") if fired else []
    return _output(fired, strength, "snapshot_diff", tick_refs=refs)


def _bar_closed(bar: Mapping[str, Any], index: int) -> bool:
    if "closed" not in bar or not isinstance(bar["closed"], bool):
        raise ValueError(f"bar {index} closed must be boolean")
    return bar["closed"]


def _setup_qualifies(bars: Sequence[Mapping[str, Any]], index: int, direction: str) -> bool:
    current = _field_number(bars[index], "close")
    lagged = _field_number(bars[index - 4], "close")
    return current < lagged if direction == "buy" else current > lagged


def _setup_state(bars: Sequence[Mapping[str, Any]], direction: str) -> tuple[int, list[int]]:
    latest = len(bars) - 1
    count = 0
    refs: list[int] = []
    for index in range(4, len(bars)):
        usable = _bar_closed(bars[index], index) or index == latest
        if usable and _setup_qualifies(bars, index, direction):
            count += 1
            refs.append(index)
        else:
            count = 0
            refs = []
    return count, refs


def _completed_setups(bars: Sequence[Mapping[str, Any]], direction: str) -> list[int]:
    count = 0
    completed_run = False
    output: list[int] = []
    for index in range(4, len(bars)):
        if _bar_closed(bars[index], index) and _setup_qualifies(bars, index, direction):
            if not completed_run:
                count += 1
                if count == 9:
                    output.append(index)
                    completed_run = True
        else:
            count = 0
            completed_run = False
    return output


def _countdown_qualifies(bars: Sequence[Mapping[str, Any]], index: int, direction: str) -> bool:
    close = _field_number(bars[index], "close")
    if direction == "buy":
        return close <= _field_number(bars[index - 2], "low")
    return close >= _field_number(bars[index - 2], "high")


def detect_td_sequential(book_or_ticks: Any, params: Mapping[str, Any] | None) -> SignalOutput:
    config = _validated_params("td_sequential", params)
    bars = _records(book_or_ticks, ("bars", "ohlcv"))
    if len(bars) < 5:
        return _output(False, 0.0, "tick")
    direction = config["direction"]
    latest = len(bars) - 1
    if config["phase"] == "setup":
        count, indexes = _setup_state(bars, direction)
        if count > 9:
            return _output(False, 0.0, "tick")
        strength = min(1.0, count / 9.0)
        refs = _ordered_refs(bars, indexes[-9:], "bar") if indexes else []
        fired = count == 9 and _bar_closed(bars[latest], latest)
        return _output(fired, strength, "tick", bar_refs=refs)
    setup_ends = _completed_setups(bars, direction)
    if not setup_ends:
        return _output(False, 0.0, "tick")
    setup_end = setup_ends[-1]
    qualifying: list[int] = []
    target_index: int | None = None
    for index in range(setup_end + 1, len(bars)):
        usable = _bar_closed(bars[index], index) or index == latest
        if usable and _countdown_qualifies(bars, index, direction):
            qualifying.append(index)
            if len(qualifying) == 13:
                target_index = index
                break
    if target_index is not None and target_index != latest:
        return _output(False, 0.0, "tick")
    strength = min(1.0, len(qualifying) / 13.0)
    refs = _ordered_refs(bars, qualifying, "bar") if qualifying else []
    fired = target_index == latest and _bar_closed(bars[latest], latest)
    return _output(fired, strength, "tick", bar_refs=refs)


DETECTORS.update({
    "order_wall": detect_order_wall,
    "order_imbalance": detect_order_imbalance,
    "ignition": detect_ignition,
    "spoofing": detect_spoofing,
    "wall_breaker": detect_wall_breaker,
    "limit_leak": detect_limit_leak,
    "td_sequential": detect_td_sequential,
})


def self_test() -> None:
    def snapshot(ref: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]],
                 *, last: float = 10.0, cumulative: float = 0.0, offset: float = 300.0,
                 limit_up: float = 11.0) -> dict[str, Any]:
        suffix = ""
        for char in reversed(ref):
            if char.isdigit():
                suffix = char + suffix
            elif suffix:
                break
        sequence = int(suffix) if suffix else 0
        return {"tick_ref": ref, "bids": bids, "asks": asks, "last_price": last,
                "cumulative_volume": cumulative, "session_offset_seconds": offset,
                "limit_up_price": limit_up, "symbol": "600000", "session_id": "2026-08-28-AM",
                "ts_ms": sequence * 3_000}

    wall = [snapshot("w0", [(9.99, 100)], [(10.01, 100)]),
            snapshot("w1", [(9.99, 100)], [(10.01, 100)]),
            snapshot("w2", [(9.99, 301)], [(10.01, 100)])]
    imbalance = [snapshot("i0", [(9.99, 401)], [(10.01, 100)])]
    ignition = [snapshot("g0", [(9.99, 100)], [(10.01, 100)], cumulative=0),
                snapshot("g1", [(9.99, 100)], [(10.01, 100)], cumulative=10),
                snapshot("g2", [(9.99, 100)], [(10.01, 100)], cumulative=20),
                snapshot("g3", [(10.00, 100)], [(10.02, 100)], last=10.01, cumulative=41)]
    spoof = [snapshot("s0", [(9.99, 100)], [(10.01, 100)]),
             snapshot("s1", [(9.99, 100)], [(10.01, 100)]),
             snapshot("s2", [(9.99, 301)], [(10.01, 100)]),
             snapshot("s3", [(9.99, 100)], [(10.01, 100)], cumulative=0)]
    breaker = [snapshot("b0", [(9.99, 100)], [(10.01, 100)]),
               snapshot("b1", [(9.99, 100)], [(10.01, 201)], last=10.00, cumulative=0),
               snapshot("b2", [(10.00, 100)], [(10.01, 100)], last=10.01, cumulative=120),
               snapshot("b3", [(10.01, 100)], [(10.02, 100)], last=10.02, cumulative=240)]
    leak = [snapshot("l0", [(11.0, 1_000)], [], last=11.0, limit_up=11.0),
            snapshot("l1", [(11.0, 400)], [], last=11.0, limit_up=11.0)]
    bars = [{"bar_ref": f"t{index}", "close": 20.0 - index, "high": 20.5 - index,
             "low": 19.5 - index, "closed": True} for index in range(13)]
    samples = {
        "order_wall": detect_order_wall(wall, {"lookback_snapshots": 2, "min_abs_volume": 0,
                                                "min_notional": 0, "opening_buffer_seconds": 0}),
        "order_imbalance": detect_order_imbalance(imbalance, {"ratio_threshold": 3, "min_total_volume": 0}),
        "ignition": detect_ignition(ignition, {"lookback_intervals": 2, "volume_multiplier": 2,
                                                "min_trade_volume": 0}),
        "spoofing": detect_spoofing(spoof, {"lookback_snapshots": 2, "large_order_multiplier": 2,
                                             "min_order_volume": 0, "max_lifetime_snapshots": 1}),
        "wall_breaker": detect_wall_breaker(breaker, {"lookback_snapshots": 1, "wall_multiplier": 2,
                                                        "min_wall_volume": 0, "break_fraction": 0.5,
                                                        "min_execution_fraction": 0.5}),
        "limit_leak": detect_limit_leak(leak, {"min_seal_volume": 0}),
        "td_sequential": detect_td_sequential(bars, {"direction": "buy", "phase": "setup"}),
    }
    assert set(samples) == set(SIGNAL_SPECS) == set(DETECTORS)
    for signal_id, output in samples.items():
        assert validate_signal_output(output)
        assert output["fired"], signal_id
        schema = signal_schema(signal_id)
        assert schema["validation_posture"] == "train_only"
        assert schema["hypothesis_id"] is None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list-signals", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed"}))
        return 0
    args = build_parser().parse_args(args_list)
    if args.self_test:
        raise SystemExit("--self-test must be used alone")
    if args.list_signals:
        print(json.dumps({"signals": [signal_schema(signal_id) for signal_id in SIGNAL_SPECS]},
                         ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
