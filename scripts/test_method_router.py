#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import method_router
import risk_regime_snapshot


FIXED_NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)


def risk_snapshot(regime: str, *, snapshot_date: str = "2026-08-29") -> dict[str, str]:
    return {
        "schema_version": "risk_regime_snapshot.v1",
        "generated_at": "2026-08-29T07:00:00+00:00",
        "snapshot_date": snapshot_date,
        "stale_after": "2026-08-30T00:00:00+08:00",
        "risk_regime": regime,
    }


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


class RiskRegimeMethodHookTests(unittest.TestCase):
    def test_active_deleveraging_takes_raw_min_for_offensive_methods(self) -> None:
        payload = risk_snapshot("active_deleveraging")
        loaded, audit = method_router.load_current_risk_snapshot(
            snapshot_json=json.dumps(payload),
            now=FIXED_NOW,
        )
        raw_before = dict(method_router.BASE["A_short"])
        raw_after, audit = method_router.apply_risk_regime_tightening(raw_before, loaded, audit)

        self.assertTrue(audit["applied"])
        self.assertEqual(audit["source"], "session_snapshot")
        for method in method_router.OFFENSIVE_METHODS:
            self.assertEqual(
                raw_after[method],
                min(raw_before[method], method_router.BASE["US_deleveraging"][method]),
            )
        self.assertEqual(raw_after["wyckoff"], raw_before["wyckoff"])
        self.assertEqual(
            audit["tightened_methods"]["youzi_emotion"],
            {"raw_before": 34, "raw_after": 0},
        )

    def test_missing_stale_and_malformed_snapshots_fail_open(self) -> None:
        raw = dict(method_router.BASE["US_early_theme"])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "risk_regime" / "current.json"
            missing, missing_audit = method_router.load_current_risk_snapshot(path=path, now=FIXED_NOW)
            unchanged, _ = method_router.apply_risk_regime_tightening(raw, missing, missing_audit)
            self.assertEqual(missing_audit["status"], "missing")
            self.assertEqual(unchanged, raw)

            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(risk_snapshot("forced_liquidation", snapshot_date="2026-08-28")), encoding="utf-8")
            stale, stale_audit = method_router.load_current_risk_snapshot(path=path, now=FIXED_NOW)
            self.assertIsNone(stale)
            self.assertEqual(stale_audit["status"], "stale")

            path.write_text("{not-json", encoding="utf-8")
            malformed, malformed_audit = method_router.load_current_risk_snapshot(path=path, now=FIXED_NOW)
            self.assertIsNone(malformed)
            self.assertEqual(malformed_audit["status"], "invalid")

    def test_current_session_snapshot_overrides_cached_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "risk_regime" / "current.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(risk_snapshot("normal")), encoding="utf-8")
            loaded, audit = method_router.load_current_risk_snapshot(
                snapshot_json=json.dumps(risk_snapshot("forced_liquidation")),
                path=path,
                now=FIXED_NOW,
            )
        self.assertEqual(loaded["risk_regime"], "forced_liquidation")
        self.assertEqual(audit["source"], "session_snapshot")

    def test_normal_snapshot_leaves_raw_weights_unchanged(self) -> None:
        loaded, audit = method_router.load_current_risk_snapshot(
            snapshot_json=json.dumps(risk_snapshot("normal")),
            now=FIXED_NOW,
        )
        raw = dict(method_router.BASE["US_bigtech"])
        got, audit = method_router.apply_risk_regime_tightening(raw, loaded, audit)
        self.assertEqual(got, raw)
        self.assertFalse(audit["applied"])
        self.assertEqual(audit["status"], "not_severe")

    def test_full_risk_snapshot_is_written_atomically(self) -> None:
        payload = risk_snapshot("active_deleveraging")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "risk_regime" / "current.json"
            written = risk_regime_snapshot.save_current_snapshot(payload, path)
            self.assertEqual(Path(written), path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_true_deleveraging_still_dominates_when_options_words_also_present(self) -> None:
        # Real systemic-risk language must still win over a co-mentioned options
        # structure read (matches method-rotation-matrix.md 冲突裁决: macro/regime
        # outranks Gamma execution window).
        theme = "标普 GEX put wall 跌破，触发 margin call 连锁强平"
        scenario = method_router.choose("US", "swing", theme, event=False)
        self.assertEqual(scenario, "US_deleveraging")


if __name__ == "__main__":
    unittest.main()
