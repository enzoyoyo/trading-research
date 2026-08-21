#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_run


class ResearchRunVersionTests(unittest.TestCase):
    def test_runtime_version_is_read_from_skill_frontmatter(self) -> None:
        skill_text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        declared = next(line.split(":", 1)[1].strip() for line in skill_text.splitlines() if line.startswith("version:"))
        self.assertEqual(research_run.skill_version(), declared)
        self.assertTrue(declared.startswith("v2."))

    def test_capability_map_key_is_version_neutral(self) -> None:
        source = (Path(__file__).resolve().parent / "research_run.py").read_text(encoding="utf-8")
        self.assertIn('"methods_available":', source)
        self.assertNotIn('"v25_methods_available":', source)

    def test_research_and_decision_chains_are_fail_closed(self) -> None:
        research = research_run.research_contract("insufficient")
        self.assertEqual(
            research["required_order"],
            ["macro", "sector", "company", "sentiment", "capital_structure", "opportunity"],
        )
        self.assertTrue(all(stage["state"] == "data_gap" for stage in research["stages"]))
        self.assertFalse(research["gate"]["may_raise_action_or_position"])
        decision = research_run.decision_contract("conflict")
        self.assertEqual(decision["status"], "blocked_pending_evidence")
        self.assertEqual(decision["max_action_level"], "L0")
        self.assertEqual(decision["entry_permission"], "BLOCK")
        self.assertEqual(decision["position_multiplier"], 0.0)
        self.assertEqual(decision["risk_posture"], "neutral")
        self.assertEqual(decision["premortem_min_failures"], 3)
        self.assertEqual(decision["final_authority"], "Decision Compiler")


if __name__ == "__main__":
    unittest.main()
