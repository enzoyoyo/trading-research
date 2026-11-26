#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import okx_public_snapshot  # noqa: E402


NOW = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)


def envelope(data: list, code: str = "0", msg: str = "") -> dict:
    return {"code": code, "msg": msg, "data": data}


def instrument() -> dict:
    return {
        "instType": "SPOT",
        "instId": "XMU-USDT",
        "baseCcy": "XMU",
        "quoteCcy": "USDT",
        "state": "live",
        "instCategory": "3",
        "tickSz": "0.01",
        "lotSz": "0.000001",
        "minSz": "0.001",
        "listTime": "1784163600000",
    }


def ticker() -> dict:
    return {
        "instId": "XMU-USDT",
        "last": "100",
        "askPx": "100.1",
        "bidPx": "99.9",
        "open24h": "95",
        "high24h": "102",
        "low24h": "94",
        "vol24h": "1000",
        "volCcy24h": "100000",
        "ts": "1785167990000",
    }


def book() -> dict:
    return {
        "asks": [["100.1", "5", "0", "2"], ["100.2", "10", "0", "2"]],
        "bids": [["99.9", "6", "0", "2"], ["99.8", "10", "0", "2"]],
        "ts": "1785167990000",
        "seqId": 123,
    }


def completed_candles() -> list[list[str]]:
    # Newest first. Last element is confirmation flag (1=complete).
    return [
        ["1785024000000", "94", "101", "93", "98", "100", "9800", "9800", "1"],
        ["1784937600000", "92", "96", "91", "94", "90", "8460", "8460", "1"],
        ["1784851200000", "91", "94", "89", "92", "80", "7360", "7360", "1"],
        ["1784764800000", "90", "92", "88", "91", "70", "6370", "6370", "1"],
        ["1784678400000", "89", "91", "87", "90", "60", "5400", "5400", "1"],
    ]


class OkxPublicSnapshotTests(unittest.TestCase):
    def build(self, **overrides) -> dict:
        values = {
            "inst_id": "XMU-USDT",
            "instrument_response": envelope([instrument()]),
            "ticker_response": envelope([ticker()]),
            "book_response": envelope([book()]),
            "candles_1d_response": envelope(completed_candles()),
            "candles_4h_response": envelope(completed_candles()),
            "fetched_at": NOW,
            "site": "global",
        }
        values.update(overrides)
        return okx_public_snapshot.build_public_snapshot(**values)

    def test_builds_auditable_tokenized_stock_snapshot_without_credentials(self) -> None:
        result = self.build()
        self.assertEqual(result["schema_version"], "okx_public_market_snapshot.v1")
        self.assertEqual(result["venue"], "okx")
        self.assertEqual(result["channel"], "cex_spot")
        self.assertEqual(result["mode"], "public")
        self.assertEqual(result["instrument"]["inst_id"], "XMU-USDT")
        self.assertEqual(result["instrument"]["inst_category"], "3")
        self.assertEqual(result["product_identity"]["instrument_id"], "XMU-USDT")
        self.assertTrue(result["product_identity"]["mapping_verified"])
        self.assertIsNone(result["product_identity"]["region_eligible"])
        self.assertEqual(result["product_identity"]["evidence_refs"], ["OKX-PUBLIC-INSTRUMENT:XMU-USDT"])
        self.assertEqual(result["market"]["last"], 100.0)
        self.assertEqual(result["market"]["spread_bps"], 20.0)
        self.assertEqual(result["market"]["top5_bid_notional"], 1597.4)
        self.assertEqual(result["technical_observables"]["completed_1d_candles"], 5)
        self.assertAlmostEqual(result["technical_observables"]["return_1d_pct"], 2.0408, places=4)
        self.assertAlmostEqual(result["technical_observables"]["return_5d_pct"], 11.1111, places=4)
        self.assertFalse(result["credentials_used"])
        self.assertTrue(result["no_order_execution"])

    def test_category_mismatch_is_an_identity_failure(self) -> None:
        row = instrument()
        row["instCategory"] = "1"
        with self.assertRaisesRegex(ValueError, "instCategory"):
            self.build(instrument_response=envelope([row]))

    def test_non_live_instrument_is_not_presented_as_tradeable(self) -> None:
        row = instrument()
        row["state"] = "suspend"
        with self.assertRaisesRegex(ValueError, "not live"):
            self.build(instrument_response=envelope([row]))

    def test_empty_market_data_raises_instead_of_emitting_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "ticker"):
            self.build(ticker_response=envelope([]))
        with self.assertRaisesRegex(ValueError, "order book"):
            self.build(book_response=envelope([]))

    def test_okx_business_error_is_not_treated_as_empty_success(self) -> None:
        with self.assertRaisesRegex(ValueError, "51000"):
            self.build(ticker_response=envelope([], code="51000", msg="invalid instrument"))

    def test_inst_id_and_site_are_strict(self) -> None:
        for bad in ("MU-USDT", "XMU/USDT", "XMU-USDC", "https://example.com"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "inst_id"):
                self.build(inst_id=bad)
        with self.assertRaisesRegex(ValueError, "site"):
            self.build(site="custom")

    def test_incomplete_candles_do_not_count_as_completed_history(self) -> None:
        candles = completed_candles()
        candles.insert(0, ["1785110400000", "99", "103", "98", "101", "5", "500", "500", "0"])
        result = self.build(candles_1d_response=envelope(candles))
        self.assertEqual(result["technical_observables"]["completed_1d_candles"], 5)
        self.assertEqual(result["technical_observables"]["incomplete_1d_candles"], 1)

    def test_non_positive_instrument_increments_fail_closed(self) -> None:
        for field in ("tickSz", "lotSz", "minSz"):
            row = instrument()
            row[field] = "0"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "positive"):
                self.build(instrument_response=envelope([row]))

    def test_zero_notional_book_side_fails_closed(self) -> None:
        row = book()
        row["bids"] = [["99.9", "0", "0", "1"]]
        with self.assertRaisesRegex(ValueError, "positive size"):
            self.build(book_response=envelope([row]))

    def test_zero_size_book_level_cannot_publish_bbo_or_hide_behind_deeper_depth(self) -> None:
        for side, rows in (
            ("bids", [["99.9", "0", "0", "1"], ["99.8", "10", "0", "1"]]),
            ("asks", [["100.1", "0", "0", "1"], ["100.2", "10", "0", "1"]]),
        ):
            row = book()
            row[side] = rows
            with self.subTest(side=side), self.assertRaisesRegex(ValueError, "positive size"):
                self.build(book_response=envelope([row]))

    def test_book_levels_must_be_strictly_sorted_before_publishing_bbo(self) -> None:
        for side, rows, message in (
            ("bids", [["99.8", "10", "0", "1"], ["99.9", "10", "0", "1"]], "descending"),
            ("asks", [["100.2", "10", "0", "1"], ["100.1", "10", "0", "1"]], "ascending"),
            ("bids", [["99.9", "10", "0", "1"], ["99.9", "5", "0", "1"]], "descending"),
            ("asks", [["100.1", "10", "0", "1"], ["100.1", "5", "0", "1"]], "ascending"),
        ):
            row = book()
            row[side] = rows
            with self.subTest(side=side, rows=rows), self.assertRaisesRegex(ValueError, message):
                self.build(book_response=envelope([row]))

    def test_bid_ask_and_spread_use_one_order_book_snapshot(self) -> None:
        ticker_row = ticker()
        ticker_row["bidPx"] = "90"
        ticker_row["askPx"] = "110"
        result = self.build(ticker_response=envelope([ticker_row]))
        self.assertEqual(result["market"]["bid"], 99.9)
        self.assertEqual(result["market"]["ask"], 100.1)
        self.assertEqual(result["market"]["spread_bps"], 20.0)
        self.assertEqual(result["market"]["bid"], result["market"]["top5_bids"][0]["price"])
        self.assertEqual(result["market"]["ask"], result["market"]["top5_asks"][0]["price"])

    def test_order_book_explicit_instrument_mismatch_fails_closed(self) -> None:
        row = book()
        row["instId"] = "XSKHY-USDT"
        with self.assertRaisesRegex(ValueError, "order book instId"):
            self.build(book_response=envelope([row]))

    def test_future_listing_or_market_timestamps_fail_closed(self) -> None:
        future_ms = str(int((NOW.timestamp() + 86400) * 1000))
        instrument_row = instrument()
        instrument_row["listTime"] = future_ms
        with self.assertRaisesRegex(ValueError, "future listing"):
            self.build(instrument_response=envelope([instrument_row]))

        ticker_row = ticker()
        ticker_row["ts"] = future_ms
        with self.assertRaisesRegex(ValueError, "future market timestamp"):
            self.build(ticker_response=envelope([ticker_row]))

    def test_stale_market_timestamp_fails_closed(self) -> None:
        stale_ms = str(int((NOW.timestamp() - 121) * 1000))
        ticker_row = ticker()
        ticker_row["ts"] = stale_ms
        with self.assertRaisesRegex(ValueError, "stale market timestamp"):
            self.build(ticker_response=envelope([ticker_row]))

    def test_huge_integer_public_numeric_field_fails_closed_without_overflow(self) -> None:
        ticker_row = ticker()
        ticker_row["last"] = int("1" + "0" * 400)

        with self.assertRaisesRegex(ValueError, "invalid numeric field: ticker.last"):
            self.build(ticker_response=envelope([ticker_row]))

    def test_extreme_finite_bbo_keeps_midpoint_and_spread_finite(self) -> None:
        ticker_row = ticker()
        ticker_row.update({"last": "1.75e308", "bidPx": "1.70e308", "askPx": "1.79e308"})
        book_row = book()
        book_row["bids"] = [["1.70e308", "1"]]
        book_row["asks"] = [["1.79e308", "1"]]
        candles = completed_candles()
        for row in candles:
            row[4] = "1.70e308"

        result = self.build(
            ticker_response=envelope([ticker_row]),
            book_response=envelope([book_row]),
            candles_1d_response=envelope(candles),
        )

        self.assertTrue(math.isfinite(result["market"]["midpoint"]))
        self.assertTrue(math.isfinite(result["market"]["spread_bps"]))
        self.assertGreaterEqual(result["market"]["midpoint"], result["market"]["bid"])
        self.assertLessEqual(result["market"]["midpoint"], result["market"]["ask"])

    def test_book_level_notional_arithmetic_overflow_fails_closed(self) -> None:
        ticker_row = ticker()
        ticker_row.update({"last": "1.05e308", "bidPx": "1e308", "askPx": "1.1e308"})
        book_row = book()
        book_row["bids"] = [["1e308", "10"]]
        book_row["asks"] = [["1.1e308", "1"]]

        with self.assertRaisesRegex(ValueError, r"invalid derived numeric field: bids\[0\].notional"):
            self.build(ticker_response=envelope([ticker_row]), book_response=envelope([book_row]))

    def test_book_total_notional_arithmetic_overflow_fails_closed(self) -> None:
        ticker_row = ticker()
        ticker_row.update({"last": "4.5e307", "bidPx": "4e307", "askPx": "5e307"})
        book_row = book()
        book_row["bids"] = [[value, "1"] for value in ("4e307", "3.9e307", "3.8e307", "3.7e307", "3.6e307")]
        book_row["asks"] = [["5e307", "1"]]

        with self.assertRaisesRegex(ValueError, "invalid derived numeric field: bids.total_notional"):
            self.build(ticker_response=envelope([ticker_row]), book_response=envelope([book_row]))

    def test_return_percentage_arithmetic_overflow_fails_closed(self) -> None:
        ticker_row = ticker()
        ticker_row["last"] = "1e308"
        candles = completed_candles()
        for row in candles:
            row[4] = "1e-308"

        with self.assertRaisesRegex(ValueError, "invalid derived numeric field: return_1d_pct"):
            self.build(ticker_response=envelope([ticker_row]), candles_1d_response=envelope(candles))

    def test_positive_subnormal_midpoint_and_notional_are_not_quantized_to_zero(self) -> None:
        ticker_row = ticker()
        ticker_row.update({"last": "1.5e-308", "bidPx": "1e-308", "askPx": "2e-308"})
        book_row = book()
        book_row["bids"] = [["1e-308", "0.1"]]
        book_row["asks"] = [["2e-308", "0.1"]]
        candles = completed_candles()
        for row in candles:
            row[4] = "1e-308"

        result = self.build(
            ticker_response=envelope([ticker_row]),
            book_response=envelope([book_row]),
            candles_1d_response=envelope(candles),
        )

        self.assertGreater(result["market"]["midpoint"], 0.0)
        self.assertGreater(result["market"]["top5_bids"][0]["notional"], 0.0)
        self.assertGreater(result["market"]["top5_bid_notional"], 0.0)

    def test_fetch_timestamp_is_captured_after_all_public_responses(self) -> None:
        events: list[str] = []

        class RecordingDateTime:
            @classmethod
            def now(cls, _tz: timezone) -> datetime:
                events.append("clock")
                return NOW

        payloads = [
            envelope([instrument()]),
            envelope([ticker()]),
            envelope([book()]),
            envelope(completed_candles()),
            envelope(completed_candles()),
        ]

        def fake_get_json(*_args: object, **_kwargs: object) -> dict:
            events.append("fetch")
            return payloads.pop(0)

        with (
            patch.object(okx_public_snapshot, "datetime", RecordingDateTime),
            patch.object(okx_public_snapshot, "_get_json", side_effect=fake_get_json),
            patch.object(okx_public_snapshot, "build_public_snapshot", return_value={"ok": True}),
        ):
            okx_public_snapshot.fetch_public_snapshot("XMU-USDT", site="global", timeout=1.0)
        self.assertEqual(events, ["fetch", "fetch", "fetch", "fetch", "fetch", "clock"])


if __name__ == "__main__":
    unittest.main()
