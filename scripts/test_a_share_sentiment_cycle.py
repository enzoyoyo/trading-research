#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_freshness_guard import parse_trade_date, validate_as_of_alignment
from a_share_sentiment_cycle import (
    classify_emotion_phase,
    classify_fund_flow_state,
    self_test as sentiment_self_test,
)


class DataFreshnessGuardTests(unittest.TestCase):
    def test_parse_trade_date(self) -> None:
        self.assertEqual(parse_trade_date("20260819").isoformat(), "2026-08-19")

    def test_mismatch_blocks_formal_write(self) -> None:
        payload = validate_as_of_alignment(
            as_of="2026-08-18",
            target_trade_date="20260819",
            observed_at="2026-08-19T07:00:00Z",
        )
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["may_write_formal_conclusion"])


class ASentimentCycleTests(unittest.TestCase):
    def test_emotion_phase_ferment(self) -> None:
        phase = classify_emotion_phase(
            {
                "zt_count": 45,
                "zb_count": 8,
                "dt_count": 3,
                "max_board_height": 4,
                "zb_rate": 8 / 45,
                "yesterday_zt_premium_pct": 2.0,
            }
        )
        self.assertEqual(phase["phase"], "ferment")

    def test_fund_flow_recovery(self) -> None:
        state = classify_fund_flow_state(
            [
                {"main_net": -2},
                {"main_net": -1},
                {"main_net": -0.5},
                {"main_net": 1.2},
            ]
        )
        self.assertEqual(state["state"], "recovery_inflow")

    def test_self_test_passes(self) -> None:
        payload = sentiment_self_test()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["self_test"], "passed")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
