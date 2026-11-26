#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_store import connect
from prediction_ledger import register_prediction, settle_due_predictions


def payload() -> dict:
    return {
        "prediction_id": "PR-TEST-1",
        "symbol": "TEST",
        "market": "US",
        "longbridge_symbol": "TEST.US",
        "horizon_id": "swing_days",
        "direction": "up",
        "probability": 0.6,
        "as_of": "2026-01-01T00:00:00+00:00",
        "due_at": "2026-01-03T00:00:00+00:00",
        "regime": "neutral",
        "calibration_bucket": "TEST×swing_days×neutral×up×1pct",
        "reference_price": 100.0,
        "drivers": ["E1", "E2", "E3"],
        "invalidation": ["revenue warning"],
        "no_trade_if": ["quote stale"],
        "consensus": {
            "consensus_view": "flat",
            "price_discounts": "stable demand",
            "variant_view": "positive revisions",
        },
        "premortem": [
            {"failure": "demand miss", "indicator": "orders"},
            {"failure": "margin miss", "indicator": "gross margin"},
            {"failure": "risk-off", "indicator": "credit spread"},
        ],
        "resolution": {"metric": "directional_return", "threshold_pct": 1.0},
    }


class PredictionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "memory.sqlite"
        self.conn = connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def test_register_and_settle(self) -> None:
        self.assertTrue(
            register_prediction(
                self.conn,
                payload(),
                registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )["ok"]
        )
        result = settle_due_predictions(
            self.conn,
            datetime(2026, 1, 3, 1, tzinfo=timezone.utc),
            quote_fn=lambda symbol: {"price": 102.0, "source": "fixture", "symbol": symbol},
            normalize_quote_fn=lambda value: (value["price"], value["source"]),
        )
        self.assertEqual(result["settled_count"], 1)
        self.assertEqual(result["settled"][0]["actual"], 1)
        self.assertEqual(
            self.conn.execute("SELECT status FROM predictions").fetchone()[0],
            "settled",
        )

    def test_missing_quote_stays_open(self) -> None:
        register_prediction(
            self.conn,
            payload(),
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        result = settle_due_predictions(
            self.conn,
            datetime(2026, 1, 3, 1, tzinfo=timezone.utc),
            quote_fn=lambda _symbol: None,
            normalize_quote_fn=lambda _value: (None, None),
        )
        self.assertTrue(result["fail_closed"])
        self.assertEqual(result["settled_count"], 0)
        self.assertEqual(
            self.conn.execute("SELECT status FROM predictions").fetchone()[0],
            "open",
        )

    def test_rejects_non_settleable_or_weakly_evidenced_payload(self) -> None:
        bad = payload()
        bad["probability"] = 1.0
        bad["drivers"] = ["E1"]
        bad["premortem"] = []
        result = register_prediction(
            self.conn,
            bad,
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertFalse(result["ok"])
        self.assertIn("probability_must_be_strictly_between_0_and_1", result["errors"])
        self.assertIn("drivers_require_3_distinct_eids", result["errors"])
        self.assertIn("premortem_requires_3_failures_with_indicators", result["errors"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM predictions").fetchone()[0], 0)

    def test_rejects_nonpositive_threshold(self) -> None:
        bad = payload()
        bad["resolution"]["threshold_pct"] = 0
        bad["calibration_bucket"] = "TEST×swing_days×neutral×up×0pct"
        result = register_prediction(
            self.conn,
            bad,
            registered_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertFalse(result["ok"])
        self.assertIn("resolution_threshold_pct_must_be_positive", result["errors"])

    def test_excludes_stale_settlement(self) -> None:
        self.assertTrue(
            register_prediction(
                self.conn,
                payload(),
                registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )["ok"]
        )
        result = settle_due_predictions(
            self.conn,
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            quote_fn=lambda symbol: {"price": 150.0, "source": "fixture", "symbol": symbol},
            normalize_quote_fn=lambda value: (value["price"], value["source"]),
        )
        self.assertEqual(result["settled_count"], 0)
        self.assertEqual(result["stale_settlement_excluded"], ["PR-TEST-1"])
        self.assertTrue(result["fail_closed"])

    def test_identity_mismatch_never_settles(self) -> None:
        register_prediction(
            self.conn,
            payload(),
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        result = settle_due_predictions(
            self.conn,
            datetime(2026, 1, 3, 1, tzinfo=timezone.utc),
            quote_fn=lambda _symbol: {
                "price": 102.0,
                "source": "fixture",
                "symbol": "OTHER.US",
            },
            normalize_quote_fn=lambda value: (value["price"], value["source"]),
        )
        self.assertEqual(result["settled_count"], 0)
        self.assertEqual(result["identity_mismatch"], ["PR-TEST-1"])

    def test_stale_write_lock_is_structured_fail_closed(self) -> None:
        register_prediction(
            self.conn,
            payload(),
            registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        locker = connect(self.db_path)
        try:
            locker.execute("BEGIN IMMEDIATE")
            self.conn.execute("PRAGMA busy_timeout = 1")
            result = settle_due_predictions(
                self.conn,
                datetime(2026, 2, 1, tzinfo=timezone.utc),
                quote_fn=lambda _symbol: None,
                normalize_quote_fn=lambda _value: (None, None),
            )
        finally:
            locker.rollback()
            locker.close()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stale_settlement_excluded"], [])
        self.assertEqual(result["skipped_lock_timeout"], ["PR-TEST-1"])
        self.assertEqual(
            self.conn.execute("SELECT status FROM predictions").fetchone()[0],
            "open",
        )


if __name__ == "__main__":
    unittest.main()
