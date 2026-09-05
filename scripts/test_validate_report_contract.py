#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_report  # noqa: E402

KOL_TEST_NOW = datetime(2026, 8, 22, tzinfo=UTC)


class ReportCoverageContractTests(unittest.TestCase):
    def test_numeric_win_rate_requires_shared_denominator_but_bare_phrase_still_passes(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        baseline = (skill_root / "templates" / "report-contract-pass.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("追高胜率下降", baseline)
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_text(baseline, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(validate_report.validate(report), 0)

            report.write_text(
                baseline.replace("追高胜率下降", "胜率 ６７％（n=4，弃权率 60%）"),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(validate_report.validate(report), 0)

            report.write_text(
                baseline.replace("追高胜率下降", "胜率 100%"),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report)
            self.assertIn("evaluated_n", output.getvalue())

            report.write_text(
                baseline.replace("追高胜率下降", "胜率 100%（n=4）"),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report)
            self.assertIn("abstention_rate", output.getvalue())

            report.write_text(
                baseline.replace("追高胜率下降", "胜率 100%（n=4，弃权率 60%）"),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(validate_report.validate(report), 0)

            report.write_text(
                baseline.replace(
                    "追高胜率下降",
                    "win rate 1.0（evaluated_n=4，abstention_rate=0）",
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(validate_report.validate(report), 0)

    def test_numeric_win_stat_patterns_cover_examples_without_matching_bare_words(self) -> None:
        for text in (
            "胜率 67%", "命中率 8/12", "win rate 0.64", "准确率为0.71",
            "win rate 1.0", "胜率 0",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(validate_report.NUMERIC_WIN_STAT_PATTERN.search(text))
        self.assertIsNone(validate_report.NUMERIC_WIN_STAT_PATTERN.search("追高胜率下降"))
        self.assertIsNone(validate_report.NUMERIC_WIN_STAT_PATTERN.search("accuracy improved"))

    def purpose_bound_report_fixture(self, root: Path) -> tuple[Path, Path]:
        skill_root = Path(__file__).resolve().parents[1]
        report_text = (skill_root / "templates" / "report-contract-pass.md").read_text(
            encoding="utf-8"
        )
        report_text = report_text.replace(
            "- stale_after: 2026-06-12 收盘后",
            "- stale_after: 2026-08-31T00:00:00Z",
        ).replace(
            "- review clock: 每周收盘后复盘",
            "- review clock: 2026-08-07T20:00:00Z weekly close review\n"
            "- provenance_target: TSLA\n"
            "- canonical_eids: E1, E2, E3, E4",
        )
        bundle = {
            "schema_version": "research_provenance.v1",
            "as_of": "2026-08-01T00:00:00Z",
            "bundle_purpose": {
                "kind": "report_evidence", "target_ids": ["TSLA"],
                "decision_use": "supports_conclusion",
            },
            "source_documents": [{
                "document_id": "DOC-TSLA", "source_type": "primary_document",
                "title": "TSLA evidence", "publisher": "fixture",
                "published_at": "2026-07-31", "retrieved_at": "2026-08-01T00:00:00Z",
                "original_url": "https://example.com/tsla", "access_state": "public",
                "license_scope": "public", "storage_scope": "private",
                "redistribution_allowed": "derived_only",
            }],
            "quote_anchors": [], "framework_claims": [],
            "analysis_claims": [{
                "claim_id": "AC-TSLA", "statement": "TSLA report evidence remains bounded.",
                "provenance_mode": "source_summary", "source_document_ids": ["DOC-TSLA"],
                "quote_anchor_ids": [], "framework_claim_ids": [],
                "evidence_refs": ["E1", "E2", "E3", "E4"],
                "decision_use": "supports_conclusion",
                "counterevidence_status": "searched_none_found", "counterevidence_refs": [],
                "falsifier": "A source or target mismatch invalidates this claim.",
            }],
            "behavior_cross_checks": [],
            "claim_coverage": {
                "source_document_ids": ["DOC-TSLA"], "framework_claim_ids": [],
                "analysis_claim_ids": ["AC-TSLA"], "accepted_claim_ids": ["AC-TSLA"],
                "watch_only_claim_ids": [], "context_only_claim_ids": [],
                "rejected_claim_ids": [], "canonical_eids": ["E1", "E2", "E3", "E4"],
                "target_binding": "TSLA",
            },
            "no_order_execution": True,
        }
        report = root / "purpose-bound-report.md"
        bundle_path = root / "purpose-bound-provenance.json"
        report.write_text(report_text, encoding="utf-8")
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        return report, bundle_path

    def assert_real_kol_handoff_strict(self, bundle_path: Path) -> dict:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        result = validate_report.provenance_guard.validate_bundle(
            bundle, base_dir=bundle_path.parent
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["memory_link"]["kol_method_card_ids"])
        signal = result["suggested_module_signal"]
        self.assertEqual(signal["module"], "x_frontline")
        self.assertEqual(signal["sub_framework"], "kol_method_card")
        self.assertEqual(signal["max_action_level"], "L0")
        self.assertEqual(signal["position_multiplier"], 0.0)
        self.assertTrue(signal["tighten_only"])
        self.assertTrue(signal["no_order_execution"])
        self.assertIn(signal, result["suggested_module_signals"])
        return result

    def real_kol_safe_report_text(self) -> str:
        skill_root = Path(__file__).resolve().parents[1]
        report_text = (skill_root / "templates" / "report-contract-pass.md").read_text(
            encoding="utf-8"
        )
        report_text = report_text.replace(
            "- stale_after: 2026-06-12 收盘后",
            "- stale_after: 2026-08-31T00:00:00Z",
        )
        report_text = report_text.replace(
            "TSLA：行动等级 L1 试错/观察；新钱不追；若已持有，保留核心但不加。",
            "TSLA：行动等级 L0 仅研究观察；新钱不建仓；若已持有，不因 KOL 证据加仓。",
        )
        report_text = report_text.replace(
            "- 低吸：只有 E1/E2 继续确认且 E3 不恶化，才允许小仓试错。",
            "- 低吸：不执行；仅在来源刷新后重新研究。",
        )
        report_text = report_text.replace(
            "- 冲突处理：基本面偏多 vs 宏观偏弱，按 Decision Compiler 降到 L1。",
            "- 冲突处理：KOL 证据只作研究 handoff，按 Decision Compiler 降到 L0。",
        )
        report_text = report_text.replace(
            "- review clock: 每周收盘后复盘",
            "- review clock: 每周收盘后复盘\n"
            "- kol_method_card_id: KMC-BALDER-20260731-FINAL",
        )
        return report_text.replace(
            "Decision Compiler → L1 / hard_veto=false / final_position_multiplier=0.5",
            "Decision Compiler → L0 / hard_veto=false / final_position_multiplier=0.0",
        )

    def assert_real_kol_report_rejected(
        self,
        report_text: str,
        *,
        expected_message: str = "KOL",
    ) -> str:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "kol-adversarial-report.md"
            report.write_text(report_text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)
        rendered = output.getvalue()
        self.assertIn(expected_message, rendered)
        return rendered

    def assert_real_kol_report_accepted(self, report_text: str) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "kol-compatible-report.md"
            report.write_text(report_text, encoding="utf-8")
            output = io.StringIO()
            try:
                with contextlib.redirect_stdout(output):
                    result = validate_report.validate(report, bundle, now=KOL_TEST_NOW)
            except SystemExit:
                self.fail(f"expected KOL report to pass, got: {output.getvalue()}")
        self.assertEqual(result, 0)
        self.assertIn("OK:", output.getvalue())

    def test_real_kol_ledger_rejects_l1_positive_client_report(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        report_text = self.real_kol_safe_report_text().replace(
            "Decision Compiler → L0", "Decision Compiler → L1"
        )
        output = io.StringIO()
        self.assert_real_kol_handoff_strict(bundle)

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "kol-l1-positive-report.md"
            report.write_text(report_text, encoding="utf-8")
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertIn("KOL", output.getvalue())

    def test_real_kol_ledger_rejects_positive_client_multiplier_at_l0(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        report_text = self.real_kol_safe_report_text().replace(
            "final_position_multiplier=0.0", "final_position_multiplier=0.5"
        )
        output = io.StringIO()
        self.assert_real_kol_handoff_strict(bundle)

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "kol-l0-positive-multiplier-report.md"
            report.write_text(report_text, encoding="utf-8")
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertIn("final_position_multiplier=0.0", output.getvalue())

    def test_real_kol_ledger_accepts_l0_zero_client_report(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        report_text = self.real_kol_safe_report_text()
        self.assert_real_kol_handoff_strict(bundle)

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "kol-l0-report.md"
            report.write_text(report_text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertEqual(result, 0)
        self.assertIn("OK:", output.getvalue())

    def test_real_kol_ledger_accepts_explicit_installed_legacy_card_at_l0(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle_path = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(
            bundle["claim_coverage"]["target_binding"],
            "KMC-BALDER-20260731-FINAL",
        )
        for card_id in (
            "KMC-BALDER-20260717",
            "KMC-CITRINI-20260717",
            "KMC-FRANK-20260717",
        ):
            with self.subTest(card_id=card_id):
                report_text = self.real_kol_safe_report_text().replace(
                    "KMC-BALDER-20260731-FINAL",
                    card_id,
                )
                self.assert_real_kol_report_accepted(report_text)

    def test_real_kol_ledger_rejects_missing_or_unknown_card_selection(self) -> None:
        base = self.real_kol_safe_report_text()
        mutations = {
            "missing": (
                base.replace("\n- kol_method_card_id: KMC-BALDER-20260731-FINAL", ""),
                "report_kol_method_card_id_missing",
            ),
            "duplicated": (
                base.replace(
                    "kol_method_card_id: KMC-BALDER-20260731-FINAL",
                    "kol_method_card_id: KMC-BALDER-20260731-FINAL\n"
                    "- kol_method_card_id: KMC-BALDER-20260731-FINAL",
                ),
                "report_kol_method_card_id_missing",
            ),
            "blank_then_valid": (
                base.replace(
                    "kol_method_card_id: KMC-BALDER-20260731-FINAL",
                    "kol_method_card_id: \n"
                    "- kol_method_card_id: KMC-BALDER-20260731-FINAL",
                ),
                "report_kol_method_card_id_missing",
            ),
            "unknown": (
                base.replace("KMC-BALDER-20260731-FINAL", "KMC-NOT-IN-LEDGER"),
                "report_kol_method_card_id_unknown",
            ),
        }
        for name, (report_text, expected) in mutations.items():
            with self.subTest(name=name):
                self.assert_real_kol_report_rejected(
                    report_text,
                    expected_message=expected,
                )

    def test_real_kol_legacy_compatibility_requires_nonmaterial_safe_signal(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle_path = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        result = validate_report.provenance_guard.validate_bundle(
            bundle,
            base_dir=bundle_path.parent,
        )
        self.assertTrue(result["ok"], result)
        result["memory_link"]["materiality_eligible"] = True
        report_text = self.real_kol_safe_report_text().replace(
            "KMC-BALDER-20260731-FINAL",
            "KMC-BALDER-20260717",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            validate_report._validate_provenance_binding(
                report_text,
                0,
                set(),
                result,
                bundle,
            )
        self.assertIn("report_kol_registry_safety_invalid", output.getvalue())

    def test_real_kol_ledger_rejects_conflicting_action_declarations(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        base = self.real_kol_safe_report_text()
        mutations = {
            "compiler_result": base.replace("Decision Compiler → L0", "Decision Compiler → L1"),
            "compiled_action": base.replace(
                "- Decision Compiler → L0",
                "- Decision Compiler → L0\n- compiled_action: L1",
            ),
        }
        for name, report_text in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / f"kol-conflicting-{name}.md"
                report.write_text(report_text, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=KOL_TEST_NOW)
            self.assertIn("KOL", output.getvalue())

    def test_real_kol_ledger_requires_single_compiler_result_declaration(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        base = self.real_kol_safe_report_text()
        mutations = {
            "missing": base.replace("Decision Compiler → L0", "Compiler result omitted"),
            "duplicated": base.replace(
                "Decision Compiler → L0 / hard_veto=false",
                "Decision Compiler → L0 / Decision Compiler → L0 / hard_veto=false",
            ),
        }
        for name, report_text in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / f"kol-{name}-compiler-result.md"
                report.write_text(report_text, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=KOL_TEST_NOW)
            self.assertIn("Decision Compiler", output.getvalue())

    def test_real_kol_ledger_requires_exactly_one_zero_multiplier(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        base = self.real_kol_safe_report_text()
        mutations = {
            "missing": base.replace(" / final_position_multiplier=0.0", ""),
            "duplicated": base.replace(
                "final_position_multiplier=0.0",
                "final_position_multiplier=0.0 / final_position_multiplier=0.0",
            ),
        }
        for name, report_text in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / f"kol-{name}-multiplier.md"
                report.write_text(report_text, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=KOL_TEST_NOW)
            self.assertIn("final_position_multiplier", output.getvalue())

    def test_real_kol_ledger_rejects_unnegated_positive_position_language(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        bundle = skill_root / "templates" / "kol-method-cards-public-ledger.json"
        base = self.real_kol_safe_report_text()
        for phrase in (
            "允许小仓试错",
            "允许建仓",
            "允许开仓",
            "允许试仓",
            "允许加仓",
            "允许增持",
            "允许建立仓位",
            "允许建立小仓位",
            "允许建立头寸",
        ):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as tmp:
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}；其余条件不变。",
                )
                report = Path(tmp) / "kol-positive-position-language.md"
                report.write_text(report_text, encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=KOL_TEST_NOW)
            self.assertIn("KOL", output.getvalue())

    def test_real_kol_ledger_rejects_buy_and_establish_small_position_bypass(self) -> None:
        report_text = self.real_kol_safe_report_text().replace(
            "- 低吸：不执行；仅在来源刷新后重新研究。",
            "- 低吸：可以买入并建立小仓位。",
        )
        self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_small_position_trial_bypass(self) -> None:
        report_text = self.real_kol_safe_report_text().replace(
            "- 低吸：不执行；仅在来源刷新后重新研究。",
            "- 低吸：可小仓试错。",
        )
        self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_action_level_alias_bypass(self) -> None:
        report_text = self.real_kol_safe_report_text().replace(
            "- Decision Compiler → L0",
            "- action_level: L1\n- Decision Compiler → L0",
        )
        self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_chinese_action_suggestion_alias_bypass(self) -> None:
        report_text = self.real_kol_safe_report_text().replace(
            "- 低吸：不执行；仅在来源刷新后重新研究。",
            "- 动作建议：L1 小仓试错。",
        )
        self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_second_compiler_result_alias_bypass(self) -> None:
        report_text = self.real_kol_safe_report_text().replace(
            "Decision Compiler → L0 / hard_veto=false",
            "Decision Compiler → L0 / Decision Compiler result: L1 / hard_veto=false",
        )
        self.assert_real_kol_report_rejected(
            report_text,
            expected_message="Decision Compiler",
        )

    def test_real_kol_ledger_rejects_all_documented_action_label_aliases(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "max_action_level: L1",
            "当前动作：L1",
            "建议动作：L1",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "- Decision Compiler → L0",
                    f"- {declaration}\n- Decision Compiler → L0",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_counts_all_compiler_result_aliases(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "Decision Compiler result: L1",
            "Decision Compiler 结果：L1",
            "Decision Compiler 最终 result: L1",
            "Decision Compiler 最终结果：L1",
            "Decision Compiler 最终 -> L1",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "Decision Compiler → L0 / hard_veto=false",
                    f"Decision Compiler → L0 / {declaration} / hard_veto=false",
                )
                self.assert_real_kol_report_rejected(
                    report_text,
                    expected_message="Decision Compiler",
                )

    def test_real_kol_ledger_rejects_documented_english_position_actions(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "buy",
            "open a position",
            "initiate a position",
            "add to position",
            "increase position",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}.",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_allows_clearly_negated_position_language(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "不可以买入或建立小仓位",
            "不可小仓试错",
            "禁止加仓",
            "不得开仓",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}。",
                )
                self.assert_real_kol_report_accepted(report_text)

    def test_real_kol_ledger_allows_attributed_evidence_position_quotes(self) -> None:
        base = self.real_kol_safe_report_text()
        for attributed_evidence in (
            "原作者公开表示“允许建仓”",
            "来源原文写道“可以买入”",
            "原作者曾说“可小仓试错”",
        ):
            with self.subTest(attributed_evidence=attributed_evidence):
                report_text = base.replace(
                    "- [E1] LongBridge quote 显示价格仍在关键均线上方，趋势未破。",
                    f"- [E1] {attributed_evidence}；此处仅记录来源主张，不是客户行动建议。",
                )
                self.assert_real_kol_report_accepted(report_text)

    def test_real_kol_ledger_rejects_positive_language_in_client_sections(self) -> None:
        base = self.real_kol_safe_report_text()
        mutations = {
            "core_conclusion": base.replace(
                "TSLA：行动等级 L0 仅研究观察；新钱不建仓；若已持有，不因 KOL 证据加仓。",
                "TSLA：行动等级 L0 仅研究观察；新钱允许建仓；若已持有，不因 KOL 证据加仓。",
            ),
            "action_line": base.replace(
                "- 低吸：不执行；仅在来源刷新后重新研究。",
                "- 低吸：允许建仓。",
            ),
            "action_plan": base.replace(
                "行动线：", "行动计划："
            ).replace(
                "- 低吸：不执行；仅在来源刷新后重新研究。",
                "- 低吸：可以买入。",
            ),
            "empty_position": base.replace(
                "- 空仓：不追，等回踩关键均线后再评估。",
                "- 若空仓：可小仓试错。",
            ),
            "compiler_output": base.replace(
                "Decision Compiler → L0 / hard_veto=false",
                "Decision Compiler → L0 / 可小仓试错 / hard_veto=false",
            ),
            "unattributed_evidence": base.replace(
                "- [E1] LongBridge quote 显示价格仍在关键均线上方，趋势未破。",
                "- [E1] 允许建仓。",
            ),
            "attributed_evidence_with_client_instruction": base.replace(
                "- [E1] LongBridge quote 显示价格仍在关键均线上方，趋势未破。",
                "- [E1] 来源原文写道“仅研究”；因此允许建仓。",
            ),
        }
        for name, report_text in mutations.items():
            with self.subTest(name=name):
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_double_negation_positive_actions(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "不禁止建仓",
            "不反对买入",
            "不是不能开仓",
            "未禁止加仓",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}。",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_allows_coordinated_negation_lists(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "不得买入、建仓、开仓或加仓",
            "不得买入，建仓，开仓或加仓",
            "仅研究，不得买入、建仓、开仓或加仓",
            "do not buy or open/add to a position",
            "do not buy, open, or add to a position",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}。",
                )
                self.assert_real_kol_report_accepted(report_text)

    def test_real_kol_ledger_rejects_negation_reset_clauses(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "不可追高但可小仓试错",
            "不得建仓，不过可以加仓",
            "do not buy, but open a position",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}。",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_extended_action_label_aliases(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "行动建议：L1",
            "最终动作：L1",
            "最终行动：L1",
            "当前行动：L1",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "- Decision Compiler → L0",
                    f"- {declaration}\n- Decision Compiler → L0",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_allows_safe_extended_action_label_aliases(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "行动建议：L0",
            "最终动作：L0",
            "最终行动：L0",
            "当前行动：L0",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "- Decision Compiler → L0",
                    f"- {declaration}\n- Decision Compiler → L0",
                )
                self.assert_real_kol_report_accepted(report_text)

    def test_real_kol_ledger_rejects_unsafe_entry_and_holding_axes(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "entry_permission=TEST",
            "entry_permission: BUILD",
            "entry_permission：ADD",
            "holding_directive=EXIT",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "- Decision Compiler → L0",
                    f"- {declaration}\n- Decision Compiler → L0",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_allows_safe_entry_and_holding_axes(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "entry_permission=WATCH / holding_directive=HOLD",
            "entry_permission=BLOCK / holding_directive=HOLD",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "- Decision Compiler → L0",
                    f"- {declaration}\n- Decision Compiler → L0",
                )
                self.assert_real_kol_report_accepted(report_text)

    def test_real_kol_ledger_counts_extended_compiler_result_aliases(self) -> None:
        base = self.real_kol_safe_report_text()
        for declaration in (
            "Decision Compiler final: L1",
            "Decision Compiler final result: L1",
            "Decision Compiler (final): L1",
            "Decision Compiler（最终）：L1",
            "Decision Compiler verdict: L1",
            "Decision Compiler output: L1",
            "Decision Compiler 结论：L1",
            "Decision Compiler 判定：L1",
        ):
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "Decision Compiler → L0 / hard_veto=false",
                    f"Decision Compiler → L0 / {declaration} / hard_veto=false",
                )
                self.assert_real_kol_report_rejected(
                    report_text,
                    expected_message="Decision Compiler",
                )

    def test_real_kol_ledger_rejects_english_exposure_share_holding_actions(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "open exposure",
            "initiate exposure",
            "add exposure",
            "increase exposure",
            "add shares",
            "increase holdings",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。",
                    f"- 低吸：{phrase}.",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_keeps_structured_aliases_global_inside_evidence(self) -> None:
        base = self.real_kol_safe_report_text()
        for attributed_evidence in (
            "来源原文写道“行动建议：L1”",
            "原作者公开表示“entry_permission=ADD”",
            "来源原文写道“Decision Compiler verdict: L1”",
        ):
            with self.subTest(attributed_evidence=attributed_evidence):
                report_text = base.replace(
                    "- [E1] LongBridge quote 显示价格仍在关键均线上方，趋势未破。",
                    f"- [E1] {attributed_evidence}。",
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_rejects_normalized_declaration_alias_matrix(self) -> None:
        base = self.real_kol_safe_report_text()
        declarations = (
            "Decision Compiler (final) -> L1", "Decision Compiler（最终）→ L1",
            "Decision Compiler final -> L1", "Decision Compiler final result -> L1",
            "Decision Compiler verdict -> L1", "Decision Compiler output -> L1",
            "Decision Compiler 结论 -> L1", "Decision Compiler 判定 → L1",
            "行动建议 -> L1", "最终动作 → L1", "Action Level: L1",
            "Max Action Level: L1", "Compiled Action: L1", "action-level: L1",
            "compiled-action: L1", "entry_permission -> ADD", "entry permission: ADD",
            "entry-permission: ADD", "holding_directive -> EXIT",
            "holding directive: EXIT", "holding-directive: EXIT",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                report_text = base.replace(
                    "- Decision Compiler → L0", f"- {declaration}\n- Decision Compiler → L0"
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_bounds_attribution_to_source_action_spans(self) -> None:
        base = self.real_kol_safe_report_text()
        evidence_line = "- [E1] LongBridge quote 显示价格仍在关键均线上方，趋势未破。"
        for text in (
            "原作者表示允许建仓。", "原作者表示“允许建仓，也可以买入”。",
            "原作者表示允许建仓并建议加仓；此处仅记录来源主张。",
            "According to the author, he said buy and increase holdings; source evidence only.",
        ):
            with self.subTest(safe_attribution=text):
                self.assert_real_kol_report_accepted(base.replace(evidence_line, f"- [E1] {text}"))
        for text, expected_action in (
            ("原作者表示允许建仓，因此本报告建议可以买入。", "买入"),
            ("原作者表示允许建仓因此本报告建议可以买入。", "买入"),
            ("According to the author, he said buy; therefore this report recommends increasing holdings.", "increasing holdings"),
            ("According to the author, he said buy; therefore we recommend buying.", "buying"),
        ):
            with self.subTest(appended_client_action=text):
                output = self.assert_real_kol_report_rejected(
                    base.replace(evidence_line, f"- [E1] {text}")
                )
                self.assertIn(expected_action, output)

    def test_real_kol_ledger_rejects_english_double_negation_actions(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "cannot not buy", "not prohibited to buy",
            "not forbidden to open a position", "not opposed to increase holdings",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。", f"- 低吸：{phrase}."
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_real_kol_ledger_allows_conservative_data_gap_position_language(self) -> None:
        base = self.real_kol_safe_report_text()
        gap_line = "- X/Grok 未发现可复核新增一线信号，只作线索，不提高仓位。"
        for phrase in (
            "没有足够证据支持建仓。", "尚无证据支持买入。", "无法支持开仓。",
            "未发现可建仓依据。", "无依据允许加仓。",
        ):
            with self.subTest(phrase=phrase):
                self.assert_real_kol_report_accepted(base.replace(gap_line, f"- {phrase}"))

    def test_real_kol_ledger_does_not_blanket_exempt_data_gap_actions(self) -> None:
        base = self.real_kol_safe_report_text()
        gap_line = "- X/Grok 未发现可复核新增一线信号，只作线索，不提高仓位。"
        cases = {
            "structured_alias": "entry permission: ADD",
            "double_negative": "不是没有证据支持建仓。",
            "direct_positive": "数据不足，但仍可建仓。",
        }
        for name, phrase in cases.items():
            with self.subTest(name=name):
                self.assert_real_kol_report_rejected(base.replace(gap_line, f"- {phrase}"))
        output = self.assert_real_kol_report_rejected(base.replace(
            gap_line, "- 没有足够证据支持建仓，因此本报告建议可以买入。"
        ))
        self.assertIn("买入", output)

    def test_real_kol_ledger_rejects_trade_and_share_count_actions(self) -> None:
        base = self.real_kol_safe_report_text()
        for phrase in (
            "open a trade", "initiate a trade", "add to the trade", "increase share count",
            "buying", "opening a position", "initiating a trade", "adding to the trade",
            "increasing holdings", "increasing share count",
        ):
            with self.subTest(phrase=phrase):
                report_text = base.replace(
                    "- 低吸：不执行；仅在来源刷新后重新研究。", f"- 低吸：{phrase}."
                )
                self.assert_real_kol_report_rejected(report_text)

    def test_non_kol_partial_report_preserves_l1_one_multiplier(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        report_text = (skill_root / "templates" / "report-contract-pass.md").read_text(
            encoding="utf-8"
        ).replace("final_position_multiplier=0.5", "final_position_multiplier=1.0")
        bundle = {
            "schema_version": "research_provenance.v1",
            "as_of": "2026-07-14T00:00:00Z",
            "source_documents": [{
                "document_id": "DOC1", "source_type": "primary_document", "title": "Source",
                "publisher": "Publisher", "published_at": "2026-07-14",
                "original_url": "https://example.com/source",
            }],
            "quote_anchors": [], "framework_claims": [], "analysis_claims": [],
            "behavior_cross_checks": [], "no_order_execution": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "non-kol-partial-report.md"
            bundle_path = root / "non-kol-partial-provenance.json"
            report.write_text(report_text, encoding="utf-8")
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            guard_result = validate_report.provenance_guard.validate_bundle(bundle, base_dir=root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = validate_report.validate(report, bundle_path)

        self.assertTrue(guard_result["ok"], guard_result)
        self.assertEqual(guard_result["provenance_readiness"], "partial")
        self.assertEqual(guard_result["memory_link"]["kol_method_card_ids"], [])
        self.assertEqual(guard_result["suggested_module_signal"]["max_action_level"], "L1")
        self.assertEqual(guard_result["suggested_module_signal"]["position_multiplier"], 1.0)
        self.assertEqual(
            guard_result["suggested_module_signals"],
            [guard_result["suggested_module_signal"]],
        )
        self.assertEqual(result, 0)
        self.assertIn("OK:", output.getvalue())

    def test_non_kol_verified_bundle_remains_client_report_compatible(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        report = skill_root / "templates" / "report-contract-pass.md"
        bundle_path = skill_root / "templates" / "research-provenance-bundle-pass.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        guard_result = validate_report.provenance_guard.validate_bundle(
            bundle, base_dir=bundle_path.parent
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            result = validate_report.validate(report, bundle_path)

        self.assertTrue(guard_result["ok"], guard_result)
        self.assertEqual(guard_result["provenance_readiness"], "verified")
        self.assertEqual(guard_result["memory_link"]["kol_method_card_ids"], [])
        self.assertIsNone(guard_result["suggested_module_signal"])
        self.assertEqual(guard_result["suggested_module_signals"], [])
        self.assertEqual(result, 0)
        self.assertIn("OK:", output.getvalue())

    def test_purpose_bound_l1_report_has_fresh_target_and_eid_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report, bundle = self.purpose_bound_report_fixture(Path(tmp))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = validate_report.validate(
                    report,
                    bundle,
                    now=datetime(2026, 8, 2, tzinfo=UTC),
                )

        self.assertEqual(result, 0)
        self.assertIn("OK:", output.getvalue())

    def test_purpose_bound_report_rejects_blank_freshness_values(self) -> None:
        mutations = {
            "stale_after": ("stale_after: 2026-08-31T00:00:00Z", "stale_after: ", "report_freshness_stale_after_missing"),
            "must_refresh_if": (
                "must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现",
                "must_refresh_if: ",
                "report_freshness_trigger_missing",
            ),
            "review_clock": (
                "review clock: 2026-08-07T20:00:00Z weekly close review",
                "review clock: ",
                "report_review_clock_missing",
            ),
        }
        for name, (old, new, expected) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report, bundle = self.purpose_bound_report_fixture(root)
                report.write_text(report.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=datetime(2026, 8, 2, tzinfo=UTC))
            self.assertIn(expected, output.getvalue())

    def test_purpose_bound_report_requires_single_freshness_declarations(self) -> None:
        mutations = {
            "stale_after_identical": (
                "stale_after: 2026-08-31T00:00:00Z",
                "stale_after: 2026-08-31T00:00:00Z\n"
                "- stale_after: 2026-08-31T00:00:00Z",
                "report_freshness_stale_after_declaration_count",
            ),
            "stale_after_conflicting": (
                "stale_after: 2026-08-31T00:00:00Z",
                "stale_after: 2026-08-31T00:00:00Z\n"
                "- stale_after: 2026-08-01T00:00:00Z",
                "report_freshness_stale_after_declaration_count",
            ),
            "must_refresh_if_identical": (
                "must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现",
                "must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现\n"
                "- must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现",
                "report_freshness_trigger_declaration_count",
            ),
            "must_refresh_if_conflicting": (
                "must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现",
                "must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现\n"
                "- must_refresh_if: none",
                "report_freshness_trigger_declaration_count",
            ),
            "review_clock_identical": (
                "review clock: 2026-08-07T20:00:00Z weekly close review",
                "review clock: 2026-08-07T20:00:00Z weekly close review\n"
                "- review clock: 2026-08-07T20:00:00Z weekly close review",
                "report_review_clock_declaration_count",
            ),
            "review_clock_conflicting": (
                "review clock: 2026-08-07T20:00:00Z weekly close review",
                "review clock: 2026-08-07T20:00:00Z weekly close review\n"
                "- review clock: none",
                "report_review_clock_declaration_count",
            ),
        }
        for name, (old, new, expected) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report, bundle = self.purpose_bound_report_fixture(root)
                report.write_text(
                    report.read_text(encoding="utf-8").replace(old, new),
                    encoding="utf-8",
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=KOL_TEST_NOW)
            self.assertIn(expected, output.getvalue())

    def test_purpose_bound_report_rejects_expired_or_unparseable_freshness(self) -> None:
        for value, expected in (
            ("2026-08-01T23:59:59Z", "report_freshness_expired"),
            ("after the next event maybe", "report_freshness_stale_after_invalid"),
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report, bundle = self.purpose_bound_report_fixture(root)
                report.write_text(
                    report.read_text(encoding="utf-8").replace("2026-08-31T00:00:00Z", value),
                    encoding="utf-8",
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=datetime(2026, 8, 2, tzinfo=UTC))
            self.assertIn(expected, output.getvalue())

    def test_purpose_bound_report_rejects_target_or_canonical_eid_mismatch(self) -> None:
        mutations = {
            "target": ("provenance_target: TSLA", "provenance_target: NVDA", "report_provenance_target_mismatch"),
            "eids": ("canonical_eids: E1, E2, E3, E4", "canonical_eids: E1, E2, E3", "report_provenance_eid_mismatch"),
        }
        for name, (old, new, expected) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report, bundle = self.purpose_bound_report_fixture(root)
                report.write_text(report.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                    validate_report.validate(report, bundle, now=datetime(2026, 8, 2, tzinfo=UTC))
            self.assertIn(expected, output.getvalue())

    def test_purpose_bound_report_accepts_date_only_and_rfc3339_stale_after(self) -> None:
        for stale_after in ("2026-08-31", "2026-08-31T12:30:00+08:00"):
            with self.subTest(stale_after=stale_after), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                report, bundle = self.purpose_bound_report_fixture(root)
                report.write_text(
                    report.read_text(encoding="utf-8").replace("2026-08-31T00:00:00Z", stale_after),
                    encoding="utf-8",
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = validate_report.validate(report, bundle, now=datetime(2026, 8, 2, tzinfo=UTC))
            self.assertEqual(result, 0)

    def test_purpose_bound_report_rejects_forged_canonical_eid_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, bundle_path = self.purpose_bound_report_fixture(root)
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            bundle["claim_coverage"]["canonical_eids"] = ["E999"]
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            report.write_text(
                report.read_text(encoding="utf-8").replace(
                    "canonical_eids: E1, E2, E3, E4",
                    "canonical_eids: E999",
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle_path, now=KOL_TEST_NOW)

        self.assertIn("claim_coverage_canonical_eid_unbound", output.getvalue())

    def test_canonical_eid_declaration_cannot_substitute_for_evidence_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, bundle = self.purpose_bound_report_fixture(root)
            text = report.read_text(encoding="utf-8")
            for index in range(1, 5):
                text = text.replace(f"E{index}", f"REF{index}")
            text = text.replace(
                "canonical_eids: REF1, REF2, REF3, REF4",
                "canonical_eids: E1, E2, E3, E4",
            )
            text += "\n非证据备注：E1 E2 E3 E4。\n"
            report.write_text(text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertIn("report_evidence_canonical_eid_missing", output.getvalue())

    def test_conflicting_action_declarations_cannot_hide_behind_first_l0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, bundle = self.purpose_bound_report_fixture(root)
            text = report.read_text(encoding="utf-8").replace(
                "TSLA：行动等级 L1 试错/观察；新钱不追；若已持有，保留核心但不加。",
                "TSLA：行动等级 L0 仅观察；另一个决策段行动等级 L3 建仓。",
            )
            text = "\n".join(
                line for line in text.splitlines()
                if "provenance_target:" not in line and "canonical_eids:" not in line
            ) + "\n"
            report.write_text(text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertIn("report_action_declaration_conflict", output.getvalue())

    def test_report_primary_target_must_match_provenance_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, bundle_path = self.purpose_bound_report_fixture(root)
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            bundle["bundle_purpose"]["target_ids"] = ["NVDA"]
            bundle["claim_coverage"]["target_binding"] = "NVDA"
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            report.write_text(
                report.read_text(encoding="utf-8").replace(
                    "provenance_target: TSLA",
                    "provenance_target: NVDA",
                ) + "\n## NVDA auxiliary note\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle_path, now=KOL_TEST_NOW)

        self.assertIn("report_primary_target_conflict", output.getvalue())

    def test_purpose_bound_stale_after_rejects_arbitrary_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, bundle = self.purpose_bound_report_fixture(root)
            report.write_text(
                report.read_text(encoding="utf-8").replace(
                    "stale_after: 2026-08-31T00:00:00Z",
                    "stale_after: 2026-08-31 arbitrary-unparseable-suffix",
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertIn("report_freshness_stale_after_invalid", output.getvalue())

    def test_purpose_bound_refresh_trigger_must_be_observable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report, bundle = self.purpose_bound_report_fixture(root)
            report.write_text(
                report.read_text(encoding="utf-8").replace(
                    "must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现",
                    "must_refresh_if: banana 1",
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle, now=KOL_TEST_NOW)

        self.assertIn("report_freshness_trigger_invalid", output.getvalue())

    def assert_kol_handoff_rejected(self, *, level: str, multiplier: float) -> str:
        skill_root = Path(__file__).resolve().parents[1]
        report = skill_root / "templates" / "report-contract-pass.md"
        signal = {
            "module": "x_frontline",
            "sub_framework": "kol_method_card",
            "max_action_level": level,
            "position_multiplier": multiplier,
            "hard_veto": False,
            "tighten_only": True,
            "no_order_execution": True,
        }
        guard_result = {
            "ok": True,
            "provenance_readiness": "verified",
            "errors": [],
            "warnings": [],
            "memory_link": {"kol_method_card_ids": ["KMC-UNSAFE"]},
            "suggested_module_signal": signal,
            "suggested_module_signals": [signal],
            "no_order_execution": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "kol-provenance.json"
            bundle_path.write_text("{}", encoding="utf-8")
            output = io.StringIO()
            with (
                mock.patch.object(
                    validate_report.provenance_guard,
                    "validate_bundle",
                    return_value=guard_result,
                ),
                contextlib.redirect_stdout(output),
                self.assertRaises(SystemExit),
            ):
                validate_report.validate(report, bundle_path)
        return output.getvalue()

    def test_report_rejects_l1_kol_handoff(self) -> None:
        output = self.assert_kol_handoff_rejected(level="L1", multiplier=0.0)
        self.assertIn("KOL handoff", output)

    def test_report_rejects_positive_multiplier_kol_handoff(self) -> None:
        output = self.assert_kol_handoff_rejected(level="L0", multiplier=0.25)
        self.assertIn("KOL handoff", output)

    def test_report_without_coverage_or_watch_contract_fails(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "templates" / "report-contract-pass.md"
        text = fixture.read_text(encoding="utf-8")
        text = text.replace("intelligence coverage: 1.0；critical gaps=0；", "")
        text = text.replace("research watch triggers: none；", "")
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "missing-coverage-watch.md"
            report.write_text(text, encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report)
        self.assertIn("intelligence coverage", output.getvalue())

    def test_report_rejects_blocked_provenance_bundle(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        report = skill_root / "templates" / "report-contract-pass.md"
        bundle = json.loads((skill_root / "templates" / "research-provenance-bundle-pass.json").read_text(encoding="utf-8"))
        bundle["no_order_execution"] = False
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "blocked-provenance.json"
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle_path)
        self.assertIn("provenance", output.getvalue())

    def test_partial_provenance_caps_report_at_l1(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        report_text = (skill_root / "templates" / "report-contract-pass.md").read_text(encoding="utf-8").replace("L1", "L2")
        bundle = {
            "schema_version": "research_provenance.v1",
            "as_of": "2026-07-14T00:00:00Z",
            "source_documents": [{
                "document_id": "DOC1", "source_type": "primary_document", "title": "Source",
                "publisher": "Publisher", "published_at": "2026-07-14",
                "original_url": "https://example.com/source",
            }],
            "quote_anchors": [], "framework_claims": [], "analysis_claims": [],
            "behavior_cross_checks": [], "no_order_execution": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            bundle_path = Path(tmp) / "partial-provenance.json"
            report.write_text(report_text, encoding="utf-8")
            bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                validate_report.validate(report, bundle_path)
        self.assertIn("partial", output.getvalue())


if __name__ == "__main__":
    unittest.main()
