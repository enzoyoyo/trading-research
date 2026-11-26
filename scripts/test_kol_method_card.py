#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decision_compiler  # noqa: E402
import provenance_guard  # noqa: E402
from test_provenance_guard import make_valid_bundle  # noqa: E402


def make_valid_kol_bundle(root: Path) -> dict:
    bundle = make_valid_bundle(root)
    bundle["source_documents"][0].update({
        "author": "@test_author",
        "retrieved_at": "2026-07-17T10:03:00Z",
        "access_state": "public",
        "published_time_precision": "date_only",
    })

    def stage(
        summary: str,
        *,
        status: str = "supported",
        label: str = "framework_inference",
        mode: str = "framework_inference",
        claim_type: str = "opinion",
        source_ids: list[str] | None = None,
        framework_ids: list[str] | None = None,
        unknowns: list[str] | None = None,
    ) -> dict:
        return {
            "evidence_status": status,
            "summary": summary,
            "source_document_ids": ["DOC1"] if source_ids is None else source_ids,
            "framework_claim_ids": ["FC1"] if framework_ids is None else framework_ids,
            "inference_label": label,
            "provenance_mode": mode,
            "claim_type": claim_type,
            "supporting_eids": ["E1"] if status == "supported" else [],
            "contradicting_eids": [],
            "unknowns": [] if unknowns is None else unknowns,
        }

    def unknown(label: str) -> dict:
        return stage(
            "not_found_publicly", status="unknown", label="not_found_publicly",
            mode="unverified", claim_type="assumption", source_ids=[], framework_ids=[],
            unknowns=[label],
        )

    bundle["kol_method_cards"] = [{
        "method_card_id": "KMC-TEST-1",
        "author": "Test Author",
        "author_handle": "@test_author",
        "market_scope": ["US"],
        "time_horizon": ["swing"],
        "source_document_ids": ["DOC1"],
        "lifecycle": {
            "public_claim": stage(
                "The author publicly describes an evidence discipline.",
                label="direct_assertion", mode="source_summary", framework_ids=[],
            ),
            "falsifiable_thesis": {
                **stage("Evidence discipline should reduce unsupported conclusions."),
                "falsifier": "A recorded conclusion lacks a traceable supporting source.",
            },
            "universe_selection": unknown("No public mechanical universe rule."),
            "entry": unknown("No public mechanical entry rule."),
            "add_reduce": unknown("No public add/reduce ladder."),
            "stop_invalidation": {
                **stage("Invalidate when the primary source contradicts the thesis."),
                "falsifier": "A primary source contradicts the thesis.",
            },
            "take_profit": unknown("No public take-profit rule."),
            "exit": unknown("No public exit rule."),
            "sizing_risk": stage("Treat the method as context, not sizing authority."),
            "post_trade_calibration": unknown("No audited public calibration protocol."),
        },
        "promotion_gaps": [],
        "subscriber_gaps": [],
        "portable_parts": ["Trace every reusable method to a public source."],
        "do_not_port": ["Do not imitate exact trades or unverified performance."],
        "unknowns": ["No audited performance record."],
        "decision_boundary": {
            "allowed_effects": ["tighten", "refresh_source", "request_manual_review"],
            "forbidden_effects": [
                "raise_action_level", "raise_position_cap", "raise_reliability",
                "revive_l0", "order_execution",
            ],
            "position_multiplier": 0.0,
            "self_reported_performance_can_raise_reliability": False,
            "no_order_execution": True,
        },
    }]
    return bundle


class KolMethodCardTests(unittest.TestCase):
    def validate(self, bundle: dict, root: Path) -> dict:
        return provenance_guard.validate_bundle(bundle, base_dir=root)

    def kol_handoff(self, result: dict) -> dict:
        signal = result["suggested_module_signal"]
        self.assertEqual(signal["module"], "x_frontline")
        self.assertEqual(signal["sub_framework"], "kol_method_card")
        self.assertEqual(signal["max_action_level"], "L0")
        self.assertEqual(signal["position_multiplier"], 0.0)
        self.assertTrue(signal["tighten_only"])
        self.assertTrue(signal["no_order_execution"])
        self.assertIn(signal, result["suggested_module_signals"])
        return signal

    def generic_provenance_signal(self, result: dict) -> dict:
        matches = [
            signal for signal in result["suggested_module_signals"]
            if signal.get("module") == "research_readiness"
        ]
        self.assertEqual(len(matches), 1, result)
        return matches[0]

    def compile_guard_output(self, guard_result: dict) -> dict:
        signals = guard_result.get("suggested_module_signals")
        if not isinstance(signals, list):
            legacy = guard_result.get("suggested_module_signal")
            signals = [legacy] if isinstance(legacy, dict) else []
        return decision_compiler.compile_payload({
            "module_signals": [
                {
                    "module": "fundamentals",
                    "max_action_level": "L3",
                    "position_multiplier": 0.8,
                    "hard_veto": False,
                },
                *signals,
            ],
        })

    def public_ledger(self) -> dict:
        path = Path(__file__).resolve().parents[1] / "templates" / "kol-method-cards-public-ledger.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def final_balder_card(self, ledger: dict | None = None) -> dict:
        ledger = self.public_ledger() if ledger is None else ledger
        matches = [
            card for card in ledger["kol_method_cards"]
            if card.get("method_card_id") == "KMC-BALDER-20260731-FINAL"
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_valid_card_uses_existing_provenance_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.validate(make_valid_kol_bundle(root), root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "verified")
        self.assertEqual(result["memory_link"]["kol_method_card_ids"], ["KMC-TEST-1"])
        self.kol_handoff(result)
        self.assertEqual(len(result["suggested_module_signals"]), 1)

    def test_kol_framework_inference_requires_usable_framework_claim(self) -> None:
        for status, counterevidence_status in (
            ("rejected", "contradicted"),
            ("needs_verification", "searched_none_found"),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_kol_bundle(root)
                bundle["analysis_claims"] = []
                framework = bundle["framework_claims"][0]
                framework["status"] = status
                framework["counterevidence_status"] = counterevidence_status

                result = self.validate(bundle, root)

            self.assertFalse(result["ok"], result)
            self.assertIn("kol_stage_framework_not_usable", {row["code"] for row in result["errors"]})

    def test_promotion_gap_mapping_fields_reject_unknown_enums(self) -> None:
        invalid_fields = {
            "inference_label": "invented_label",
            "provenance_mode": "invented_mode",
            "claim_type": "experience_report",
        }
        for field, value in invalid_fields.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_kol_bundle(root)
                bundle["kol_method_cards"][0]["promotion_gaps"] = [{
                    "source_document_ids": ["DOC1"],
                    "reason": "A traceable claim cannot be promoted without verification.",
                    "inference_label": "self_reported_performance",
                    "provenance_mode": "unverified",
                    "claim_type": "opinion",
                    "can_raise_reliability": False,
                    field: value,
                }]

                result = self.validate(bundle, root)

            self.assertFalse(result["ok"], result)
            self.assertIn("kol_promotion_gap_mapping_invalid", {row["code"] for row in result["errors"]})

    def test_promotion_gap_cannot_raise_reliability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["kol_method_cards"][0]["promotion_gaps"] = [{
                "source_document_ids": ["DOC1"],
                "reason": "Self-reported performance remains unverified.",
                "inference_label": "self_reported_performance",
                "provenance_mode": "unverified",
                "claim_type": "opinion",
                "can_raise_reliability": True,
            }]

            result = self.validate(bundle, root)

        self.assertFalse(result["ok"], result)
        self.assertIn(
            "kol_promotion_gap_reliability_raise_forbidden",
            {row["code"] for row in result["errors"]},
        )

    def test_traceable_self_reported_promotion_gap_warns_and_caps_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["kol_method_cards"][0]["promotion_gaps"] = [{
                "source_document_ids": ["DOC1"],
                "reason": "Self-reported performance remains unverified.",
                "inference_label": "self_reported_performance",
                "provenance_mode": "unverified",
                "claim_type": "opinion",
                "can_raise_reliability": False,
            }]

            result = self.validate(bundle, root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn(
            "kol_self_reported_performance_unverified",
            {row["code"] for row in result["warnings"]},
        )

    def test_public_kol_ledger_uses_canonical_tracked_storage_scope(self) -> None:
        ledger = self.public_ledger()

        self.assertEqual(
            {document.get("storage_scope") for document in ledger["source_documents"]},
            {"tracked_allowed"},
        )

    def test_frank_projection_keeps_f1_self_report_out_of_portable_direct_rules(self) -> None:
        ledger = self.public_ledger()
        frank = next(card for card in ledger["kol_method_cards"] if card["author"] == "Frank Trading")
        gaps_by_source = {
            tuple(gap["source_document_ids"]): gap for gap in frank["promotion_gaps"]
        }

        self.assertEqual(
            set(gaps_by_source),
            {("KOL-SRC-F1",), ("KOL-SRC-F4",)},
        )
        f1_gap = gaps_by_source[("KOL-SRC-F1",)]
        self.assertEqual(f1_gap["inference_label"], "self_reported_performance")
        self.assertEqual(f1_gap["provenance_mode"], "unverified")
        self.assertEqual(f1_gap["claim_type"], "opinion")
        self.assertIs(f1_gap["can_raise_reliability"], False)
        self.assertRegex(f1_gap["reason"].lower(), r"gain|return|performance|exact")

        take_profit = frank["lifecycle"]["take_profit"]
        self.assertEqual(take_profit["evidence_status"], "partial")
        self.assertEqual(take_profit["inference_label"], "self_reported_performance")
        self.assertEqual(take_profit["provenance_mode"], "unverified")
        self.assertEqual(take_profit["claim_type"], "opinion")

        sizing = frank["lifecycle"]["sizing_risk"]
        self.assertEqual(sizing["inference_label"], "direct_assertion")
        self.assertIsNone(re.search(r"gain|return|performance|profit|account", sizing["summary"], re.I))
        self.assertRegex(" ".join(frank["do_not_port"]).lower(), r"exact.*trade|return")

    def test_balder_projection_does_not_promote_absent_or_inferred_rules(self) -> None:
        ledger = self.public_ledger()
        balder = next(
            card for card in ledger["kol_method_cards"]
            if card["method_card_id"] == "KMC-BALDER-20260717"
        )

        for stage_name in ("universe_selection", "stop_invalidation"):
            with self.subTest(stage=stage_name):
                stage = balder["lifecycle"][stage_name]
                self.assertEqual(stage["evidence_status"], "unknown")
                self.assertEqual(stage["inference_label"], "not_found_publicly")
                self.assertEqual(stage["provenance_mode"], "unverified")
                self.assertEqual(stage["claim_type"], "assumption")
                self.assertEqual(stage["source_document_ids"], [])
                self.assertEqual(stage["framework_claim_ids"], [])
                self.assertTrue(stage["unknowns"])

        entry = balder["lifecycle"]["entry"]
        self.assertEqual(entry["evidence_status"], "partial")
        self.assertEqual(entry["inference_label"], "direct_assertion")

    def test_balder_20260731_card_is_distinct_bounded_and_nonmaterial(self) -> None:
        ledger = self.public_ledger()
        card = self.final_balder_card(ledger)
        old_card = next(
            row for row in ledger["kol_method_cards"]
            if row["method_card_id"] == "KMC-BALDER-20260717"
        )
        result = self.validate(
            ledger,
            Path(__file__).resolve().parents[1] / "templates",
        )

        self.assertEqual(card["author_handle"], "@balder714059")
        self.assertEqual(old_card["author_handle"], "@Balder13946731")
        self.assertNotEqual(card["method_card_id"], old_card["method_card_id"])
        self.assertEqual(
            set(card["lifecycle"]),
            {
                "public_claim", "falsifiable_thesis", "universe_selection", "entry",
                "add_reduce", "stop_invalidation", "take_profit", "exit",
                "sizing_risk", "post_trade_calibration",
            },
        )
        self.assertEqual(ledger["bundle_purpose"]["target_ids"], [card["method_card_id"]])
        coverage = ledger["claim_coverage"]
        self.assertEqual(coverage["target_binding"], card["method_card_id"])
        self.assertEqual(coverage["accepted_claim_ids"], [])
        self.assertEqual(coverage["canonical_eids"], [])
        self.assertEqual(len(coverage["source_document_ids"]), 2)
        self.assertEqual(len(coverage["framework_claim_ids"]), 4)
        self.assertEqual(len(coverage["analysis_claim_ids"]), 4)
        self.assertEqual(len(ledger["quote_anchors"]), 7)
        self.assertEqual(
            ledger["research_readiness"],
            {
                "depth_mode": "deep_dive",
                "information_value": "medium",
                "knowability_status": "partially_knowable",
                "readiness_level": "needs_refresh",
                "readiness_basis": "The public method skeleton is source-grounded, but benchmark, formula, thresholds, publication origin, trade semantics, and independent outcome evidence are missing or conflicted.",
                "blocking_gaps": [
                    "canonical public DOM parity is unverified",
                    "benchmark and relative-strength formula are undisclosed",
                    "tracking inclusion/removal is not verified trade entry/exit",
                    "no canonical EvidenceItem IDs or independent outcome ledger",
                    "MRVL and NFLX state labels conflict between prose and image",
                ],
                "stale_after": "2026-08-01T22:11:43Z",
                "must_refresh_if": [
                    "a canonical article capture or correction becomes available",
                    "the author publishes the benchmark, formula, thresholds, or confirmation-state definitions",
                    "independent point-in-time market data or transaction evidence becomes available",
                    "any later source resolves the prose/image state conflict",
                ],
                "max_action_level_cap": "L0",
                "module_signal": {
                    "module": "research_readiness", "max_action_level": "L0",
                    "position_multiplier": 0.0, "hard_veto": False,
                    "tighten_only": True,
                    "reason": "needs_refresh:partial_single_article_and_nonreproducible_signal",
                },
            },
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertFalse(result["memory_link"]["materiality_eligible"])
        self.assertEqual(result["memory_link"]["accepted_claim_ids"], [])
        self.assertEqual(result["memory_link"]["canonical_eids"], [])
        self.kol_handoff(result)

    def test_balder_20260731_private_capture_is_hash_bound_and_not_distributed(self) -> None:
        ledger = self.public_ledger()
        balder_source_ids = set(self.final_balder_card(ledger)["source_document_ids"])
        documents = [
            row for row in ledger["source_documents"]
            if row["document_id"] in balder_source_ids
        ]
        scoped_text = json.dumps(
            {"documents": documents, "card": self.final_balder_card(ledger)},
            ensure_ascii=False,
        )

        self.assertEqual(len(documents), 2)
        self.assertEqual({row["capture_bytes_storage_scope"] for row in documents}, {"private"})
        self.assertEqual(
            {row["document_id"]: row["content_sha256"] for row in documents},
            {
                "DOC-BALDER-2026-07-31": "93894cf3d46129c89edb12b4a317ef8afd095b420d128de37449f80c64de7469",
                "DOC-BALDER-IMG-08": "a13382d6c26c793a145ad3690dd3eb321c751ce4ac489cc70c3dd8065900ea81",
            },
        )
        self.assertTrue(all("local_path" not in row for row in documents))
        self.assertNotRegex(scoped_text, r"/Users/|\\Users\\|\.eml(?:\"|$)")
        self.assertEqual(
            {row["disposition"] for row in ledger["source_capture_manifest"]},
            {"private_capture_not_distributed", "private_image_capture_not_distributed"},
        )
        self.assertEqual(
            {row["quote_id"]: row.get("verbatim_sha256") for row in ledger["quote_anchors"]},
            {
                "Q-BAL-01": "2872308ebbb506cc1d11a293fa65d83ffde9e9f2faf8fadcaefcf526b09272fe",
                "Q-BAL-02": "8b62d41f578e50acbe77fd2fdf4abaada72c0a4fc7066b69ebab1f6474bd57bc",
                "Q-BAL-03": "4741baf868941da15508213cd6da8b4ac54eca824248132dbb138fcd88d3e4f8",
                "Q-BAL-04": "a5bff368f3e3c5137432747a5b6abebac1057a8a4e7fca4224d757b3526ffa15",
                "Q-BAL-05": "210baa5868d50e28552f22247556475a861c132c70e57c0dc7ab5f7891547fb1",
                "Q-BAL-06": "a17842ae5b73527915e2fe8a4ce883825a0f0b6f4e859b9f07daacbc8e1d3c81",
                "Q-BAL-07": "172c7bcb06d6b26bdf130d116b430b2c29307f57285a454b2899fba346d7464c",
            },
        )

        tampered = copy.deepcopy(ledger)
        tampered["quote_anchors"][0]["verbatim_sha256"] = "0" * 64
        result = self.validate(
            tampered,
            Path(__file__).resolve().parents[1] / "templates",
        )
        self.assertFalse(result["ok"], result)
        self.assertIn(
            "quote_attestation_hash_mismatch",
            {row["code"] for row in result["errors"]},
        )

    def test_balder_20260731_boundary_is_exactly_tighten_only(self) -> None:
        card = self.final_balder_card()
        self.assertEqual(
            card["decision_boundary"],
            {
                "allowed_effects": ["tighten", "refresh_source", "request_manual_review"],
                "forbidden_effects": [
                    "raise_action_level", "raise_position_cap", "raise_reliability",
                    "revive_l0", "order_execution",
                ],
                "position_multiplier": 0,
                "self_reported_performance_can_raise_reliability": False,
                "no_order_execution": True,
            },
        )
        rejected = " ".join(card["do_not_port"]).lower()
        for term in ("87bp", "31.8", "dashboard", "hidden", "cron", "order"):
            self.assertIn(term, rejected)

    def test_balder_prediction_edge_matrix_is_symbolic_and_tighten_only(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "templates" / "scenario-regression-evals.jsonl"
        rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line.strip()]
        cases = [
            row for row in rows
            if row.get("metadata", {}).get("method_card_id") == "KMC-BALDER-20260731-FINAL"
        ]

        self.assertEqual(
            {row["metadata"]["edge_case_id"] for row in cases},
            {f"E{index:02d}" for index in range(1, 16)},
        )
        self.assertEqual(len(cases), 15)
        for case in cases:
            with self.subTest(case=case["name"]):
                self.assertIs(case["metadata"].get("symbolic_only"), True)
                self.assertEqual(
                    {signal["module"] for signal in case["input"]["module_signals"]},
                    {"fundamentals", "research_readiness"},
                )
                self.assertEqual(
                    case["expect"],
                    {
                        "compiled_action": "L0", "entry_permission": "WATCH",
                        "final_position_multiplier": 0.0, "hard_veto": False,
                    },
                )
                serialized = json.dumps(case, ensure_ascii=False)
                self.assertNotRegex(serialized, r"87bp|31\.8|37\.9|(?<![A-Za-z0-9])(?:36|38)(?![A-Za-z0-9])")

    def test_verified_kol_guard_output_cannot_preserve_positive_compiler_multiplier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard_result = self.validate(make_valid_kol_bundle(root), root)
            compiled = self.compile_guard_output(guard_result)

        self.assertTrue(guard_result["ok"], guard_result)
        self.assertEqual(guard_result["provenance_readiness"], "verified")
        self.assertTrue(compiled["ok"], compiled)
        self.assertEqual(compiled["compiled_action"], "L0")
        self.assertEqual(compiled["entry_permission"], "WATCH")
        self.assertEqual(compiled["final_position_multiplier"], 0.0)
        self.assertIn("x_frontline:kol_method_card", compiled["dominant_constraints"])

    def test_partial_kol_guard_output_cannot_preserve_positive_compiler_multiplier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["public_claim"]
            stage.update({
                "evidence_status": "partial",
                "supporting_eids": [],
                "unknowns": ["The public claim is only partially evidenced."],
            })
            guard_result = self.validate(bundle, root)
            compiled = self.compile_guard_output(guard_result)

        self.assertTrue(guard_result["ok"], guard_result)
        self.assertEqual(guard_result["provenance_readiness"], "partial")
        self.assertTrue(compiled["ok"], compiled)
        self.assertEqual(compiled["compiled_action"], "L0")
        self.assertEqual(compiled["entry_permission"], "WATCH")
        self.assertEqual(compiled["final_position_multiplier"], 0.0)
        self.assertIn("x_frontline:kol_method_card", compiled["dominant_constraints"])

    def test_legacy_bundle_without_cards_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.validate(make_valid_bundle(root), root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["memory_link"]["kol_method_card_ids"], [])
        self.assertIsNone(result["suggested_module_signal"])
        self.assertEqual(result["suggested_module_signals"], [])

    def test_source_requires_author(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["source_documents"][0].pop("author")
            result = self.validate(bundle, root)
        self.assertIn("kol_source_author_missing", {row["code"] for row in result["errors"]})

    def test_source_requires_original_url_and_publication_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["source_documents"][0].pop("original_url")
            bundle["source_documents"][0].pop("published_at")
            result = self.validate(bundle, root)
        codes = {row["code"] for row in result["errors"]}
        self.assertIn("source_metadata_missing", codes)
        self.assertIn("kol_published_at_missing", codes)

    def test_source_requires_retrieved_time_and_access_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["source_documents"][0].pop("retrieved_at")
            bundle["source_documents"][0].pop("access_state")
            result = self.validate(bundle, root)
        codes = {row["code"] for row in result["errors"]}
        self.assertIn("kol_retrieved_at_missing", codes)
        self.assertIn("kol_access_state_missing", codes)

    def test_lifecycle_requires_every_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            del bundle["kol_method_cards"][0]["lifecycle"]["exit"]
            result = self.validate(bundle, root)
        self.assertIn("kol_lifecycle_missing_stage", {row["code"] for row in result["errors"]})

    def test_stage_requires_inference_label_and_eid_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["entry"]
            stage.pop("inference_label")
            stage.pop("contradicting_eids")
            result = self.validate(bundle, root)
        codes = {row["code"] for row in result["errors"]}
        self.assertIn("kol_inference_label_missing", codes)
        self.assertIn("kol_eid_handoff_missing", codes)

    def test_alias_claim_type_must_be_mapped_at_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["kol_method_cards"][0]["lifecycle"]["public_claim"]["claim_type"] = "fact_claim"
            result = self.validate(bundle, root)
        self.assertIn("kol_noncanonical_claim_type", {row["code"] for row in result["errors"]})

    def test_self_reported_performance_cannot_raise_reliability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["post_trade_calibration"]
            stage.update({
                "evidence_status": "supported", "inference_label": "self_reported_performance",
                "provenance_mode": "source_summary", "claim_type": "reported_metric",
                "source_document_ids": ["DOC1"],
            })
            result = self.validate(bundle, root)
        self.assertIn("kol_self_reported_performance_mapping", {row["code"] for row in result["errors"]})

    def test_direct_quote_stage_requires_existing_quote_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["public_claim"]
            stage["provenance_mode"] = "direct_quote"
            stage["quote_anchor_ids"] = []
            result = self.validate(bundle, root)
        self.assertIn("kol_direct_quote_anchor_missing", {row["code"] for row in result["errors"]})

    def test_direct_quote_anchor_source_must_belong_to_stage_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            second_source = dict(bundle["source_documents"][0])
            second_source.update({
                "document_id": "DOC2",
                "title": "Second source",
                "original_url": "https://example.com/second-source",
            })
            bundle["source_documents"].append(second_source)
            card = bundle["kol_method_cards"][0]
            card["source_document_ids"].append("DOC2")
            stage = card["lifecycle"]["public_claim"]
            stage.update({
                "provenance_mode": "direct_quote",
                "source_document_ids": ["DOC2"],
                "quote_anchor_ids": ["Q1"],
            })
            result = self.validate(bundle, root)
        self.assertIn("kol_direct_quote_source_mismatch", {row["code"] for row in result["errors"]})

    def test_partial_stage_keeps_generic_l1_and_strict_kol_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["public_claim"]
            stage.update({
                "evidence_status": "partial",
                "supporting_eids": [],
                "unknowns": ["The public claim is only partially evidenced."],
            })
            result = self.validate(bundle, root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn("kol_partial_evidence_stage", {row["code"] for row in result["warnings"]})
        self.kol_handoff(result)
        generic = self.generic_provenance_signal(result)
        self.assertEqual(generic["max_action_level"], "L1")
        self.assertEqual(generic["position_multiplier"], 1.0)
        self.assertTrue(generic["tighten_only"])

    def test_self_reported_performance_requires_traceable_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["post_trade_calibration"]
            stage.update({
                "evidence_status": "partial",
                "inference_label": "self_reported_performance",
                "provenance_mode": "unverified",
                "claim_type": "opinion",
                "source_document_ids": [],
                "framework_claim_ids": [],
                "supporting_eids": [],
                "unknowns": ["No traceable performance source was supplied."],
            })
            result = self.validate(bundle, root)
        self.assertIn("kol_self_reported_source_missing", {row["code"] for row in result["errors"]})

    def test_traceable_self_reported_performance_is_partial_and_tighten_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            stage = bundle["kol_method_cards"][0]["lifecycle"]["post_trade_calibration"]
            stage.update({
                "evidence_status": "partial",
                "inference_label": "self_reported_performance",
                "provenance_mode": "unverified",
                "claim_type": "opinion",
                "source_document_ids": ["DOC1"],
                "framework_claim_ids": [],
                "supporting_eids": [],
                "unknowns": ["Performance remains unaudited self-report."],
            })
            result = self.validate(bundle, root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn(
            "kol_self_reported_performance_unverified",
            {row["code"] for row in result["warnings"]},
        )
        self.kol_handoff(result)
        generic = self.generic_provenance_signal(result)
        self.assertEqual(generic["max_action_level"], "L1")
        self.assertEqual(generic["position_multiplier"], 1.0)

    def test_restricted_card_source_requires_subscriber_gap_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            restricted = dict(bundle["source_documents"][0])
            restricted.update({
                "document_id": "DOC2",
                "title": "Restricted source",
                "original_url": "https://example.com/restricted-source",
                "access_state": "subscriber_only",
            })
            bundle["source_documents"].append(restricted)
            bundle["kol_method_cards"][0]["source_document_ids"].append("DOC2")
            result = self.validate(bundle, root)
        self.assertIn("kol_restricted_source_gap_missing", {row["code"] for row in result["errors"]})

    def test_restricted_source_is_rejected_outside_subscriber_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            restricted = dict(bundle["source_documents"][0])
            restricted.update({
                "document_id": "DOC2",
                "title": "Restricted source",
                "original_url": "https://example.com/restricted-source",
                "access_state": "blocked",
            })
            bundle["source_documents"].append(restricted)
            card = bundle["kol_method_cards"][0]
            card["source_document_ids"].append("DOC2")
            card["subscriber_gaps"] = [{
                "source_document_ids": ["DOC2"],
                "reason": "The blocked source is recorded only as a gap.",
            }]
            card["promotion_gaps"] = [{
                "source_document_ids": ["DOC2"],
                "reason": "A restricted source must not appear here.",
            }]
            result = self.validate(bundle, root)
        self.assertIn(
            "kol_restricted_source_outside_subscriber_gaps",
            {row["code"] for row in result["errors"]},
        )

    def test_gap_rows_require_nonempty_source_document_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["kol_method_cards"][0]["subscriber_gaps"] = [{
                "source_document_ids": [],
                "reason": "A source-free gap row is ambiguous.",
            }]
            result = self.validate(bundle, root)
        self.assertIn("kol_gap_source_missing", {row["code"] for row in result["errors"]})

    def test_decision_boundary_is_tighten_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            boundary = bundle["kol_method_cards"][0]["decision_boundary"]
            boundary["allowed_effects"].append("raise_action_level")
            boundary["position_multiplier"] = 1.0
            result = self.validate(bundle, root)
        self.assertIn("kol_decision_boundary_invalid", {row["code"] for row in result["errors"]})

    def test_partial_source_is_l1_capped_when_explicitly_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["source_documents"][0]["access_state"] = "partial"
            for stage in bundle["kol_method_cards"][0]["lifecycle"].values():
                if stage["source_document_ids"]:
                    stage["evidence_status"] = "partial"
            result = self.validate(bundle, root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.kol_handoff(result)
        generic = self.generic_provenance_signal(result)
        self.assertEqual(generic["max_action_level"], "L1")
        self.assertEqual(generic["position_multiplier"], 1.0)

    def test_subscriber_source_cannot_support_lifecycle_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_kol_bundle(root)
            bundle["source_documents"][0]["access_state"] = "subscriber_only"
            result = self.validate(bundle, root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["provenance_readiness"], "blocked")
        self.assertIn("kol_restricted_source_used", {row["code"] for row in result["errors"]})


if __name__ == "__main__":
    unittest.main()
