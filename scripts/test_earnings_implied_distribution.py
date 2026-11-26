#!/usr/bin/env python3
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
import sys
import tempfile
import unittest
from unittest.mock import patch
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

        self.assertEqual(got["status"], "partial")
        self.assertEqual(got["expiry"], "2026-08-21")
        self.assertEqual(got["atm_strike"], 100.0)
        self.assertEqual(got["atm_call_mid"], 5.0)
        self.assertEqual(got["atm_put_mid"], 4.0)
        self.assertEqual(got["implied_move_premium_pct"], 0.09)
        self.assertEqual(got["implied_vs_typical_ratio"], 1.5)
        self.assertEqual(got["relative_value_label"], "RICH")
        self.assertEqual(got["method_source"], "independent_unvalidated_median_adaptation")
        self.assertTrue(got["risk_neutral"])
        self.assertFalse(got["is_direction_prediction"])
        self.assertEqual(got["position_multiplier"], 0.0)
        self.assertTrue(got["no_order_execution"])

    def test_skewed_event_sample_cannot_be_mislabeled_as_author_mean_method(self):
        from earnings_move_history import build_history
        events, candles = [], []
        for i, move in enumerate([.04]*8+[.60]):
            prior=date(2026,1,1)+timedelta(days=i*2)
            reaction=prior+timedelta(days=1)
            events.append({'form_type':'8-K','source_family':'sec_edgar',
                           'filed_at':reaction.isoformat()+'T08:00:00-05:00'})
            candles.extend([{'date':prior.isoformat(),'open':100,'close':100},
                            {'date':reaction.isoformat(),'open':100*(1+move),'close':100*(1+move)}])
        history=build_history('MSFT',events,candles,n=9)
        self.assertEqual(history['sample_count'],9)
        payload={'data':{'current_price':100,'options':[
            contract('MSFT260821C00100000',3,5),contract('MSFT260821P00100000',3,5)]}}
        got=subject.calculate_distribution('MSFT',payload,expiry='2026-08-21',
            hist_median=history['hist_median'],observed_on='2026-08-21')
        mean=statistics.mean(e['abs_move'] for e in history['events'])
        # Same actual fixture observations: median says RICH, mean would say CHEAP.
        self.assertEqual(got['relative_value_label'],'RICH')
        self.assertLess(got['implied_move_premium_pct']/mean,subject.CHEAP_THRESHOLD)
        self.assertFalse(got['comparison_contract']['author_formula_reproduced'])
        self.assertEqual(got['comparison_contract']['threshold_validation'],'unvalidated_for_median')
        self.assertIn('median_ratio_thresholds_not_validated',[g['gap'] for g in got['data_gaps']])

    def test_missing_sample_metadata_never_becomes_verified(self):
        payload={'data':{'current_price':100,'options':[
            contract('MSFT260821C00100000',3,5),contract('MSFT260821P00100000',3,5)]}}
        for source in ('caller_provided','earnings_move_history_cache'):
            got=subject.calculate_distribution('MSFT',payload,expiry='2026-08-21',
                hist_median=.04,hist_median_source=source,observed_on='2026-08-21')
            meta=got['comparison_contract']
            for key in ('sample_count','sample_window','historical_data_as_of'):
                self.assertIsNone(meta[key])
            self.assertEqual(meta['sample_metadata_status'],'not_verified_by_scalar_median_interface')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'MSFT.json').write_text(json.dumps({'hist_median':.04}))
            value,_,gaps=subject.resolve_hist_median('MSFT',None,move_root=root)
            self.assertIsNone(value)
            self.assertTrue(gaps)

    def clock_fixture(self, **kwargs):
        payload = {"data": {"current_price": 100, "options": [
            contract("MSFT260821C00100000", 3, 5), contract("MSFT260821P00100000", 3, 5)]}}
        timestamp = kwargs.pop("timestamp", None)
        if timestamp is not None:
            payload["timestamp"] = timestamp
        return subject.calculate_distribution("MSFT", payload, expiry="2026-08-21",
            hist_median=.04, observed_on="2026-08-21", **kwargs)

    def test_missing_quote_clock_keeps_descriptive_implied_without_fake_freshness(self):
        got = self.clock_fixture()
        self.assertIsNone(got["as_of"])
        self.assertIsNotNone(got["computed_at"])
        self.assertEqual(got["implied_move_premium_pct"], .08)
        self.assertIn("quote_clock_missing", [g["gap"] for g in got["data_gaps"]])
        self.assertEqual(got["source_freshness"]["status"], "unknown_no_age_policy")
        self.assertEqual(got["source_freshness"]["latest_print_check"], "unknown_latest_earnings_date")

    def test_invalid_and_future_source_clocks_are_not_accepted(self):
        for stamp in ("2099-01-01T00:00:00Z", "garbage", "2026-01-01T12:00:00"):
            with self.subTest(stamp=stamp):
                got = self.clock_fixture(timestamp=stamp, hist_metadata={"history_as_of": stamp})
                self.assertIsNone(got["as_of"])
                self.assertIsNone(got["comparison_contract"]["historical_data_as_of"])
                self.assertIsNone(got["relative_value_label"])
                self.assertEqual(got["implied_move_premium_pct"], .08)
        got = self.clock_fixture(hist_metadata={"computed_at":"2099-01-01T00:00:00Z"})
        self.assertIsNone(got["relative_value_label"])
        self.assertIn("future_clock", [g["reason_code"] for g in got["data_gaps"]])

    def test_cache_metadata_is_retained_unaudited_without_relabeling_old_computation(self):
        windows = [{"event_date":"2026-01-20", "reaction_date":"2026-01-21"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "MSFT.json"
            path.write_text(json.dumps({"hist_median":.04,"sample_count":9,
                "events":[dict(windows[0], abs_move=.04)], "as_of":"2026-02-01T00:00:00Z"}))
            metadata = {}
            value, source, gaps = subject.resolve_hist_median("MSFT",None,move_root=root,metadata=metadata)
            self.assertEqual(value,.04)
            self.assertEqual(gaps,[])
            got = self.clock_fixture(hist_metadata=metadata,hist_median_source=source)
            meta = got["comparison_contract"]
            self.assertEqual(meta["sample_count"],9)
            self.assertEqual(meta["window_dates"],windows)
            self.assertEqual(meta["sample_metadata_status"],"reported_not_verified")
            self.assertIsNone(meta["historical_data_as_of"])
            self.assertFalse(meta["author_formula_reproduced"])
            self.assertEqual(got["source_freshness"]["clocks"]["history_computation"]["as_of"],
                             "2026-02-01T00:00:00+00:00")
            metadata["history_as_of"] = "2026-01-21T21:00:00Z"
            got = self.clock_fixture(hist_metadata=metadata)
            self.assertEqual(got["comparison_contract"]["history_as_of"],"2026-01-21T21:00:00+00:00")
            got = self.clock_fixture(hist_metadata=metadata,latest_known_earnings_date="2026-04-20")
            self.assertIsNone(got["relative_value_label"])
            self.assertEqual(got["source_freshness"]["latest_print_check"],"hist_median_excludes_latest_print")

    def test_fetch_failure_does_not_create_quote_clock(self):
        with patch.object(subject,"resolve_hist_median",return_value=(None,"unavailable",[])), \
             patch.object(subject,"fetch_cboe_payload",return_value=(None,{"gap":"fixture_fetch_failure","reason_code":"missing"})):
            got = subject.run_distribution("MSFT")
        self.assertIsNone(got["as_of"])
        self.assertEqual(got["status"],"insufficient_data")
        self.assertIn("fixture_fetch_failure",[g["gap"] for g in got["data_gaps"]])
        self.assertIsNotNone(got["computed_at"])

    def test_old_quote_clock_stays_old(self):
        got = self.clock_fixture(timestamp="2026-01-01T12:00:00Z")
        self.assertEqual(got["as_of"],"2026-01-01T12:00:00+00:00")
        self.assertEqual(got["source_freshness"]["clocks"]["quote"]["source"],"payload.timestamp")
        self.assertNotEqual(got["as_of"],got["computed_at"])

    def test_future_history_window_rejected_without_latest_event_input(self):
        got = self.clock_fixture(hist_metadata={"window_dates":[{"event_date":"2099-01-01"}]})
        self.assertIsNone(got["relative_value_label"])
        self.assertIn("historical_window_dates_invalid",[g["gap"] for g in got["data_gaps"]])

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
