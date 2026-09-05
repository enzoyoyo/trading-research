#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import self_optimization_ledger as ledger  # noqa: E402


class LedgerHealthGapTests(unittest.TestCase):
    def test_health_reports_missing_trailing_dates_after_today_append(self) -> None:
        rows = [
            {"date": "2026-07-22", "status": "no_necessary_upgrade"},
            {"date": "2026-07-26", "status": "no_necessary_upgrade"},
        ]

        result = ledger.health_report(rows, today=date(2026, 7, 26))

        self.assertFalse(result["ok"])
        issue = next(row for row in result["issues"] if row["type"] == "ledger_missing_trailing_dates")
        self.assertEqual(issue["missing_dates"], ["2026-07-23", "2026-07-24", "2026-07-25"])
        self.assertEqual(issue["consecutive_days"], 3)

    def test_health_ignores_single_missing_day_below_threshold(self) -> None:
        rows = [
            {"date": "2026-07-24", "status": "no_necessary_upgrade"},
            {"date": "2026-07-26", "status": "no_necessary_upgrade"},
        ]

        result = ledger.health_report(rows, today=date(2026, 7, 26))

        self.assertTrue(result["ok"])
        self.assertFalse(any(row["type"] == "ledger_missing_trailing_dates" for row in result["issues"]))


if __name__ == "__main__":
    unittest.main()
