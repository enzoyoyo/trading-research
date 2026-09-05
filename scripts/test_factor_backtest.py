#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
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
    @staticmethod
    def conditional_panels(days: int = 180, symbols: int = 12) -> dict[str, list[dict]]:
        rng = random.Random(91)
        panels: dict[str, list[dict]] = {}
        regimes = backtest.RISK_REGIME_BINS
        for symbol_index in range(symbols):
            price = 100.0
            rows = []
            for day in range(days):
                daily_return = 0.0004 * symbol_index + rng.gauss(0.0, 0.002)
                price *= 1.0 + daily_return
                rows.append({
                    "date": f"D{day:03d}", "close": price,
                    "low": price * 0.99, "high": price * 1.01,
                    "amount": 1_000_000.0 + symbol_index * 10_000,
                    "turnover_rate": 1.0 + symbol_index * 0.01,
                    "risk_regime": regimes[day % len(regimes)],
                })
            panels[f"S{symbol_index:02d}"] = rows
        return panels

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

    def test_condition_bins_count_in_scan_and_threshold_never_relaxes(self) -> None:
        panels = self.conditional_panels()
        result = backtest.conditional_factor_validation(
            panels, "mom_20_1", horizon=1, condition_by="risk_regime",
            null_trials=20, seed=42, min_cross_section=10, min_dates=10,
        )
        self.assertEqual(result["k"], 5)
        self.assertEqual(result["n_factors_scanned"], 5)
        self.assertEqual(result["threshold"], 3.5)
        self.assertEqual(result["threshold_policy"], "fixed_3.5_not_relaxed_for_condition_bins")
        self.assertTrue(all(row["factor_validation"]["n_factors_scanned"] == 5 for row in result["bins"]))
        self.assertEqual(result["seed"], 42)
        self.assertEqual(result["null_kind"], "cross_section_shuffle")
        global_dates = set(result["global_validation"]["section_dates"])
        self.assertTrue(all(
            set(row["factor_validation"]["section_dates"]).issubset(global_dates)
            for row in result["bins"]
        ))

    def test_condition_bin_below_min_dates_is_fail_closed_without_verdict(self) -> None:
        panels = self.conditional_panels()
        result = backtest.conditional_factor_validation(
            panels, "mom_20_1", horizon=1, condition_by="risk_regime",
            null_trials=20, seed=42, min_cross_section=10, min_dates=40,
        )
        self.assertTrue(all(row["status"] == "insufficient_sample" for row in result["bins"]))
        self.assertTrue(all(row["effective_sections"] < 40 for row in result["bins"]))
        self.assertTrue(all(row["verdict"] is None for row in result["bins"]))
        self.assertTrue(all(row["hypothesis_payload"] is None for row in result["bins"]))
        self.assertEqual(result["pair_status"], "pending_insufficient_sample")
        self.assertEqual(result["dimension_status"], "pending_family_aggregation")

    def test_global_train_only_cannot_be_promoted_by_good_condition_bins(self) -> None:
        panels = self.conditional_panels()
        with mock.patch.object(
            backtest.verdict_engine, "classify",
            side_effect=["train_only", *(["confirmed_alive"] * 5)],
        ):
            result = backtest.conditional_factor_validation(
                panels, "mom_20_1", horizon=1, condition_by="risk_regime",
                null_trials=20, seed=42, min_cross_section=10, min_dates=10,
            )
        self.assertEqual(result["global_state"], "train_only")
        self.assertTrue(all(row["verdict"]["raw_state"] == "confirmed_alive" for row in result["bins"]))
        self.assertTrue(all(row["verdict"]["state"] == "train_only" for row in result["bins"]))
        self.assertEqual(result["degradation_count"], 0)
        self.assertEqual(result["pair_status"], "no_degradation")
        self.assertEqual(result["dimension_status"], "pending_family_aggregation")

    def test_confirmed_global_failure_bin_is_explicit_degradation(self) -> None:
        panels = self.conditional_panels()
        with mock.patch.object(
            backtest.verdict_engine, "classify",
            side_effect=["confirmed_alive", "noise", *(["confirmed_alive"] * 4)],
        ):
            result = backtest.conditional_factor_validation(
                panels, "mom_20_1", horizon=1, condition_by="risk_regime",
                null_trials=20, seed=42, min_cross_section=10, min_dates=10,
            )
        self.assertEqual(result["degradation_count"], 1)
        self.assertEqual(result["degradations"][0]["after"], "train_only")
        self.assertEqual(result["pair_status"], "degradation_observed")
        self.assertFalse(result["dimension_retained"])
        payload = result["bins"][0]["hypothesis_payload"]
        self.assertEqual(payload["status"], "train_only")
        self.assertIn("alpha_t < 3.5", payload["falsifiers"][0])

    def test_condition_values_are_lagged_one_session(self) -> None:
        panels = self.conditional_panels(days=30, symbols=3)
        assignments, binning, _gaps = backtest._lagged_condition_assignments(panels, "risk_regime")
        self.assertNotIn("D000", assignments)
        self.assertEqual(assignments["D001"], "normal")
        self.assertEqual(assignments["D002"], "stress_building")
        self.assertEqual(binning["lag_sessions"], 1)

    def test_volatility_condition_persists_lagged_quintile_boundaries(self) -> None:
        panels = self.conditional_panels(days=80, symbols=12)
        assignments, binning, _gaps = backtest._lagged_condition_assignments(
            panels, "vol_20_quintile"
        )
        self.assertEqual(binning["source_factor"], "vol_20")
        self.assertEqual(binning["lag_sessions"], 1)
        self.assertTrue(binning["boundaries_by_date"])
        self.assertTrue(all(
            len(boundaries) == 4
            for boundaries in binning["boundaries_by_date"].values()
        ))
        self.assertEqual(binning["date_assignments"], assignments)
        self.assertTrue(set(assignments.values()).issubset({"Q1", "Q2", "Q3", "Q4", "Q5"}))

    def test_volatility_condition_prefix_is_invariant_to_future_tail(self) -> None:
        full = self.conditional_panels(days=100, symbols=12)
        prefix = {symbol: rows[:80] for symbol, rows in full.items()}
        mutated = json.loads(json.dumps(full))
        for rows in mutated.values():
            for row in rows[80:]:
                row["close"] *= 1.0 + (int(row["date"][1:]) - 79) * 0.5
        prefix_assignments, prefix_binning, _ = backtest._lagged_condition_assignments(
            prefix, "vol_20_quintile"
        )
        full_assignments, full_binning, _ = backtest._lagged_condition_assignments(
            mutated, "vol_20_quintile"
        )
        comparable_dates = set(prefix_assignments)
        self.assertEqual(
            prefix_assignments,
            {date: full_assignments[date] for date in comparable_dates},
        )
        self.assertEqual(
            prefix_binning["boundaries_by_date"],
            {
                date: full_binning["boundaries_by_date"][date]
                for date in prefix_binning["boundaries_by_date"]
            },
        )

    def test_unjudgeable_bins_cannot_reject_condition_dimension(self) -> None:
        panels = self.conditional_panels()
        with mock.patch.object(backtest.verdict_engine, "classify", return_value=None):
            result = backtest.conditional_factor_validation(
                panels, "mom_20_1", horizon=1, condition_by="risk_regime",
                null_trials=20, seed=42, min_cross_section=10, min_dates=10,
            )
        self.assertEqual(result["judgeable_bin_count"], 0)
        self.assertEqual(result["unjudgeable_bin_count"], 5)
        self.assertEqual(result["pair_status"], "pending_unjudgeable")
        self.assertTrue(all(row["verdict"] is None for row in result["bins"]))
        self.assertTrue(all(row["hypothesis_payload"] is None for row in result["bins"]))

    def test_family_counts_all_factor_horizon_bins_and_only_family_can_reject(self) -> None:
        panels = self.conditional_panels()
        all_confirmed = ["confirmed_alive"] * 12
        with mock.patch.object(backtest.verdict_engine, "classify", side_effect=all_confirmed):
            rejected = backtest.conditional_family_validation(
                panels, ["mom_20_1", "rev_5"], [1], condition_by="risk_regime",
                null_trials=20, seed=42, min_cross_section=10, min_dates=10,
                family_complete=True,
            )
        self.assertEqual(rejected["n_factors_scanned"], 10)
        self.assertEqual(
            rejected["results"][0]["n_factors_scanned_formula"],
            "2 factors * 1 horizons * 5 condition bins",
        )
        self.assertEqual(rejected["threshold"], 3.5)
        self.assertTrue(rejected["all_bins_judgeable"])
        self.assertEqual(rejected["dimension_status"], "rejected_no_degradation")

        one_degradation = [
            "confirmed_alive", *( ["confirmed_alive"] * 5),
            "confirmed_alive", "noise", *( ["confirmed_alive"] * 4),
        ]
        with mock.patch.object(backtest.verdict_engine, "classify", side_effect=one_degradation):
            retained = backtest.conditional_family_validation(
                panels, ["mom_20_1", "rev_5"], [1], condition_by="risk_regime",
                null_trials=20, seed=42, min_cross_section=10, min_dates=10,
                family_complete=True,
            )
        self.assertTrue(retained["dimension_retained"])
        self.assertEqual(retained["degradation_count"], 1)
        self.assertEqual(retained["dimension_status"], "retained_degradation_observed")

        with mock.patch.object(
            backtest.verdict_engine, "classify",
            side_effect=["confirmed_alive", "noise", *( ["confirmed_alive"] * 4)],
        ):
            incomplete = backtest.conditional_family_validation(
                panels, ["mom_20_1"], [1], condition_by="risk_regime",
                null_trials=20, seed=42, min_cross_section=10, min_dates=10,
                family_complete=False,
            )
        self.assertEqual(incomplete["degradation_count"], 1)
        self.assertFalse(incomplete["dimension_retained"])
        self.assertEqual(incomplete["dimension_status"], "pending_family_aggregation")
        self.assertEqual(backtest.register_condition_hypotheses(incomplete), [])

    def test_condition_seed_changes_null_not_bin_assignments(self) -> None:
        panels = self.conditional_panels(days=80, symbols=12)
        first = backtest.conditional_factor_validation(
            panels, "mom_20_1", horizon=1, condition_by="vol_20_quintile",
            null_trials=10, seed=42, min_cross_section=10, min_dates=3,
        )
        second = backtest.conditional_factor_validation(
            panels, "mom_20_1", horizon=1, condition_by="vol_20_quintile",
            null_trials=10, seed=43, min_cross_section=10, min_dates=3,
        )
        self.assertEqual(first["binning"], second["binning"])
        self.assertNotEqual(first["global_validation"]["null_seed"], second["global_validation"]["null_seed"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "condition.json"
            backtest._atomic_save(path, first)
            readback = json.loads(path.read_text())
        self.assertEqual(
            readback["binning"]["boundaries_by_date"],
            first["binning"]["boundaries_by_date"],
        )
        self.assertEqual(readback["seed"], 42)

    def test_condition_state_ceiling_matrix_never_promotes_nonconfirmed_global(self) -> None:
        states = ("confirmed_alive", "train_only", "noise", "reversed_strict")
        for global_state in states:
            for raw_state in states:
                with self.subTest(global_state=global_state, raw_state=raw_state):
                    got = backtest.apply_condition_state_ceiling(global_state, raw_state)
                    if global_state == "confirmed_alive":
                        expected = "confirmed_alive" if raw_state == "confirmed_alive" else "train_only"
                    else:
                        expected = global_state
                    self.assertEqual(got, expected)

    def test_condition_hypothesis_registration_searches_then_creates_only_valid_bins(self) -> None:
        payload = backtest._condition_hypothesis_payload(
            factor_name="mom_20_1", condition_by="risk_regime", bin_id="normal",
            horizon=5, raw_state="noise", conditioned_state="train_only",
        )
        condition_result = {
            "dimension_retained": True,
            "bins": [
                {"hypothesis_payload": payload},
                {"hypothesis_payload": None, "status": "insufficient_sample"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"TRADING_RESEARCH_HYPOTHESES_PATH": str(Path(tmp) / "hypotheses.json")}
        ):
            registrations = backtest.register_condition_hypotheses(condition_result)
            stored = json.loads((Path(tmp) / "hypotheses.json").read_text())["hypotheses"]
        self.assertEqual(len(registrations), 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "train_only")
        self.assertTrue(stored[0]["falsifiers"])

    def test_condition_hypothesis_identity_is_stable_across_state_changes(self) -> None:
        first_payload = backtest._condition_hypothesis_payload(
            factor_name="mom_20_1", condition_by="risk_regime", bin_id="normal",
            horizon=5, raw_state="noise", conditioned_state="train_only",
        )
        second_payload = backtest._condition_hypothesis_payload(
            market="US", factor_name="mom_20_1", condition_by="risk_regime", bin_id="normal",
            horizon=5, raw_state="confirmed_alive", conditioned_state="confirmed_alive",
        )
        first_payload = backtest._condition_hypothesis_payload(
            market="US", factor_name="mom_20_1", condition_by="risk_regime", bin_id="normal",
            horizon=5, raw_state="noise", conditioned_state="train_only",
        )
        other_market_payload = backtest._condition_hypothesis_payload(
            market="A", factor_name="mom_20_1", condition_by="risk_regime", bin_id="normal",
            horizon=5, raw_state="noise", conditioned_state="train_only",
        )
        self.assertEqual(first_payload["statement"], second_payload["statement"])
        self.assertNotEqual(first_payload["statement"], other_market_payload["statement"])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"TRADING_RESEARCH_HYPOTHESES_PATH": str(Path(tmp) / "hypotheses.json")}
        ):
            first = backtest.register_condition_hypotheses({
                "dimension_retained": True, "bins": [{"hypothesis_payload": first_payload}],
            })
            second = backtest.register_condition_hypotheses({
                "dimension_retained": True, "bins": [{"hypothesis_payload": second_payload}],
            })
            third = backtest.register_condition_hypotheses({
                "dimension_retained": True, "bins": [{"hypothesis_payload": other_market_payload}],
            })
            stored = json.loads((Path(tmp) / "hypotheses.json").read_text())["hypotheses"]
        self.assertEqual(first[0]["action"], "created")
        self.assertEqual(second[0]["action"], "updated")
        self.assertEqual(third[0]["action"], "created")
        self.assertEqual(len(stored), 2)
        self.assertEqual(first[0]["hypothesis"]["hypothesis_id"], second[0]["hypothesis"]["hypothesis_id"])

    def test_panel_provenance_allows_us_and_hk_forward_longbridge(self) -> None:
        us_panels = {"SPY": [{
            "date": "D001", "panel_source": "longbridge", "panel_adjust": "forward",
            "panel_adjust_basis": "forward_snapshot_20260829",
            "panel_pit_caveats": ["forward_adjust_rewrites_history"],
        }]}
        provenance, gaps = backtest.summarize_panel_provenance(us_panels, "US")
        self.assertEqual(gaps, [])
        self.assertEqual(provenance["sources_used"], ["longbridge"])
        self.assertIn("forward_adjust_rewrites_history", provenance["pit_caveats"])
        hk_provenance, hk_gaps = backtest.summarize_panel_provenance(us_panels, "HK")
        self.assertEqual(hk_gaps, [])
        self.assertEqual(hk_provenance["sources_used"], ["longbridge"])
        crossed = {"SPY": [
            {
                "panel_source": "akshare", "panel_adjust": "forward",
                "panel_adjust_basis": "mixed", "panel_pit_caveats": ["qfq_rewrites_history"],
            },
            {
                "panel_source": "longbridge", "panel_adjust": "qfq",
                "panel_adjust_basis": "mixed",
                "panel_pit_caveats": ["forward_adjust_rewrites_history"],
            },
        ]}
        _crossed_provenance, crossed_gaps = backtest.summarize_panel_provenance(crossed, "US")
        self.assertGreaterEqual(
            sum(row["reason_code"] == "panel_adjust_mismatch" for row in crossed_gaps), 2
        )
        partially_unmarked = {"MIXED": [
            {
                "panel_source": "akshare", "panel_adjust": "qfq",
                "panel_adjust_basis": "qfq_snapshot_20260829",
                "panel_pit_caveats": ["qfq_rewrites_history"],
            },
            {
                "panel_source": None, "panel_adjust": "forward",
                "panel_adjust_basis": None, "panel_pit_caveats": [],
            },
        ]}
        for market in ("A", "US"):
            _partial_provenance, partial_gaps = backtest.summarize_panel_provenance(
                partially_unmarked, market
            )
            self.assertTrue(any(
                row.get("row_index") == 1 and row["gap"] == "panel_source_missing"
                for row in partial_gaps
            ))
            self.assertTrue(any(
                row.get("row_index") == 1 and row["gap"] == "adjust_basis_missing"
                for row in partial_gaps
            ))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "US").mkdir()
            (root / "US" / "SPY.json").write_text(json.dumps({
                "source": "longbridge", "panel_source": "longbridge",
                "adjust": "forward", "adjust_basis": "forward_snapshot_20260829",
                "pit_caveats": ["forward_adjust_rewrites_history"],
                "rows": [{"date": "D001", "close": 100.0}],
            }), encoding="utf-8")
            loaded, gaps, _basis = backtest.engine.load_panels("US", ["SPY"], root)
        loaded_provenance, provenance_gaps = backtest.summarize_panel_provenance(loaded, "US")
        self.assertEqual(gaps + provenance_gaps, [])
        self.assertEqual(loaded_provenance["adjusts_used"], ["forward"])

    def test_panel_provenance_a_rejects_longbridge_with_legacy_gap(self) -> None:
        panels = {"600519": [{
            "date": "D001", "panel_source": "longbridge", "panel_adjust": "forward",
            "panel_adjust_basis": "forward_snapshot_20260829",
            "panel_pit_caveats": ["forward_adjust_rewrites_history"],
        }]}
        _provenance, gaps = backtest.summarize_panel_provenance(panels, "A")
        self.assertTrue(any(
            row["gap"] == "A_HK_panel_requires_akshare_family" for row in gaps
        ))

    def test_panel_provenance_hk_rejects_unknown_source(self) -> None:
        panels = {"00700": [{
            "date": "D001", "panel_source": "unknown", "panel_adjust": "unknown",
            "panel_adjust_basis": "unknown_snapshot_20260829", "panel_pit_caveats": [],
        }]}
        _provenance, gaps = backtest.summarize_panel_provenance(panels, "HK")
        self.assertTrue(any(
            row["gap"] == "HK_panel_source_not_registered" for row in gaps
        ))

    def test_panel_provenance_allows_a_share_akshare_sina_family(self) -> None:
        panels = {
            "000001.SZ": [{
                "date": "D001",
                "panel_source": "akshare_sina",
                "panel_adjust": "sina_qfq",
                "panel_adjust_basis": "sina_qfq_snapshot_20260829",
                "panel_pit_caveats": ["qfq_rewrites_history"],
            }],
            "600000.SH": [{
                "date": "D001",
                "panel_source": "akshare_sina",
                "panel_adjust": "sina_qfq",
                "panel_adjust_basis": "sina_qfq_snapshot_20260829",
                "panel_pit_caveats": ["qfq_rewrites_history"],
            }],
        }
        provenance, gaps = backtest.summarize_panel_provenance(panels, "A")
        self.assertEqual(provenance["sources_used"], ["akshare_sina"])
        self.assertFalse(any(
            row["reason_code"] in {"illegal_panel_source", "panel_adjust_mismatch"}
            for row in gaps
        ))

    def test_panel_provenance_rejects_cross_symbol_mixed_adjust_basis(self) -> None:
        panels = {
            "000001.SZ": [{
                "date": "D001",
                "panel_source": "akshare",
                "panel_adjust": "qfq",
                "panel_adjust_basis": "qfq_snapshot_20260829",
                "panel_pit_caveats": ["qfq_rewrites_history"],
            }],
            "600000.SH": [{
                "date": "D001",
                "panel_source": "akshare_sina",
                "panel_adjust": "sina_qfq",
                "panel_adjust_basis": "sina_qfq_snapshot_20260829",
                "panel_pit_caveats": ["qfq_rewrites_history"],
            }],
        }
        _provenance, gaps = backtest.summarize_panel_provenance(panels, "A")
        mixed_gap = next(row for row in gaps if row["reason_code"] == "mixed_adjust_basis")
        self.assertEqual(mixed_gap["gap"], "cross_symbol_adjust_basis_mismatch")
        self.assertEqual(
            mixed_gap["bases"],
            ["qfq_snapshot_20260829", "sina_qfq_snapshot_20260829"],
        )

    def test_panel_provenance_rejects_akshare_sina_for_us(self) -> None:
        panels = {"SPY": [{
            "date": "D001",
            "panel_source": "akshare_sina",
            "panel_adjust": "sina_qfq",
            "panel_adjust_basis": "sina_qfq_snapshot_20260829",
            "panel_pit_caveats": ["qfq_rewrites_history"],
        }]}
        _provenance, gaps = backtest.summarize_panel_provenance(panels, "US")
        self.assertTrue(any(
            row["reason_code"] == "illegal_panel_source" for row in gaps
        ))

    def test_omitting_condition_by_does_not_call_condition_validator(self) -> None:
        panels = self.conditional_panels(days=30, symbols=6)
        with mock.patch.object(backtest, "conditional_factor_validation") as conditional:
            backtest.run_backtest(
                "US", panels, "rev_5", bins=3, rebalance_days=2, fac_shift=1,
                fee_bps=0.0, slippage_bps=0.0, stamp_bps=0.0,
                min_cross_section=6, train_frac=0.6,
            )
        conditional.assert_not_called()

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
