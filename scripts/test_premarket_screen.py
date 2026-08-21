#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import premarket_screen as screen


def make_panels(symbols: int = 6, days: int = 70, trade_date: str = "2026-03-10") -> dict:
    panels = {}
    for index in range(symbols):
        rows = []
        for day in range(days - 1):
            rows.append({"date": f"2026-01-{day + 1:02d}" if day < 31 else f"2026-02-{day - 30:02d}",
                         "close": 100 + index * day + day * 0.1, "low": 99 + index * day,
                         "high": 101 + index * day, "turnover_rate": index + 1,
                         "amount": 100000 + index * 1000})
        rows.append({"date": trade_date, "close": 100 + index * days, "low": 99 + index * days,
                     "high": 101 + index * days, "turnover_rate": index + 1,
                     "amount": 100000 + index * 1000})
        panels[f"S{index}"] = rows
    return panels


def registry(names: list[str]) -> list[dict]:
    return [{"hypothesis_id": f"hyp_{name}", "status": "confirmed_alive", "tags": [name, "A"]} for name in names]


class PremarketScreenTests(unittest.TestCase):
    def test_zero_confirmed_fails_closed(self) -> None:
        result = screen.screen_panels(market="A", trade_date="20260310", panels=make_panels(),
                                      registry_rows=[], factors=["mom_20_1", "rev_5"], min_cross_section=6)
        self.assertEqual(result["ranking_status"], "no_confirmed_factors_ranking_unavailable")
        self.assertIn("no_confirmed_factors", result["ranking_blockers"])
        self.assertIn("insufficient_cross_section", result["ranking_blockers"])
        self.assertTrue(all(row["watch_priority_rank"] is None for row in result["rows"]))
        self.assertEqual(result["position_multiplier"], 0.0)

    def test_vote_floor_and_integer_votes(self) -> None:
        result = screen.screen_panels(market="A", trade_date="20260310", panels=make_panels(),
                                      registry_rows=registry(["mom_20_1", "rev_5"]),
                                      factors=["mom_20_1", "rev_5"], conviction_floor=2,
                                      min_cross_section=6)
        self.assertEqual(result["ranking_status"], "ok")
        self.assertTrue(all(isinstance(row["vote"], int) for row in result["rows"]))
        self.assertTrue(any(row["edge_status"] == "no_edge" for row in result["rows"]))
        self.assertTrue(all(row["watch_priority_rank"] is None for row in result["rows"] if abs(row["vote"]) < 2))

    def test_three_nulls_excluded_but_watch_row_retained(self) -> None:
        panels = make_panels()
        panels["S0"] = panels["S0"][-3:]
        factors = ["mom_20_1", "mom_60_5", "rev_5"]
        result = screen.screen_panels(market="A", trade_date="20260310", panels=panels,
                                      registry_rows=registry(factors), factors=factors, conviction_floor=1,
                                      min_cross_section=6)
        row = next(item for item in result["rows"] if item["symbol"] == "S0")
        self.assertEqual(row["null_count"], 3)
        self.assertEqual(row["edge_status"], "null_factors>=3")
        self.assertIsNone(row["watch_priority_rank"])
        self.assertIn({"symbol": "S0", "reason": "null_factors>=3"}, result["excluded"])

    def test_freshness_blocked_degrades_entire_ranking(self) -> None:
        result = screen.screen_panels(market="A", trade_date="20260311", panels=make_panels(),
                                      registry_rows=registry(["mom_20_1"]), factors=["mom_20_1"],
                                      conviction_floor=1, min_cross_section=6)
        self.assertEqual(result["freshness"]["status"], "blocked")
        self.assertEqual(result["ranking_status"], "freshness_blocked_ranking_unavailable")
        self.assertIn("freshness_blocked", result["ranking_blockers"])
        self.assertTrue(all(row["watch_priority_rank"] is None for row in result["rows"]))
        signal = result["suggested_module_signals"][0]
        self.assertTrue(signal["tighten_only"] and signal["cannot_raise_upstream"])
        self.assertEqual(signal["position_multiplier"], 0.0)

    def test_structure_has_no_percentage_score_or_action_grant(self) -> None:
        result = screen.screen_panels(market="A", trade_date="20260310", panels=make_panels(),
                                      registry_rows=registry(["mom_20_1"]), factors=["mom_20_1"],
                                      min_cross_section=6)
        serialized = json.dumps(result)
        for forbidden in ("entry_score_100", "weighted_score_0_5", "composite_score"):
            self.assertNotIn(forbidden, serialized)
        self.assertTrue(result["watch_priority_only"])
        self.assertEqual(result["position_multiplier"], 0.0)

    def test_existing_files_with_only_two_valid_histories_cannot_rank(self) -> None:
        panels = make_panels(symbols=8, days=70)
        for symbol in list(panels)[2:]:
            panels[symbol] = panels[symbol][-10:]
        result = screen.screen_panels(
            market="A", trade_date="20260310", panels=panels,
            registry_rows=registry(["mom_60_5"]), factors=["mom_60_5"],
            conviction_floor=1, min_cross_section=5,
        )
        self.assertEqual(result["valid_cross_section"], 2)
        self.assertEqual(result["ranking_status"], "insufficient_cross_section_ranking_unavailable")
        self.assertIn("insufficient_cross_section", result["ranking_blockers"])
        self.assertTrue(all(row["watch_priority_rank"] is None for row in result["rows"]))

    def test_ranking_blockers_preserve_concurrent_failures(self) -> None:
        result = screen.screen_panels(market="A", trade_date="20260311", panels=make_panels(symbols=4),
                                      registry_rows=[], factors=["mom_20_1"], min_cross_section=6)
        self.assertEqual(result["ranking_status"], "no_confirmed_factors_ranking_unavailable")
        self.assertEqual(set(result["ranking_blockers"]),
                         {"no_confirmed_factors", "insufficient_cross_section", "freshness_blocked"})

    def test_cli_help_explains_default_and_long_window_factors(self) -> None:
        proc = subprocess.run(["python3", str(Path(screen.__file__)), "--help"],
                              capture_output=True, text=True, timeout=3)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("default-enabled", proc.stdout)
        self.assertIn("range_pos_252", proc.stdout)


if __name__ == "__main__":
    unittest.main()
