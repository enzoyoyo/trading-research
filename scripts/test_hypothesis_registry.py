#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import hypothesis_registry as registry


def factor_row(
    hypothesis_id: str = "hyp_factor", *, status: str = "train_only", key: str = "A|mom_20_1|5"
) -> dict:
    return {
        "hypothesis_id": hypothesis_id,
        "statement": "factor=mom_20_1;market=A;universe=watchlist;horizon=5;run_id=fr_1;state=train_only",
        "status": status,
        "record_type": "factor_verdict",
        "reconciliation_key": key,
        "falsifiers": ["next rolling OOS alpha_t < 3.5"],
        "updated_at_utc": "2026-08-01T00:00:00Z",
    }


def factor_verdict(*, state: str | None = "train_only", key: str = "A|mom_20_1|5") -> dict:
    return {
        "schema_version": "factor_verdict.v1",
        "status": "judged" if state is not None else "not_judgeable",
        "market": "A",
        "factor": "mom_20_1",
        "horizon_id": "5",
        "reconciliation_key": key,
        "state": state,
        "no_order_execution": True,
    }


class HypothesisReconcileTests(unittest.TestCase):
    def test_matching_status_and_drift_are_distinct(self) -> None:
        matched = registry.reconcile_registry([factor_row()], factor_verdict())
        self.assertEqual(matched["compared_count"], 1)
        self.assertEqual(matched["drift_count"], 0)
        self.assertEqual(matched["no_recent_evidence_count"], 0)

        drift = registry.reconcile_registry([factor_row()], factor_verdict(state="confirmed_alive"))
        self.assertEqual(drift["drift_count"], 1)
        self.assertEqual(drift["drift"][0]["registry_status"], "train_only")
        self.assertEqual(drift["drift"][0]["latest_verdict_state"], "confirmed_alive")

    def test_absent_factor_is_no_recent_evidence_not_drift(self) -> None:
        generic = {
            "hypothesis_id": "hyp_macro", "statement": "inflation remains sticky",
            "status": "open", "updated_at_utc": "2026-08-01T00:00:00Z",
        }
        result = registry.reconcile_registry(
            [factor_row(), generic], factor_verdict(key="A|rev_5|5")
        )
        self.assertEqual(result["drift_count"], 0)
        self.assertEqual(result["no_recent_evidence_count"], 1)
        self.assertEqual(result["excluded_non_factor_count"], 1)
        self.assertEqual(result["no_recent_evidence"][0]["hypothesis_id"], "hyp_factor")

    def test_not_judgeable_neither_drifts_nor_erases_status(self) -> None:
        rows = [factor_row()]
        before = json.dumps(rows, sort_keys=True)
        result = registry.reconcile_registry(rows, factor_verdict(state=None))
        self.assertEqual(result["not_judgeable_count"], 1)
        self.assertEqual(result["drift_count"], 0)
        self.assertEqual(result["no_recent_evidence_count"], 0)
        self.assertEqual(json.dumps(rows, sort_keys=True), before)

    def test_registered_hypothesis_id_has_priority_over_duplicate_key(self) -> None:
        rows = [factor_row("hyp_old"), factor_row("hyp_exact")]
        verdict = factor_verdict(state="confirmed_alive")
        verdict["registration"] = {"hypothesis": {"hypothesis_id": "hyp_exact"}}
        result = registry.reconcile_registry(rows, verdict)
        self.assertEqual(result["compared_count"], 1)
        self.assertEqual(result["drift"][0]["hypothesis_id"], "hyp_exact")
        self.assertEqual(result["no_recent_evidence"][0]["hypothesis_id"], "hyp_old")
        self.assertFalse(result["ambiguous"])

    def test_duplicate_reconciliation_key_is_reported_ambiguous(self) -> None:
        rows = [factor_row("hyp_a"), factor_row("hyp_b")]
        result = registry.reconcile_registry(rows, factor_verdict())
        self.assertEqual(len(result["ambiguous"]), 2)
        self.assertEqual(result["compared_count"], 0)

    def test_cli_reconcile_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = root / "hypotheses.json"
            verdict_path = root / "latest.json"
            registry.save_registry(registry_path, [factor_row()])
            verdict_path.write_text(json.dumps(factor_verdict()), encoding="utf-8")
            before = registry_path.read_bytes()
            env = dict(os.environ)
            env[registry.ENV_PATH] = str(registry_path)
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "reconcile", "--verdict", str(verdict_path)],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["read_only"])
            self.assertTrue(payload["no_order_execution"])
            self.assertEqual(registry_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
