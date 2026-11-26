#!/usr/bin/env python3
"""Regression tests for polymarket-expired-markets-health-illusion (2026-07-26 audit).

search_markets() used to return every market a query matched, including
already-resolved/closed ones, with no active/closed filtering -- a "Fed rate
cut" search returned only expired meeting markets (implied_probability 0.0
for each) while the top-level result still reported ok:true and
data_gaps:[]. These tests lock in: closed markets are filtered out, live
markets are kept and sorted by volume, and an all-dead result surfaces an
explicit data gap instead of a false-healthy empty/zero response.

Network calls (safe_json, the sole network boundary in this module) are
mocked; is_market_alive/market_volume/search_markets' filtering logic is
exercised against fixtures shaped like real gamma-api responses (field names
and the closed=True/active=True combination were verified live against
gamma-api.polymarket.com on 2026-07-26 -- see polymarket_signal.py's
is_market_alive docstring).
"""
from __future__ import annotations

import argparse
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import polymarket_signal as pm  # noqa: E402


def make_market(question, closed=False, active=True, volume=None, volume24hr=None, **extra):
    market = {
        "question": question,
        "closed": closed,
        "active": active,
        "volume": volume,
        "volume24hr": volume24hr,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
        "clobTokenIds": '["tok-yes", "tok-no"]',
        "conditionId": f"cond-{question}",
        "slug": question.lower().replace(" ", "-"),
    }
    market.update(extra)
    return market


class IsMarketAliveTests(unittest.TestCase):
    def test_closed_true_is_dead(self) -> None:
        self.assertFalse(pm.is_market_alive({"closed": True, "active": True}))

    def test_active_false_is_dead(self) -> None:
        self.assertFalse(pm.is_market_alive({"closed": False, "active": False}))

    def test_closed_true_and_active_true_is_dead(self) -> None:
        # The real-world case: gamma-api leaves active=True on resolved
        # markets, so closed must be checked independently of active.
        self.assertFalse(pm.is_market_alive({"closed": True, "active": True}))

    def test_open_market_is_alive(self) -> None:
        self.assertTrue(pm.is_market_alive({"closed": False, "active": True}))

    def test_missing_fields_default_to_alive(self) -> None:
        self.assertTrue(pm.is_market_alive({}))


class MarketVolumeTests(unittest.TestCase):
    def test_prefers_volume_over_volume24hr(self) -> None:
        self.assertEqual(pm.market_volume({"volume": "100", "volume24hr": "5"}), 100.0)

    def test_falls_back_to_volume24hr(self) -> None:
        self.assertEqual(pm.market_volume({"volume": None, "volume24hr": "5"}), 5.0)

    def test_defaults_to_zero(self) -> None:
        self.assertEqual(pm.market_volume({}), 0.0)


class SearchMarketsFilteringTests(unittest.TestCase):
    def _mock_gamma_response(self, events):
        def fake_safe_json(url, timeout):
            if url.startswith(pm.GAMMA):
                return {"events": events}, None
            return None, "unexpected URL in test"
        return fake_safe_json

    def test_all_closed_markets_yield_no_active_market_matched_gap(self) -> None:
        events = [{
            "title": "Fed rate cut by...?",
            "slug": "fed-rate-cut-by-629",
            "markets": [
                make_market("Fed rate cut by January 2026 meeting?", closed=True, volume="588114.76"),
                make_market("Fed rate cut by March 2026 meeting?", closed=True, volume=None),
            ],
        }]
        with mock.patch.object(pm, "safe_json", self._mock_gamma_response(events)):
            markets, gaps = pm.search_markets("Fed rate cut", timeout=15)
        self.assertEqual(markets, [])
        self.assertEqual(len(gaps), 1)
        self.assertIn("no_active_market_matched", gaps[0]["error"])
        self.assertIn("2 market(s)", gaps[0]["error"])

    def test_live_markets_pass_through_closed_ones_filtered(self) -> None:
        events = [{
            "title": "Fed rate cut by...?",
            "slug": "fed-rate-cut-by-629",
            "markets": [
                make_market("Fed rate cut by January 2026 meeting?", closed=True, volume="588114.76"),
                make_market("Fed rate cut by July 2026 meeting?", closed=False, volume="9121.75"),
                make_market("Fed rate cut by December 2026 meeting?", closed=False, volume="381.80"),
            ],
        }]
        with mock.patch.object(pm, "safe_json", self._mock_gamma_response(events)):
            markets, gaps = pm.search_markets("Fed rate cut", timeout=15)
        self.assertEqual(gaps, [])
        questions = [m["question"] for m in markets]
        self.assertEqual(questions, ["Fed rate cut by July 2026 meeting?", "Fed rate cut by December 2026 meeting?"])

    def test_live_markets_sorted_by_volume_descending(self) -> None:
        events = [{
            "title": "E", "slug": "e",
            "markets": [
                make_market("low vol", closed=False, volume="10"),
                make_market("high vol", closed=False, volume="9999"),
                make_market("mid vol", closed=False, volume="500"),
            ],
        }]
        with mock.patch.object(pm, "safe_json", self._mock_gamma_response(events)):
            markets, _gaps = pm.search_markets("x", timeout=15)
        self.assertEqual([m["question"] for m in markets], ["high vol", "mid vol", "low vol"])

    def test_no_markets_matched_at_all_has_no_gap(self) -> None:
        # Distinguish "query matched nothing" (existing, unrelated behavior)
        # from "query matched only dead markets" (the bug this fixes).
        with mock.patch.object(pm, "safe_json", self._mock_gamma_response([])):
            markets, gaps = pm.search_markets("nonexistent query xyz", timeout=15)
        self.assertEqual(markets, [])
        self.assertEqual(gaps, [])


class MainOutputHealthIllusionTests(unittest.TestCase):
    def test_all_dead_markets_report_ok_false_with_data_gap(self) -> None:
        with mock.patch.object(
            pm, "search_markets",
            return_value=([], [{"source": "https://gamma-api.polymarket.com/public-search?q=x",
                                 "error": "no_active_market_matched: 3 market(s) matched 'x' but all are closed/resolved"}]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = pm.main(["--query", "Fed rate cut", "--limit", "3"])
        self.assertEqual(rc, 2)
        import json
        output = json.loads(buf.getvalue())
        self.assertFalse(output["ok"])
        self.assertEqual(output["signals"], [])
        self.assertTrue(any("no_active_market_matched" in g["error"] for g in output["data_gaps"]))

    def test_self_test_still_passes(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = pm.self_test()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
