#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import provenance_guard  # noqa: E402
from test_provenance_guard import make_valid_bundle  # noqa: E402


class ProvenanceSourceCaptureTests(unittest.TestCase):
    def test_future_bundle_as_of_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["as_of"] = "2999-01-01T00:00:00Z"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertIn("future_as_of", {row["code"] for row in result["errors"]})

    def test_invalid_or_misordered_source_times_are_rejected(self) -> None:
        mutations = {
            "published": ("published_at", "not-a-date", "source_published_at_invalid"),
            "retrieved": ("retrieved_at", "2026-07-13 12:00", "source_retrieved_at_invalid"),
            "published_after_retrieved": (
                "published_at", "2026-07-14T12:00:00Z", "source_time_order_invalid"
            ),
            "retrieved_after_as_of": (
                "retrieved_at", "2026-07-15T00:00:00Z", "source_time_order_invalid"
            ),
        }
        for name, (field, value, expected_code) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                document = bundle["source_documents"][0]
                document.update({"retrieved_at": "2026-07-13T12:00:00Z", "access_state": "public"})
                document[field] = value

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn(expected_code, {row["code"] for row in result["errors"]})

    def test_invalid_and_blocked_access_states_fail_closed(self) -> None:
        for access_state, expected_code in (
            ("mystery", "source_access_state_invalid"),
            ("blocked", "source_access_blocked"),
            ("subscriber_only", "source_access_blocked"),
        ):
            with self.subTest(access_state=access_state), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                bundle["source_documents"][0].update({
                    "retrieved_at": "2026-07-13T12:00:00Z",
                    "access_state": access_state,
                })

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn(expected_code, {row["code"] for row in result["errors"]})

    def test_partial_access_warns_and_caps_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["source_documents"][0].update({
                "retrieved_at": "2026-07-13T12:00:00Z",
                "access_state": "partial",
            })

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "partial")
        self.assertIn("source_access_partial", {row["code"] for row in result["warnings"]})
        self.assertFalse(result["memory_link"]["materiality_eligible"])

    def test_stitched_or_ellipsized_quote_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text("第一句话完整存在。中间还有不能跳过的内容。第二句话完整存在。\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            bundle = {
                "schema_version": "research_provenance.v1",
                "as_of": "2026-07-14T00:00:00Z",
                "source_documents": [{
                    "document_id": "DOC1", "source_type": "primary_interview",
                    "title": "采访", "publisher": "official_site", "published_at": "2026-06-08",
                    "original_url": "https://example.com/interview", "local_path": "source.md",
                    "content_sha256": digest, "license_scope": "public",
                    "redistribution_allowed": "derived_only",
                }],
                "quote_anchors": [{
                    "quote_id": "Q1", "document_id": "DOC1",
                    "verbatim_text": "第一句话完整存在……第二句话完整存在。",
                    "locator": {"line_start": 1, "line_end": 1}, "speaker": "郑希",
                    "quote_use": "internal_evidence",
                }],
                "framework_claims": [], "analysis_claims": [], "behavior_cross_checks": [],
                "no_order_execution": True,
            }

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertEqual(result["provenance_readiness"], "blocked")
        self.assertIn("quote_not_exact", {row["code"] for row in result["errors"]})

    def test_source_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["source_documents"][0]["content_sha256"] = "0" * 64

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("source_hash_mismatch", {row["code"] for row in result["errors"]})

    def test_check_then_read_symlink_swap_cannot_capture_outside_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "bundle"
            root.mkdir()
            source = root / "source.md"
            outside = parent / "outside.md"
            outside_text = "OUTSIDE-RACE-CONTENT must never reach quote validation.\n"
            outside.write_text(outside_text, encoding="utf-8")
            bundle = make_valid_bundle(root)
            resolved_source = source.resolve()
            bundle["source_documents"][0]["content_sha256"] = hashlib.sha256(
                outside.read_bytes()
            ).hexdigest()
            bundle["quote_anchors"][0]["verbatim_text"] = outside_text.strip()

            original_is_file = Path.is_file
            original_open = os.open
            swapped = False

            def swap_source() -> None:
                nonlocal swapped
                if swapped:
                    return
                source.unlink()
                source.symlink_to(outside)
                swapped = True

            def racing_is_file(candidate: Path) -> bool:
                if candidate == resolved_source:
                    swap_source()
                    return True
                return original_is_file(candidate)

            def racing_open(
                path: os.PathLike[str] | str,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if path == "source.md" and dir_fd is not None:
                    swap_source()
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(Path, "is_file", new=racing_is_file),
                mock.patch.object(os, "open", new=racing_open),
                mock.patch.object(provenance_guard, "_secure_open_capability_error", return_value=None),
                mock.patch.object(
                    provenance_guard,
                    "_validate_locator",
                    wraps=provenance_guard._validate_locator,
                ) as locator_spy,
            ):
                result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertTrue(swapped, "the deterministic check/read race did not run")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "blocked")
        self.assertIn("source_path_unsafe", {row["code"] for row in result["errors"]})
        locator_inputs = "\n".join(str(arg) for call in locator_spy.call_args_list for arg in call.args)
        self.assertNotIn(outside_text.strip(), locator_inputs)

    def test_final_component_symlink_is_blocked_without_exposing_outside_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "bundle"
            root.mkdir()
            outside = parent / "outside.md"
            outside_text = "OUTSIDE-FINAL-SYMLINK must never reach quote validation.\n"
            outside.write_text(outside_text, encoding="utf-8")
            bundle = make_valid_bundle(root)
            (root / "source.md").unlink()
            (root / "source.md").symlink_to(outside)
            bundle["source_documents"][0]["content_sha256"] = hashlib.sha256(
                outside.read_bytes()
            ).hexdigest()
            bundle["quote_anchors"][0]["verbatim_text"] = outside_text.strip()

            with mock.patch.object(
                provenance_guard,
                "_validate_locator",
                wraps=provenance_guard._validate_locator,
            ) as locator_spy:
                result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "blocked")
        self.assertIn("source_path_unsafe", {row["code"] for row in result["errors"]})
        locator_inputs = "\n".join(str(arg) for call in locator_spy.call_args_list for arg in call.args)
        self.assertNotIn(outside_text.strip(), locator_inputs)

    def test_intermediate_directory_symlink_is_blocked_without_exposing_outside_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "bundle"
            root.mkdir()
            outside_dir = parent / "outside"
            outside_dir.mkdir()
            outside = outside_dir / "source.md"
            outside_text = "OUTSIDE-DIRECTORY-SYMLINK must never reach quote validation.\n"
            outside.write_text(outside_text, encoding="utf-8")
            bundle = make_valid_bundle(root)
            (root / "linked").symlink_to(outside_dir, target_is_directory=True)
            bundle["source_documents"][0]["local_path"] = "linked/source.md"
            bundle["source_documents"][0]["content_sha256"] = hashlib.sha256(
                outside.read_bytes()
            ).hexdigest()
            bundle["quote_anchors"][0]["verbatim_text"] = outside_text.strip()

            with mock.patch.object(
                provenance_guard,
                "_validate_locator",
                wraps=provenance_guard._validate_locator,
            ) as locator_spy:
                result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["provenance_readiness"], "blocked")
        self.assertIn("source_path_unsafe", {row["code"] for row in result["errors"]})
        locator_inputs = "\n".join(str(arg) for call in locator_spy.call_args_list for arg in call.args)
        self.assertNotIn(outside_text.strip(), locator_inputs)

    def test_unsafe_local_path_forms_are_rejected_before_open(self) -> None:
        unsafe_paths = (
            "/absolute/source.md",
            "C:/absolute/source.md",
            "C:\\absolute\\source.md",
            "",
            ".",
            "./source.md",
            "../source.md",
            "nested/../source.md",
            "nested//source.md",
            "nested/source.md/",
        )
        for local_path in unsafe_paths:
            with self.subTest(local_path=local_path), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bundle = make_valid_bundle(root)
                bundle["source_documents"][0]["local_path"] = local_path

                result = provenance_guard.validate_bundle(bundle, base_dir=root)

            self.assertFalse(result["ok"], result)
            self.assertIn("source_path_unsafe", {row["code"] for row in result["errors"]})

    def test_non_regular_source_capture_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            directory = root / "not-a-file"
            directory.mkdir()
            bundle["source_documents"][0]["local_path"] = "not-a-file"

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertIn("source_path_unsafe", {row["code"] for row in result["errors"]})

    def test_missing_no_follow_capability_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)

            with mock.patch.object(
                provenance_guard,
                "_secure_open_capability_error",
                return_value="O_NOFOLLOW unavailable",
            ):
                result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"], result)
        self.assertIn("source_secure_open_unavailable", {row["code"] for row in result["errors"]})

    def test_quote_locator_must_cover_the_exact_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_valid_bundle(root)
            bundle["quote_anchors"][0]["locator"] = {"line_start": 2, "line_end": 2}

            result = provenance_guard.validate_bundle(bundle, base_dir=root)

        self.assertFalse(result["ok"])
        self.assertIn("quote_locator_mismatch", {row["code"] for row in result["errors"]})


if __name__ == "__main__":
    unittest.main()
