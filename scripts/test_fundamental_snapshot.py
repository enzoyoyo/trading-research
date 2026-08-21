#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fundamental_snapshot as subject


class FundamentalConsensusTests(unittest.TestCase):
    def test_us_snapshot_adds_frozen_consensus_without_inventing_beat_rate(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
            self.assertIn(command[1], {"consensus", "forecast-eps"})
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"period": "FY2027", "eps_consensus": 12.5}),
                stderr="",
            )

        evidence = {
            "parts": {"financial_report": {"indicators": {"revenue": 1}}},
            "regulatory_evidence": [],
            "source_health": [],
            "gaps": [],
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            subject, "collect_company_evidence", return_value=evidence
        ):
            got = subject.us_share_fundamentals(
                "MSFT",
                consensus_runner=runner,
                consensus_root=Path(tmp),
                observed_at="2026-08-21T12:00:00Z",
            )

            consensus = got["parts"]["consensus"]
            self.assertEqual(consensus["eps_consensus"], 12.5)
            self.assertEqual(consensus["consensus_history"], "unavailable")
            self.assertIsNone(consensus["beat_rate_last_8"])
            self.assertEqual(consensus["beat_rate_sample_count"], 0)
            self.assertTrue(Path(consensus["frozen_snapshot_path"]).is_file())
            self.assertTrue(got["no_order_execution"])

    def test_beat_rate_requires_eight_point_in_time_pairs(self) -> None:
        snapshots: list[dict[str, object]] = []
        for index in range(8):
            period = f"FY202{index}Q1"
            snapshots.extend([
                {
                    "observed_at": f"202{index}-01-01T12:00:00Z",
                    "rows": [{"fiscal_period": period, "eps_consensus": 1.0, "reported_eps": None, "reported_at": None}],
                },
                {
                    "observed_at": f"202{index}-02-01T12:00:00Z",
                    "rows": [{"fiscal_period": period, "eps_consensus": 1.1, "reported_eps": 1.2, "reported_at": f"202{index}-02-01T11:00:00Z"}],
                },
            ])

        got = subject.compute_beat_rate_last_8(snapshots)

        self.assertEqual(got["consensus_history"], "available")
        self.assertEqual(got["beat_rate_sample_count"], 8)
        self.assertEqual(got["beat_rate_last_8"], 1.0)

        insufficient = subject.compute_beat_rate_last_8(snapshots[:-2])
        self.assertEqual(insufficient["consensus_history"], "insufficient")
        self.assertIsNone(insufficient["beat_rate_last_8"])

    def test_unavailable_consensus_is_gap_and_empty_parts_still_fail(self) -> None:
        def runner(_command: list[str], **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout="", stderr="unsupported")

        empty_evidence = {
            "parts": {},
            "regulatory_evidence": [],
            "source_health": [],
            "gaps": [],
        }
        failed_source = {"status": "fail", "error_class": "offline", "stderr_tail": ""}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            subject, "collect_company_evidence", return_value=empty_evidence
        ), mock.patch.object(subject, "run_py", return_value=failed_source):
            got = subject.us_share_fundamentals(
                "MSFT", consensus_runner=runner, consensus_root=Path(tmp)
            )

        self.assertEqual(got["status"], "fail")
        self.assertNotIn("consensus", got["parts"])
        self.assertIn("EPS consensus unavailable", [row["gap"] for row in got["gaps"]])
        self.assertIn("fundamental parts empty", [row["gap"] for row in got["gaps"]])


if __name__ == "__main__":
    unittest.main()
