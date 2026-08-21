#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import factor_backtest as backtest


class FactorBacktestTests(unittest.TestCase):
    def test_fac_shift_zero_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "fac_shift must be >=1"):
            backtest.validate_config(bins=5, rebalance_days=5, fac_shift=0,
                                     fee_bps=3.0, slippage_bps=5.0, stamp_bps=5.0,
                                     stamp_direction="sell")

    def test_float_fee_configuration_is_valid(self) -> None:
        backtest.validate_config(bins=3, rebalance_days=2, fac_shift=1,
                                 fee_bps=3.25, slippage_bps=4.5, stamp_bps=5.0,
                                 stamp_direction="both")

    def test_weight_drift_and_turnover_known_answer(self) -> None:
        equal = {symbol: 1.0 / 3 for symbol in ("A", "B", "C")}
        returns = {"A": 0.10, "B": 0.0, "C": -0.10}
        drifted = backtest.drift_weights(equal, returns)
        self.assertAlmostEqual(drifted["A"], 1.1 / 3.0)
        self.assertAlmostEqual(drifted["B"], 1.0 / 3.0)
        self.assertAlmostEqual(drifted["C"], 0.9 / 3.0)
        expected = abs(1 / 3 - 1.1 / 3) + abs(1 / 3 - 1 / 3) + abs(1 / 3 - 0.9 / 3)
        self.assertAlmostEqual(backtest.portfolio_turnover(drifted, equal), expected)
        self.assertAlmostEqual(backtest.portfolio_turnover({}, equal), 1.0)

    def test_sell_stamp_is_strictly_less_than_both_sides(self) -> None:
        old = {"A": 0.6, "B": 0.4}
        new = {"A": 0.2, "C": 0.8}
        turnover = backtest.portfolio_turnover(old, new)
        _buy, sell = backtest.turnover_sides(old, new)
        sell_cost = sell * 5.0 / 10000.0
        both_cost = turnover * 5.0 / 10000.0
        self.assertGreater(sell_cost, 0.0)
        self.assertLess(sell_cost, both_cost)

    def test_walk_forward_split_uses_ls_returns_not_epoch_count(self) -> None:
        epochs = [{} for _ in range(40)]
        ls_returns = [float(index) for index in range(12)]
        train, test = backtest._time_split(ls_returns, 0.7)
        self.assertEqual((len(train), len(test)), (8, 4))
        self.assertNotEqual(len(train), int(len(epochs) * 0.7))
        self.assertEqual(train + test, ls_returns)

    def test_interruption_settles_last_price_and_counts_not_zero_fill(self) -> None:
        rows = [{"date": "2026-01-01", "close": 10.0},
                {"date": "2026-01-02", "close": 12.0}]
        value, interrupted, last = backtest.epoch_return(rows, "2026-01-01", "2026-01-04")
        self.assertAlmostEqual(value, 0.2)
        self.assertTrue(interrupted)
        self.assertEqual(last, "2026-01-02")
        missing, interrupted, last = backtest.epoch_return(rows, "2026-01-03", "2026-01-04")
        self.assertIsNone(missing)
        self.assertFalse(interrupted)
        self.assertIsNone(last)

    def test_null_filtered_before_bins_and_monotonicity_numeric(self) -> None:
        rows = [{"symbol": "null", "factor": None}] + [
            {"symbol": str(index), "factor": float(index)} for index in range(6)
        ]
        bins = backtest.assign_bins(rows, bins=3, min_cross_section=6)
        self.assertIsNotNone(bins)
        self.assertEqual([[row["symbol"] for row in bucket] for bucket in bins],
                         [["0", "1"], ["2", "3"], ["4", "5"]])
        self.assertAlmostEqual(backtest.engine.spearman([1, 2, 3], [0.1, 0.2, 0.4]), 1.0)

    def test_attribution_positive_factor_premium(self) -> None:
        rows = []
        for index in range(20):
            factor = float(index - 10)
            size = float((index * 7) % 11 - 5)
            rows.append({"factor": factor, "size": size,
                         "fwd_return": 1.0 + 0.5 * size + 2.0 * factor})
        result = backtest.cross_section_attribution(rows)
        self.assertEqual(result["n"], 20)
        self.assertGreater(result["beta_factor"], 0)
        self.assertGreater(result["beta_size"], 0)

    def test_run_backtest_monotonicity_and_interruption_counter(self) -> None:
        dates = [f"2026-01-{day:02d}" for day in range(1, 10)]
        panels = {}
        factors = {}
        for symbol_index in range(6):
            symbol = f"S{symbol_index}"
            rows = []
            factor_map = {}
            for day_index, trade_date in enumerate(dates):
                if symbol == "S5" and trade_date == dates[-1]:
                    continue
                growth = 1.0 + 0.01 * symbol_index
                rows.append({"date": trade_date, "close": 100.0 * growth ** day_index,
                             "amount": 1_000_000 + symbol_index * 10_000,
                             "pct_change": (growth - 1) * 100})
                factor_map[trade_date] = float(symbol_index)
            panels[symbol] = rows
            factors[symbol] = factor_map
        # Bind by object identity because panel rows do not carry a symbol field.
        factor_by_id = {id(rows): factors[symbol] for symbol, rows in panels.items()}
        with mock.patch("factor_backtest.engine.compute_factor", side_effect=lambda rows, name: factor_by_id[id(rows)]):
            result = backtest.run_backtest("US", panels, "rev_5", bins=3,
                                           rebalance_days=2, fac_shift=1,
                                           fee_bps=0.0, slippage_bps=0.0, stamp_bps=0.0,
                                           min_cross_section=6, train_frac=0.6)
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["monotonicity_spearman"], 0)
        interrupted = next(item for item in result["failure_modes"] if item["type"] == "suspended_or_delisted_count")
        self.assertGreaterEqual(interrupted["count"], 1)
        self.assertEqual(interrupted["settlement"], "last_available_price_no_zero_fill")

    def test_self_test_cli_exact_output_hermetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run([sys.executable, str(Path(backtest.__file__)), "--self-test"],
                                  capture_output=True, text=True, timeout=3,
                                  env={**os.environ, "HOME": tmp, "FACTOR_PANEL_DIR": tmp,
                                       "FACTOR_BACKTEST_DIR": tmp})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), '{"ok": true, "self_test": "passed"}')
        self.assertEqual(json.loads(proc.stdout), {"ok": True, "self_test": "passed"})


if __name__ == "__main__":
    unittest.main()
