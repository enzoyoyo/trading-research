#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import method_router


class MethodRotationMatrixSyncTests(unittest.TestCase):
    def test_every_runtime_scenario_normalizes_to_exactly_100(self) -> None:
        for scenario, weights in method_router.BASE.items():
            with self.subTest(scenario=scenario):
                normalized = method_router.normalize(weights)
                self.assertEqual(sum(normalized.values()), 100)
                self.assertEqual(set(normalized), set(method_router.METHOD_LABELS))

    def test_documented_raw_matrix_is_generated_from_runtime_base(self) -> None:
        text = (SKILL_ROOT / "references" / "method-rotation-matrix.md").read_text(encoding="utf-8")
        start = "<!-- runtime-matrix:start -->"
        end = "<!-- runtime-matrix:end -->"
        self.assertIn(start, text)
        self.assertIn(end, text)
        documented = text.split(start, 1)[1].split(end, 1)[0].strip()
        self.assertEqual(documented, method_router.markdown_matrix().strip())


class DeleveragingMisrouteRegressionTests(unittest.TestCase):
    """Regression coverage for method-router-deleveraging-keyword-misroute.

    Before the fix, DELEVERAGING_KEYWORDS contained bare entity/option words
    (aapl, apple, gold, vix, skew, gex) and choose() returned on first hit, so
    any AAPL/GEX/VIX research request — the highest-frequency US queries —
    was swept into the US_deleveraging macro scenario instead of its actual
    scenario. These four cases are the categories called out by the audit.
    """

    def test_bad_words_removed_from_deleveraging_keywords(self) -> None:
        # Guard against silently reintroducing the exact words that caused the bug.
        bad_words = {"aapl", "apple", "gold", "黄金", "vix", "skew", "gex"}
        self.assertFalse(bad_words & set(method_router.DELEVERAGING_KEYWORDS))

    def test_bad_words_removed_from_macro_policy_keywords(self) -> None:
        # Same over-broad-word bug also leaked into the macro-context overlay list.
        bad_words = {"aapl", "apple", "skew", "gex"}
        self.assertFalse(bad_words & set(method_router.MACRO_POLICY_KEYWORDS))

    def test_bare_aapl_ticker_routes_to_bigtech_not_deleveraging(self) -> None:
        scenario = method_router.choose("US", "swing", "AAPL", event=False)
        self.assertEqual(scenario, "US_bigtech")

    def test_aapl_earnings_theme_routes_to_bigtech_not_deleveraging(self) -> None:
        scenario = method_router.choose("US", "swing", "apple earnings preview", event=False)
        self.assertEqual(scenario, "US_bigtech")

    def test_gex_options_structure_theme_routes_to_options_gamma(self) -> None:
        scenario = method_router.choose("US", "swing", "NVDA GEX put wall", event=False)
        self.assertEqual(scenario, "US_options_gamma")

    def test_vix_hedge_theme_routes_to_options_gamma_not_deleveraging(self) -> None:
        scenario = method_router.choose("US", "swing", "VIX 对冲策略", event=False)
        self.assertEqual(scenario, "US_options_gamma")

    def test_true_deleveraging_language_still_routes_to_deleveraging(self) -> None:
        theme = "美股 basis trade unwind 引发连锁 margin call，杠杆盘全线强平，流动性挤兑蔓延"
        scenario = method_router.choose("US", "swing", theme, event=False)
        self.assertEqual(scenario, "US_deleveraging")

    def test_true_deleveraging_still_dominates_when_options_words_also_present(self) -> None:
        # Real systemic-risk language must still win over a co-mentioned options
        # structure read (matches method-rotation-matrix.md 冲突裁决: macro/regime
        # outranks Gamma execution window).
        theme = "标普 GEX put wall 跌破，触发 margin call 连锁强平"
        scenario = method_router.choose("US", "swing", theme, event=False)
        self.assertEqual(scenario, "US_deleveraging")


if __name__ == "__main__":
    unittest.main()
