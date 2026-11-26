#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decision_compiler as COMPILER  # noqa: E402

MODULE_PATH = Path(__file__).with_name("intelligence_coverage.py")
SPEC = importlib.util.spec_from_file_location("intelligence_coverage", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IntelligenceCoverageTests(unittest.TestCase):
    def base(self) -> dict:
        return {
            "schema_version": "intelligence_coverage.v1",
            "as_of": "2026-07-12T00:00:00Z",
            "targets": ["AAA.US"],
            "requirements": [
                {"dimension": "quote", "criticality": "high", "min_independent_sources": 1}
            ],
            "observations": [],
        }

    def live(self, target: str = "AAA.US", family: str = "p1", dimension: str = "quote") -> dict:
        return {
            "target": target,
            "dimension": dimension,
            "source": f"{family}_{dimension}",
            "provider_family": family,
            "status": "live",
            "observed_at": "2026-07-11T23:59:00Z",
            "stale_after": "2026-07-12T00:05:00Z",
            "data_present": True,
            "fallback_level": "T1",
        }

    def capital_commitment_baseline(self, as_of: str) -> list[dict]:
        return [
            {
                "module": module,
                "max_action_level": "L3",
                "position_multiplier": 1.0,
                "hard_veto": False,
                "evidence_refs": [f"EID-{module}"],
                "observed_at": as_of,
                "stale_after": "2026-07-12T00:05:00Z",
            }
            for module in ("risk_regime", "portfolio_risk_budget")
        ]

    def test_complete_live_coverage_is_neutral(self) -> None:
        payload = self.base()
        payload["observations"] = [self.live()]
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["coverage_ratio"], 1.0)
        self.assertEqual(result["data_gaps"], [])
        self.assertEqual(result["suggested_module_signal"]["max_action_level"], "L3")
        self.assertTrue(result["suggested_module_signal"]["tighten_only"])
        self.assertTrue(result["suggested_module_signal"]["cannot_raise_upstream"])

    def test_coverage_signal_passes_strict_decision_compiler(self) -> None:
        payload = self.base()
        payload["observations"] = [self.live()]
        result = MODULE.compile_coverage(payload)
        signal = result["suggested_module_signal"]
        self.assertEqual(signal["evidence_refs"], [result["coverage_id"]])
        self.assertEqual(signal["observed_at"], payload["as_of"])
        compiled = COMPILER.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "as_of": payload["as_of"],
                "query_tier": "T2",
                "intent": "open",
                "has_position": False,
                "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"],
            },
            "module_signals": self.capital_commitment_baseline(payload["as_of"]) + [signal],
        }, now=datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00")))
        self.assertTrue(compiled["ok"], compiled["validation_errors"])
        self.assertEqual(compiled["contract_status"], "strict_pass")
        self.assertIn("data_quality:source_coverage", compiled["dominant_constraints"])

    def test_unsupported_is_gap_not_synthetic_zero(self) -> None:
        payload = self.base()
        payload["observations"] = [{
            "target": "AAA.US",
            "dimension": "quote",
            "source": "unsupported_feed",
            "provider_family": "p1",
            "status": "unsupported",
            "data_present": False,
        }]
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["coverage"][0]["coverage_state"], "unsupported")
        self.assertEqual(result["data_gaps"][0]["reason_code"], "unsupported")
        self.assertEqual(result["suggested_module_signal"]["position_multiplier"], 0.0)

    def test_stale_observation_does_not_count_as_covered(self) -> None:
        payload = self.base()
        row = self.live()
        row["stale_after"] = "2026-07-11T23:59:59Z"
        payload["observations"] = [row]
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["coverage"][0]["coverage_state"], "stale")
        self.assertEqual(result["data_gaps"][0]["reason_code"], "stale")

    def test_stale_source_does_not_expire_fresh_coverage_gap_signal(self) -> None:
        payload = self.base()
        payload["requirements"][0]["criticality"] = "medium"
        row = self.live()
        row["stale_after"] = "2026-07-11T23:59:59Z"
        payload["observations"] = [row]
        result = MODULE.compile_coverage(payload)
        compiled = COMPILER.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "as_of": payload["as_of"],
                "query_tier": "T2",
                "intent": "open",
                "has_position": False,
                "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"],
            },
            "module_signals": (
                self.capital_commitment_baseline(payload["as_of"])
                + [result["suggested_module_signal"]]
            ),
        }, now=datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00")))
        self.assertTrue(compiled["ok"], compiled["validation_errors"])
        self.assertEqual(compiled["compiled_action"], "L1")

    def test_same_provider_family_does_not_fake_independence(self) -> None:
        payload = self.base()
        payload["requirements"][0]["min_independent_sources"] = 2
        one = self.live(family="same")
        two = self.live(family="same")
        two["source"] = "same_provider_second_endpoint"
        payload["observations"] = [one, two]
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["coverage"][0]["independent_provider_count"], 1)
        self.assertEqual(result["data_gaps"][0]["reason_code"], "insufficient_independent_sources")

    def test_delayed_or_cached_coverage_caps_at_l2(self) -> None:
        payload = self.base()
        row = self.live()
        row["status"] = "delayed"
        row["fallback_level"] = "T2"
        payload["observations"] = [row]
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["coverage"][0]["coverage_state"], "covered_reference")
        self.assertEqual(result["suggested_module_signal"]["max_action_level"], "L2")
        self.assertEqual(result["suggested_module_signal"]["position_multiplier"], 0.75)

    def test_delayed_or_cached_requires_fallback_lineage(self) -> None:
        for status in ("delayed", "cached"):
            payload = self.base()
            row = self.live()
            row["status"] = status
            row.pop("fallback_level")
            payload["observations"] = [row]
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "fallback_level"):
                MODULE.compile_coverage(payload)

    def test_mixed_live_and_reference_sources_are_not_all_live(self) -> None:
        payload = self.base()
        payload["requirements"][0]["min_independent_sources"] = 2
        live = self.live(family="live_family")
        delayed = self.live(family="delayed_family")
        delayed["status"] = "delayed"
        delayed["fallback_level"] = "T2"
        payload["observations"] = [live, delayed]
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["coverage"][0]["coverage_state"], "covered_reference")
        self.assertEqual(result["coverage"][0]["live_independent_provider_count"], 1)
        self.assertEqual(result["suggested_module_signal"]["max_action_level"], "L2")

    def test_collection_queue_round_robins_targets(self) -> None:
        payload = {
            "schema_version": "intelligence_coverage.v1",
            "as_of": "2026-07-12T00:00:00Z",
            "targets": ["AAA.US", "BBB.US", "CCC.US"],
            "requirements": [
                {"dimension": "filing", "criticality": "high", "min_independent_sources": 1},
                {"dimension": "news", "criticality": "medium", "min_independent_sources": 1},
            ],
            "observations": [],
            "max_queue": 6,
        }
        result = MODULE.compile_coverage(payload)
        targets = [row["target"] for row in result["fair_collection_queue"]]
        self.assertEqual(targets, ["AAA.US", "BBB.US", "CCC.US", "AAA.US", "BBB.US", "CCC.US"])

    def test_collection_queue_preserves_global_criticality_before_fairness(self) -> None:
        payload = {
            "schema_version": "intelligence_coverage.v1",
            "as_of": "2026-07-12T00:00:00Z",
            "targets": ["AAA.US", "BBB.US"],
            "requirements": [
                {"dimension": "high_dimension", "criticality": "high", "min_independent_sources": 1},
                {"dimension": "low_dimension", "criticality": "low", "min_independent_sources": 1},
            ],
            "observations": [
                self.live(target="AAA.US", dimension="high_dimension"),
                self.live(target="BBB.US", dimension="low_dimension"),
            ],
            "max_queue": 1,
        }
        result = MODULE.compile_coverage(payload)
        self.assertEqual(result["fair_collection_queue"], [{
            "target": "BBB.US",
            "dimension": "high_dimension",
            "priority": "high",
            "reason_code": "missing",
            "next_action": "collect_independent_current_source",
        }])

    def test_invalid_or_undeclared_inputs_fail_closed(self) -> None:
        payload = self.base()
        payload["observations"] = [self.live(target="OTHER.US")]
        with self.assertRaisesRegex(ValueError, "not declared"):
            MODULE.compile_coverage(payload)
        payload = self.base()
        payload["schema_version"] = "wrong"
        with self.assertRaisesRegex(ValueError, "schema_version"):
            MODULE.compile_coverage(payload)

    def test_future_or_impossible_timestamps_fail_closed(self) -> None:
        payload = self.base()
        row = self.live()
        row["observed_at"] = "2026-07-12T00:00:01Z"
        payload["observations"] = [row]
        with self.assertRaisesRegex(ValueError, "cannot be after as_of"):
            MODULE.compile_coverage(payload)

        payload = self.base()
        row = self.live()
        row["stale_after"] = row["observed_at"]
        payload["observations"] = [row]
        with self.assertRaisesRegex(ValueError, "must be after observed_at"):
            MODULE.compile_coverage(payload)


if __name__ == "__main__":
    unittest.main()
