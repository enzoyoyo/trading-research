#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import earnings_implied_distribution as subject


def contract(code: str, bid: object, ask: object) -> dict[str, object]:
    return {"option": code, "bid": bid, "ask": ask, "iv": 0.2, "delta": 0.5}


class EarningsImpliedDistributionTests(unittest.TestCase):
    def test_atm_straddle_implied_move_and_rich_label(self) -> None:
        payload = {
            "timestamp": "2026-08-21T20:00:00Z",
            "data": {
                "current_price": 100,
                "options": [
                    contract("MSFT260821C00095000", 7, 9),
                    contract("MSFT260821P00095000", 1, 3),
                    contract("MSFT260821C00100000", 4, 6),
                    contract("MSFT260821P00100000", 3, 5),
                    contract("MSFT260918C00100000", 8, 10),
                    contract("MSFT260918P00100000", 7, 9),
                ],
            },
        }

        got = subject.calculate_distribution(
            "MSFT", payload, expiry="2026-08-21", hist_median=0.06, observed_on="2026-08-21"
        )

        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["expiry"], "2026-08-21")
        self.assertEqual(got["atm_strike"], 100.0)
        self.assertEqual(got["atm_call_mid"], 5.0)
        self.assertEqual(got["atm_put_mid"], 4.0)
        self.assertEqual(got["implied_move_premium_pct"], 0.09)
        self.assertEqual(got["implied_vs_typical_ratio"], 1.5)
        self.assertEqual(got["relative_value_label"], "RICH")
        self.assertEqual(got["method_source"], "balder_public_docs")
        self.assertTrue(got["risk_neutral"])
        self.assertFalse(got["is_direction_prediction"])
        self.assertEqual(got["position_multiplier"], 0.0)
        self.assertTrue(got["no_order_execution"])

    def test_missing_or_crossed_mid_fails_closed(self) -> None:
        payload = {
            "data": {
                "current_price": 100,
                "options": [
                    contract("MSFT260821C00100000", None, 6),
                    contract("MSFT260821P00100000", 6, 5),
                ],
            },
        }

        got = subject.calculate_distribution(
            "MSFT", payload, expiry="2026-08-21", hist_median=0.06, observed_on="2026-08-21"
        )

        self.assertEqual(got["status"], "insufficient_data")
        self.assertIsNone(got["implied_move_premium_pct"])
        self.assertIsNone(got["implied_vs_typical_ratio"])
        self.assertIsNone(got["relative_value_label"])
        self.assertIn("atm_call_put_mid_unavailable", [row["gap"] for row in got["data_gaps"]])

    def test_nearest_expiry_without_event_date_is_disclosed_not_directional(self) -> None:
        payload = {
            "data": {
                "current_price": 100,
                "options": [
                    contract("MSFT260821C00100000", 4, 6),
                    contract("MSFT260821P00100000", 3, 5),
                ],
            },
        }

        got = subject.calculate_distribution("MSFT", payload, observed_on="2026-08-20")

        self.assertEqual(got["expiry_selection"], "nearest_available_not_earnings_verified")
        self.assertIn("earnings_release_date_unavailable_expiry_not_verified", [row["gap"] for row in got["data_gaps"]])
        self.assertEqual(got["status"], "partial")

    def test_hist_median_reads_b2_cache_when_not_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "MSFT.json").write_text(json.dumps({"hist_median": 0.08, "sample_count": 8}), encoding="utf-8")

            value, source, gaps = subject.resolve_hist_median("MSFT", None, move_root=root)

            self.assertEqual((value, source, gaps), (0.08, "earnings_move_history_cache", []))


if __name__ == "__main__":
    unittest.main()
