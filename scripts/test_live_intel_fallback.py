#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("live_intel_run.py")
SPEC = importlib.util.spec_from_file_location("live_intel_run_for_test", MODULE_PATH)
assert SPEC and SPEC.loader
live = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = live
SPEC.loader.exec_module(live)


class LiveIntelFallbackTests(unittest.TestCase):
    def test_successful_fallback_is_explicit_and_read_only(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **kwargs):
            calls.append(command)
            payload = {"ok": True, "mode": "live_discovery", "candidates": [{"title": "x"}], "no_order_execution": True}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        result = live.run_search_fallback("NVDA", {"routed": {"market": "US"}}, runner=runner)
        self.assertTrue(result["ok"])
        self.assertTrue(result["no_order_execution"])
        self.assertEqual(result["invoked_by"], "live_intel_run_grok_failure")
        command = calls[0]
        self.assertIn("--allow-external-search", command)
        self.assertIn("--profile", command)
        self.assertIn("news", command)
        self.assertNotIn("submit_order", " ".join(command))

    def test_sensitive_or_empty_fallback_payload_stays_failed(self) -> None:
        def runner(command, **kwargs):
            payload = {"ok": False, "mode": "blocked_sensitive_query", "privacy": {"query_sent_to_selected_engines": False}}
            return subprocess.CompletedProcess(command, 3, json.dumps(payload), "")

        result = live.run_search_fallback("service 172.20.1.9", {"routed": {"market": "unknown"}}, runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "blocked_sensitive_query")
        self.assertFalse(result["privacy"]["query_sent_to_selected_engines"])

    def test_invalid_json_fails_closed(self) -> None:
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, "not-json", "error")

        result = live.run_search_fallback("NVDA", {"routed": {"market": "US"}}, runner=runner)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_json")
        self.assertTrue(result["no_order_execution"])


if __name__ == "__main__":
    unittest.main()
