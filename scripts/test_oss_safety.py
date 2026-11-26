#!/usr/bin/env python3
"""Minimal safety tests for the open-source trading-research distribution."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_loader  # noqa: E402


class TestOssSafety(unittest.TestCase):
    def test_skill_file_loads(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("trading-research", skill.lower())
        self.assertNotIn("/Users/", skill)
        self.assertNotRegex(skill, r"(?i)\benzo\b")

    def test_example_config_available(self):
        example = ROOT / "config.example.yaml"
        self.assertTrue(example.is_file())
        data = example.read_text(encoding="utf-8")
        self.assertIn("allow_live_trading: false", data)
        self.assertIn("allow_order_submission: false", data)
        # Optional YAML parse when PyYAML present
        try:
            import yaml  # type: ignore
        except Exception:
            self.skipTest("PyYAML not installed")
        parsed = yaml.safe_load(data)
        self.assertEqual(parsed["mode"], "research_only")
        self.assertFalse(parsed["safety"]["allow_live_trading"])

    def test_missing_secret_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VOLC_DOUBAO_SEARCH_API_KEY", None)
            with self.assertRaises(RuntimeError):
                config_loader.require_env("VOLC_DOUBAO_SEARCH_API_KEY")

    def test_logs_do_not_leak_secrets(self):
        dirty = "Authorization Bearer abcdefghijklmnop OKX_API_KEY=super-secret-value-12345"
        clean = config_loader.scrub_secrets(dirty)
        self.assertNotIn("super-secret-value-12345", clean)
        self.assertNotIn("abcdefghijklmnop", clean)
        self.assertIn("<redacted>", clean)

    def test_mock_core_flow_config_status(self):
        status = config_loader.public_status({})
        self.assertFalse(status["live_trading_enabled"])
        self.assertFalse(status["orders_allowed"])
        self.assertFalse(status["secret_values_printed"])

    def test_live_trading_flags_rejected(self):
        with mock.patch.dict(os.environ, {"TRADING_RESEARCH_ALLOW_LIVE": "true"}):
            with self.assertRaises(RuntimeError):
                config_loader.assert_research_only({"safety": {"allow_live_trading": True}})

    def test_env_example_and_gitignore_exclude_secrets(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", gitignore)
        self.assertIn("!.env.example", gitignore)
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("TRADING_RESEARCH_ALLOW_LIVE", env_example)
        # No obviously assigned high-entropy secrets in example
        self.assertNotRegex(env_example, r"(?im)^[A-Z0-9_]+=.{" + "20,}")

    def test_okx_supervisor_rejects_unknown_mode_and_has_no_order_client(self):
        path = SCRIPTS / "okx_execution_supervisor.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn('ALLOWED_MODES = {"public", "read_only", "demo", "live"}', source)
        self.assertNotRegex(source, r"(?i)place_order\(|submit_order\(|create_order\(")
        spec = importlib.util.spec_from_file_location("okx_execution_supervisor", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        result = mod.compile_supervision({"mode": "not-a-mode"})
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("no_order_execution") or result.get("analysis_only"))
        self.assertNotEqual(result.get("new_entries_allowed"), True)
        blockers = result.get("blockers") or []
        self.assertTrue(any("no_order_execution" in str(b) for b in blockers) or result.get("no_order_execution"))

    def test_world_readable_env_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = Path(td) / "search.env"
            env_path.write_text("VOLC_DOUBAO_SEARCH_API_KEY=example-test-key\n", encoding="utf-8")
            os.chmod(env_path, 0o644)
            with self.assertRaises(RuntimeError):
                config_loader.load_env_file(env_path)


if __name__ == "__main__":
    unittest.main()
