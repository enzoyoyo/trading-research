#!/usr/bin/env python3
"""Regression tests for memory_review.py adjustment application.

Covers the memory-review-inert-adjustment-kinds audit finding
(2026-07-26): derive_recommendations' tag_rules can emit holding_clock /
take_profit_rule / conservatism_filter kinds that apply_adjustments used to
drop silently (no branch, no warning). These tests lock in that every kind
tag_rules can produce is either consumed by apply_adjustments or explicitly
reported in skipped_adjustments with a reason -- never silently dropped.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import memory_review  # noqa: E402
import provenance_guard  # noqa: E402
import calibration_scorecard  # noqa: E402
from memory_store import connect  # noqa: E402
from test_provenance_guard import make_covered_bundle  # noqa: E402
from trading_memory_core import cmd_record_decision, cmd_record_result  # noqa: E402


class ApplyAdjustmentsKindCoverageTests(unittest.TestCase):
    """Every kind that tag_rules can emit must be classified: applied or skipped-with-reason."""

    def test_all_tag_rule_kinds_are_classified_not_left_to_generic_fallback(self) -> None:
        # Fabricate stats that trip every entry in derive_recommendations'
        # tag_rules table (see memory_review.py failure_tags -> kind
        # mapping), then require classify_adjustment to give each resulting
        # kind an explicit verdict (auto-apply or named skip reason) rather
        # than falling through to the generic "unrecognized kind" message --
        # that fallback firing here would mean a tag_rules kind shipped
        # without a matching apply_adjustments/skip-reason entry.
        stats = {
            "sample_count": 10,
            "failure_tags": {
                "low_coverage_loss": 5,
                "quick_loss": 5,
                "chase_reversal": 5,
                "fake_breakout": 5,
                "timeout_failure": 5,
                "take_profit_too_early": 5,
                "watch_missed_opportunity": 5,
            },
            "by_symbol": {},
            "by_direction": {},
            "ineffective_factors": {},
        }
        _recos, adjustments = memory_review.derive_recommendations(stats, min_count=1, min_share=0.1, max_delta=0.15)
        kinds = {adj["kind"] for adj in adjustments}
        self.assertEqual(
            kinds,
            {
                "data_coverage_gate", "entry_timing_filter", "breakout_confirmation",
                "holding_clock", "take_profit_rule", "conservatism_filter",
            },
        )
        for adj in adjustments:
            reason = memory_review.classify_adjustment(adj)
            generic_fallback = f"unrecognized adjustment kind {adj['kind']!r}; no handler in apply_adjustments"
            if adj["kind"] in memory_review.MULTIPLIER_ADJUSTMENT_KINDS:
                self.assertIsNone(reason, f"{adj['kind']} should be auto-applicable")
            else:
                self.assertIsInstance(reason, str, f"{adj['kind']} should carry an explicit skip reason")
                self.assertNotEqual(reason, generic_fallback, f"{adj['kind']} fell through to the generic fallback -- add it to UNAPPLIABLE_ADJUSTMENT_REASONS")

    def test_holding_clock_is_skipped_with_reason_not_dropped(self) -> None:
        base = {"symbol_multiplier": 1.0, "direction_multiplier": 1.0, "data_gap_multiplier": 1.0, "factor_multiplier": 1.0}
        adjustments = [{"kind": "holding_clock", "target": "global", "reason": "超时失败偏多", "multiplier": 0.85}]
        applied, skipped = memory_review.apply_adjustments(base, adjustments, "AAPL", "long")
        self.assertEqual(applied, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["kind"], "holding_clock")
        self.assertIn("skip_reason", skipped[0])
        self.assertTrue(skipped[0]["skip_reason"])
        # base multipliers must be untouched -- no silent mapping onto an
        # unrelated sizing lever.
        self.assertEqual(base, {"symbol_multiplier": 1.0, "direction_multiplier": 1.0, "data_gap_multiplier": 1.0, "factor_multiplier": 1.0})

    def test_take_profit_rule_and_conservatism_filter_are_skipped_with_reason(self) -> None:
        base = {"symbol_multiplier": 1.0, "direction_multiplier": 1.0, "data_gap_multiplier": 1.0, "factor_multiplier": 1.0}
        adjustments = [
            {"kind": "take_profit_rule", "target": "global", "multiplier": 0.9},
            {"kind": "conservatism_filter", "target": "global", "multiplier": 0.9},
        ]
        applied, skipped = memory_review.apply_adjustments(base, adjustments, "AAPL", "long")
        self.assertEqual(applied, [])
        self.assertEqual({s["kind"] for s in skipped}, {"take_profit_rule", "conservatism_filter"})
        for s in skipped:
            self.assertTrue(s["skip_reason"])

    def test_known_kinds_still_apply_as_multipliers(self) -> None:
        base = {"symbol_multiplier": 1.0, "direction_multiplier": 1.0, "data_gap_multiplier": 1.0, "factor_multiplier": 1.0}
        adjustments = [
            {"kind": "symbol_penalty", "target": "AAPL", "multiplier": 0.8},
            {"kind": "direction_penalty", "target": "long", "multiplier": 0.9},
            {"kind": "factor_penalty", "target": "model_score", "multiplier": 0.95},
        ]
        applied, skipped = memory_review.apply_adjustments(base, adjustments, "AAPL", "long")
        self.assertEqual(len(applied), 3)
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(base["symbol_multiplier"], 0.8)
        self.assertAlmostEqual(base["direction_multiplier"], 0.9)
        self.assertAlmostEqual(base["factor_multiplier"], 0.95)

    def test_symbol_penalty_for_a_different_symbol_is_not_reported_as_skipped(self) -> None:
        # Not applicable != unrecognized. A symbol_penalty targeting a
        # different symbol than the current preflight call is normal
        # filtering, not a dropped/unhandled kind, so it must not clutter
        # skipped_adjustments.
        base = {"symbol_multiplier": 1.0, "direction_multiplier": 1.0, "data_gap_multiplier": 1.0, "factor_multiplier": 1.0}
        adjustments = [{"kind": "symbol_penalty", "target": "TSLA", "multiplier": 0.8}]
        applied, skipped = memory_review.apply_adjustments(base, adjustments, "AAPL", "long")
        self.assertEqual(applied, [])
        self.assertEqual(skipped, [])
        self.assertAlmostEqual(base["symbol_multiplier"], 1.0)

    def test_unrecognized_future_kind_is_skipped_with_generic_reason(self) -> None:
        base = {"symbol_multiplier": 1.0, "direction_multiplier": 1.0, "data_gap_multiplier": 1.0, "factor_multiplier": 1.0}
        adjustments = [{"kind": "some_future_kind", "target": "global", "multiplier": 0.9}]
        applied, skipped = memory_review.apply_adjustments(base, adjustments, "AAPL", "long")
        self.assertEqual(applied, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("some_future_kind", skipped[0]["skip_reason"])


class ReviewAndPreflightSurfaceSkippedAdjustmentsTests(unittest.TestCase):
    def _seed_timeout_failures(self, conn, root: Path, count: int = 4) -> None:
        """Record decisions+results that trigger the timeout_failure tag_rule (-> holding_clock)."""
        for i in range(count):
            decision = {
                "symbol": "AAPL",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "review_clock": "2026-07-20T00:00:00+00:00",
            }
            dpath = root / f"decision-{i}.json"
            dpath.write_text(json.dumps(decision), encoding="utf-8")
            out = cmd_record_decision(conn, argparse.Namespace(payload=str(dpath), db=None))
            result = {
                "validation_time": "2026-07-21T00:00:00+00:00",
                "outcome": "failure",
                "return_pct": -1.0,
                "failure_tags": ["timeout_failure"],
            }
            rpath = root / f"result-{i}.json"
            rpath.write_text(json.dumps(result), encoding="utf-8")
            cmd_record_result(conn, argparse.Namespace(payload=str(rpath), decision_id=out["decision_id"], db=None))

    def test_cmd_review_reports_skipped_adjustments_for_holding_clock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            self._seed_timeout_failures(conn, root, count=4)
            review = memory_review.cmd_review(
                conn,
                argparse.Namespace(
                    window=36, min_samples=1, cooldown_minutes=0, force=True,
                    pattern_min_count=1, pattern_min_share=0.1, max_adjustment_delta=0.15,
                ),
            )
            conn.close()
            self.assertTrue(review["ok"])
            self.assertIn("skipped_adjustments", review)
            kinds = {a["kind"] for a in review["adjustments"]}
            self.assertIn("holding_clock", kinds)
            skipped_kinds = {a["kind"] for a in review["skipped_adjustments"]}
            self.assertIn("holding_clock", skipped_kinds)
            for a in review["skipped_adjustments"]:
                self.assertTrue(a.get("skip_reason"))

    def test_cmd_preflight_exposes_skipped_adjustments_field(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            self._seed_timeout_failures(conn, root, count=4)
            memory_review.cmd_review(
                conn,
                argparse.Namespace(
                    window=36, min_samples=1, cooldown_minutes=0, force=True,
                    pattern_min_count=1, pattern_min_share=0.1, max_adjustment_delta=0.15,
                ),
            )
            preflight = memory_review.cmd_preflight(
                conn, argparse.Namespace(symbol="AAPL", market="US", direction="long", window=36)
            )
            conn.close()
            self.assertIn("skipped_adjustments", preflight)
            skipped_kinds = {a["kind"] for a in preflight["skipped_adjustments"]}
            self.assertIn("holding_clock", skipped_kinds)


class ExternalProvenanceMemoryIsolationTests(unittest.TestCase):
    def _table_counts(self, conn) -> dict[str, int]:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("decisions", "results", "reviews", "claims", "memory_events")
        }

    def _conclusion_bundle(self, root: Path) -> dict:
        bundle = make_covered_bundle(root)
        claim = bundle["analysis_claims"][0]
        claim["decision_use"] = "supports_conclusion"
        coverage = bundle["claim_coverage"]
        coverage["context_only_claim_ids"] = []
        coverage["accepted_claim_ids"] = ["AC1"]
        coverage["canonical_eids"] = ["E1"]
        bundle["bundle_purpose"]["decision_use"] = "supports_conclusion"
        return bundle

    def test_partial_and_blocked_guard_links_are_nonmaterial_and_do_not_write_memory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            before = self._table_counts(conn)

            partial_bundle = self._conclusion_bundle(root)
            partial_bundle["source_documents"][0]["access_state"] = "partial"
            partial = provenance_guard.validate_bundle(partial_bundle, base_dir=root)

            blocked_bundle = self._conclusion_bundle(root)
            blocked_bundle["analysis_claims"][0]["counterevidence_status"] = "contradicted"
            blocked_bundle["claim_coverage"]["accepted_claim_ids"] = []
            blocked_bundle["claim_coverage"]["rejected_claim_ids"] = ["AC1"]
            blocked = provenance_guard.validate_bundle(blocked_bundle, base_dir=root)

            after = self._table_counts(conn)
            conn.close()

        self.assertTrue(partial["ok"], partial)
        self.assertEqual(partial["provenance_readiness"], "partial")
        self.assertEqual(partial["memory_link"]["accepted_claim_ids"], ["AC1"])
        self.assertEqual(partial["memory_link"]["admission_status"], "partial_watch_only")
        self.assertFalse(partial["memory_link"]["materiality_eligible"])

        self.assertFalse(blocked["ok"], blocked)
        self.assertEqual(blocked["memory_link"]["accepted_claim_ids"], [])
        self.assertEqual(blocked["memory_link"]["rejected_claim_ids"], ["AC1"])
        self.assertEqual(blocked["memory_link"]["admission_status"], "blocked_gap_only")
        self.assertRegex(blocked["memory_link"]["rejection_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(blocked["memory_link"]["materiality_eligible"])
        for key in (
            "source_document_ids", "quote_anchor_ids", "framework_claim_ids",
            "analysis_claim_ids", "behavior_cross_check_ids", "kol_method_card_ids",
            "canonical_eids",
        ):
            self.assertEqual(blocked["memory_link"][key], [], key)
        self.assertEqual(after, before, "provenance validation must not write any Memory table")

    def test_kol_purpose_cannot_become_material_without_a_card_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = self._conclusion_bundle(root)
            bundle["bundle_purpose"]["kind"] = "kol_method_handoff"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        codes = {row["code"] for row in result["errors"]}
        self.assertIn("kol_target_binding_missing", codes)
        self.assertIn("kol_claim_promotion_forbidden", codes)
        self.assertEqual(result["memory_link"]["accepted_claim_ids"], [])
        self.assertEqual(result["memory_link"]["canonical_eids"], [])
        self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_brier_requires_both_explicit_probability_and_binary_outcome(self) -> None:
        complete = (
            {"factors": {"estimated_win_rate": 0.7}, "direction": "long"},
            {"outcome": "success"},
        )
        missing_probability = ({"factors": {}, "direction": "long"}, {"outcome": "success"})
        missing_outcome = ({"factors": {"estimated_win_rate": 0.7}, "direction": "long"}, {})

        self.assertEqual(len(calibration_scorecard.collect_pairs([complete])), 1)
        self.assertEqual(calibration_scorecard.collect_pairs([missing_probability]), [])
        self.assertEqual(calibration_scorecard.collect_pairs([missing_outcome]), [])
        self.assertIsNone(calibration_scorecard.brier([]))

    def test_calibration_discloses_candidate_and_two_abstention_denominators(self) -> None:
        items = []
        for index in range(10):
            decision = {
                "factors": {"estimated_win_rate": 0.7},
                "direction": "long",
            }
            if 4 <= index < 7:
                decision["edge_status"] = "no_edge"
            if index < 4:
                outcome = "success"
            elif index < 7:
                outcome = "failure"
            else:
                outcome = "neutral"
            items.append((decision, {"outcome": outcome}))

        pairs = calibration_scorecard.collect_pairs(items)
        disclosure = calibration_scorecard.calibration_denominators(items)

        self.assertEqual(len(pairs), 4)
        self.assertTrue(all(pair["actual"] == 1 for pair in pairs))
        self.assertEqual(disclosure["candidate_n"], 10)
        self.assertEqual(disclosure["evaluated_n"], 4)
        self.assertEqual(disclosure["abstention_rate"], 0.6)
        self.assertEqual(disclosure["abstention_by_reason"]["no_edge_floor"], 3)
        self.assertEqual(disclosure["abstention_by_reason"]["outcome_mixed_neutral"], 3)
        overlapping = calibration_scorecard.calibration_denominators([(
            {
                "factors": {"estimated_win_rate": 0.7},
                "edge_status": "no_edge",
            },
            {"outcome": "neutral"},
        )])
        self.assertEqual(overlapping["abstention_by_reason"]["no_edge_floor"], 1)
        self.assertEqual(overlapping["abstention_by_reason"]["outcome_mixed_neutral"], 0)
        self.assertIsNone(calibration_scorecard.calibration_denominators([])["abstention_rate"])
        missing_probability = calibration_scorecard.calibration_denominators([(
            {"edge_status": "no_edge", "factors": {}}, {"outcome": "neutral"},
        )])
        self.assertEqual(missing_probability["candidate_n"], 0)
        self.assertEqual(missing_probability["abstention_by_reason"]["no_edge_floor"], 0)

        boolean_probabilities = calibration_scorecard.calibration_denominators(
            [({"factors": {"estimated_win_rate": True}}, {"outcome": "success"})],
            paper_candidates=[{"predicted": False, "actual": 0}],
        )
        self.assertEqual(boolean_probabilities["candidate_n"], 0)
        self.assertEqual(boolean_probabilities["evaluated_n"], 0)

    def test_calibration_round_trip_reads_no_edge_from_decision_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "memory.sqlite"
            conn = connect(db)
            decision = {
                "symbol": "EDGE",
                "market": "US",
                "direction": "watch",
                "action_level": "L0",
                "factors": {"estimated_win_rate": 0.7},
                "edge_status": "no_edge",
                "review_clock": "2099-01-01T00:00:00+00:00",
            }
            decision_path = root / "decision.json"
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            recorded = cmd_record_decision(
                conn, argparse.Namespace(payload=str(decision_path), db=str(db))
            )
            result_path = root / "result.json"
            result_path.write_text(json.dumps({
                "return_pct": 1.0, "outcome": "success",
            }), encoding="utf-8")
            cmd_record_result(conn, argparse.Namespace(
                payload=str(result_path), decision_id=recorded["decision_id"], db=str(db),
            ))
            card = calibration_scorecard.build_scorecard(
                conn, window=10, min_samples=1, source="skill"
            )
            conn.close()
        self.assertEqual(card["candidate_n"], 1)
        self.assertEqual(card["evaluated_n"], 0)
        self.assertEqual(card["abstention_rate"], 1.0)
        self.assertEqual(card["abstention_by_reason"]["no_edge_floor"], 1)
        self.assertEqual(card["reason"], "no_evaluated_predictions_after_abstention")

    def test_paper_candidate_denominator_uses_window_not_table_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "memory.sqlite")
            conn.execute(
                "CREATE TABLE calibration_samples_paper (sample_id TEXT PRIMARY KEY, "
                "decision_ref TEXT, symbol TEXT, predicted_p REAL, outcome INTEGER, "
                "source TEXT, recorded_at TEXT, outcome_time TEXT, prediction_field TEXT, "
                "source_payload_json TEXT)"
            )
            for index in range(3):
                conn.execute(
                    "INSERT INTO calibration_samples_paper VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"paper:{index}", f"decision:{index}", f"P{index}.US", 0.6,
                        index % 2, "paper", f"2026-01-0{index + 1}T00:00:00Z",
                        f"2026-01-0{index + 2}T00:00:00Z", "win_rate_proxy", "{}",
                    ),
                )
            conn.commit()
            card = calibration_scorecard.build_scorecard(
                conn, window=2, min_samples=1, source="paper"
            )
            conn.close()
        self.assertEqual(card["paper_samples_total"], 3)
        self.assertEqual(card["candidate_n"], 2)
        self.assertEqual(card["evaluated_n"], 0)
        self.assertEqual(card["excluded_n"], 2)
        self.assertEqual(card["paper_legacy_samples"], 3)
        self.assertIsNone(card["abstention_rate"])


if __name__ == "__main__":
    unittest.main()
