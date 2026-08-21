#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import self_optimization_check as check  # noqa: E402


class LearningPacketSchemaTests(unittest.TestCase):
    def write_packet(self, root: Path, payload: dict) -> None:
        (root / "paper_learning_packet_20260710T000000Z.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_v2_packet_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_packet(root, {
                "schema_version": "paper_learning_packet.v2",
                "summary": {"closed_trades": 5},
                "materiality_gate": {"enough_for_trading_research_skill_upgrade": True},
                "skill_upgrade_candidates": [],
            })
            with patch.dict(os.environ, {"PAPER_LEARNING_PACKET_DIR": str(root)}):
                result = check.latest_learning_packet()
            self.assertEqual(result["status"], "present")
            self.assertEqual(result["schema_version"], "paper_learning_packet.v2")

    def test_unknown_packet_schema_is_rejected_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_packet(root, {
                "schema_version": "paper_learning_packet.v999",
                "summary": {"closed_trades": 999},
                "materiality_gate": {"enough_for_trading_research_skill_upgrade": True},
                "skill_upgrade_candidates": ["unsafe_candidate"],
            })
            with patch.dict(os.environ, {"PAPER_LEARNING_PACKET_DIR": str(root)}):
                result = check.latest_learning_packet()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason"], "packet_schema_mismatch")
            self.assertEqual(result["schema_version"], "paper_learning_packet.v999")
            self.assertNotIn("materiality_gate", result)
            self.assertNotIn("skill_upgrade_candidates", result)

    def test_missing_packet_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_packet(root, {"summary": {"closed_trades": 999}})
            with patch.dict(os.environ, {"PAPER_LEARNING_PACKET_DIR": str(root)}):
                result = check.latest_learning_packet()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason"], "packet_schema_mismatch")
            self.assertIsNone(result["schema_version"])


if __name__ == "__main__":
    unittest.main()
