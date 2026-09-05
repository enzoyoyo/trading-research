#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_skill  # noqa: E402


class OpenStockGuardTests(unittest.TestCase):
    def test_clean_room_marker_must_be_inside_openstock_section(self) -> None:
        text = """## OpenStock
- project: Open-Dev-Society/OpenStock
- commit: 4597c9a668118844b588f95eddb9342eed31c41d
- license: AGPL-3.0

## MiroFish
- methodology_only_no_code_copied=true
"""
        errors = validate_skill.openstock_clean_room_errors(text)
        self.assertIn("OpenStock section missing methodology_only_no_code_copied=true", errors)

    def test_complete_openstock_section_passes_scoped_guard(self) -> None:
        text = """## OpenStock
- project: Open-Dev-Society/OpenStock
- commit: 4597c9a668118844b588f95eddb9342eed31c41d
- license: AGPL-3.0
- methodology_only_no_code_copied=true
"""
        self.assertEqual(validate_skill.openstock_clean_room_errors(text), [])


class MulticaCollaborationContractTests(unittest.TestCase):
    COMPLETE = """## Multica 可选单向协作
- `standalone_default=true`
- `fable_5=one_max_compact_context_framework_pass;tools=none;files=none;code=none;retry=none`
- `grok=web_x_source_ledger_only`
- `gemini=long_context_index_only_when_needed`
- `sol_ultra=sole_corrector_writer_validator`
- `downstream_return_to_fable=false`
"""

    def test_complete_one_way_contract_passes(self) -> None:
        self.assertEqual(validate_skill.multica_collaboration_contract_errors(self.COMPLETE), [])

    def test_contract_fails_without_standalone_or_no_return_markers(self) -> None:
        broken = self.COMPLETE.replace("standalone_default=true", "standalone_default=false").replace(
            "downstream_return_to_fable=false", "downstream_return_to_fable=true"
        )
        errors = validate_skill.multica_collaboration_contract_errors(broken)
        self.assertIn("Multica collaboration section missing standalone_default=true", errors)
        self.assertIn("Multica collaboration section missing downstream_return_to_fable=false", errors)

    def test_contract_rejects_contradictory_downstream_return_marker(self) -> None:
        unsafe = self.COMPLETE + "- `downstream_return_to_fable=true`\n"
        errors = validate_skill.multica_collaboration_contract_errors(unsafe)
        self.assertIn("Multica collaboration section forbids downstream_return_to_fable=true", errors)

    def test_contract_rejects_second_unsafe_fable_role(self) -> None:
        unsafe = self.COMPLETE + "- `fable_5=implementation_and_validation`\n"
        errors = validate_skill.multica_collaboration_contract_errors(unsafe)
        self.assertIn(
            "Multica collaboration section forbids fable_5=implementation_and_validation",
            errors,
        )

    def test_contract_rejects_prefixed_fake_assignment_keys(self) -> None:
        prefixed_only = """## Multica 可选单向协作
- `not_standalone_default=true`
- `not_fable_5=one_max_compact_context_framework_pass;tools=none;files=none;code=none;retry=none`
- `not_grok=web_x_source_ledger_only`
- `not_gemini=long_context_index_only_when_needed`
- `not_sol_ultra=sole_corrector_writer_validator`
- `not_downstream_return_to_fable=false`
"""
        errors = validate_skill.multica_collaboration_contract_errors(prefixed_only)
        expected = {
            "Multica collaboration section missing standalone_default=true",
            "Multica collaboration section missing fable_5=one_max_compact_context_framework_pass;tools=none;files=none;code=none;retry=none",
            "Multica collaboration section missing grok=web_x_source_ledger_only",
            "Multica collaboration section missing gemini=long_context_index_only_when_needed",
            "Multica collaboration section missing sol_ultra=sole_corrector_writer_validator",
            "Multica collaboration section missing downstream_return_to_fable=false",
        }
        self.assertTrue(expected.issubset(set(errors)), errors)


class DailyJournalWindowContractTests(unittest.TestCase):
    RUNTIME_DEFAULT_10 = """import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=10)
"""
    REFERENCE_DAYS_10 = """## Daily Journal
- `python3 scripts/daily_journal.py --compose --days 10 --publish`
- 幂等重组最近 10 个交易日。
"""

    def test_reference_and_runtime_agree_on_ten_day_window(self) -> None:
        self.assertEqual(
            validate_skill.daily_journal_window_contract_errors(
                self.REFERENCE_DAYS_10,
                self.RUNTIME_DEFAULT_10,
            ),
            [],
        )

    def test_stale_three_day_reference_is_rejected(self) -> None:
        stale_reference = self.REFERENCE_DAYS_10.replace("--days 10", "--days 3").replace(
            "最近 10 个交易日", "最近 3 个交易日"
        )
        errors = validate_skill.daily_journal_window_contract_errors(
            stale_reference,
            self.RUNTIME_DEFAULT_10,
        )
        self.assertIn("daily journal reference must prescribe --days 10", errors)

    def test_runtime_default_other_than_ten_is_rejected(self) -> None:
        runtime_default_3 = self.RUNTIME_DEFAULT_10.replace("default=10", "default=3")
        errors = validate_skill.daily_journal_window_contract_errors(
            self.REFERENCE_DAYS_10,
            runtime_default_3,
        )
        self.assertIn("daily journal runtime --days default must be 10", errors)


class MethodModuleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.decision_text = (cls.root / "references" / "decision-compiler.md").read_text(encoding="utf-8")
        cls.method_source = (cls.root / "scripts" / "method_router.py").read_text(encoding="utf-8")
        cls.compiler_source = (cls.root / "scripts" / "decision_compiler.py").read_text(encoding="utf-8")

    def test_all_runtime_methods_map_only_to_registered_modules(self) -> None:
        self.assertEqual(
            validate_skill.method_module_mapping_errors(
                self.decision_text, self.method_source, self.compiler_source
            ),
            [],
        )

    def test_missing_method_row_is_rejected(self) -> None:
        broken = self.decision_text.replace(
            "| `counter_consensus` | `endogenous_structure` | 使用 `counter_consensus_thesis` 子框架与 Cap 行 |\n",
            "",
        )
        errors = validate_skill.method_module_mapping_errors(
            broken, self.method_source, self.compiler_source
        )
        self.assertIn("method mapping missing: counter_consensus", errors)

    def test_unregistered_module_is_rejected(self) -> None:
        broken = self.decision_text.replace(
            "| `factor` | `quant_robustness` |",
            "| `factor` | `invented_module` |",
        )
        errors = validate_skill.method_module_mapping_errors(
            broken, self.method_source, self.compiler_source
        )
        self.assertIn("method mapping uses unregistered module for factor: invented_module", errors)

    def test_overlay_allowlist_headers_and_table_agree(self) -> None:
        self.assertEqual(
            validate_skill.overlay_module_contract_errors(
                self.decision_text, self.root / "references"
            ),
            [],
        )

    def test_overlay_declaration_after_line_eight_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refs = Path(tmp)
            for filename in validate_skill.OVERLAY_MODULE_CONTRACTS:
                shutil.copy2(self.root / "references" / filename, refs / filename)
            target = refs / "semis-index-divergence-overlay.md"
            target.write_text("\n" * 8 + target.read_text(encoding="utf-8"), encoding="utf-8")
            errors = validate_skill.overlay_module_contract_errors(self.decision_text, refs)
        self.assertTrue(any("semis-index-divergence-overlay.md must declare" in error for error in errors), errors)

    def test_overlay_cannot_declare_two_primary_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refs = Path(tmp)
            for filename in validate_skill.OVERLAY_MODULE_CONTRACTS:
                shutil.copy2(self.root / "references" / filename, refs / filename)
            target = refs / "semis-index-divergence-overlay.md"
            lines = target.read_text(encoding="utf-8").splitlines()
            lines.insert(3, "> 主落点声明：编译进 `macro`。")
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            errors = validate_skill.overlay_module_contract_errors(self.decision_text, refs)
        self.assertTrue(any("semis-index-divergence-overlay.md must declare" in error for error in errors), errors)


class ReleaseValidationIsolationTests(unittest.TestCase):
    def test_relocated_candidate_uses_trusted_account_home_for_live_path_sandbox(self) -> None:
        source = Path(__file__).resolve().parent / "release_validation_runner.py"
        with tempfile.TemporaryDirectory() as td:
            scripts = Path(td) / "injected" / "trading-research" / "scripts"
            scripts.mkdir(parents=True)
            relocated = scripts / source.name
            shutil.copy2(source, relocated)
            spec = importlib.util.spec_from_file_location(
                "relocated_release_validation_runner", relocated
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        expected = (
            account_home / ".hermes" / "longbridge-paper-trading",
            account_home / ".hermes" / "longbridge-paper-home",
            account_home / ".longbridge",
            account_home / ".config" / "hermes",
        )
        self.assertEqual(module.OWNER_HOME, account_home)
        self.assertEqual(module.PROTECTED_LIVE_PATHS, expected)
        for path in expected:
            self.assertIn(
                f'(deny file-read* file-write* (subpath "{path}"))',
                module.RELEASE_SANDBOX_PROFILE,
            )
        self.assertTrue(module.assert_live_path_sandbox_coverage())

    def test_child_environment_is_fresh_local_and_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            temp_dir = root / "tmp"
            home.mkdir()
            temp_dir.mkdir()
            db = root / "memory.sqlite"
            env = validate_skill.release_validation_env(home, temp_dir, db)

        self.assertEqual(env["HOME"], str(home))
        self.assertEqual(env["TRADING_MEMORY_DB"], str(db))
        self.assertEqual(
            env["PAPER_TRADE_LIFECYCLE_MODULE"],
            str(validate_skill.BUNDLED_LIFECYCLE_MODULE),
        )
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(env["TRADING_RESEARCH_RELEASE_VALIDATION"], "1")
        self.assertEqual(
            env["PYTHONPATH"],
            str(validate_skill.RELEASE_VALIDATION_GUARD.parent),
        )
        for key in (
            "LONGPORT_APP_KEY",
            "LONGPORT_APP_SECRET",
            "LONGPORT_ACCESS_TOKEN",
            "OPENAI_API_KEY",
            "XAI_API_KEY",
        ):
            self.assertNotIn(key, env)

    def test_bundled_sitecustomize_blocks_network_connections(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            temp_dir = root / "tmp"
            home.mkdir()
            temp_dir.mkdir()
            env = validate_skill.release_validation_env(
                home, temp_dir, root / "memory.sqlite"
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import socket; socket.create_connection(('127.0.0.1', 9))",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("network disabled by trading-research release validation", proc.stderr)


if __name__ == "__main__":
    unittest.main()
