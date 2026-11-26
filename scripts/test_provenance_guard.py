#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import provenance_guard  # noqa: E402


def make_valid_bundle(root: Path) -> dict:
    source = root / "source.md"
    source.write_text("A direct quote about disciplined evidence.\n", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "schema_version": "research_provenance.v1",
        "as_of": "2026-07-14T00:00:00Z",
        "source_documents": [{
            "document_id": "DOC1", "source_type": "primary_interview",
            "title": "Interview", "publisher": "official_site", "published_at": "2026-06-08",
            "original_url": "https://example.com/interview", "local_path": "source.md",
            "content_sha256": digest, "license_scope": "public",
            "storage_scope": "tracked_allowed",
            "redistribution_allowed": "derived_only",
        }],
        "quote_anchors": [{
            "quote_id": "Q1", "document_id": "DOC1",
            "verbatim_text": "A direct quote about disciplined evidence.",
            "locator": {"line_start": 1, "line_end": 1}, "speaker": "Source",
            "quote_use": "internal_evidence",
        }],
        "framework_claims": [{
            "framework_claim_id": "FC1", "statement": "Use disciplined evidence.",
            "quote_anchor_ids": ["Q1"], "derivation_note": "Minimal abstraction.",
            "scope": "research_process", "status": "supported",
            "counterevidence_status": "searched_none_found", "counterevidence_refs": [],
        }],
        "analysis_claims": [{
            "claim_id": "AC1", "statement": "The analysis follows evidence discipline.",
            "provenance_mode": "framework_inference", "source_document_ids": [],
            "quote_anchor_ids": [], "framework_claim_ids": ["FC1"], "evidence_refs": ["E1"],
            "decision_use": "context_only", "counterevidence_status": "searched_none_found",
            "counterevidence_refs": [], "falsifier": "A primary source contradicts it.",
        }],
        "behavior_cross_checks": [], "no_order_execution": True,
    }


def make_covered_bundle(root: Path) -> dict:
    """Return the v1 bundle with the optional admission contract populated."""
    bundle = make_valid_bundle(root)
    bundle["source_documents"][0].update({
        "retrieved_at": "2026-07-13T12:00:00Z",
        "access_state": "public",
        "media_type": "text/plain",
    })
    bundle["bundle_purpose"] = {
        "kind": "report_evidence",
        "target_ids": ["TARGET-EXAMPLE"],
        "decision_use": "context_only",
    }
    bundle["claim_coverage"] = {
        "source_document_ids": ["DOC1"],
        "framework_claim_ids": ["FC1"],
        "analysis_claim_ids": ["AC1"],
        "accepted_claim_ids": [],
        "watch_only_claim_ids": [],
        "context_only_claim_ids": ["AC1"],
        "rejected_claim_ids": [],
        "canonical_eids": [],
        "target_binding": "TARGET-EXAMPLE",
    }
    return bundle


class ProvenanceGuardTests(unittest.TestCase):
    def test_valid_quote_backed_framework_bundle_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "verified")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["memory_link"]["source_document_ids"], ["DOC1"])
        self.assertEqual(result["memory_link"]["quote_anchor_ids"], ["Q1"])
        self.assertEqual(result["memory_link"]["framework_claim_ids"], ["FC1"])
        self.assertEqual(result["memory_link"]["analysis_claim_ids"], ["AC1"])
        self.assertEqual(result["memory_link"]["behavior_cross_check_ids"], [])
        self.assertIsNone(result["suggested_module_signal"])
        self.assertEqual(result["suggested_module_signals"], [])
        self.assertTrue(result["no_order_execution"])

    def test_required_base_sections_cannot_be_omitted(self) -> None:
        required_sections = (
            "source_documents",
            "quote_anchors",
            "framework_claims",
            "analysis_claims",
            "behavior_cross_checks",
        )
        for section in required_sections:
            with self.subTest(section=section), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                bundle.pop(section)

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn("required_section_missing", {row["code"] for row in result["errors"]})

    def test_kol_method_cards_section_remains_optional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            self.assertNotIn("kol_method_cards", bundle)

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "verified")

    def test_required_base_sections_may_be_empty_arrays(self) -> None:
        bundle = {
            "schema_version": "research_provenance.v1",
            "as_of": "2026-07-17T00:00:00Z",
            "source_documents": [],
            "quote_anchors": [],
            "framework_claims": [],
            "analysis_claims": [],
            "behavior_cross_checks": [],
            "no_order_execution": True,
        }

        result = provenance_guard.validate_bundle(bundle)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "verified")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])

    def test_supplied_invalid_source_governance_enums_are_blocked(self) -> None:
        invalid_values = {
            "license_scope": ("not_a_license", "invalid_license_scope"),
            "storage_scope": ("metadata_only", "invalid_storage_scope"),
            "redistribution_allowed": ("maybe", "invalid_redistribution_allowed"),
        }
        for field, (value, expected_code) in invalid_values.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                bundle["source_documents"][0][field] = value

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn(expected_code, {row["code"] for row in result["errors"]})

    def test_missing_source_governance_warns_and_caps_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            document = bundle["source_documents"][0]
            for field in ("license_scope", "storage_scope", "redistribution_allowed"):
                document.pop(field)

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn("source_governance_missing", {row["code"] for row in result["warnings"]})

    def test_all_stable_ids_are_required_and_nonempty(self) -> None:
        sections = {
            "source_documents": "document_id",
            "quote_anchors": "quote_id",
            "framework_claims": "framework_claim_id",
            "analysis_claims": "claim_id",
            "behavior_cross_checks": "cross_check_id",
        }
        for section, field in sections.items():
            with self.subTest(section=section), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                if section == "behavior_cross_checks":
                    bundle[section] = [{
                        "cross_check_id": "BC1",
                        "statement_claim_id": "AC1",
                        "behavior_evidence_refs": ["E2"],
                        "subject_match": "confirmed",
                        "temporal_alignment": "aligned",
                        "tenure_alignment": "confirmed",
                        "result": "supports",
                        "causality_claimed": False,
                    }]
                bundle[section][0][field] = ""

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn("stable_id_missing", {row["code"] for row in result["errors"]})

    def test_quote_anchor_requires_speaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["quote_anchors"][0].pop("speaker")

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertIn("quote_required_field_missing", {row["code"] for row in result["errors"]})

    def test_framework_claim_requires_documented_contract_fields(self) -> None:
        for field in ("statement", "derivation_note", "scope"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                bundle["framework_claims"][0].pop(field)

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn("framework_required_field_missing", {row["code"] for row in result["errors"]})

    def test_analysis_claim_requires_statement_and_explicit_reference_arrays(self) -> None:
        fields = (
            "statement",
            "source_document_ids",
            "quote_anchor_ids",
            "framework_claim_ids",
            "evidence_refs",
            "counterevidence_refs",
        )
        for field in fields:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                bundle["analysis_claims"][0].pop(field)

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn("analysis_required_field_missing", {row["code"] for row in result["errors"]})

    def test_framework_claim_without_counterevidence_search_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["framework_claims"][0]["counterevidence_status"] = "not_checked"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("framework_counterevidence_not_checked", {row["code"] for row in result["errors"]})

    def test_framework_inference_with_unknown_framework_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["analysis_claims"][0]["framework_claim_ids"] = ["MISSING"]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("analysis_framework_missing", {row["code"] for row in result["errors"]})

    def test_unverified_claim_cannot_support_a_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            claim = bundle["analysis_claims"][0]
            claim["provenance_mode"] = "unverified"
            claim["framework_claim_ids"] = []
            claim["decision_use"] = "supports_conclusion"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("unverified_claim_supports_conclusion", {row["code"] for row in result["errors"]})
        self.assertEqual(result["suggested_module_signal"]["module"], "research_readiness")
        self.assertEqual(result["suggested_module_signal"]["max_action_level"], "L0")
        self.assertEqual(result["suggested_module_signal"]["position_multiplier"], 0.0)
        self.assertTrue(result["suggested_module_signal"]["tighten_only"])

    def test_behavior_support_requires_subject_time_and_tenure_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["behavior_cross_checks"] = [{
                "cross_check_id": "BC1",
                "statement_claim_id": "AC1",
                "behavior_evidence_refs": ["E-HOLDING-1"],
                "subject_match": "confirmed",
                "temporal_alignment": "aligned",
                "tenure_alignment": "unknown",
                "result": "supports",
                "causality_claimed": False,
            }]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("behavior_alignment_insufficient", {row["code"] for row in result["errors"]})

    def test_behavior_cross_check_cannot_claim_causality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["behavior_cross_checks"] = [{
                "cross_check_id": "BC1", "statement_claim_id": "AC1",
                "behavior_evidence_refs": ["E-HOLDING-1"], "subject_match": "confirmed",
                "temporal_alignment": "aligned", "tenure_alignment": "confirmed",
                "result": "supports", "causality_claimed": True,
            }]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("behavior_causality_forbidden", {row["code"] for row in result["errors"]})

    def test_bundle_must_preserve_no_order_execution_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["no_order_execution"] = False

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("order_execution_boundary_missing", {row["code"] for row in result["errors"]})
        self.assertTrue(result["no_order_execution"])

    def test_uncaptured_source_summary_is_partial_and_watch_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            document = bundle["source_documents"][0]
            document.pop("local_path")
            document.pop("content_sha256")
            bundle["quote_anchors"] = []
            bundle["framework_claims"] = []
            claim = bundle["analysis_claims"][0]
            claim["provenance_mode"] = "source_summary"
            claim["source_document_ids"] = ["DOC1"]
            claim["framework_claim_ids"] = []
            claim["decision_use"] = "context_only"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn("source_capture_missing", {row["code"] for row in result["warnings"]})
        self.assertEqual(result["suggested_module_signal"]["max_action_level"], "L1")
        self.assertEqual(result["suggested_module_signal"]["position_multiplier"], 1.0)
        self.assertTrue(result["suggested_module_signal"]["tighten_only"])
        self.assertEqual(
            result["suggested_module_signals"],
            [result["suggested_module_signal"]],
        )

    def test_direct_quote_claim_requires_existing_quote_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            claim = bundle["analysis_claims"][0]
            claim["provenance_mode"] = "direct_quote"
            claim["source_document_ids"] = ["DOC1"]
            claim["quote_anchor_ids"] = ["MISSING"]
            claim["framework_claim_ids"] = []

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("analysis_quote_missing", {row["code"] for row in result["errors"]})

    def test_duplicate_stable_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["source_documents"].append(dict(bundle["source_documents"][0]))

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("duplicate_id", {row["code"] for row in result["errors"]})

    def test_unknown_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["schema_version"] = "research_provenance.v0"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("unsupported_schema_version", {row["code"] for row in result["errors"]})

    def test_source_summary_requires_existing_source_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            claim = bundle["analysis_claims"][0]
            claim["provenance_mode"] = "source_summary"
            claim["source_document_ids"] = ["MISSING"]
            claim["framework_claim_ids"] = []

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("analysis_source_missing", {row["code"] for row in result["errors"]})

    def test_supported_framework_claim_requires_quote_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["framework_claims"][0]["quote_anchor_ids"] = []

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("framework_quote_missing", {row["code"] for row in result["errors"]})

    def test_contradicted_framework_cannot_remain_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["framework_claims"][0]["counterevidence_status"] = "contradicted"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("framework_status_conflict", {row["code"] for row in result["errors"]})

    def test_conclusion_supporting_claim_requires_falsifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            claim = bundle["analysis_claims"][0]
            claim["decision_use"] = "supports_conclusion"
            claim["falsifier"] = ""

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("analysis_falsifier_missing", {row["code"] for row in result["errors"]})

    def test_conclusion_supporting_claim_requires_counterevidence_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            claim = bundle["analysis_claims"][0]
            claim["decision_use"] = "supports_conclusion"
            claim["counterevidence_status"] = "not_checked"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("analysis_counterevidence_not_checked", {row["code"] for row in result["errors"]})

    def test_contradicted_or_mixed_claim_cannot_support_conclusion(self) -> None:
        for counter_status in ("contradicted", "mixed"):
            with self.subTest(counter_status=counter_status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                claim = bundle["analysis_claims"][0]
                claim["decision_use"] = "supports_conclusion"
                claim["counterevidence_status"] = counter_status

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn("analysis_counterevidence_conflict", {row["code"] for row in result["errors"]})

    def test_covered_bundle_binds_target_and_decision_use_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_covered_bundle(root)

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["memory_link"]["accepted_claim_ids"], [])
        self.assertEqual(result["memory_link"]["rejected_claim_ids"], [])
        self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_empty_or_unbound_claim_coverage_fails_closed(self) -> None:
        mutations = {
            "empty": (lambda bundle: bundle["claim_coverage"].update({"analysis_claim_ids": []}), "claim_coverage_empty"),
            "unknown_claim": (
                lambda bundle: bundle["claim_coverage"].update({
                    "analysis_claim_ids": ["MISSING"], "context_only_claim_ids": ["MISSING"]
                }),
                "claim_coverage_claim_missing",
            ),
            "target_mismatch": (
                lambda bundle: bundle["claim_coverage"].update({"target_binding": "OTHER"}),
                "claim_coverage_target_mismatch",
            ),
            "decision_use_mismatch": (
                lambda bundle: bundle["claim_coverage"].update({
                    "watch_only_claim_ids": ["AC1"], "context_only_claim_ids": []
                }),
                "claim_coverage_decision_use_mismatch",
            ),
        }
        for name, (mutate, expected_code) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_covered_bundle(root)
                mutate(bundle)

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn(expected_code, {row["code"] for row in result["errors"]})
            self.assertEqual(result["memory_link"]["accepted_claim_ids"], [])
            self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_blocked_output_separates_rejected_ids_and_never_accepts_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_covered_bundle(root)
            claim = bundle["analysis_claims"][0]
            claim["decision_use"] = "supports_conclusion"
            claim["counterevidence_status"] = "contradicted"
            coverage = bundle["claim_coverage"]
            coverage["context_only_claim_ids"] = []
            coverage["accepted_claim_ids"] = ["AC1"]
            coverage["rejected_claim_ids"] = ["AC1"]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "blocked")
        self.assertEqual(result["memory_link"]["accepted_claim_ids"], [])
        self.assertEqual(result["memory_link"]["rejected_claim_ids"], ["AC1"])
        self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_capture_manifest_enforces_semantic_role_and_claim_binding(self) -> None:
        mutations = {
            "semantic_without_claims": (
                lambda row, bundle: row.update({"supported_claim_ids": []}),
                "capture_manifest_semantic_claims_missing",
            ),
            "nonsemantic_with_claims": (
                lambda row, bundle: row.update({"source_role": "nonsemantic"}),
                "capture_manifest_nonsemantic_claims_forbidden",
            ),
            "hash_mismatch": (
                lambda row, bundle: row.update({"content_sha256": "0" * 64}),
                "capture_manifest_hash_mismatch",
            ),
            "review_locator_missing": (
                lambda row, bundle: row.update({"review_locator": ""}),
                "capture_manifest_review_locator_missing",
            ),
        }
        for name, (mutate, expected_code) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_covered_bundle(root)
                document = bundle["source_documents"][0]
                row = {
                    "document_id": "DOC1",
                    "content_sha256": document["content_sha256"],
                    "media_type": "text/plain",
                    "source_role": "semantic",
                    "disposition": "reviewed",
                    "review_locator": "review-index:1",
                    "supported_claim_ids": ["AC1"],
                }
                bundle["source_capture_manifest"] = [row]
                mutate(row, bundle)

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn(expected_code, {item["code"] for item in result["errors"]})

    def test_semantic_raster_requires_dimensions_and_text_cannot_cover_image_only_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_covered_bundle(root)
            image_doc = {
                "document_id": "IMG1", "source_type": "primary_image", "title": "Chart",
                "publisher": "official_site", "published_at": "2026-06-08",
                "retrieved_at": "2026-07-13T12:00:00Z", "access_state": "public",
                "original_url": "https://example.com/chart.png", "content_sha256": "1" * 64,
                "media_type": "image/png", "license_scope": "public",
                "storage_scope": "private", "redistribution_allowed": "derived_only",
            }
            bundle["source_documents"].append(image_doc)
            bundle["analysis_claims"][0]["source_document_ids"] = ["IMG1"]
            bundle["claim_coverage"]["source_document_ids"] = ["IMG1"]
            bundle["source_capture_manifest"] = [{
                "document_id": "IMG1", "content_sha256": "1" * 64,
                "media_type": "image/png", "source_role": "semantic", "disposition": "reviewed",
                "review_locator": "review-index:2", "supported_claim_ids": ["AC1"],
            }]

            missing_dimensions = provenance_guard.validate_bundle(bundle, base_dir=root)
            bundle["source_capture_manifest"][0].update({
                "document_id": "DOC1",
                "content_sha256": bundle["source_documents"][0]["content_sha256"],
                "media_type": "text/plain",
            })
            false_text_coverage = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertIn(
            "capture_manifest_image_dimensions_missing",
            {row["code"] for row in missing_dimensions["errors"]},
        )
        self.assertIn(
            "capture_manifest_text_image_coverage",
            {row["code"] for row in false_text_coverage["errors"]},
        )

    def test_private_capture_quote_attestation_is_narrow_partial_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_covered_bundle(root)
            document = bundle["source_documents"][0]
            document.pop("local_path")
            document.update({
                "access_state": "partial",
                "capture_bytes_storage_scope": "private",
                "author": "Test Author",
                "retrieved_at": "2026-07-13T12:00:00Z",
                "published_time_precision": "date_only",
            })
            bundle["bundle_purpose"].update({
                "kind": "kol_method_handoff",
                "target_ids": ["KMC-ATTESTED"],
            })
            bundle["claim_coverage"].update({
                "accepted_claim_ids": [],
                "canonical_eids": [],
                "target_binding": "KMC-ATTESTED",
            })
            quote = bundle["quote_anchors"][0]
            quote["locator"] = {"review_index": "L-01"}
            quote["verbatim_sha256"] = hashlib.sha256(
                quote["verbatim_text"].encode("utf-8")
            ).hexdigest()
            unknown_stage = {
                "evidence_status": "unknown",
                "summary": "not_found_publicly",
                "source_document_ids": [],
                "quote_anchor_ids": [],
                "framework_claim_ids": [],
                "inference_label": "not_found_publicly",
                "provenance_mode": "unverified",
                "claim_type": "assumption",
                "supporting_eids": [],
                "contradicting_eids": [],
                "unknowns": ["No public rule is established by this fixture."],
            }
            bundle["kol_method_cards"] = [{
                "method_card_id": "KMC-ATTESTED",
                "author": "Test Author",
                "author_handle": "@test_author",
                "market_scope": ["test"],
                "time_horizon": ["unknown"],
                "source_document_ids": ["DOC1"],
                "lifecycle": {
                    stage: copy.deepcopy(unknown_stage)
                    for stage in (
                        "public_claim", "falsifiable_thesis", "universe_selection", "entry",
                        "add_reduce", "stop_invalidation", "take_profit", "exit",
                        "sizing_risk", "post_trade_calibration",
                    )
                },
                "promotion_gaps": [],
                "subscriber_gaps": [],
                "portable_parts": ["Keep this capture research-only."],
                "do_not_port": ["Do not infer a trading rule."],
                "unknowns": ["The method remains unknown."],
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
            bundle["source_capture_manifest"] = [{
                "document_id": "DOC1",
                "content_sha256": document["content_sha256"],
                "media_type": "text/plain",
                "source_role": "semantic",
                "disposition": "private_capture_not_distributed",
                "review_locator": "review-index:L-01",
                "supported_claim_ids": ["AC1"],
                "quote_anchor_ids": ["Q1"],
                "quote_locators": {"Q1": {"review_index": "L-01"}},
            }]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)
            quote["verbatim_sha256"] = "0" * 64
            tampered = provenance_guard.validate_bundle(bundle, base_dir=root)
            bundle["bundle_purpose"]["kind"] = "report_evidence"
            wrong_purpose = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertFalse(result["memory_link"]["materiality_eligible"])
        self.assertIn("quote_private_capture_attested", {row["code"] for row in result["warnings"]})
        self.assertIn("quote_attestation_hash_mismatch", {row["code"] for row in tampered["errors"]})
        self.assertIn("quote_source_not_captured", {row["code"] for row in wrong_purpose["errors"]})

    def test_framework_inference_cannot_use_rejected_framework(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            framework = bundle["framework_claims"][0]
            framework["status"] = "rejected"
            framework["counterevidence_status"] = "contradicted"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("analysis_framework_not_usable", {row["code"] for row in result["errors"]})

    def test_behavior_cross_check_requires_existing_statement_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["behavior_cross_checks"] = [{
                "cross_check_id": "BC1", "statement_claim_id": "MISSING",
                "behavior_evidence_refs": ["E-HOLDING-1"], "subject_match": "confirmed",
                "temporal_alignment": "aligned", "tenure_alignment": "confirmed",
                "result": "supports", "causality_claimed": False,
            }]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("behavior_statement_missing", {row["code"] for row in result["errors"]})

    def test_behavior_cross_check_requires_disclosed_behavior_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["behavior_cross_checks"] = [{
                "cross_check_id": "BC1", "statement_claim_id": "AC1",
                "behavior_evidence_refs": [], "subject_match": "confirmed",
                "temporal_alignment": "aligned", "tenure_alignment": "confirmed",
                "result": "supports", "causality_claimed": False,
            }]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("behavior_evidence_missing", {row["code"] for row in result["errors"]})

    def test_unknown_provenance_mode_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["analysis_claims"][0]["provenance_mode"] = "invented"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_provenance_mode", {row["code"] for row in result["errors"]})

    def test_public_excerpt_must_be_short(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            long_quote = "x" * 301
            source = root / "source.md"
            source.write_text(long_quote + "\n", encoding="utf-8")
            bundle["source_documents"][0]["content_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            anchor = bundle["quote_anchors"][0]
            anchor["verbatim_text"] = long_quote
            anchor["quote_use"] = "public_excerpt"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("public_excerpt_too_long", {row["code"] for row in result["errors"]})

    def test_public_excerpt_without_redistribution_permission_is_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["quote_anchors"][0]["quote_use"] = "public_excerpt"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn("redistribution_not_confirmed", {row["code"] for row in result["warnings"]})

    def test_source_document_requires_traceable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["source_documents"][0].pop("original_url")

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("source_metadata_missing", {row["code"] for row in result["errors"]})

    def test_as_of_requires_timezone_aware_rfc3339(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["as_of"] = "2026-07-14T00:00:00"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_as_of", {row["code"] for row in result["errors"]})

    def test_cli_emits_json_and_returns_zero_for_valid_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle_path = root / "bundle.json"
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = provenance_guard.main(["--bundle", str(bundle_path)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provenance_readiness"], "verified")

    def test_cli_returns_structured_error_for_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text("{not-json", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = provenance_guard.main(["--bundle", str(bundle_path)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "invalid_bundle_json")
        self.assertTrue(payload["no_order_execution"])

    def test_missing_stable_id_returns_error_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["source_documents"][0].pop("document_id")

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("source_metadata_missing", {row["code"] for row in result["errors"]})
        self.assertEqual(result["memory_link"]["source_document_ids"], [])

    def test_unknown_framework_status_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["framework_claims"][0]["status"] = "looks_good"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_framework_status", {row["code"] for row in result["errors"]})

    def test_unknown_behavior_result_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["behavior_cross_checks"] = [{
                "cross_check_id": "BC1",
                "statement_claim_id": "AC1",
                "behavior_evidence_refs": ["E1"],
                "subject_match": "confirmed",
                "temporal_alignment": "aligned",
                "tenure_alignment": "confirmed",
                "result": "confident",
                "causality_claimed": False,
            }]

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_behavior_result", {row["code"] for row in result["errors"]})

    def test_unknown_decision_use_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["analysis_claims"][0]["decision_use"] = "auto_trade"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_decision_use", {row["code"] for row in result["errors"]})

    def test_unknown_quote_use_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["quote_anchors"][0]["quote_use"] = "full_republication"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_quote_use", {row["code"] for row in result["errors"]})

    def test_source_url_must_be_http_or_https(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["source_documents"][0]["original_url"] = "javascript:alert(1)"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_source_url", {row["code"] for row in result["errors"]})

    def test_unknown_counterevidence_status_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["framework_claims"][0]["counterevidence_status"] = "probably_fine"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_counterevidence_status", {row["code"] for row in result["errors"]})

    def test_cli_rejects_non_object_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.json"
            bundle_path.write_text("[]", encoding="utf-8")
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = provenance_guard.main(["--bundle", str(bundle_path)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "invalid_bundle_shape")

    def test_non_list_section_is_rejected_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["quote_anchors"] = "not-a-list"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("invalid_section_type", {row["code"] for row in result["errors"]})


class PurposeBoundAdmissionFailClosedTests(unittest.TestCase):
    NOW = datetime(2026, 8, 2, tzinfo=UTC)

    def conclusion_bundle(self, root: Path) -> dict:
        bundle = make_covered_bundle(root)
        claim = bundle["analysis_claims"][0]
        claim["decision_use"] = "supports_conclusion"
        coverage = bundle["claim_coverage"]
        coverage["context_only_claim_ids"] = []
        coverage["accepted_claim_ids"] = ["AC1"]
        coverage["canonical_eids"] = ["E1"]
        bundle["bundle_purpose"]["decision_use"] = "supports_conclusion"
        return bundle

    def validate(self, bundle: dict, root: Path) -> dict:
        return provenance_guard.validate_bundle(bundle, base_dir=root, now=self.NOW)

    def assert_blocked(self, result: dict, code: str) -> None:
        self.assertFalse(result["ok"], result)
        self.assertIn(code, {row["code"] for row in result["errors"]})
        self.assertEqual(result["memory_link"]["accepted_claim_ids"], [])
        self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_unknown_bundle_purpose_kind_cannot_grant_materiality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.conclusion_bundle(root)
            bundle["bundle_purpose"]["kind"] = "not_a_registered_purpose"
            result = self.validate(bundle, root)

        self.assert_blocked(result, "bundle_purpose_kind_invalid")

    def test_canonical_eids_must_come_from_accepted_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.conclusion_bundle(root)
            bundle["claim_coverage"]["canonical_eids"] = ["E999"]
            result = self.validate(bundle, root)

        self.assert_blocked(result, "claim_coverage_canonical_eid_unbound")

    def test_semantic_manifest_document_must_reach_supported_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.conclusion_bundle(root)
            second_capture = root / "unrelated.md"
            second_capture.write_text("Unrelated source.\n", encoding="utf-8")
            second = dict(bundle["source_documents"][0])
            second.update({
                "document_id": "DOC2",
                "title": "Unrelated",
                "local_path": "unrelated.md",
                "content_sha256": hashlib.sha256(second_capture.read_bytes()).hexdigest(),
            })
            bundle["source_documents"].append(second)
            bundle["claim_coverage"]["source_document_ids"] = ["DOC2"]
            bundle["source_capture_manifest"] = [{
                "document_id": "DOC2",
                "content_sha256": second["content_sha256"],
                "media_type": "text/markdown",
                "source_role": "semantic",
                "disposition": "reviewed_capture",
                "review_locator": {"line_start": 1, "line_end": 1},
                "supported_claim_ids": ["AC1"],
            }]
            result = self.validate(bundle, root)

        self.assert_blocked(result, "capture_manifest_claim_source_mismatch")

    def test_kol_target_must_name_an_installed_card(self) -> None:
        ledger_path = Path(__file__).resolve().parents[1] / "templates" / "kol-method-cards-public-ledger.json"
        base = json.loads(ledger_path.read_text(encoding="utf-8"))
        cases = (
            ("KMC-NOT-PRESENT", "kol_target_binding_missing"),
            ("KMC-BALDER-20260717", "kol_target_source_binding_mismatch"),
        )
        for target, code in cases:
            with self.subTest(target=target):
                ledger = copy.deepcopy(base)
                ledger["bundle_purpose"]["target_ids"] = [target]
                ledger["claim_coverage"]["target_binding"] = target
                result = provenance_guard.validate_bundle(
                    ledger,
                    base_dir=ledger_path.parent,
                    now=self.NOW,
                )
                self.assert_blocked(result, code)

    def test_private_quote_locator_must_match_attested_review_range(self) -> None:
        ledger_path = Path(__file__).resolve().parents[1] / "templates" / "kol-method-cards-public-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["quote_anchors"][0]["locator"] = {
            "review_index": "NOT-IN-ATTESTED-RANGE",
        }

        result = provenance_guard.validate_bundle(
            ledger,
            base_dir=ledger_path.parent,
            now=self.NOW,
        )

        self.assert_blocked(result, "private_capture_attestation_locator_mismatch")

    def test_mixed_framework_dependency_cannot_be_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.conclusion_bundle(root)
            framework = bundle["framework_claims"][0]
            framework["status"] = "mixed"
            framework["counterevidence_status"] = "mixed"
            framework["counterevidence_refs"] = ["E-CONTRA"]
            result = self.validate(bundle, root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn(
            "claim_coverage_framework_mixed",
            {row["code"] for row in result["warnings"]},
        )
        self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_contradictory_behavior_blocks_accepted_conclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.conclusion_bundle(root)
            bundle["behavior_cross_checks"] = [{
                "cross_check_id": "BC1",
                "statement_claim_id": "AC1",
                "behavior_evidence_refs": ["E2"],
                "subject_match": "confirmed",
                "temporal_alignment": "aligned",
                "tenure_alignment": "confirmed",
                "result": "contradicts",
                "causality_claimed": False,
            }]
            result = self.validate(bundle, root)

        self.assert_blocked(result, "claim_coverage_behavior_conflict")

    def test_purpose_decision_use_must_match_covered_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.conclusion_bundle(root)
            bundle["bundle_purpose"]["decision_use"] = "context_only"
            result = self.validate(bundle, root)

        self.assert_blocked(result, "bundle_purpose_decision_use_mismatch")

if __name__ == "__main__":
    unittest.main()
