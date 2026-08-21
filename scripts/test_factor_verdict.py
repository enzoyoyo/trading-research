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
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
import factor_verdict as verdict


def stats(*, overall_alpha: float, train_alpha: float, test_alpha: float,
          overall_ic: float = 0.04, train_ic: float = 0.04, test_ic: float = 0.03) -> dict:
    return {
        "status": "ok", "mean_ic": overall_ic, "random_ic_mean": 0.0,
        "alpha_t": overall_alpha,
        "train": {"mean_ic": train_ic, "alpha_t": train_alpha},
        "test": {"mean_ic": test_ic, "alpha_t": test_alpha},
    }


def engine_run(row: dict, direction: str = "+") -> dict:
    return {
        "schema_version": "factor_engine_run.v1", "run_id": "fr_test", "status": "ok", "market": "A",
        "universe": {"symbols": 40, "basis": "user_watchlist_survivorship_biased"},
        "n_factors_scanned": 8,
        "factors": {"mom_20_1": {"direction_hypothesis": direction, "calculation_ref": "x",
                                      "horizons": {"5": row}}},
        "no_order_execution": True,
    }


def backtest(costs: str = "yes", segmented: bool = True) -> dict:
    return {
        "schema_version": "factor_backtest_run.v1", "status": "ok", "factor": "mom_20_1",
        "costs_included": costs,
        "train": {"periods": 8} if segmented else None,
        "test": {"periods": 4} if segmented else None,
        "robustness_checks": ["no_lookahead_fac_shift>=1", "survivorship_current_constituents"],
        "failure_modes": [], "no_order_execution": True,
    }


class FactorVerdictTests(unittest.TestCase):
    def judge(self, row: dict, direction: str = "+", bt: dict | None = None) -> dict:
        return verdict.build_verdict(engine_run(row, direction), backtest() if bt is None else bt,
                                     factor="mom_20_1", horizon="5")

    def test_four_state_boundaries(self) -> None:
        self.assertEqual(self.judge(stats(overall_alpha=5.0, train_alpha=3.5, test_alpha=3.5))["state"], "confirmed_alive")
        self.assertEqual(self.judge(stats(overall_alpha=5.0, train_alpha=3.5, test_alpha=3.499))["state"], "train_only")
        reversed_row = stats(overall_alpha=-3.5, train_alpha=-1.0, test_alpha=-3.5,
                             overall_ic=-0.04, train_ic=-0.01, test_ic=-0.03)
        self.assertEqual(self.judge(reversed_row)["state"], "reversed_strict")
        self.assertEqual(self.judge(stats(overall_alpha=0.2, train_alpha=3.499, test_alpha=0.1))["state"], "noise")

    def test_same_sign_required_for_confirmed(self) -> None:
        row = stats(overall_alpha=5.0, train_alpha=4.0, test_alpha=4.0, train_ic=0.03, test_ic=-0.02)
        self.assertEqual(self.judge(row)["state"], "noise")

    def test_missing_backtest_and_gate_failures_force_hypothesis_only(self) -> None:
        row = stats(overall_alpha=5.0, train_alpha=4.0, test_alpha=4.0)
        missing = verdict.build_verdict(engine_run(row), None, factor="mom_20_1", horizon="5")
        self.assertEqual(missing["decision_use"], "hypothesis_only")
        self.assertEqual(self.judge(row, bt=backtest(costs="no"))["decision_use"], "hypothesis_only")
        self.assertEqual(self.judge(row, bt=backtest(segmented=False))["decision_use"], "hypothesis_only")

    def test_too_few_oos_periods_fail_closed(self) -> None:
        row = stats(overall_alpha=5.0, train_alpha=4.0, test_alpha=4.0)
        short = backtest()
        short["test"]["periods"] = verdict.MIN_OOS_PERIODS - 1
        result = self.judge(row, bt=short)
        self.assertEqual(result["decision_use"], "hypothesis_only")
        self.assertIn("missing_walk_forward", result["data_gaps"])

    def test_missing_embedded_direction_uses_local_registry_and_reversed_is_reachable(self) -> None:
        reversed_row = stats(overall_alpha=-3.5, train_alpha=-1.0, test_alpha=-3.5,
                             overall_ic=-0.04, train_ic=-0.01, test_ic=-0.03)
        run = engine_run(reversed_row)
        run["factors"]["mom_20_1"].pop("direction_hypothesis")
        result = verdict.build_verdict(run, backtest(), factor="mom_20_1", horizon="5")
        self.assertEqual(result["state"], "reversed_strict")
        self.assertNotIn("missing_direction_hypothesis", result["data_gaps"])

    def test_unavailable_direction_is_an_explicit_gap(self) -> None:
        row = stats(overall_alpha=0.2, train_alpha=0.1, test_alpha=0.1)
        run = engine_run(row)
        run["factors"]["mom_20_1"].pop("direction_hypothesis")
        import factor_engine
        with mock.patch.dict(factor_engine.FACTOR_METHODS, {}, clear=True):
            result = verdict.build_verdict(run, backtest(), factor="mom_20_1", horizon="5")
        self.assertIn("missing_direction_hypothesis", result["data_gaps"])
        self.assertEqual(result["decision_use"], "hypothesis_only")

    def test_survivorship_ceiling_is_not_parameterized(self) -> None:
        result = self.judge(stats(overall_alpha=8.0, train_alpha=8.0, test_alpha=8.0))
        self.assertEqual(result["decision_use"], "ranking_support")
        self.assertEqual(result["readiness_level"], "working_view")
        self.assertNotIn("risk_cap_support", json.dumps(result))

    def test_missing_random_contract_not_judgeable(self) -> None:
        row = stats(overall_alpha=5.0, train_alpha=4.0, test_alpha=4.0)
        row.pop("random_ic_mean")
        result = self.judge(row)
        self.assertIsNone(result["state"])
        self.assertEqual(result["status"], "not_judgeable")
        self.assertEqual(result["readiness_level"], "research_hypothesis")

    def test_registry_cli_actual_create_in_tempdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            env = dict(os.environ)
            env["TRADING_RESEARCH_HYPOTHESES_PATH"] = str(registry)
            payload = self.judge(stats(overall_alpha=5.0, train_alpha=4.0, test_alpha=4.0))["hypothesis_payload"]
            payload_path = Path(tmp) / "payload.json"
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "create", "--payload", str(payload_path)],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(registry.exists())
            rows = json.loads(registry.read_text())["hypotheses"]
            self.assertEqual(rows[0]["status"], "confirmed_alive")
            self.assertEqual(rows[0]["source_module"], "quant_robustness")

    def test_fixture_contract_regression(self) -> None:
        verdict.validate_fixture(ROOT / "templates" / "factor-experiment-example.json")


if __name__ == "__main__":
    unittest.main()
