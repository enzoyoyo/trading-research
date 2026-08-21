#!/usr/bin/env python3
"""Regression test for quote-tier-schema-mismatch (2026-07-26 audit).

longbridge_query.py's SDK path normalizes quotes to {price, prev_close,
change_pct, high, low, volume, pre_market?, post_market?}. Its CLI fallback
path used to pass the CLI's raw JSON straight through instead, so a
downstream consumer reading item.get("price") (record_due_results.py) would
silently get None on the CLI-fallback path and treat a live quote the same
as a missing one.

The fixture below is a real `longbridge quote AAPL.US --format json` capture
(2026-07-26), field names verified against actual CLI output rather than the
CLI's --help text (which uses different, stale field names).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import longbridge_query as lq  # noqa: E402


# Captured 2026-07-26 via: longbridge quote AAPL.US --format json
REAL_CLI_QUOTE_ROW = {
    "change_percentage": "3.53",
    "change_value": "11.360",
    "high": "334.370",
    "last": "333.020",
    "low": "321.620",
    "open": "321.790",
    "overnight": {
        "high": "321.840", "last": "321.530", "low": "319.820",
        "prev_close": "321.660", "timestamp": "2026-07-24T08:00:00Z",
        "turnover": "9393055.000", "volume": 29272,
    },
    "post_market": {
        "high": "333.880", "last": "333.800", "low": "332.400",
        "prev_close": "333.020", "timestamp": "2026-07-24T23:59:48Z",
        "turnover": "677025623.246", "volume": 2032788,
    },
    "pre_market": {
        "high": "322.980", "last": "321.990", "low": "320.243",
        "prev_close": "321.660", "timestamp": "2026-07-24T13:30:00Z",
        "turnover": "43109738.608", "volume": 133996,
    },
    "prev_close": "321.660",
    "status": "Normal",
    "symbol": "AAPL.US",
    "turnover": "15740273026.000",
    "volume": 47489415,
}

SDK_PATH_SCHEMA_KEYS = {"symbol", "price", "prev_close", "change_pct", "high", "low", "volume"}


class NormalizeCliQuoteTests(unittest.TestCase):
    def test_normalizes_real_cli_row_to_sdk_schema(self) -> None:
        item = lq.normalize_cli_quote(REAL_CLI_QUOTE_ROW)
        self.assertTrue(SDK_PATH_SCHEMA_KEYS.issubset(item.keys()))
        self.assertEqual(item["symbol"], "AAPL.US")
        self.assertEqual(item["price"], 333.02)
        self.assertEqual(item["prev_close"], 321.66)
        self.assertAlmostEqual(item["change_pct"], 3.53, places=2)
        self.assertEqual(item["high"], 334.37)
        self.assertEqual(item["low"], 321.62)
        self.assertEqual(item["volume"], 47489415)
        self.assertEqual(item["pre_market"], 321.99)
        self.assertEqual(item["post_market"], 333.8)

    def test_downstream_price_field_contract_is_satisfiable(self) -> None:
        """Mirrors record_due_results.py's longbridge_quote(): item.get("price") must be a usable float."""
        item = lq.normalize_cli_quote(REAL_CLI_QUOTE_ROW)
        price = item.get("price")
        self.assertIsInstance(price, float)
        self.assertGreater(price, 0)

    def test_missing_last_field_yields_none_price_not_a_crash(self) -> None:
        row = {"symbol": "GHOST.US", "prev_close": "10.0", "high": "11", "low": "9", "volume": 100}
        item = lq.normalize_cli_quote(row)
        self.assertIsNone(item["price"])

    def test_no_pre_post_market_keys_when_absent(self) -> None:
        row = {"symbol": "NOEXT.US", "last": "100", "prev_close": "99", "high": "101", "low": "98", "volume": 10}
        item = lq.normalize_cli_quote(row)
        self.assertNotIn("pre_market", item)
        self.assertNotIn("post_market", item)

    def test_cmd_quote_cli_fallback_path_returns_normalized_list(self) -> None:
        calls = {}

        def fake_run_cli_json(args, timeout=180):
            calls["args"] = args
            return [REAL_CLI_QUOTE_ROW]

        original = lq.run_cli_json
        lq.run_cli_json = fake_run_cli_json
        try:
            result = lq.cmd_quote(None, ["AAPL.US"])
        finally:
            lq.run_cli_json = original
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["price"], 333.02)
        self.assertIn("quote", calls["args"])

    def test_cmd_quote_sdk_exception_fallback_also_normalizes(self) -> None:
        class ExplodingCtx:
            def quote(self, symbols):
                raise RuntimeError("connection reset")

        def fake_run_cli_json(args, timeout=180):
            return [REAL_CLI_QUOTE_ROW]

        original = lq.run_cli_json
        lq.run_cli_json = fake_run_cli_json
        try:
            result = lq.cmd_quote(ExplodingCtx(), ["AAPL.US"])
        finally:
            lq.run_cli_json = original
        self.assertEqual(result[0]["price"], 333.02)
        self.assertTrue(SDK_PATH_SCHEMA_KEYS.issubset(result[0].keys()))


if __name__ == "__main__":
    unittest.main()
