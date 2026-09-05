#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import microstructure_signals as signals


def snapshot(
    ref: str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    last: float = 10.0,
    cumulative: float = 0.0,
    offset: float = 300.0,
    limit_up: float = 11.0,
) -> dict[str, Any]:
    suffix = ""
    for char in reversed(ref):
        if char.isdigit():
            suffix = char + suffix
        elif suffix:
            break
    sequence = int(suffix) if suffix else 0
    return {
        "tick_ref": ref,
        "bids": bids,
        "asks": asks,
        "last_price": last,
        "cumulative_volume": cumulative,
        "session_offset_seconds": offset,
        "limit_up_price": limit_up,
        "symbol": "600000",
        "session_id": "2026-08-28-AM",
        "ts_ms": sequence * 3_000,
    }


def setup_bars(count: int = 13, *, last_closed: bool = True) -> list[dict[str, Any]]:
    bars = []
    for index in range(count):
        close = 20.0 - index
        bars.append({
            "bar_ref": f"bar-{index:02d}",
            "open": close + 0.1,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1_000.0,
            "closed": True,
        })
    if bars:
        bars[-1]["closed"] = last_closed
    return bars


class MicrostructureAssertions:
    expected_fidelity = {
        "order_wall": "tick",
        "order_imbalance": "tick",
        "ignition": "snapshot_diff",
        "spoofing": "snapshot_diff",
        "wall_breaker": "snapshot_diff",
        "limit_leak": "snapshot_diff",
        "td_sequential": "tick",
    }

    def assert_output_contract(self, output: dict[str, Any]) -> None:
        self.assertEqual(set(output), {"fired", "strength", "evidence", "input_fidelity"})
        self.assertIs(type(output["fired"]), bool)
        self.assertTrue(math.isfinite(output["strength"]))
        self.assertGreaterEqual(output["strength"], 0.0)
        self.assertLessEqual(output["strength"], 1.0)
        self.assertEqual(set(output["evidence"]), {"tick_refs", "bar_refs"})
        self.assertTrue(all(isinstance(ref, str) and ref
                            for field in ("tick_refs", "bar_refs")
                            for ref in output["evidence"][field]))
        if output["fired"]:
            self.assertTrue(output["evidence"]["tick_refs"] or output["evidence"]["bar_refs"])
        json.dumps(output, allow_nan=False)
        self.assertTrue(signals.validate_signal_output(output))


class MicrostructureContractTests(MicrostructureAssertions, unittest.TestCase):
    def test_static_schemas_match_contract_a_v11(self) -> None:
        self.assertEqual(set(signals.SIGNAL_SPECS), set(self.expected_fidelity))
        self.assertEqual(set(signals.DETECTORS), set(self.expected_fidelity))
        expected_keys = {"signal_id", "name_cn", "name_en", "category", "inputs",
                         "computation_ref", "params", "raw_output",
                         "validation_posture", "hypothesis_id"}
        for signal_id, expected_fidelity in self.expected_fidelity.items():
            with self.subTest(signal_id=signal_id):
                schema = signals.signal_schema(signal_id)
                self.assertEqual(set(schema), expected_keys)
                self.assertEqual(schema["signal_id"], signal_id)
                self.assertEqual(schema["validation_posture"], "train_only")
                self.assertIsNone(schema["hypothesis_id"])
                self.assertEqual(schema["raw_output"]["input_fidelity"], expected_fidelity)
                self.assert_output_contract(schema["raw_output"])
                relative_path, anchor = schema["computation_ref"].split("#", 1)
                self.assertEqual(anchor, "v1")
                formula_path = SCRIPTS.parent / relative_path
                self.assertTrue(formula_path.is_file())
                formula_text = formula_path.read_text(encoding="utf-8")
                for param in schema["params"]:
                    self.assertEqual(set(param), {"name", "default", "range", "unit"})
                    self.assertIn(f"| {param['name']}", formula_text)

    def test_tick_by_tick_declarations_are_snapshot_diff(self) -> None:
        for signal_id in ("ignition", "spoofing", "wall_breaker"):
            schema = signals.SIGNAL_SPECS[signal_id]
            self.assertIn("tick_by_tick", schema["inputs"])
            self.assertEqual(schema["raw_output"]["input_fidelity"], "snapshot_diff")

    def test_snapshot_approximations_declare_method_errors_and_failure_conditions(self) -> None:
        for signal_id in ("ignition", "spoofing", "wall_breaker", "limit_leak"):
            relative_path = signals.SIGNAL_SPECS[signal_id]["computation_ref"].split("#", 1)[0]
            formula = (SCRIPTS.parent / relative_path).read_text(encoding="utf-8")
            for label in ("Approximation =", "ErrorSources =", "InvalidWhen =", "FidelityLock ="):
                self.assertIn(label, formula)

    def test_output_validator_rejects_fired_without_refs_and_extra_keys(self) -> None:
        missing_refs = {"fired": True, "strength": 1.0,
                        "evidence": {"tick_refs": [], "bar_refs": []},
                        "input_fidelity": "tick"}
        with self.assertRaises(ValueError):
            signals.validate_signal_output(missing_refs)
        extra = {**missing_refs, "state": "confirmed"}
        with self.assertRaises(ValueError):
            signals.validate_signal_output(extra)

    def test_fired_evidence_refs_must_exist_in_input(self) -> None:
        rows = OrderImbalanceTests.rows(400)
        del rows[0]["tick_ref"]
        with self.assertRaisesRegex(ValueError, "missing required tick_ref/ref"):
            signals.detect_order_imbalance(rows, OrderImbalanceTests.params)
        rows[0]["tick_ref"] = None
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            signals.detect_order_imbalance(rows, OrderImbalanceTests.params)


class OrderWallTests(MicrostructureAssertions, unittest.TestCase):
    params = {"side": "bid", "net_add_threshold": 2.0, "lookback_snapshots": 2,
              "min_abs_volume": 0, "min_notional": 0,
              "opening_buffer_seconds": 0, "price_tick": 0.01}

    @staticmethod
    def rows(current_volume: float, *, offset: float = 300.0,
             last: float = 10.0, limit_up: float = 11.0) -> list[dict[str, Any]]:
        return [snapshot("wall-0", [(9.99, 100)], [(10.01, 100)]),
                snapshot("wall-1", [(9.99, 100)], [(10.01, 100)]),
                snapshot("wall-2", [(9.99, current_volume)], [(10.01, 100)],
                         offset=offset, last=last, limit_up=limit_up)]

    def test_order_wall_fired_state(self) -> None:
        output = signals.detect_order_wall(self.rows(301), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], ["wall-1", "wall-2"])
        self.assert_output_contract(output)

    def test_order_wall_not_fired_state(self) -> None:
        output = signals.detect_order_wall(self.rows(250), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_order_wall_equality_boundary_is_not_fired(self) -> None:
        output = signals.detect_order_wall(self.rows(300), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_order_wall_opening_and_limit_filters(self) -> None:
        buffered = {**self.params, "opening_buffer_seconds": 180}
        self.assertFalse(signals.detect_order_wall(self.rows(301, offset=179), buffered)["fired"])
        locked = self.rows(301, last=11.0, limit_up=11.0)
        locked[-1]["is_limit_locked"] = True
        self.assertFalse(signals.detect_order_wall(locked, self.params)["fired"])

    def test_order_wall_requires_limit_filter_context(self) -> None:
        rows = self.rows(301)
        del rows[-1]["limit_up_price"]
        with self.assertRaisesRegex(ValueError, "limit filter requires"):
            signals.detect_order_wall(rows, self.params)


class OrderImbalanceTests(MicrostructureAssertions, unittest.TestCase):
    params = {"side": "bid", "ratio_threshold": 3.0, "min_total_volume": 0}

    @staticmethod
    def rows(bid_volume: float, ask_volume: float = 100.0) -> list[dict[str, Any]]:
        return [snapshot("imbalance-0", [(9.99, bid_volume)], [(10.01, ask_volume)])]

    def test_order_imbalance_fired_state(self) -> None:
        output = signals.detect_order_imbalance(self.rows(400), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], ["imbalance-0"])
        self.assert_output_contract(output)

    def test_order_imbalance_not_fired_state(self) -> None:
        output = signals.detect_order_imbalance(self.rows(200), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_order_imbalance_equality_boundary_is_not_fired(self) -> None:
        output = signals.detect_order_imbalance(self.rows(300), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_order_imbalance_zero_denominator_fails_closed(self) -> None:
        output = signals.detect_order_imbalance(self.rows(400, 0), self.params)
        self.assertFalse(output["fired"])
        self.assertTrue(math.isfinite(output["strength"]))


class IgnitionTests(MicrostructureAssertions, unittest.TestCase):
    params = {"volume_multiplier": 1.0, "lookback_intervals": 2,
              "min_trade_volume": 400.0, "min_price_move_ticks": 1,
              "price_tick": 0.01}

    @staticmethod
    def rows(current_delta: float, current_price: float = 10.01) -> list[dict[str, Any]]:
        return [snapshot("ignition-0", [(9.99, 100)], [(10.01, 100)], cumulative=0),
                snapshot("ignition-1", [(9.99, 100)], [(10.01, 100)], cumulative=100),
                snapshot("ignition-2", [(9.99, 100)], [(10.01, 100)], last=10.0, cumulative=200),
                snapshot("ignition-3", [(10.0, 100)], [(10.02, 100)], last=current_price,
                         cumulative=200 + current_delta)]

    def test_ignition_fired_state(self) -> None:
        output = signals.detect_ignition(self.rows(401), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], ["ignition-2", "ignition-3"])
        self.assertEqual(output["input_fidelity"], "snapshot_diff")
        self.assert_output_contract(output)

    def test_ignition_not_fired_state(self) -> None:
        output = signals.detect_ignition(self.rows(401, current_price=10.0), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_ignition_volume_equality_boundary_is_not_fired(self) -> None:
        output = signals.detect_ignition(self.rows(400), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)


class SpoofingTests(MicrostructureAssertions, unittest.TestCase):
    params = {"side": "bid", "large_order_multiplier": 2.0,
              "lookback_snapshots": 2, "min_order_volume": 0,
              "max_lifetime_snapshots": 1, "cancel_fraction": 0.75,
              "max_trade_match_fraction": 0.25, "price_tick": 0.01}

    @staticmethod
    def rows(added: float, traded: float = 0.0, *, side: str = "bid",
             crossed: bool = False) -> list[dict[str, Any]]:
        if side == "bid":
            before_bids, before_asks = [(9.99, 100)], [(10.01, 100)]
            appeared_bids, appeared_asks = [(9.99, 100 + added)], [(10.01, 100)]
            removed_bids, removed_asks = [(9.99, 100)], [(10.01, 100)]
        else:
            before_bids, before_asks = [(9.99, 100)], [(10.01, 100)]
            appeared_bids, appeared_asks = [(9.99, 100)], [(10.01, 100 + added)]
            removed_bids, removed_asks = [(9.99, 100)], []
        return [snapshot("spoof-0", before_bids, before_asks, last=10.0, cumulative=0),
                snapshot("spoof-1", before_bids, before_asks, last=10.0, cumulative=0),
                snapshot("spoof-2", appeared_bids, appeared_asks, last=10.0, cumulative=0),
                snapshot("spoof-3", removed_bids, removed_asks,
                         last=10.02 if crossed else 10.0, cumulative=traded)]

    def test_spoofing_fired_state_with_inclusive_trade_match_boundary(self) -> None:
        output = signals.detect_spoofing(self.rows(201, traded=50.25), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], ["spoof-1", "spoof-2", "spoof-3"])
        self.assert_output_contract(output)

    def test_spoofing_not_fired_when_disappearance_is_explained_by_trades(self) -> None:
        output = signals.detect_spoofing(self.rows(201, traded=201), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_spoofing_large_order_equality_boundary_is_not_fired(self) -> None:
        output = signals.detect_spoofing(self.rows(200), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_spoofing_does_not_repeat_a_historical_removal(self) -> None:
        rows = self.rows(201)
        rows.append(snapshot("spoof-4", [(9.99, 100)], [(10.01, 100)], cumulative=0))
        output = signals.detect_spoofing(rows, self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_spoofing_does_not_repeat_after_a_gradual_threshold_crossing(self) -> None:
        params = {**self.params, "max_lifetime_snapshots": 2}
        rows = self.rows(201)
        rows[-1]["bids"] = [(9.99, 140)]
        first = signals.detect_spoofing(rows, params)
        self.assertTrue(first["fired"])
        rows.append(snapshot("spoof-4", [(9.99, 100)], [(10.01, 100)], cumulative=0))
        repeated = signals.detect_spoofing(rows, params)
        self.assertFalse(repeated["fired"])

    def test_spoofing_uses_cancelled_candidate_volume_for_trade_match(self) -> None:
        baseline = [(9.99, 10_000), (9.98, 100), (9.97, 100), (9.96, 100), (9.95, 100)]
        appeared = [(9.99, 10_201), (9.98, 100), (9.97, 100), (9.96, 100), (9.95, 100)]
        rows = [snapshot("match-0", baseline, [(10.01, 100)], cumulative=0),
                snapshot("match-1", baseline, [(10.01, 100)], cumulative=0),
                snapshot("match-2", appeared, [(10.01, 100)], cumulative=0),
                snapshot("match-3", [], [(10.01, 100)], cumulative=201)]
        output = signals.detect_spoofing(rows, self.params)
        self.assertFalse(output["fired"])

    def test_spoofing_rejects_internal_cumulative_volume_reset(self) -> None:
        params = {**self.params, "max_lifetime_snapshots": 2}
        rows = self.rows(201)
        rows[-1]["bids"] = [(9.99, 250)]
        rows[-1]["cumulative_volume"] = 100
        rows.append(snapshot("spoof-4", [(9.99, 100)], [(10.01, 100)], cumulative=50))
        with self.assertRaisesRegex(ValueError, "non-decreasing"):
            signals.detect_spoofing(rows, params)


class WallBreakerTests(MicrostructureAssertions, unittest.TestCase):
    params = {"wall_multiplier": 2.0, "lookback_snapshots": 1,
              "min_wall_volume": 0, "max_sweep_snapshots": 3,
              "min_sweep_steps": 2, "break_fraction": 0.5,
              "min_execution_fraction": 0.5, "price_tick": 0.01}

    @staticmethod
    def rows(*, traded: float, final_price: float) -> list[dict[str, Any]]:
        return [snapshot("breaker-0", [(9.99, 100)], [(10.01, 100)], last=10.0, cumulative=0),
                snapshot("breaker-1", [(9.99, 100)], [(10.01, 201)], last=10.0, cumulative=0),
                snapshot("breaker-2", [(10.0, 100)], [(10.01, 100)], last=10.01,
                         cumulative=traded / 2),
                snapshot("breaker-3", [(10.01, 100)], [(10.02, 100)], last=final_price,
                         cumulative=traded)]

    def test_wall_breaker_fired_state(self) -> None:
        output = signals.detect_wall_breaker(self.rows(traded=201, final_price=10.02), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], ["breaker-1", "breaker-2", "breaker-3"])
        self.assert_output_contract(output)

    def test_wall_breaker_not_fired_state(self) -> None:
        output = signals.detect_wall_breaker(self.rows(traded=50, final_price=10.02), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_wall_breaker_price_equality_boundary_is_not_fired(self) -> None:
        output = signals.detect_wall_breaker(self.rows(traded=201, final_price=10.01), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_wall_breaker_requires_positive_trades_across_minimum_sweep_steps(self) -> None:
        rows = self.rows(traded=201, final_price=10.02)
        rows[2]["cumulative_volume"] = 0
        output = signals.detect_wall_breaker(rows, self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_wall_breaker_does_not_repeat_a_historical_break(self) -> None:
        rows = self.rows(traded=201, final_price=10.02)
        rows.append(snapshot("breaker-4", [(10.01, 100)], [(10.02, 100)],
                             last=10.02, cumulative=201))
        output = signals.detect_wall_breaker(rows, self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_wall_breaker_does_not_repeat_after_gradual_break_threshold(self) -> None:
        rows = self.rows(traded=201, final_price=10.02)
        rows[-1]["asks"] = [(10.01, 80), (10.02, 100)]
        first = signals.detect_wall_breaker(rows, self.params)
        self.assertTrue(first["fired"])
        rows.append(snapshot("breaker-4", [(10.01, 100)], [(10.02, 100)],
                             last=10.03, cumulative=250))
        repeated = signals.detect_wall_breaker(rows, self.params)
        self.assertFalse(repeated["fired"])

    def test_spoofing_and_wall_breaker_are_mutually_discriminating(self) -> None:
        spoof_params = {**SpoofingTests.params, "side": "ask"}
        breaker_params = {**self.params, "min_sweep_steps": 1}
        low_trade = SpoofingTests.rows(201, traded=0, side="ask", crossed=False)
        high_trade = SpoofingTests.rows(201, traded=201, side="ask", crossed=True)
        self.assertTrue(signals.detect_spoofing(low_trade, spoof_params)["fired"])
        self.assertFalse(signals.detect_wall_breaker(low_trade, breaker_params)["fired"])
        self.assertFalse(signals.detect_spoofing(high_trade, spoof_params)["fired"])
        self.assertTrue(signals.detect_wall_breaker(high_trade, breaker_params)["fired"])


class LimitLeakTests(MicrostructureAssertions, unittest.TestCase):
    params = {"seal_drop_fraction": 0.5, "min_seal_volume": 0, "price_tick": 0.01}

    @staticmethod
    def rows(current_seal: float, *, current_price: float = 11.0) -> list[dict[str, Any]]:
        return [snapshot("leak-0", [(11.0, 1_024)], [], last=11.0, limit_up=11.0),
                snapshot("leak-1", [(11.0, current_seal)], [], last=current_price, limit_up=11.0)]

    def test_limit_leak_fired_state_on_open(self) -> None:
        output = signals.detect_limit_leak(self.rows(900, current_price=10.99), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], ["leak-0", "leak-1"])
        self.assert_output_contract(output)

    def test_limit_leak_not_fired_state(self) -> None:
        output = signals.detect_limit_leak(self.rows(768), self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_limit_leak_drop_equality_boundary_is_fired(self) -> None:
        output = signals.detect_limit_leak(self.rows(512), self.params)
        self.assertTrue(output["fired"])
        self.assert_output_contract(output)

    def test_limit_leak_zero_seal_with_zero_minimum_fails_closed(self) -> None:
        rows = self.rows(0)
        rows[0]["bids"] = []
        output = signals.detect_limit_leak(rows, self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_limit_leak_requires_a_locked_previous_snapshot(self) -> None:
        rows = self.rows(512)
        rows[0]["asks"] = [(11.0, 500)]
        self.assertFalse(signals.detect_limit_leak(rows, self.params)["fired"])
        rows[0]["is_limit_locked"] = False
        rows[0]["asks"] = []
        self.assertFalse(signals.detect_limit_leak(rows, self.params)["fired"])


class TDSequentialTests(MicrostructureAssertions, unittest.TestCase):
    params = {"direction": "buy", "phase": "setup"}

    def test_td_setup_fired_state(self) -> None:
        output = signals.detect_td_sequential(setup_bars(13), self.params)
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["tick_refs"], [])
        self.assertEqual(len(output["evidence"]["bar_refs"]), 9)
        self.assert_output_contract(output)

    def test_td_setup_not_fired_state(self) -> None:
        output = signals.detect_td_sequential(setup_bars(12), self.params)
        self.assertFalse(output["fired"])
        self.assertLess(output["strength"], 1.0)
        self.assert_output_contract(output)

    def test_td_setup_strict_comparison_boundary_is_not_fired(self) -> None:
        bars = setup_bars(13)
        bars[-1]["close"] = bars[-5]["close"]
        output = signals.detect_td_sequential(bars, self.params)
        self.assertFalse(output["fired"])
        self.assert_output_contract(output)

    def test_td_setup_ghost_and_confirmed_share_refs_but_only_confirmed_fires(self) -> None:
        ghost = signals.detect_td_sequential(setup_bars(13, last_closed=False), self.params)
        confirmed = signals.detect_td_sequential(setup_bars(13, last_closed=True), self.params)
        self.assertFalse(ghost["fired"])
        self.assertTrue(confirmed["fired"])
        self.assertEqual(ghost["strength"], confirmed["strength"])
        self.assertEqual(ghost["evidence"], confirmed["evidence"])
        self.assertEqual(ghost["strength"], 1.0)

    def test_td_countdown_13_ghost_confirmed_and_no_repeat(self) -> None:
        params = {"direction": "buy", "phase": "countdown"}
        confirmed_bars = setup_bars(26, last_closed=True)
        ghost_bars = setup_bars(26, last_closed=False)
        confirmed = signals.detect_td_sequential(confirmed_bars, params)
        ghost = signals.detect_td_sequential(ghost_bars, params)
        repeated = signals.detect_td_sequential(setup_bars(27), params)
        self.assertTrue(confirmed["fired"])
        self.assertFalse(ghost["fired"])
        self.assertEqual(confirmed["strength"], 1.0)
        self.assertEqual(ghost["strength"], 1.0)
        self.assertEqual(confirmed["evidence"], ghost["evidence"])
        self.assertEqual(len(confirmed["evidence"]["bar_refs"]), 13)
        self.assertFalse(repeated["fired"])
        self.assertEqual(repeated["strength"], 0.0)

    def test_td_countdown_can_fire_for_a_later_reset_sequence(self) -> None:
        bars = setup_bars(26)
        for local_index in range(26):
            close = 100.0 - local_index
            bars.append({"bar_ref": f"second-{local_index:02d}", "open": close + 0.1,
                         "high": close + 0.5, "low": close - 0.5, "close": close,
                         "volume": 1_000.0, "closed": True})
        output = signals.detect_td_sequential(bars, {"direction": "buy", "phase": "countdown"})
        self.assertTrue(output["fired"])
        self.assertEqual(output["evidence"]["bar_refs"][0], "second-13")
        self.assertEqual(output["evidence"]["bar_refs"][-1], "second-25")


class DeterminismAndCliTests(MicrostructureAssertions, unittest.TestCase):
    def fired_cases(self) -> list[tuple[Any, list[dict[str, Any]], dict[str, Any]]]:
        return [
            (signals.detect_order_wall, OrderWallTests.rows(301), OrderWallTests.params),
            (signals.detect_order_imbalance, OrderImbalanceTests.rows(400), OrderImbalanceTests.params),
            (signals.detect_ignition, IgnitionTests.rows(401), IgnitionTests.params),
            (signals.detect_spoofing, SpoofingTests.rows(201), SpoofingTests.params),
            (signals.detect_wall_breaker, WallBreakerTests.rows(traded=201, final_price=10.02), WallBreakerTests.params),
            (signals.detect_limit_leak, LimitLeakTests.rows(512), LimitLeakTests.params),
            (signals.detect_td_sequential, setup_bars(13), TDSequentialTests.params),
        ]

    def test_all_detectors_are_deterministic_and_do_not_mutate_inputs(self) -> None:
        for detector, rows, params in self.fired_cases():
            with self.subTest(detector=detector.__name__):
                rows_before, params_before = copy.deepcopy(rows), copy.deepcopy(params)
                first = detector(rows, params)
                second = detector(rows, params)
                self.assertEqual(first, second)
                self.assertEqual(rows, rows_before)
                self.assertEqual(params, params_before)
                self.assertIsNot(first["evidence"], second["evidence"])
                self.assertTrue(first["fired"])
                self.assert_output_contract(first)

    def test_unknown_out_of_range_and_non_finite_params_fail_explicitly(self) -> None:
        with self.assertRaises(ValueError):
            signals.detect_order_imbalance(OrderImbalanceTests.rows(400), {"unknown": 1})
        with self.assertRaises(ValueError):
            signals.detect_order_imbalance(OrderImbalanceTests.rows(400), {"ratio_threshold": 0.5})
        with self.assertRaises(ValueError):
            signals.detect_order_imbalance(OrderImbalanceTests.rows(400), {"ratio_threshold": math.nan})

    def test_off_grid_price_fails_explicitly(self) -> None:
        rows = IgnitionTests.rows(401)
        rows[-1]["last_price"] = 10.005
        with self.assertRaisesRegex(ValueError, "off the configured tick grid"):
            signals.detect_ignition(rows, IgnitionTests.params)

    def test_cumulative_volume_regression_fails_explicitly(self) -> None:
        rows = IgnitionTests.rows(401)
        rows[-1]["cumulative_volume"] = 100
        with self.assertRaisesRegex(ValueError, "non-decreasing"):
            signals.detect_ignition(rows, IgnitionTests.params)

    def test_snapshot_diff_rejects_cross_symbol_session_and_large_gap(self) -> None:
        for field, value, pattern in (("symbol", "600001", "symbol changed"),
                                      ("session_id", "2026-08-28-PM", "session_id changed"),
                                      ("ts_ms", 20_000, "exceeds max_gap_ms")):
            with self.subTest(field=field):
                rows = IgnitionTests.rows(401)
                rows[-1][field] = value
                with self.assertRaisesRegex(ValueError, pattern):
                    signals.detect_ignition(rows, IgnitionTests.params)

    def test_missing_book_side_is_an_explicit_schema_error(self) -> None:
        rows = LimitLeakTests.rows(512)
        del rows[-1]["bids"]
        with self.assertRaisesRegex(ValueError, "missing required field: bids"):
            signals.detect_limit_leak(rows, LimitLeakTests.params)

    def test_no_wall_clock_random_or_execution_path_symbols(self) -> None:
        source = Path(signals.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue({"random", "time", "datetime"}.isdisjoint(imported))
        self.assertNotIn("ModuleSignal", source)
        self.assertNotIn("position_multiplier", source)
        self.assertNotIn("place_order", source)

    def test_self_test_cli_exact_output(self) -> None:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, str(Path(signals.__file__)), "--self-test"],
                              capture_output=True, text=True, timeout=3, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), '{"ok": true, "self_test": "passed"}')


if __name__ == "__main__":
    unittest.main()
