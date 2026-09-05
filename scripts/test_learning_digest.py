#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import learning_digest as digest  # noqa: E402


class LearningDigestHealthRenderTests(unittest.TestCase):
    def test_missing_trailing_dates_render_as_days_not_unknown_runs(self) -> None:
        payload = {
            "generated_at": "2026-08-24T00:00:00+00:00",
            "calibration_headline": "样本还不够。",
            "memory": {"sample_count": 0, "top_failures": [], "drag_symbols": []},
            "candidates": {"verified_guard_count": 0, "compiler_gap_count": 0, "manual_review_count": 0},
            "ledger": {
                "health_ok": False,
                "runs_last_7": 2,
                "latest_version": "v2.59",
                "health_issues": [
                    {"type": "ledger_missing_trailing_dates", "consecutive_days": 10}
                ],
            },
        }

        markdown = digest.render_markdown(payload)

        self.assertIn("ledger_missing_trailing_dates：连续 10 天", markdown)
        self.assertNotIn("连续 ? 次", markdown)


if __name__ == "__main__":
    unittest.main()
