#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decision_compiler as compiler  # noqa: E402


TEST_NOW = datetime.now(timezone.utc)

REGISTERED_CONTEXT = {
    "query_tier": "T2",
    "intent": "open",
    "has_position": False,
    "as_of": TEST_NOW.isoformat(),
    "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"],
}


def fresh_signal(module: str, level: str, *, multiplier: float = 1.0, **extra):
    now = TEST_NOW
    row = {
        "module": module,
        "max_action_level": level,
        "position_multiplier": multiplier,
        "hard_veto": False,
        "evidence_refs": [f"EID-{module}"],
        "observed_at": now.isoformat(),
        "stale_after": (now + timedelta(hours=1)).isoformat(),
    }
    row.update(extra)
    return row


class DecisionCompilerV2Tests(unittest.TestCase):
    def test_same_module_uses_min_while_cross_module_still_multiplies(self) -> None:
        same_module = compiler.compile_payload({
            "module_signals": [
                {"module": "endogenous_structure", "sub_framework": name, "max_action_level": "L3", "position_multiplier": 0.7}
                for name in ("overlay_a", "overlay_b", "overlay_c")
            ],
        })
        self.assertTrue(same_module["ok"])
        self.assertEqual(same_module["final_position_multiplier"], 0.7)
        superseded = [
            row for row in same_module["cap_applied"]
            if row.get("superseded_by_min")
        ]
        self.assertEqual(len(superseded), 2)
        self.assertTrue(all(row["capped_to"] == 0.7 for row in superseded))

        cross_module = compiler.compile_payload({
            "module_signals": [
                {"module": "endogenous_structure", "max_action_level": "L3", "position_multiplier": 0.7},
                {"module": "macro", "max_action_level": "L3", "position_multiplier": 0.7},
            ],
        })
        self.assertTrue(cross_module["ok"])
        self.assertEqual(cross_module["final_position_multiplier"], 0.49)
        self.assertFalse(any(row.get("superseded_by_min") for row in cross_module["cap_applied"]))

    def test_strict_v2_same_module_sub_frameworks_use_one_module_min(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                **REGISTERED_CONTEXT,
                "required_modules": [
                    "endogenous_structure", "risk_regime", "portfolio_risk_budget", "data_quality",
                ],
            },
            "module_signals": [
                fresh_signal("endogenous_structure", "L3", multiplier=0.7, sub_framework=name)
                for name in ("overlay_a", "overlay_b", "overlay_c")
            ] + [
                fresh_signal("risk_regime", "L3"),
                fresh_signal("portfolio_risk_budget", "L3"),
                fresh_signal("data_quality", "L3"),
            ],
        }, now=TEST_NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["contract_status"], "strict_pass")
        self.assertEqual(result["final_position_multiplier"], 0.7)
        self.assertEqual(sum(bool(row.get("superseded_by_min")) for row in result["cap_applied"]), 2)

    def test_l5_exit_dominates_l3_add(self) -> None:
        result = compiler.compile_payload({
            "module_signals": [
                {"module": "fundamentals", "max_action_level": "L3", "position_multiplier": 1.0},
                {"module": "risk_regime", "max_action_level": "L5", "position_multiplier": 0.0},
            ]
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["compiled_action"], "L5")
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["holding_directive"], "EXIT")
        self.assertEqual(result["final_position_multiplier"], 0.0)

    def test_l4_reduce_dominates_entry_levels(self) -> None:
        result = compiler.compile_payload({
            "module_signals": [
                {"module": "fundamentals", "max_action_level": "L2", "position_multiplier": 0.7},
                {"module": "portfolio_risk_budget", "max_action_level": "L4", "position_multiplier": 0.5},
            ]
        })
        self.assertEqual(result["compiled_action"], "L4")
        self.assertEqual(result["holding_directive"], "REDUCE")
        self.assertEqual(result["entry_permission"], "BLOCK")

    def test_unknown_module_is_rejected_even_in_legacy_mode(self) -> None:
        result = compiler.compile_payload({
            "module_signals": [
                {"module": "unknown_parallel_score", "max_action_level": "L3", "position_multiplier": 1.0}
            ]
        })
        self.assertFalse(result["ok"])
        self.assertIn("unknown_module:unknown_parallel_score", result["validation_errors"])
        self.assertEqual(result["compiled_action"], "L0")

    def test_v2_requires_declared_required_modules(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": REGISTERED_CONTEXT,
            "module_signals": [fresh_signal("risk_regime", "L2")],
        })
        self.assertFalse(result["ok"])
        self.assertIn("missing_required_module:portfolio_risk_budget", result["validation_errors"])
        self.assertIn("missing_required_module:data_quality", result["validation_errors"])

    def test_cli_reports_oversized_raw_integer_as_structured_no_order_error(self) -> None:
        raw = '{"module_signals":[{"position_multiplier":' + "1" * 5000 + "}]}"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "request.json"
            path.write_text(raw, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("decision_compiler.py")), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "legacy_failed")
        self.assertTrue(result["no_order_execution"])
        self.assertNotIn("1" * 5000, completed.stdout)

    def test_direct_negative_multiplier_forms_are_contract_failures(self) -> None:
        invalid_values = (-1, -0.25, -0.0, Decimal("-1"), Decimal("-0"), "-1", "-0", "-0.0", "-0e0")
        for value in invalid_values:
            with self.subTest(value=repr(value)):
                result = compiler.compile_payload({
                    "module_signals": [
                        {"module": "fundamentals", "max_action_level": "L1", "position_multiplier": value}
                    ]
                })
                self.assertFalse(result["ok"])
                self.assertEqual(result["contract_status"], "legacy_failed")
                self.assertIn("invalid_position_multiplier:fundamentals", result["validation_errors"])
                self.assertEqual(result["compiled_action"], "L0")
                self.assertEqual(result["final_position_multiplier"], 0.0)

    def test_cli_preserves_lexical_negative_zero_until_multiplier_validation(self) -> None:
        for token in ("-0", "-0.0", "-0e0", "-0E+9", "-1e-9999"):
            raw = '{"module_signals":[{"module":"fundamentals","max_action_level":"L1","position_multiplier":' + token + "}]}"
            with self.subTest(token=token), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "request.json"
                path.write_text(" \n" + raw + "\n ", encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("decision_compiler.py")), str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertEqual(result["contract_status"], "legacy_failed")
            self.assertIn("invalid_position_multiplier:fundamentals", result["validation_errors"])
            self.assertTrue(result["no_order_execution"])

    def test_cli_positive_zero_and_ordinary_integer_controls_remain_valid(self) -> None:
        for token in ("0", "0.0", "0e0", '"0"', "1"):
            raw = '{"module_signals":[{"module":"fundamentals","max_action_level":"L1","position_multiplier":' + token + "}]}"
            with self.subTest(token=token), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "request.json"
                path.write_text(raw, encoding="utf-8")
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("decision_compiler.py")), str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            self.assertTrue(result["ok"])
            self.assertEqual(result["contract_status"], "legacy_unverified")

    def test_load_payload_preserves_native_int_and_raw_negative_zero_sign(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ordinary = Path(temp_dir) / "ordinary.json"
            ordinary.write_text('{"value":7}', encoding="utf-8")
            negative_zero = Path(temp_dir) / "negative-zero.json"
            negative_zero.write_text('{"value":-0}', encoding="utf-8")
            ordinary_value = compiler.load_payload(str(ordinary))["value"]
            negative_zero_value = compiler.load_payload(str(negative_zero))["value"]
        self.assertIs(type(ordinary_value), int)
        self.assertEqual(ordinary_value, 7)
        self.assertEqual(negative_zero_value, 0.0)
        self.assertLess(math.copysign(1.0, negative_zero_value), 0.0)

    def test_stdin_cli_oversized_integer_reaches_failed_contract_without_echo(self) -> None:
        huge = "9" * 5000
        raw = '{"module_signals":[{"module":"fundamentals","max_action_level":"L1","position_multiplier":-' + huge + "}]}"
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("decision_compiler.py"))],
            input=raw,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "legacy_failed")
        self.assertIn("invalid_position_multiplier:fundamentals", result["validation_errors"])
        self.assertTrue(result["no_order_execution"])
        self.assertNotIn(huge, completed.stdout)

    def test_v2_rejects_stale_or_untraceable_action_signal(self) -> None:
        now = datetime.now(timezone.utc)
        stale = fresh_signal("risk_regime", "L2")
        stale["stale_after"] = (now - timedelta(minutes=1)).isoformat()
        no_evidence = fresh_signal("portfolio_risk_budget", "L2")
        no_evidence["evidence_refs"] = []
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {**REGISTERED_CONTEXT, "as_of": now.isoformat()},
            "module_signals": [stale, no_evidence, fresh_signal("data_quality", "L2")],
        })
        self.assertFalse(result["ok"])
        self.assertIn("stale_signal:risk_regime", result["validation_errors"])
        self.assertIn("missing_evidence_refs:portfolio_risk_budget", result["validation_errors"])

    def test_v2_uses_trusted_runtime_now_for_signal_freshness(self) -> None:
        runtime_now = datetime(2026, 7, 27, 18, 15, 17, tzinfo=timezone.utc)
        payload_as_of = runtime_now - timedelta(hours=2)
        stale_after = runtime_now - timedelta(hours=1)
        signals = []
        for module in ("risk_regime", "portfolio_risk_budget", "data_quality"):
            signals.append({
                "module": module,
                "max_action_level": "L2",
                "position_multiplier": 1.0,
                "hard_veto": False,
                "evidence_refs": [f"EID-{module}"],
                "observed_at": (payload_as_of - timedelta(minutes=10)).isoformat(),
                "stale_after": stale_after.isoformat(),
            })

        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                **REGISTERED_CONTEXT,
                "as_of": payload_as_of.isoformat(),
            },
            "module_signals": signals,
        }, now=runtime_now)

        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "strict_failed")
        self.assertIn("stale_signal:risk_regime", result["validation_errors"])
        self.assertEqual(result["compiled_action"], "L0")
        self.assertEqual(result["entry_permission"], "BLOCK")

    def test_v2_rejects_runtime_future_signal_when_payload_clock_is_forward(self) -> None:
        runtime_now = datetime(2026, 7, 27, 18, 15, 17, tzinfo=timezone.utc)
        payload_as_of = runtime_now + timedelta(hours=2)
        signals = []
        for module in ("risk_regime", "portfolio_risk_budget", "data_quality"):
            signals.append({
                "module": module,
                "max_action_level": "L2",
                "position_multiplier": 1.0,
                "hard_veto": False,
                "evidence_refs": [f"EID-{module}"],
                "observed_at": (runtime_now + timedelta(hours=1)).isoformat(),
                "stale_after": (runtime_now + timedelta(hours=3)).isoformat(),
            })

        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                **REGISTERED_CONTEXT,
                "as_of": payload_as_of.isoformat(),
            },
            "module_signals": signals,
        }, now=runtime_now)

        self.assertFalse(result["ok"])
        self.assertIn("future_decision_context_as_of", result["validation_errors"])
        self.assertIn("future_observed_at:risk_regime", result["validation_errors"])

    def test_v2_requires_complete_typed_decision_context(self) -> None:
        runtime_now = datetime(2026, 7, 27, 18, 15, 17, tzinfo=timezone.utc)
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"],
            },
            "module_signals": [
                {"module": "risk_regime", "max_action_level": "L0", "hard_veto": True},
                {"module": "portfolio_risk_budget", "max_action_level": "L0", "hard_veto": False},
                {"module": "data_quality", "max_action_level": "L0", "hard_veto": False},
            ],
        }, now=runtime_now)

        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "strict_failed")
        self.assertIn("invalid_query_tier", result["validation_errors"])
        self.assertIn("invalid_intent", result["validation_errors"])
        self.assertIn("invalid_has_position", result["validation_errors"])
        self.assertIn("invalid_decision_context_as_of", result["validation_errors"])

    def test_v2_complete_request_passes_with_two_axis_output(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": REGISTERED_CONTEXT,
            "module_signals": [
                fresh_signal("risk_regime", "L2", multiplier=0.8),
                fresh_signal("portfolio_risk_budget", "L2", multiplier=0.5),
                fresh_signal("data_quality", "L3", multiplier=1.0),
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["contract_status"], "strict_pass")
        self.assertEqual(result["compiled_action"], "L2")
        self.assertEqual(result["entry_permission"], "BUILD")
        self.assertEqual(result["holding_directive"], "HOLD")
        self.assertAlmostEqual(result["final_position_multiplier"], 0.4)

    def test_v2_non_open_intents_cannot_emit_entry_authority(self) -> None:
        for intent in ("research", "hold", "reduce", "exit"):
            for has_position in (False, True):
                with self.subTest(intent=intent, has_position=has_position):
                    result = compiler.compile_payload({
                        "schema_version": "decision_request.v2",
                        "decision_context": {
                            "query_tier": "T1",
                            "intent": intent,
                            "has_position": has_position,
                            "as_of": TEST_NOW.isoformat(),
                            "required_modules": ["risk_regime"],
                        },
                        "module_signals": [fresh_signal("risk_regime", "L3")],
                    }, now=TEST_NOW)

                    self.assertTrue(result["ok"])
                    self.assertEqual(result["contract_status"], "strict_pass")
                    self.assertEqual(result["entry_permission"], "BLOCK")
                    self.assertEqual(result["compiled_action"], "L0")
                    self.assertEqual(result["final_position_multiplier"], 0.0)

    def test_v2_l0_action_signal_still_requires_evidence_and_freshness(self) -> None:
        context = {
            "query_tier": "T1",
            "intent": "hold",
            "has_position": True,
            "as_of": TEST_NOW.isoformat(),
            "required_modules": ["risk_regime"],
        }
        missing_evidence = {
            "module": "risk_regime",
            "max_action_level": "L0",
            "hard_veto": True,
            "position_multiplier": 0.0,
        }
        future = {
            **missing_evidence,
            "evidence_refs": ["EID-risk-regime"],
            "observed_at": (TEST_NOW + timedelta(minutes=5)).isoformat(),
            "stale_after": (TEST_NOW + timedelta(hours=1)).isoformat(),
        }

        for name, signal, expected_error in (
            ("missing", missing_evidence, "missing_evidence_refs:risk_regime"),
            ("future", future, "future_observed_at:risk_regime"),
        ):
            with self.subTest(name=name):
                result = compiler.compile_payload({
                    "schema_version": "decision_request.v2",
                    "decision_context": context,
                    "module_signals": [signal],
                }, now=TEST_NOW)
                self.assertFalse(result["ok"])
                self.assertIn(expected_error, result["validation_errors"])
                self.assertEqual(result["compiled_action"], "L0")
                self.assertEqual(result["entry_permission"], "BLOCK")

    def test_hard_veto_exits_existing_position(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "hold",
                "has_position": True,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": ["risk_regime"],
            },
            "module_signals": [fresh_signal("risk_regime", "L1", multiplier=0.0, hard_veto=True)],
        })
        self.assertTrue(result["ok"])
        self.assertTrue(result["hard_veto"])
        self.assertEqual(result["compiled_action"], "L5")
        self.assertEqual(result["holding_directive"], "EXIT")
        self.assertEqual(result["entry_permission"], "BLOCK")

    def test_epistemic_hard_veto_blocks_entry_without_forcing_exit(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "hold",
                "has_position": True,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": ["data_quality"],
            },
            "module_signals": [fresh_signal("data_quality", "L1", multiplier=0.0, hard_veto=True)],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["holding_directive"], "HOLD")
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["final_position_multiplier"], 0.0)
        self.assertIs(result["epistemic_veto"], True)
        self.assertIs(result["manual_review_required"], True)
        self.assertEqual(result["compiled_action"], "L0")

    def test_market_risk_veto_dominates_epistemic_veto(self) -> None:
        result = compiler.compile_payload({
            "decision_context": {"has_position": True},
            "module_signals": [
                {"module": "data_quality", "max_action_level": "L1", "position_multiplier": 0.0, "hard_veto": True},
                {"module": "risk_regime", "max_action_level": "L1", "position_multiplier": 0.0, "hard_veto": True},
            ],
        })
        self.assertEqual(result["holding_directive"], "EXIT")
        self.assertIs(result["epistemic_veto"], True)

    def test_zero_multiplier_signal_is_a_dominant_constraint(self) -> None:
        result = compiler.compile_payload({
            "module_signals": [
                {"module": "fundamentals", "max_action_level": "L3", "position_multiplier": 1.0},
                {"module": "prediction_market_prior", "max_action_level": "L2", "position_multiplier": 0.0},
            ],
        })
        self.assertEqual(result["compiled_action"], "L0")
        self.assertIn("prediction_market_prior", result["dominant_constraints"])

    def test_duplicate_module_signal_is_rejected_in_v2(self) -> None:
        context = {**REGISTERED_CONTEXT, "required_modules": ["risk_regime"]}
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": context,
            "module_signals": [fresh_signal("risk_regime", "L2"), fresh_signal("risk_regime", "L1")],
        })
        self.assertFalse(result["ok"])
        self.assertIn("duplicate_module_signal:risk_regime", result["validation_errors"])

    def _open_v2_signals(self, *, evidence_refs=None, module="risk_regime"):
        signals = [
            fresh_signal("risk_regime", "L3"),
            fresh_signal("portfolio_risk_budget", "L3"),
            fresh_signal("data_quality", "L3"),
        ]
        if evidence_refs is not None:
            for row in signals:
                if row["module"] == module:
                    row["evidence_refs"] = evidence_refs
        return signals

    def test_v2_rejects_parser_sentinel_in_evidence_refs_without_open_authority(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": REGISTERED_CONTEXT,
            "module_signals": self._open_v2_signals(
                evidence_refs=[compiler.INVALID_JSON_INTEGER]
            ),
        }, now=TEST_NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "strict_failed")
        self.assertIn("invalid_evidence_refs:risk_regime", result["validation_errors"])
        self.assertEqual(result["compiled_action"], "L0")
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["final_position_multiplier"], 0.0)
        self.assertTrue(result["no_order_execution"])
        self.assertNotIn(compiler.INVALID_JSON_INTEGER, json.dumps(result, ensure_ascii=False))

    def test_v2_rejects_non_string_evidence_refs_including_huge_integer(self) -> None:
        cases = [
            [int("1" + "0" * 1024)],
            [1],
            [1.5],
            [True],
            [{"eid": "EID-risk_regime"}],
            [["EID-risk_regime"]],
            [None],
            ["", "EID-risk_regime"],
            ["   "],
            ["EID-ok", compiler.INVALID_JSON_INTEGER],
        ]
        for refs in cases:
            with self.subTest(refs=str(type(refs[0]).__name__) if refs else "empty"):
                result = compiler.compile_payload({
                    "schema_version": "decision_request.v2",
                    "decision_context": REGISTERED_CONTEXT,
                    "module_signals": self._open_v2_signals(evidence_refs=refs),
                }, now=TEST_NOW)
                self.assertFalse(result["ok"], refs)
                self.assertEqual(result["contract_status"], "strict_failed")
                self.assertTrue(
                    any(
                        err.startswith("invalid_evidence_refs:risk_regime")
                        or err.startswith("missing_evidence_refs:risk_regime")
                        for err in result["validation_errors"]
                    ),
                    result["validation_errors"],
                )
                self.assertEqual(result["entry_permission"], "BLOCK")
                self.assertEqual(result["final_position_multiplier"], 0.0)
                self.assertNotEqual(result.get("entry_permission"), "ADD")

    def test_cli_rejects_raw_oversized_integer_evidence_ref_without_open_authority(self) -> None:
        huge = "1" + "0" * 1024
        now = TEST_NOW
        payload = {
            "schema_version": "decision_request.v2",
            "decision_context": {
                **REGISTERED_CONTEXT,
                "as_of": now.isoformat(),
            },
            "module_signals": [
                {
                    "module": "risk_regime",
                    "max_action_level": "L3",
                    "position_multiplier": 1.0,
                    "hard_veto": False,
                    "evidence_refs": ["__H__"],
                    "observed_at": now.isoformat(),
                    "stale_after": (now + timedelta(hours=1)).isoformat(),
                },
                fresh_signal("portfolio_risk_budget", "L3"),
                fresh_signal("data_quality", "L3"),
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False).replace('"__H__"', huge)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "request.json"
            path.write_text(raw + "\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "decision_compiler.py"), str(path)],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "strict_failed")
        self.assertIn("invalid_evidence_refs:risk_regime", result["validation_errors"])
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["final_position_multiplier"], 0.0)
        self.assertTrue(result["no_order_execution"])
        self.assertNotIn(huge, completed.stdout)
        self.assertNotIn(compiler.INVALID_JSON_INTEGER, completed.stdout)

    def test_v2_normal_eid_evidence_refs_still_authorize_when_rest_is_valid(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": REGISTERED_CONTEXT,
            "module_signals": self._open_v2_signals(evidence_refs=["EID-risk_regime"]),
        }, now=TEST_NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["contract_status"], "strict_pass")
        self.assertEqual(result["entry_permission"], "ADD")
        self.assertEqual(result["final_position_multiplier"], 1.0)



class CapAndTightenOnlyRegistryTests(unittest.TestCase):
    """Cap & Tighten-Only Registry runtime enforcement (decision-compiler.md
    lines 62-79 are the single authority for the values). These cover the two
    audit-confirmed bypasses (finding cap-registry-unenforced) plus one
    adversarial case per invariant category: (1) claim/module-type position_
    multiplier hard cap, (2) tighten-only overlay behaviour (never loosens, never
    revives a zero-cap module's entry claim), (3) readiness_level/knowability
    action-level legal-combination ceiling.
    """

    def test_doc_refs_resolve_to_live_authority_identifiers(self) -> None:
        doc_path = Path(__file__).resolve().parents[1] / "references" / "decision-compiler.md"
        lines = doc_path.read_text(encoding="utf-8").splitlines()

        def referenced_line(ref: str) -> str:
            match = re.search(r"decision-compiler\.md:(\d+)", ref)
            self.assertIsNotNone(match, ref)
            line_no = int(match.group(1))
            self.assertLessEqual(line_no, len(lines), ref)
            return lines[line_no - 1]

        paired_refs: list[tuple[str, str]] = []
        for (module, sub_framework), (_, doc_ref) in compiler.MODULE_POSITION_MULTIPLIER_CAP.items():
            identifier = sub_framework or module
            if identifier == "kol_method_card":
                identifier = "kol_method_cards"
            paired_refs.append((identifier, doc_ref))
        paired_refs.extend([
            ("hk_deep_value_no_catalyst", compiler.HK_DEEP_VALUE_DOC_REF),
            ("hk_momentum_drawdown_review", compiler.HK_MOMENTUM_REVIEW_DOC_REF),
            ("counter_consensus_thesis", compiler.COUNTER_CONSENSUS_DOC_REF),
        ])

        self.assertEqual(len(paired_refs), 14)
        for identifier, doc_ref in paired_refs:
            with self.subTest(identifier=identifier, doc_ref=doc_ref):
                self.assertIn(identifier, referenced_line(doc_ref))

        readiness_ref = compiler._readiness_ceiling({
            "module": "research_readiness", "readiness_level": "draft",
        })[1]
        range_match = re.search(r"decision-compiler\.md:(\d+)-(\d+)", readiness_ref)
        self.assertIsNotNone(range_match, readiness_ref)
        start, end = map(int, range_match.groups())
        readiness_block = "\n".join(lines[start - 1:end])
        self.assertIn("readiness_level=draft/not_actionable/needs_refresh", readiness_block)
        self.assertIn("knowability_status=irreducible_uncertainty", readiness_block)

        knowability_ref = compiler._readiness_ceiling({
            "module": "research_readiness",
            "knowability_status": "irreducible_uncertainty",
        })[1]
        self.assertIn("knowability_status=irreducible_uncertainty", referenced_line(knowability_ref))
        runtime_crossref = next(line for line in lines if "MODULE_POSITION_MULTIPLIER_CAP" in line)
        self.assertIn("第 76-77 行", runtime_crossref)

    def test_audit_bypass_modeled_scenario_cannot_forge_l3_add_from_zero_cap(self) -> None:
        # Audit evidence: a lone modeled_scenario signal claiming L3/ADD with
        # position_multiplier=1.0 previously reached strict_pass even though the
        # registry caps modeled_scenario at position_multiplier=0.0. A hard-zero
        # module cannot self-issue an entry claim -- fail closed, don't clamp.
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "open",
                "has_position": False,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": ["modeled_scenario", "data_quality"],
            },
            "module_signals": [
                fresh_signal("modeled_scenario", "L3", multiplier=1.0, entry_permission="ADD"),
                fresh_signal("data_quality", "L1"),
            ],
        })
        self.assertFalse(result["ok"])
        self.assertIn("forged_zero_cap_entry:modeled_scenario", result["validation_errors"])
        self.assertEqual(result["compiled_action"], "L0")
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["final_position_multiplier"], 0.0)

    def test_audit_bypass_x_frontline_multiplier_clamped_to_0_15_not_0_9(self) -> None:
        # Audit evidence: x_frontline position_multiplier=0.9 previously reached
        # strict_pass at 0.9 even though the registry caps x_frontline at 0.15.
        # Unlike the zero-cap forgery above, this is a legitimate value out of
        # range -- clamp it and record the clamp, don't reject the payload.
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "open",
                "has_position": False,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": [
                    "x_frontline", "risk_regime", "portfolio_risk_budget", "data_quality",
                ],
            },
            "module_signals": [
                fresh_signal("x_frontline", "L2", multiplier=0.9),
                fresh_signal("risk_regime", "L3"),
                fresh_signal("portfolio_risk_budget", "L3"),
                fresh_signal("data_quality", "L1"),
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["final_position_multiplier"], 0.15)
        cap_entry = next(c for c in result["cap_applied"] if c["module"] == "x_frontline")
        self.assertEqual(cap_entry["requested"], 0.9)
        self.assertEqual(cap_entry["capped_to"], 0.15)
        self.assertEqual(cap_entry["field"], "position_multiplier")

    def test_counter_consensus_caps_multiplier_and_missing_contract_to_watch(self) -> None:
        result = compiler.compile_payload({
            "module_signals": [{
                "module": "endogenous_structure",
                "sub_framework": "counter_consensus_thesis",
                "max_action_level": "L3",
                "position_multiplier": 0.8,
            }],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["final_position_multiplier"], 0.3)
        self.assertEqual(result["entry_permission"], "WATCH")
        multiplier_cap = next(
            row for row in result["cap_applied"]
            if row["field"] == "position_multiplier"
        )
        self.assertEqual(multiplier_cap["requested"], 0.8)
        self.assertEqual(multiplier_cap["capped_to"], 0.3)
        watch_cap = next(
            row for row in result["cap_applied"]
            if row["field"] == "entry_permission"
        )
        self.assertEqual(watch_cap["missing_fields"], ["falsifier", "time_stop"])

    def test_counter_consensus_complete_contract_keeps_entry_but_not_multiplier_excess(self) -> None:
        result = compiler.compile_payload({
            "module_signals": [{
                "module": "endogenous_structure",
                "sub_framework": "counter_consensus_thesis",
                "max_action_level": "L3",
                "position_multiplier": 0.8,
                "falsifier": "variant evidence fails",
                "time_stop": "2026-09-30T16:00:00-04:00",
            }],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["final_position_multiplier"], 0.3)
        self.assertEqual(result["entry_permission"], "ADD")

    def test_strict_counter_consensus_missing_text_contract_is_watch_only(self) -> None:
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                **REGISTERED_CONTEXT,
                "required_modules": [
                    "endogenous_structure", "risk_regime", "portfolio_risk_budget", "data_quality",
                ],
            },
            "module_signals": [
                fresh_signal(
                    "endogenous_structure", "L3", multiplier=0.8,
                    sub_framework="counter_consensus_thesis",
                    falsifier=True,
                    time_stop={},
                ),
                fresh_signal("risk_regime", "L3"),
                fresh_signal("portfolio_risk_budget", "L3"),
                fresh_signal("data_quality", "L3"),
            ],
        }, now=TEST_NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["contract_status"], "strict_pass")
        self.assertEqual(result["entry_permission"], "WATCH")
        self.assertEqual(result["final_position_multiplier"], 0.3)

    def test_political_disclosure_sub_framework_caps_tighter_than_generic_x_frontline(self) -> None:
        # political_disclosure vote is a stricter
        # sub-cap (0.10) than the generic x_frontline/KOL cap (0.15).
        result = compiler.compile_payload({
            "module_signals": [{
                "module": "x_frontline",
                "sub_framework": "political_disclosure",
                "max_action_level": "L2",
                "position_multiplier": 0.5,
            }],
        })
        self.assertEqual(result["final_position_multiplier"], 0.10)

    def test_kol_method_card_handoff_cannot_forge_build_from_zero_cap(self) -> None:
        # kol_method_cards handoff is position_multiplier
        # =0.0 regardless of which module carries it (x_frontline or
        # research_readiness); it cannot forge a BUILD entry either.
        result = compiler.compile_payload({
            "module_signals": [{
                "module": "research_readiness",
                "sub_framework": "kol_method_card",
                "max_action_level": "L2",
                "entry_permission": "BUILD",
                "position_multiplier": 1.0,
            }],
        })
        self.assertFalse(result["ok"])
        self.assertIn("forged_zero_cap_entry:research_readiness:kol_method_card", result["validation_errors"])

    def test_open_intent_requires_full_capital_commitment_baseline(self) -> None:
        # A caller may not make data_quality its only admission ticket. Any
        # strict open request must also prove risk_regime and
        # portfolio_risk_budget before it can receive entry authority.
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "open",
                "has_position": False,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": ["data_quality"],
            },
            "module_signals": [fresh_signal("data_quality", "L3")],
        })
        self.assertFalse(result["ok"])
        self.assertIn("required_modules_below_baseline:risk_regime", result["validation_errors"])
        self.assertIn("required_modules_below_baseline:portfolio_risk_budget", result["validation_errors"])
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["final_position_multiplier"], 0.0)

    def test_required_modules_baseline_exempt_for_hold_intent(self) -> None:
        # A hold-intent payload cannot grant new entry permission (rule 1), so it
        # carries none of the capital-commitment risk the baseline floor guards.
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "hold",
                "has_position": True,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": ["risk_regime"],
            },
            "module_signals": [fresh_signal("risk_regime", "L2")],
        })
        self.assertTrue(result["ok"])
        self.assertNotIn("required_modules_below_baseline:data_quality", result["validation_errors"])

    def test_readiness_level_draft_ceilings_l3_add_claim_down_to_l0_watch(self) -> None:
        # readiness_level=draft ceilings action to L0
        # no matter what max_action_level/entry_permission the signal itself claims.
        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T1",
                "intent": "open",
                "has_position": False,
                "as_of": TEST_NOW.isoformat(),
                "required_modules": [
                    "research_readiness", "risk_regime", "portfolio_risk_budget", "data_quality",
                ],
            },
            "module_signals": [
                fresh_signal(
                    "research_readiness", "L3", multiplier=1.0,
                    entry_permission="ADD", readiness_level="draft",
                ),
                fresh_signal("risk_regime", "L3"),
                fresh_signal("portfolio_risk_budget", "L3"),
                fresh_signal("data_quality", "L1"),
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["entry_permission"], "WATCH")
        self.assertEqual(result["compiled_action"], "L0")
        self.assertTrue(any(
            c["field"] == "entry_permission" and c["capped_to"] == "WATCH"
            for c in result["cap_applied"]
        ))

    def test_readiness_irreducible_uncertainty_blocks_self_declared_exit(self) -> None:
        # knowability_status=irreducible_uncertainty
        # ceilings to L0; research_readiness may never independently drive EXIT
        # (rule 9), so a signal that self-declares holding_directive=EXIT here
        # must be forced back to HOLD, not allowed to mechanically sell.
        result = compiler.compile_payload({
            "module_signals": [{
                "module": "research_readiness",
                "max_action_level": "L2",
                "position_multiplier": 0.5,
                "holding_directive": "EXIT",
                "knowability_status": "irreducible_uncertainty",
            }],
        })
        self.assertEqual(result["holding_directive"], "HOLD")

    def test_hk_deep_value_no_catalyst_caps_entry_to_watch(self) -> None:
        # hk_deep_value_no_catalyst caps entry_permission
        # to WATCH regardless of module or claimed level.
        result = compiler.compile_payload({
            "module_signals": [{
                "module": "fundamentals",
                "max_action_level": "L3",
                "position_multiplier": 1.0,
                "entry_permission": "ADD",
                "hk_deep_value_no_catalyst": True,
            }],
        })
        self.assertEqual(result["entry_permission"], "WATCH")

    def test_hk_momentum_drawdown_review_forces_reduce_until_reviewed(self) -> None:
        # hk_momentum_drawdown_review forbids
        # maintaining or raising the original action level before the mandatory
        # take-profit/stop-loss review completes; that means at least REDUCE.
        result = compiler.compile_payload({
            "decision_context": {"has_position": True},
            "module_signals": [{
                "module": "fundamentals",
                "max_action_level": "L1",
                "position_multiplier": 1.0,
                "hk_momentum_drawdown_review": True,
            }],
        })
        self.assertEqual(result["holding_directive"], "REDUCE")

    def test_hk_momentum_drawdown_review_completed_lifts_the_forced_reduce(self) -> None:
        result = compiler.compile_payload({
            "decision_context": {"has_position": True},
            "module_signals": [{
                "module": "fundamentals",
                "max_action_level": "L1",
                "position_multiplier": 1.0,
                "hk_momentum_drawdown_review": True,
                "review_completed": True,
            }],
        })
        self.assertEqual(result["holding_directive"], "HOLD")

    def test_v2_huge_integer_multiplier_fails_contract_without_overflow_or_elevation(self) -> None:
        signals = [
            fresh_signal("risk_regime", "L3", multiplier=int("1" + "0" * 400)),
            fresh_signal("portfolio_risk_budget", "L3", multiplier=1.0),
            fresh_signal("data_quality", "L3", multiplier=1.0),
        ]

        result = compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": REGISTERED_CONTEXT,
            "module_signals": signals,
        }, now=TEST_NOW)

        self.assertFalse(result["ok"])
        self.assertEqual(result["contract_status"], "strict_failed")
        self.assertIn("invalid_position_multiplier:risk_regime", result["validation_errors"])
        self.assertEqual(result["compiled_action"], "L0")
        self.assertEqual(result["entry_permission"], "BLOCK")
        self.assertEqual(result["final_position_multiplier"], 0.0)


if __name__ == "__main__":
    unittest.main()
