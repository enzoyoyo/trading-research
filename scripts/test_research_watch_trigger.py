#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_watch_trigger as rwt  # noqa: E402


class ResearchWatchTriggerTests(unittest.TestCase):
    def valid(self) -> dict:
        return {
            "schema_version": "research_watch_trigger.v1",
            "trigger_id": "RWT-aapl-price-cross",
            "symbol": "AAPL.US",
            "trigger_type": "price_cross",
            "condition": "cross_up",
            "threshold": 225.0,
            "reference_value": 220.0,
            "created_at": "2026-07-12T00:00:00Z",
            "expires_at": "2026-07-19T00:00:00Z",
            "cooldown_seconds": 3600,
            "rearm_rule": "recross",
            "one_shot": False,
            "evidence_refs": ["E1"],
            "on_trigger": "rerun_research",
            "no_order_execution": True,
        }

    def test_valid_trigger_compiles_to_side_effect_free_research_plan(self) -> None:
        result = rwt.compile_trigger(self.valid())
        self.assertTrue(result["ok"])
        self.assertEqual(result["schema_version"], "research_watch_trigger_compilation.v1")
        self.assertEqual(result["trigger"]["on_trigger"], "rerun_research")
        self.assertTrue(result["requires_fresh_facts"])
        self.assertTrue(result["requires_decision_recompile"])
        self.assertTrue(result["no_external_side_effects"])
        self.assertTrue(result["no_order_execution"])

    def test_order_email_cron_and_external_alert_actions_are_rejected(self) -> None:
        for action in ("place_order", "submit_order", "buy", "sell", "send_email", "create_cron", "create_alert"):
            payload = self.valid()
            payload["on_trigger"] = action
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "on_trigger"):
                rwt.compile_trigger(payload)

    def test_unknown_side_effect_fields_are_rejected(self) -> None:
        for field in ("cron_schedule", "email_to", "alert_id", "order_payload"):
            payload = self.valid()
            payload[field] = "forbidden"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "unexpected fields"):
                rwt.compile_trigger(payload)

    def test_required_safety_fields_cannot_be_omitted(self) -> None:
        for field in ("trigger_id", "symbol", "trigger_type", "condition", "created_at", "expires_at", "cooldown_seconds", "rearm_rule", "one_shot", "evidence_refs", "on_trigger", "no_order_execution"):
            payload = self.valid()
            payload.pop(field)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "missing required field"):
                rwt.compile_trigger(payload)

    def test_no_order_and_evidence_invariants_fail_closed(self) -> None:
        payload = self.valid()
        payload["no_order_execution"] = False
        with self.assertRaisesRegex(ValueError, "no_order_execution"):
            rwt.compile_trigger(payload)
        payload = self.valid()
        payload["evidence_refs"] = []
        with self.assertRaisesRegex(ValueError, "evidence_refs"):
            rwt.compile_trigger(payload)

    def test_trigger_time_window_must_be_timezone_aware_and_forward(self) -> None:
        payload = self.valid()
        payload["created_at"] = "2026-07-12T00:00:00"
        with self.assertRaisesRegex(ValueError, "created_at"):
            rwt.compile_trigger(payload)
        payload = self.valid()
        payload["expires_at"] = payload["created_at"]
        with self.assertRaisesRegex(ValueError, "expires_at"):
            rwt.compile_trigger(payload)

    def test_trigger_enums_and_types_are_strict(self) -> None:
        cases = [
            ("trigger_type", "unknown", "trigger_type"),
            ("condition", "occurs", "condition"),
            ("rearm_rule", "auto", "rearm_rule"),
            ("cooldown_seconds", -1, "cooldown_seconds"),
            ("one_shot", "yes", "one_shot"),
            ("threshold", None, "threshold"),
            ("reference_value", True, "reference_value"),
        ]
        for field, value, error in cases:
            payload = self.valid()
            payload[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                rwt.compile_trigger(payload)

    def test_identity_and_evidence_refs_are_non_empty_and_normalized(self) -> None:
        payload = self.valid()
        payload["trigger_id"] = " RWT-aapl-price-cross "
        payload["symbol"] = "aapl.us"
        payload["evidence_refs"] = [" E1 "]
        result = rwt.compile_trigger(payload)
        self.assertEqual(result["trigger"]["trigger_id"], "RWT-aapl-price-cross")
        self.assertEqual(result["trigger"]["symbol"], "AAPL.US")
        self.assertEqual(result["trigger"]["evidence_refs"], ["E1"])
        payload = self.valid()
        payload["evidence_refs"] = ["E1", 1]
        with self.assertRaisesRegex(ValueError, "evidence_refs"):
            rwt.compile_trigger(payload)


if __name__ == "__main__":
    unittest.main()
