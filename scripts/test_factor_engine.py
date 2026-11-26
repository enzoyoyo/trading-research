#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import factor_engine as engine


class FactorEngineTests(unittest.TestCase):
    @staticmethod
    def signal_observations(days: int = 30, symbols: int = 20) -> list[dict]:
        rng = random.Random(9)
        rows = []
        for day in range(days):
            for symbol in range(symbols):
                forward = symbol + day * 0.01 + rng.random() * 0.02
                # Synthetic known-answer checks the engine, not investment alpha.
                rows.append({"date": f"2026-07-{day + 1:02d}", "symbol": str(symbol),
                             "factor": forward + rng.random() * 0.01, "fwd_return": forward})
        return rows

    def test_known_answer_signal_beats_random_control(self) -> None:
        result = engine.evaluate_observations(self.signal_observations(), null_trials=100,
                                              seed=42, min_cross_section=10, min_dates=10)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["mean_ic"], 0.95)
        self.assertGreater(result["alpha_t"], 3.5)
        self.assertGreater(result["train"]["alpha_t"], 3.5)
        self.assertGreater(result["test"]["alpha_t"], 3.5)

    @staticmethod
    def persistent_signal_panels(days: int = 180, symbols: int = 20) -> dict[str, list[dict]]:
        """Synthetic price panels with persistent per-symbol drift and real weak alpha."""
        rng = random.Random(2027)
        rho, beta = 0.95, 0.003
        latent = [rng.gauss(0.0, 1.0) for _ in range(symbols)]
        prices = [100.0] * symbols
        panels = {f"S{symbol:02d}": [] for symbol in range(symbols)}
        for day in range(days):
            for symbol in range(symbols):
                latent[symbol] = rho * latent[symbol] + (1 - rho * rho) ** 0.5 * rng.gauss(0.0, 1.0)
            for symbol in range(symbols):
                daily_return = beta * latent[symbol] + rng.gauss(0.0, 0.01)
                prices[symbol] *= 1.0 + daily_return
                panels[f"S{symbol:02d}"].append({
                    "date": f"D{day:03d}", "close": prices[symbol],
                    "low": prices[symbol] * 0.99, "high": prices[symbol] * 1.01,
                    "amount": 1_000_000.0 + symbol * 10_000,
                    "turnover_rate": 1.0 + symbol * 0.01,
                })
        return panels

    def test_circular_null_three_acceptance_gates_on_synthetic_panels(self) -> None:
        panels = self.persistent_signal_panels()
        with mock.patch("factor_engine.load_panels", return_value=(panels, [], "qfq")):
            result = engine.compare_engine_nulls(
                "US", list(panels), ["mom_20_1"], [1], null_trials=100, seed=42,
                min_cross_section=15, min_dates=40,
            )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["invariants"]["circular_random_ic_std_strictly_wider_for_every_comparable_pair"])
        self.assertTrue(result["invariants"]["circular_alpha_t_never_increases"])
        self.assertEqual(result["state_changes"], [{
            "factor": "mom_20_1", "horizon": 1,
            "before": "confirmed_alive", "after": "noise",
        }])
        self.assertEqual(result["default_null_recommendation"], "circular_rotation")

    def test_circular_offsets_are_independent_non_degenerate_and_persisted(self) -> None:
        rows = self.signal_observations(days=30, symbols=12)
        result = engine.evaluate_observations(
            rows, null_trials=30, seed=73, min_cross_section=10, min_dates=20,
            null_kind="circular_rotation",
        )
        audit = result["null_audit"]["overall"]
        self.assertEqual(audit["null_kind"], "circular_rotation")
        self.assertEqual(audit["seed"], 73)
        self.assertEqual(len(audit["offsets_by_trial"]), 30)
        self.assertTrue(any(len(set(offsets)) > 1 for offsets in audit["offsets_by_trial"]))
        for offsets in audit["offsets_by_trial"]:
            for symbol, offset in zip(audit["offset_symbols"], offsets):
                low, high = audit["offset_bounds"][symbol]
                self.assertGreaterEqual(offset, low)
                self.assertLessEqual(offset, high)

    def test_cross_source_symbol_exits_null_but_not_real_ic(self) -> None:
        rows = self.signal_observations(days=30, symbols=12)
        marked = [
            {**row, "null_eligible": row["symbol"] != "0"}
            for row in rows
        ]
        full = engine.evaluate_observations(
            rows, null_trials=30, seed=42, min_cross_section=10, min_dates=20,
            null_kind="circular_rotation",
        )
        excluded = engine.evaluate_observations(
            marked, null_trials=30, seed=42, min_cross_section=10, min_dates=20,
            null_kind="circular_rotation",
        )
        self.assertEqual(excluded["mean_ic"], full["mean_ic"])
        self.assertEqual(excluded["null_excluded_symbols"], ["0"])
        self.assertIn(
            "cross_source_symbol_history_excluded_from_random_control",
            {gap["gap"] for gap in excluded["data_gaps"]},
        )

    def test_load_panels_marks_cross_source_history_null_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            market = root / "US"
            market.mkdir()
            payload = {
                "schema_version": "factor_panel_symbol.v1",
                "panel_sources": ["akshare", "longbridge"],
                "adjust_basis": "mixed_fixture",
                "rows": [{"date": "2026-01-01", "close": 10.0}],
            }
            (market / "SPY.json").write_text(json.dumps(payload), encoding="utf-8")
            panels, gaps, _basis = engine.load_panels("US", ["SPY"], root)
        self.assertFalse(panels["SPY"][0]["null_eligible"])
        self.assertFalse(gaps)

    def test_pure_noise_is_not_significant(self) -> None:
        rng = random.Random(1234)
        rows = []
        for day in range(35):
            factor_order = list(range(24))
            return_order = list(range(24))
            rng.shuffle(factor_order)
            rng.shuffle(return_order)
            for symbol in range(24):
                rows.append({"date": f"D{day:03d}", "symbol": str(symbol),
                             "factor": factor_order[symbol], "fwd_return": return_order[symbol]})
        result = engine.evaluate_observations(rows, null_trials=120, seed=71,
                                              min_cross_section=10, min_dates=10)
        self.assertEqual(result["status"], "ok")
        self.assertLess(abs(result["alpha_t"]), 3.5)

    @staticmethod
    def overlapping_noise_observations(horizon: int) -> list[dict]:
        """Time-constant random ranks plus iid daily returns have no true alpha."""
        rng = random.Random(1)
        symbols, days = 30, 260
        factors = {str(symbol): rng.random() for symbol in range(symbols)}
        panels = {}
        for symbol in range(symbols):
            price = 100.0
            rows = []
            for day in range(days):
                rows.append({"date": f"D{day:03d}", "close": price})
                price *= 1.0 + rng.gauss(0.0, 0.01)
            panels[str(symbol)] = rows
        observations = []
        for symbol, rows in panels.items():
            forwards = engine.forward_return(rows, horizon)
            for row in rows:
                observations.append({"date": row["date"], "symbol": symbol,
                                     "factor": factors[symbol],
                                     "fwd_return": forwards[row["date"]]})
        return observations

    def test_overlapping_horizon_noise_uses_non_overlapping_sections(self) -> None:
        alpha_by_horizon = {}
        for horizon in (10, 20):
            result = engine.evaluate_observations(
                self.overlapping_noise_observations(horizon), null_trials=120, seed=71,
                min_cross_section=20, min_dates=10, section_stride=horizon,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["effective_sections"], result["section_count"])
            self.assertLess(abs(result["alpha_t"]), 3.5)
            alpha_by_horizon[horizon] = result["alpha_t"]
        self.assertEqual(set(alpha_by_horizon), {10, 20})

    def test_horizon_one_preserves_all_sections_and_effective_min_dates_is_enforced(self) -> None:
        rows = self.overlapping_noise_observations(1)
        h1 = engine.evaluate_observations(rows, null_trials=40, seed=71,
                                          min_cross_section=20, min_dates=40,
                                          section_stride=1)
        self.assertEqual(h1["effective_sections"], h1["raw_section_count"])
        sparse = engine.evaluate_observations(rows, null_trials=40, seed=71,
                                              min_cross_section=20, min_dates=40,
                                              section_stride=10)
        self.assertEqual(sparse["status"], "insufficient_data")
        self.assertLess(sparse["effective_sections"], 40)
        self.assertIn("effective_sections_below_min_dates",
                      {gap["gap"] for gap in sparse["data_gaps"]})
        self.assertTrue(all(isinstance(gap, dict) for gap in sparse["data_gaps"]))

    def test_forward_return_alignment_catches_lookahead_shift(self) -> None:
        rows = [{"date": f"2026-01-0{index + 1}", "close": close}
                for index, close in enumerate([10.0, 11.0, 22.0, 20.0, 25.0])]
        got = engine.forward_return(rows, 2)
        self.assertAlmostEqual(got["2026-01-01"], 1.2)
        self.assertAlmostEqual(got["2026-01-02"], 20.0 / 11.0 - 1.0)
        self.assertAlmostEqual(got["2026-01-03"], 25.0 / 22.0 - 1.0)
        self.assertIsNone(got["2026-01-04"])
        self.assertIsNone(got["2026-01-05"])
        with self.assertRaises(ValueError):
            engine.forward_return(rows, 0)

    def test_cross_section_gate_null_filter_and_fail_closed(self) -> None:
        rows = []
        for day in range(4):
            for symbol in range(3):
                factor = float(symbol) if day < 2 or symbol < 2 else None
                rows.append({"date": f"D{day}", "symbol": str(symbol),
                             "factor": factor, "fwd_return": float(symbol)})
        result = engine.evaluate_observations(rows, null_trials=20, seed=1,
                                              min_cross_section=3, min_dates=1)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual((result["excluded_dates"], result["total_dates"]), (2, 4))
        for field in ("mean_ic", "ic_std", "icir", "random_ic_mean", "alpha_t", "coverage"):
            self.assertIsNone(result[field])

    def test_standardization_is_per_cross_section_not_full_sample(self) -> None:
        first = [{"date": "D1", "symbol": str(i), "factor": value}
                 for i, value in enumerate([1.0, 2.0, 3.0])]
        second = [{"date": "D2", "symbol": str(i), "factor": value}
                  for i, value in enumerate([100.0, 200.0, 300.0])]
        together = engine.preprocess_cross_sections(first + second, standardize="zscore")
        separately = (engine.preprocess_cross_sections(first, standardize="zscore") +
                      engine.preprocess_cross_sections(second, standardize="zscore"))
        got = {(row["date"], row["symbol"]): row["factor"] for row in together}
        expected = {(row["date"], row["symbol"]): row["factor"] for row in separately}
        self.assertEqual(got, expected)
        self.assertAlmostEqual(got[("D1", "0")], got[("D2", "0")])

    def test_documented_neutralize_names_are_cli_compatible_aliases(self) -> None:
        rows = [{"date": "D1", "symbol": str(i), "factor": float(i),
                 "size_proxy": float(i * 2)} for i in range(6)]
        short = engine.preprocess_cross_sections(rows, neutralize="size")
        documented = engine.preprocess_cross_sections(rows, neutralize="ols_residual_size")
        self.assertEqual(short, documented)
        args = engine.build_parser().parse_args([
            "run", "--market", "A", "--symbols", "600000",
            "--neutralize", "ols_residual_size_sector",
        ])
        self.assertEqual(args.neutralize, "ols_residual_size_sector")

    def test_statistics_error_is_caught_by_compute_factor(self) -> None:
        rows = [{"date": f"D{day:03d}", "close": 100.0 + day} for day in range(25)]
        with mock.patch("factor_engine.statistics.stdev", side_effect=engine.statistics.StatisticsError("fixture")):
            values = engine.compute_factor(rows, "vol_20")
        self.assertTrue(all(value is None for value in values.values()))

    def test_run_counts_factor_horizon_tests_and_marks_short_window(self) -> None:
        panels = {f"S{i}": [{"date": f"D{day:03d}", "close": 100.0 + day,
                              "low": 99.0 + day, "high": 101.0 + day,
                              "amount": 1_000_000.0, "turnover_rate": 1.0}
                             for day in range(40)] for i in range(4)}
        panels["S0"] = panels["S0"][-35:]
        with mock.patch("factor_engine.load_panels", return_value=(panels, [], "qfq")), \
             mock.patch("factor_engine.evaluate_observations", wraps=engine.evaluate_observations) as evaluate:
            result = engine.run_engine("A", list(panels), ["mom_20_1", "range_pos_252"], [1, 5],
                                       null_trials=20, min_cross_section=3, min_dates=5)
        self.assertEqual(result["n_factors_scanned"], 4)
        self.assertEqual([call.kwargs["section_stride"] for call in evaluate.call_args_list], [1, 5, 1, 5])
        self.assertTrue(all(isinstance(gap, dict)
                            for factor in result["factors"].values()
                            for horizon in factor["horizons"].values()
                            for gap in horizon["data_gaps"]))
        for horizon in ("1", "5"):
            gaps = result["factors"]["range_pos_252"]["horizons"][horizon]["data_gaps"]
            window_gap = next(gap for gap in gaps if gap.get("gap") == "panel_window_too_short_for_factor")
            self.assertEqual(window_gap["available_rows_min"], 35)
            self.assertEqual(window_gap["available_rows_median"], 40)
            self.assertEqual(window_gap["shortest_symbols"], ["S0"])
        h1_gap = next(gap for gap in result["factors"]["range_pos_252"]["horizons"]["1"]["data_gaps"]
                      if gap.get("gap") == "panel_window_too_short_for_factor")
        self.assertEqual(h1_gap["required_rows"], 844)

    def test_default_800_calendar_day_panel_at_245_trading_days_per_year_has_h10_results(self) -> None:
        trading_rows = int(800 * 245 / 365)
        start = date(2024, 1, 2)
        panels = {}
        for symbol_index in range(30):
            rows = []
            for index in range(trading_rows):
                trade_date = start + timedelta(days=int(index * 365 / 245))
                trend = 0.035 + symbol_index * 0.0007
                close = 50.0 + trend * index + ((index + symbol_index) % 11) * 0.003
                rows.append({"date": trade_date.isoformat(), "close": close,
                             "low": close * (0.99 - symbol_index * 0.00001),
                             "high": close * (1.01 + symbol_index * 0.00001),
                             "amount": 1_000_000.0 + symbol_index * 10_000 + index * 100,
                             "turnover_rate": 0.5 + symbol_index * 0.02 + (index % 7) * 0.001})
            panels[f"S{symbol_index:02d}"] = rows
        args = engine.build_parser().parse_args([
            "run", "--market", "A", "--symbols", *panels,
            "--factors", "all", "--horizons", "1,5,10",
            "--neutralize", "ols_residual_size",
        ])
        selected = list(engine.DEFAULT_FACTORS) if args.factors == "all" else args.factors.split(",")
        horizons = [int(value) for value in args.horizons.split(",")]
        with mock.patch("factor_engine.load_panels", return_value=(panels, [], "qfq")):
            result = engine.run_engine(args.market, list(panels), selected, horizons,
                                       null_trials=2, min_cross_section=args.min_cross_section,
                                       min_dates=args.min_dates, neutralize=args.neutralize)
        self.assertNotIn("range_pos_252", engine.DEFAULT_FACTORS)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(row["horizons"]["10"]["status"] == "ok"
                            for row in result["factors"].values()))
        self.assertTrue(all(not any(gap.get("gap") == "panel_window_too_short_for_factor"
                                    for gap in row["horizons"]["10"]["data_gaps"])
                            for row in result["factors"].values()))

    def test_seed_determinism_and_different_seed_changes_null(self) -> None:
        rows = self.signal_observations(days=15, symbols=12)
        first = engine.evaluate_observations(rows, null_trials=40, seed=42,
                                             min_cross_section=6, min_dates=5)
        second = engine.evaluate_observations(rows, null_trials=40, seed=42,
                                              min_cross_section=6, min_dates=5)
        other = engine.evaluate_observations(rows, null_trials=40, seed=43,
                                             min_cross_section=6, min_dates=5)
        self.assertEqual(first, second)
        self.assertNotEqual(first["random_ic_mean"], other["random_ic_mean"])

    def test_registry_excludes_known_bad_full_sample_methods(self) -> None:
        registry = json.dumps({"factors": engine.FACTOR_METHODS,
                               "preprocess": engine.PREPROCESS_METHODS}).lower()
        for forbidden in ("ewma", "boxcox", "ransac", "randomforest", "gbdt", "krr"):
            self.assertNotIn(forbidden, registry)
        self.assertEqual(len(engine.FACTOR_METHODS), 8)
        self.assertTrue(all(method["cross_section_only"] for method in engine.FACTOR_METHODS.values()))

    def test_self_test_cli_exact_output_hermetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, str(Path(engine.__file__)), "--self-test"],
                                  capture_output=True, text=True, timeout=3,
                                  env={**os.environ, "HOME": tmp, "FACTOR_PANEL_DIR": tmp,
                                       "FACTOR_RUN_DIR": tmp})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), '{"ok": true, "self_test": "passed"}')

    def test_cli_help_explains_default_and_long_window_factors(self) -> None:
        proc = subprocess.run(["python3", str(Path(engine.__file__)), "--help"],
                              capture_output=True, text=True, timeout=3)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("default-enabled", proc.stdout)
        self.assertIn("range_pos_252", proc.stdout)
        args = engine.build_parser().parse_args([
            "run", "--market", "US", "--symbols", "SPY",
        ])
        self.assertEqual(args.null, "cross_section_shuffle")
        self.assertFalse(args.compare_nulls)


if __name__ == "__main__":
    unittest.main()
