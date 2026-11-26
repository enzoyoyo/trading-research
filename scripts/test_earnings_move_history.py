#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import earnings_move_history as subject


class EarningsMoveHistoryTests(unittest.TestCase):
    def test_session_aligned_amc_and_bmo_moves_are_separate(self) -> None:
        events = [
            {"form_type": "8-K", "filed_at": "2026-04-20T16:05:00-04:00", "source_family": "sec_edgar", "accession_number": "A"},
            {"form_type": "8-K", "filed_at": "2026-05-05T08:00:00-04:00", "source_family": "sec_edgar", "accession_number": "B"},
        ]
        candles = [
            {"time": "2026-04-20", "open": 99, "close": 100},
            {"time": "2026-04-21", "open": 110, "close": 108},
            {"time": "2026-05-04", "open": 201, "close": 200},
            {"time": "2026-05-05", "open": 190, "close": 180},
        ]

        got = subject.build_history("MSFT", events, candles, n=8, min_samples=2)

        by_session = {row["session"]: row for row in got["events"]}
        self.assertAlmostEqual(by_session["AMC"]["overnight_abs_move"], 0.10)
        self.assertAlmostEqual(by_session["AMC"]["close_to_close_abs_move"], 0.08)
        self.assertEqual(by_session["AMC"]["primary_window"], "close_T_to_close_T_plus_1")
        self.assertAlmostEqual(by_session["BMO"]["overnight_abs_move"], 0.05)
        self.assertAlmostEqual(by_session["BMO"]["close_to_close_abs_move"], 0.10)
        self.assertEqual(by_session["BMO"]["primary_window"], "close_T_minus_1_to_close_T")
        self.assertAlmostEqual(got["hist_median"], 0.09)
        self.assertEqual(got["sample_count"], 2)
        self.assertEqual(got["status"], "partial")
        self.assertTrue(got["no_order_execution"])

    def test_open_boundary_uses_new_york_dst_not_fixed_utc_offset(self):
        for stamp, expected in [
            ("2026-01-20T14:29:00Z", "BMO"), ("2026-01-20T14:31:00Z", "intraday"),
            ("2026-07-20T13:29:00Z", "BMO"), ("2026-07-20T13:31:00Z", "intraday"),
            ("2026-01-20T21:05:00Z", "AMC"), ("2026-07-20T20:05:00Z", "AMC"),
        ]:
            with self.subTest(stamp=stamp):
                self.assertEqual(subject._classify_session(stamp)[0], expected)

    def test_unknown_session_and_small_sample_fail_closed(self) -> None:
        events = [
            {"form_type": "8-K", "filed_at": "2026-04-20", "source_family": "sec_edgar", "accession_number": "A"},
        ]
        candles = [
            {"time": "2026-04-20", "open": 99, "close": 100},
            {"time": "2026-04-21", "open": 110, "close": 108},
        ]

        got = subject.build_history("MSFT", events, candles, n=8, min_samples=2)

        self.assertIsNone(got["events"][0]["abs_move"])
        self.assertIsNone(got["hist_median"])
        self.assertEqual(got["sample_count"], 0)
        self.assertEqual(got["status"], "insufficient_data")
        self.assertIn("historical_sample_insufficient", [row["gap"] for row in got["data_gaps"]])

    def test_only_sec_8k_rows_are_event_candidates(self) -> None:
        rows = [
            {"form_type": "10-Q", "filed_at": "2026-01-01T08:00:00-05:00", "source_family": "sec_edgar"},
            {"form_type": "8-K/A", "filed_at": "2026-01-02T08:00:00-05:00", "source_family": "sec_edgar"},
            {"form_type": "8-K", "filed_at": "2026-01-03T08:00:00-05:00", "source_family": "longbridge"},
            {"form_type": "8-K", "filed_at": "2026-01-04T08:00:00-05:00", "source_family": "sec_edgar"},
        ]

        got = subject.select_sec_8k_events(rows, n=8)

        self.assertEqual([row["filed_at"] for row in got], ["2026-01-04T08:00:00-05:00"])

    def test_longbridge_candle_command_is_bounded_and_read_only(self) -> None:
        command = subject.longbridge_candle_command("MSFT", count=600)
        self.assertEqual(command[-7:], ["candle", "MSFT.US", "--period", "day", "--count", "600", "--json"])


if __name__ == "__main__":
    unittest.main()
