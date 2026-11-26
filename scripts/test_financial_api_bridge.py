#!/usr/bin/env python3
"""Synthetic contract fixtures only; these are not fetched quotes or outcomes."""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import financial_api_bridge as bridge

NOW = datetime(2026, 9, 4, 7, 1, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)
TEST_KEY = "synthetic-fixture-only-credential"


class Response(io.BytesIO):
    status = 200


class FakeOpener:
    def __init__(self, payload=None, error=None, raw=None):
        self.raw = raw if raw is not None else json.dumps(payload).encode()
        self.error = error
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return Response(self.raw)


def envelope(items, timestamp=STAMP, **extra):
    return {"code": 0, "message": "synthetic fixture", "request_id": "fixture-id",
            "data": {"timestamp": timestamp, "item": items, **extra}}


class FinancialApiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"FINANCIAL_API_KEY": TEST_KEY})
        self.env.start()
        self.addCleanup(self.env.stop)

    def fetch(self, command, items, *, options=None, timestamp=STAMP):
        client = FakeOpener(envelope(items, timestamp))
        return bridge.fetch(command, options=options, opener=client, now=NOW), client

    def test_missing_auth_makes_no_network_request(self):
        client = FakeOpener({})
        with patch.dict(os.environ, {"FINANCIAL_API_KEY": ""}):
            result = bridge.fetch("calendar", opener=client, now=NOW)
        self.assertEqual(result["status"], "auth_missing")
        self.assertEqual(client.calls, [])
        self.assertIsNone(result["data"])

    def test_does_not_read_other_credential_alias(self):
        with patch.dict(os.environ, {"FINANCIAL_API_KEY": "", "HITHINK_FINANCE_API_KEY": TEST_KEY}):
            result = bridge.fetch("calendar", now=NOW)
        self.assertEqual(result["status"], "auth_missing")

    def test_get_fixed_origin_and_header_only(self):
        result, client = self.fetch("quote", [{"thscode": "600519.SH", "last_price": 123.45}],
                                    options={"symbols": ["600519.SH"]}, timestamp=None)
        request, timeout = client.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.startswith(bridge.BASE_URL + "/api/a-share/prices/snapshot?"))
        self.assertEqual(request.get_header("X-api-key"), TEST_KEY)
        self.assertNotIn(TEST_KEY, request.full_url)
        self.assertNotIn(TEST_KEY, json.dumps(result))
        self.assertEqual(timeout, 20)

    def test_null_quote_time_is_not_replaced_with_fetch_time(self):
        result, _ = self.fetch("quote", [{"thscode": "600519.SH", "last_price": 123.45}],
                               options={"symbols": ["600519.SH"]}, timestamp=None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["as_of"])
        self.assertIsNone(result["items"][0]["as_of"])
        self.assertEqual(result["freshness"]["status"], "missing")
        self.assertFalse(result["may_write_formal_conclusion"])
        self.assertEqual(result["suggested_module_signals"], [])
        self.assertTrue(result["no_order_execution"])

    def test_quote_mapping_units_and_missing_values(self):
        result, _ = self.fetch("quote", [{"thscode": "600519.SH", "last_price": 123.45,
                               "prev_price": 120, "price_change_ratio_pct": 2.875,
                               "turnover": 10000, "volume": 80, "open_price": None}],
                               options={"symbols": ["600519.SH"]}, timestamp=None)
        row = result["items"][0]
        self.assertEqual(row["price"], 123.45)
        self.assertEqual(row["prev_close"], 120)
        self.assertEqual(row["change_pct"], 2.875)
        self.assertEqual(row["volume"], 80)
        self.assertIsNone(row["open"])
        self.assertIsNone(row["high"])
        self.assertEqual(result["units"]["volume"], "shares")

    def test_missing_requested_symbol_is_explicit(self):
        result, _ = self.fetch("quote", [{"thscode": "600519.SH", "last_price": 10}],
                               options={"symbols": ["600519.SH", "000001.SZ"]})
        self.assertEqual(result["coverage"]["missing_symbols"], ["000001.SZ"])
        self.assertIn("financial_api_symbols_missing", [g["gap"] for g in result["data_gaps"]])

    def test_unexpected_or_duplicate_rows_fail_closed(self):
        for rows in ([{"thscode": "000001.SZ"}], [{"thscode": "600519.SH"}] * 2):
            with self.subTest(rows=rows):
                result, _ = self.fetch("valuation", rows, options={"symbols": ["600519.SH"]})
                self.assertEqual(result["status"], "schema_error")
                self.assertFalse(result["ok"])
                self.assertEqual(result["items"], [])

    def test_negative_and_null_valuation_are_preserved(self):
        result, _ = self.fetch("valuation", [{"thscode": "600519.SH", "pe_ttm": -12.5,
                               "pb_mrq": None, "pcf_ttm": 0}], options={"symbols": ["600519.SH"]})
        row = result["items"][0]
        self.assertEqual(row["pe_ttm"], -12.5)
        self.assertIsNone(row["pb_mrq"])
        self.assertEqual(row["pcf_ttm"], 0)
        self.assertFalse(result["freshness"]["per_item_timestamp_verified"])

    def test_auth_business_failure_under_http_200_is_not_empty_success(self):
        for code, state in [(2001, "auth_missing"), (2003, "auth_invalid"),
                            (3002, "not_ready"), (3004, "unsupported"), (4001, "rate_limited"),
                            (5001, "error"), (1003, "invalid_request")]:
            with self.subTest(code=code):
                client = FakeOpener({"code": code, "message": TEST_KEY, "request_id": "fixture-id", "data": None})
                result = bridge.fetch("calendar", opener=client, now=NOW)
                self.assertEqual(result["status"], state)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["data"])
                self.assertNotIn(TEST_KEY, json.dumps(result))
                self.assertEqual(len(client.calls), 1)

    def test_http_failures_are_sanitized_and_not_retried(self):
        for code, state in [(401, "auth_missing"), (403, "auth_invalid"), (429, "rate_limited"),
                            (302, "redirect_blocked"), (503, "error")]:
            with self.subTest(code=code):
                error = urllib.error.HTTPError("https://untrusted.invalid/" + TEST_KEY, code, TEST_KEY, {}, None)
                client = FakeOpener(error=error)
                result = bridge.fetch("calendar", opener=client, now=NOW)
                self.assertEqual(result["status"], state)
                self.assertNotIn(TEST_KEY, json.dumps(result))
                self.assertEqual(len(client.calls), 1)

    def test_redirect_handler_refuses_any_target(self):
        self.assertIsNone(bridge.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_network_exception_detail_is_never_logged(self):
        client = FakeOpener(error=urllib.error.URLError(TEST_KEY))
        result = bridge.fetch("calendar", opener=client, now=NOW)
        self.assertEqual(result["status"], "network_error")
        self.assertNotIn(TEST_KEY, json.dumps(result))

    def test_reflected_secret_is_redacted_even_in_success_body(self):
        result, _ = self.fetch("search", [{"thscode": "600519.SH", "name": TEST_KEY}],
                               options={"query": "600519"})
        self.assertNotIn(TEST_KEY, json.dumps(result))
        self.assertEqual(result["items"][0]["name"], "[REDACTED]")

    def test_bad_or_non_finite_response_is_schema_error(self):
        for raw in [b"not-json", b'{"code":true,"data":{}}', b'{"code":0,"data":null}',
                    b'{"code":0,"data":{"item":[NaN]}}', b'{"code":0,"data":{"item":[4]}}',
                    b'{"code":0,"data":{"item":[{"value":1e999}]}}']:
            with self.subTest(raw=raw):
                result = bridge.fetch("calendar", opener=FakeOpener(raw=raw), now=NOW)
                self.assertEqual(result["status"], "schema_error")
                self.assertFalse(result["ok"])

    def test_large_response_rejected(self):
        result = bridge.fetch("calendar", opener=FakeOpener(raw=b"x" * (bridge.MAX_BYTES + 1)), now=NOW)
        self.assertEqual(result["status"], "oversized")

    def test_future_stale_seconds_and_missing_time_are_not_fresh(self):
        cases = [(STAMP + 120000, "future"), (STAMP - 90000000, "stale"),
                 (STAMP / 1000, "missing"), (None, "missing")]
        for timestamp, status in cases:
            with self.subTest(timestamp=timestamp):
                result, _ = self.fetch("calendar", [{"date": "20260904", "date_ms": STAMP}], timestamp=timestamp)
                self.assertEqual(result["freshness"]["status"], status)
                self.assertFalse(result["may_write_formal_conclusion"])

    def test_empty_result_is_missing(self):
        result, _ = self.fetch("calendar", [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing")

    def test_symbols_not_inferred_or_deduplicated_before_limit_check(self):
        for symbols in [["600519"], ["AAPL.US"], ["600519.SH"] * 101, [], [""]]:
            with self.subTest(symbols=symbols):
                client = FakeOpener({})
                result = bridge.fetch("valuation", options={"symbols": symbols}, opener=client, now=NOW)
                self.assertEqual(result["status"], "invalid_request")
                self.assertEqual(client.calls, [])

    def test_arbitrary_path_base_url_and_secret_query_are_rejected(self):
        for command, options in [("order", {}), ("calendar", {"url": "http://localhost"}),
                                 ("calendar", {"base_url": "https://other.invalid"}),
                                 ("search", {"query": "sk-do-not-send-this"})]:
            with self.subTest(command=command, options=options):
                client = FakeOpener({})
                result = bridge.fetch(command, options=options, opener=client, now=NOW)
                self.assertEqual(result["status"], "invalid_request")
                self.assertEqual(client.calls, [])

    def test_daily_history_is_page_only_and_adjustment_explicit(self):
        options = {"symbol": "600519.SH", "start": STAMP - 100000000, "end": STAMP,
                   "adjust": "forward"}
        result, client = self.fetch("history", [{"date_ms": STAMP - 86400000,
                        "close_price": 123.45, "volume": None}], options=options)
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"]["status"], "page_only")
        self.assertFalse(result["coverage"]["complete_series_verified"])
        self.assertIn("interval=1d", client.calls[0][0].full_url)
        self.assertIn("adjust=forward", client.calls[0][0].full_url)
        self.assertIsNone(result["items"][0]["volume"])

    def test_intraday_history_and_out_of_window_bars_rejected(self):
        options = {"symbol": "600519.SH", "start": STAMP - 100000000, "end": STAMP}
        result = bridge.fetch("history", options={**options, "interval": "1m"}, now=NOW)
        self.assertEqual(result["status"], "invalid_request")
        result, _ = self.fetch("history", [{"date_ms": STAMP + 1}], options=options)
        self.assertEqual(result["status"], "schema_error")

    def test_financial_null_and_disclosure_date_preserved(self):
        result, _ = self.fetch("income", [{"thscode": "600519.SH", "report_date_ms": STAMP - 86400000,
                              "period_end_ms": STAMP - 864000000, "operating_income": None}],
                              options={"symbol": "600519.SH"})
        self.assertIsNone(result["items"][0]["operating_income"])
        self.assertIn("financial_api_point_in_time_revision_history_unverified",
                      [g["gap"] for g in result["data_gaps"]])

    def test_future_disclosure_does_not_become_point_in_time_fact(self):
        result, _ = self.fetch("income", [{"thscode": "600519.SH", "report_date_ms": STAMP + 86400000}],
                               options={"symbol": "600519.SH"})
        self.assertIn("financial_api_disclosure_date_missing_or_future", [g["gap"] for g in result["data_gaps"]])
        self.assertFalse(result["may_write_formal_conclusion"])

    def test_dates_use_shanghai_boundaries(self):
        self.assertEqual(bridge.date_ms("2026-09-04"), 1788451200000)
        self.assertEqual(bridge.date_ms("2026-09-04", end=True) - bridge.date_ms("2026-09-04"), 86399999)


if __name__ == "__main__":
    unittest.main()
