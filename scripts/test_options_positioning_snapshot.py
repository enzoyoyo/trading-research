from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import options_positioning_snapshot as subject


def option(code: str, *, volume: int, oi: int, iv: float, delta: float) -> dict[str, object]:
    return {
        "option": code,
        "volume": volume,
        "open_interest": oi,
        "iv": iv,
        "delta": delta,
        "bid": 1.0,
        "ask": 1.2,
    }


class PositioningCalculationTests(unittest.TestCase):
    def test_computes_full_chain_totals_and_nearest_expiry_iv_metrics(self) -> None:
        payload = {
            "timestamp": "2026-08-21T20:00:00Z",
            "data": {
                "current_price": 101.0,
                "options": [
                    option("MSFT260821C00100000", volume=100, oi=500, iv=0.20, delta=0.55),
                    option("MSFT260821P00100000", volume=80, oi=600, iv=0.24, delta=-0.45),
                    option("MSFT260821C00105000", volume=50, oi=300, iv=0.22, delta=0.25),
                    option("MSFT260821P00095000", volume=70, oi=400, iv=0.27, delta=-0.25),
                    option("MSFT260918C00100000", volume=40, oi=200, iv=0.30, delta=0.50),
                    option("MSFT260918P00100000", volume=20, oi=100, iv=0.32, delta=-0.50),
                ],
            },
        }

        got = subject.calculate_snapshot("MSFT", payload, observed_on="2026-08-21")

        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["put_call_volume_ratio"], 170 / 190)
        self.assertEqual(got["oi_total"], 2100)
        self.assertEqual(got["volume_total"], 360)
        self.assertEqual(got["atm_expiry"], "2026-08-21")
        self.assertEqual(got["atm_strike"], 100.0)
        self.assertEqual(got["atm_iv"], 0.22)
        self.assertEqual(got["iv_skew_pp"], 5.0)
        self.assertEqual(got["signed_flow_direction_weight"], 0.0)
        self.assertIn("signed_flow_unavailable_no_open_close_fields", [g["gap"] for g in got["data_gaps"]])
        self.assertTrue(got["no_order_execution"])

    def test_missing_required_inputs_publish_nulls_and_structured_gaps(self) -> None:
        payload = {
            "data": {
                "current_price": 101.0,
                "options": [
                    option("MSFT260821P00100000", volume=80, oi=600, iv=0.24, delta=-0.45),
                ],
            },
        }

        got = subject.calculate_snapshot("MSFT", payload, observed_on="2026-08-21")

        gaps = {row["gap"] for row in got["data_gaps"]}
        self.assertIsNone(got["put_call_volume_ratio"])
        self.assertIsNone(got["atm_iv"])
        self.assertIsNone(got["iv_skew_pp"])
        self.assertTrue({"call_volume_zero", "atm_iv_inputs_missing", "iv_skew_inputs_missing"} <= gaps)


class FetchAndCacheTests(unittest.TestCase):
    def test_http_uses_20_second_timeout_one_retry_and_fail_closed_gap(self) -> None:
        calls: list[int] = []

        def fail(_request: object, *, timeout: int) -> object:
            calls.append(timeout)
            raise urllib.error.URLError("offline")

        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://secret-proxy.invalid"}, clear=False):
            payload, gap = subject.fetch_cboe_payload("MSFT", opener=fail)
            self.assertNotIn("HTTPS_PROXY", os.environ)

        self.assertIsNone(payload)
        self.assertEqual(calls, [20, 20])
        self.assertEqual(gap["gap"], "cboe_fetch_failed")
        self.assertEqual(gap["attempts"], 2)

    def test_daily_cache_derives_oi_delta_only_from_previous_valid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = subject.persist_snapshot(
                {"symbol": "MSFT", "oi_total": 1000, "data_gaps": []},
                root=root,
                snapshot_date=date(2026, 8, 20),
            )
            second = subject.persist_snapshot(
                {"symbol": "MSFT", "oi_total": 1125, "data_gaps": []},
                root=root,
                snapshot_date=date(2026, 8, 21),
            )

            self.assertIsNone(first["oi_delta"])
            self.assertIn("oi_history_insufficient", [row["gap"] for row in first["data_gaps"]])
            self.assertEqual(second["oi_delta"], 125)
            self.assertEqual(second["oi_delta_previous_snapshot_date"], "2026-08-20")
            cached = json.loads((root / "MSFT" / "2026-08-21.json").read_text(encoding="utf-8"))
            self.assertEqual(cached["oi_delta"], 125)

    def test_cli_contract_uses_snapshot_subcommand(self) -> None:
        args = subject.build_parser().parse_args(["snapshot", "--symbol", "MSFT", "--json"])
        self.assertEqual((args.command, args.symbol, args.json), ("snapshot", "MSFT", True))


if __name__ == "__main__":
    unittest.main()
