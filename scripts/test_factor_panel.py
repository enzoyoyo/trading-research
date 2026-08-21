#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import factor_panel as panel


class FactorPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rows = [
            {field: ("2026-08-21" if field == "date" else 1.0) for field in panel.REQUIRED_FIELDS}
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def runner(self, calls: list[str], fail: bool = False):
        def run(code: str, timeout: int):
            calls.append(code)
            if fail:
                return {"status": "fail", "error": "mock_network_blocked", "stdout": "", "stderr": ""}
            return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
        return run

    def test_cache_write_manifest_and_incremental_skip(self) -> None:
        calls: list[str] = []
        got = panel.fetch_panel("A", ["600519"], force=True, min_interval=0,
                                root=self.root, runner=self.runner(calls), today=date(2026, 8, 21))
        self.assertEqual((got["fetched"], got["calls_made"]), (1, 1))
        cache = json.loads((self.root / "A" / "600519.json").read_text())
        manifest = json.loads((self.root / "A" / "panel_manifest.json").read_text())
        self.assertEqual(cache["rows"], self.rows)
        self.assertEqual(manifest["calls_made"], 1)
        skipped = panel.fetch_panel("A", ["600519"], min_interval=0,
                                    root=self.root, runner=self.runner(calls), today=date(2026, 8, 22))
        self.assertEqual((skipped["skipped_fresh"], skipped["calls_made"], len(calls)), (1, 0, 1))

    def test_default_window_covers_h10_at_realistic_trading_density(self) -> None:
        args = panel.build_parser().parse_args(["fetch", "--market", "A", "--symbols", "600519"])
        self.assertGreaterEqual(args.window_days, 800)

    def test_force_refetches_fresh_cache(self) -> None:
        calls: list[str] = []
        for _ in range(2):
            result = panel.fetch_panel("A", ["600519"], force=True, min_interval=0,
                                       root=self.root, runner=self.runner(calls), today=date(2026, 8, 21))
            self.assertEqual(result["fetched"], 1)
        self.assertEqual(len(calls), 2)

    def test_failure_is_gap_never_zero_and_all_failure_non_ok(self) -> None:
        calls: list[str] = []
        got = panel.fetch_panel("US", ["105.MSFT"], force=True, min_interval=0,
                                root=self.root, runner=self.runner(calls, fail=True), today=date(2026, 8, 21))
        self.assertFalse(got["ok"])
        self.assertEqual(got["status"], "failed")
        self.assertEqual(got["failed"], 1)
        self.assertEqual(got["calls_made"], 2)  # initial attempt + exactly one retry
        self.assertEqual(got["data_gaps"][0]["gap"], "akshare_fetch_failed")
        self.assertNotIn("rows", got["data_gaps"][0])
        self.assertFalse((self.root / "US" / "105.MSFT.json").exists())

    def test_partial_failure_keeps_success_and_gap(self) -> None:
        calls: list[str] = []
        def mixed(code: str, timeout: int):
            calls.append(code)
            if "BAD" in code:
                return {"status": "fail", "error": "blocked", "stdout": "", "stderr": ""}
            return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
        got = panel.fetch_panel("A", ["600519", "BAD"], force=True, min_interval=0,
                                root=self.root, runner=mixed, today=date(2026, 8, 21))
        self.assertTrue(got["ok"])
        self.assertEqual((got["status"], got["fetched"], got["failed"]), ("partial", 1, 1))

    def test_prune_window_and_manifest_preserved(self) -> None:
        calls: list[str] = []
        panel.fetch_panel("A", ["600519"], force=True, min_interval=0,
                          root=self.root, runner=self.runner(calls), today=date(2026, 8, 21))
        path = self.root / "A" / "600519.json"
        payload = json.loads(path.read_text())
        payload["fetched_at"] = "2020-01-01T00:00:00+00:00"
        panel._atomic_json(path, payload)
        got = panel.prune_cache(30, root=self.root, now=datetime(2026, 8, 21, tzinfo=timezone.utc))
        self.assertEqual(got["removed_count"], 1)
        self.assertFalse(path.exists())
        self.assertTrue((self.root / "A" / "panel_manifest.json").exists())

    def test_environment_override_and_no_proxy_subprocess_layer(self) -> None:
        with mock.patch.dict(os.environ, {"FACTOR_PANEL_DIR": str(self.root)}):
            self.assertEqual(panel.cache_root(), self.root)
        fake = subprocess.CompletedProcess(["python"], 0, "[]\n", "")
        with mock.patch("factor_panel.subprocess.run", return_value=fake) as run:
            got = panel.run_akshare("print('[]')")
        self.assertEqual(got["status"], "pass")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["no_proxy"], "*")
        self.assertEqual(env["HTTP_PROXY"], "")

    def test_self_test_cli_exact_output_and_no_network(self) -> None:
        proc = subprocess.run([sys.executable, str(Path(panel.__file__)), "--self-test"],
                              capture_output=True, text=True, timeout=3,
                              env={**os.environ, "FACTOR_PANEL_DIR": str(self.root)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"ok": True, "self_test": "passed"})
        self.assertEqual(proc.stdout.strip(), '{"ok": true, "self_test": "passed"}')


if __name__ == "__main__":
    unittest.main()
