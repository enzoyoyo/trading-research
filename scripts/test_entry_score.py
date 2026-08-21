#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import entry_score  # noqa: E402
import decision_compiler  # noqa: E402


NOW = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)


def factor(score: float, evidence_id: str, *, stale: bool = False) -> dict:
    return {
        "score": score,
        "reason": f"reason-{evidence_id}",
        "evidence_refs": [evidence_id],
        "observed_at": (NOW - timedelta(minutes=5)).isoformat(),
        "stale_after": (NOW - timedelta(minutes=1) if stale else NOW + timedelta(hours=1)).isoformat(),
    }


def complete_payload() -> dict:
    return {
        "schema_version": "entry_score.v1",
        "symbol": "XMU-USDT",
        "as_of": NOW.isoformat(),
        "product_identity": {
            "venue": "okx",
            "channel": "cex_spot",
            "instrument_id": "XMU-USDT",
            "instrument_type": "SPOT",
            "instrument_category": "3",
            "mapping_scope": "exact_exchange_instrument_only",
            "state": "live",
            "mapping_verified": True,
            "region_eligible": True,
            "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
            "stale_after": (NOW + timedelta(hours=1)).isoformat(),
            "evidence_refs": ["EID-OKX-INST"],
        },
        "factors": {
            "technical": factor(4.0, "EID-TECH"),
            "capital_flow": factor(3.5, "EID-FLOW"),
            "sentiment": factor(3.0, "EID-SENT"),
            "fundamentals": factor(4.5, "EID-FUND"),
            "macro": factor(2.5, "EID-MACRO"),
        },
    }


class EntryScoreTests(unittest.TestCase):
    def test_complete_five_factor_score_preserves_canonical_weights(self) -> None:
        result = entry_score.compile_entry_score(complete_payload(), now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["entry_score_100"], 72.0)
        self.assertEqual(result["weighted_score_0_5"], 3.6)
        self.assertEqual(result["base_action_range"], "L1")
        self.assertIsNone(result["entry_permission_ceiling"])
        self.assertEqual(result["lowest_factor"]["name"], "macro")
        self.assertEqual(result["lowest_factor"]["score"], 2.5)
        self.assertIsNone(result["suggested_module_signal"])
        self.assertEqual(result["compiler_effect"], "none")
        self.assertTrue(result["cannot_authorize_action"])
        self.assertEqual(result["score_version"], "five_factor_v1")
        self.assertEqual(result["product_identity"]["instrument_id"], "XMU-USDT")
        self.assertEqual(result["factor_weights"]["fundamentals"], 0.25)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["suggested_module_signals"], [])
        self.assertTrue(result["no_order_execution"])

    def test_missing_factor_never_receives_neutral_imputation(self) -> None:
        payload = complete_payload()
        payload["factors"].pop("macro")
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("missing_factor:macro", result["validation_errors"])
        self.assertEqual(result["suggested_module_signal"]["module"], "data_quality")
        self.assertTrue(result["suggested_module_signal"]["hard_veto"])
        self.assertEqual(result["suggested_module_signal"]["position_multiplier"], 0.0)

    def test_stale_factor_blocks_score_instead_of_reusing_old_value(self) -> None:
        payload = complete_payload()
        payload["factors"]["technical"] = factor(5.0, "EID-OLD", stale=True)
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("stale_factor:technical", result["validation_errors"])

    def test_factor_without_evidence_is_not_actionable(self) -> None:
        payload = complete_payload()
        payload["factors"]["sentiment"]["evidence_refs"] = []
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIn("missing_evidence_refs:sentiment", result["validation_errors"])

    def test_factor_evidence_must_use_eid_identifiers(self) -> None:
        payload = complete_payload()
        payload["factors"]["sentiment"]["evidence_refs"] = ["not-an-eid"]
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIn("invalid_evidence_refs:sentiment", result["validation_errors"])

    def test_missing_product_identity_never_publishes_numeric_score(self) -> None:
        payload = complete_payload()
        payload.pop("product_identity")
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("missing_product_identity", result["validation_errors"])

    def test_instrument_id_must_match_scored_symbol(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["instrument_id"] = "XSKHY-USDT"
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIn("product_identity_symbol_mismatch", result["validation_errors"])

    def test_stale_product_identity_never_publishes_numeric_score(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["stale_after"] = (NOW - timedelta(seconds=1)).isoformat()
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("stale_product_identity", result["validation_errors"])

    def test_future_payload_as_of_never_publishes_numeric_score(self) -> None:
        payload = complete_payload()
        future = NOW + timedelta(days=2)
        payload["as_of"] = future.isoformat()
        for row in [payload["product_identity"], *payload["factors"].values()]:
            row["observed_at"] = (future - timedelta(minutes=1)).isoformat()
            row["stale_after"] = (future + timedelta(hours=1)).isoformat()
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("future_as_of", result["validation_errors"])

    def test_freshness_is_evaluated_against_now_not_only_payload_as_of(self) -> None:
        payload = complete_payload()
        result = entry_score.compile_entry_score(payload, now=NOW + timedelta(hours=2))
        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("stale_product_identity", result["validation_errors"])
        self.assertIn("stale_factor:technical", result["validation_errors"])

    def test_untraceable_product_identity_never_publishes_numeric_score(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["evidence_refs"] = []
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIn("missing_product_identity_evidence_refs", result["validation_errors"])

    def test_unknown_region_keeps_numeric_research_view_but_blocks_entry(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["region_eligible"] = None
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["entry_score_100"], 72.0)
        self.assertEqual(result["entry_permission_ceiling"], "BLOCK")
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["suggested_module_signals"][-1]["module"], "data_quality")
        self.assertTrue(result["suggested_module_signals"][-1]["hard_veto"])

    def test_explicit_region_ineligibility_maps_to_account_veto(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["region_eligible"] = False
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["entry_permission_ceiling"], "BLOCK")
        self.assertEqual(result["suggested_module_signals"][-1]["module"], "account")
        self.assertEqual(result["suggested_module_signals"][-1]["holding_directive"], "HOLD")

    def test_region_unknown_signal_fails_closed_in_decision_compiler(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["region_eligible"] = None
        score = entry_score.compile_entry_score(payload, now=NOW)
        compiled = decision_compiler.compile_decision({
            "schema_version": "decision_request.v2",
            "intent": "open",
            "has_position": False,
            "module_signals": [
                {
                    "module": module,
                    "max_action_level": "L3",
                    "position_multiplier": 1.0,
                    "hard_veto": False,
                    "evidence_refs": [f"EID-{module}"],
                    "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
                    "stale_after": (NOW + timedelta(hours=1)).isoformat(),
                }
                for module in ("risk_regime", "portfolio_risk_budget")
            ] + score["suggested_module_signals"],
            "decision_context": {
                "query_tier": "T2",
                "intent": "open",
                "has_position": False,
                "as_of": NOW.isoformat(),
                "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"],
            },
        }, now=NOW)
        self.assertEqual(compiled["contract_status"], "strict_pass")
        self.assertEqual(compiled["compiled_action"], "L0")
        self.assertEqual(compiled["entry_permission"], "BLOCK")
        self.assertEqual(compiled["final_position_multiplier"], 0.0)

    def test_cross_factor_dispersion_is_published_as_unresolved_conflict(self) -> None:
        payload = complete_payload()
        payload["factors"]["technical"]["score"] = 5.0
        payload["factors"]["macro"]["score"] = 1.0
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result["unresolved_conflict"])
        self.assertEqual(result["entry_permission_ceiling"], "BLOCK")
        self.assertEqual(result["suggested_module_signal"]["module"], "conflict_ledger")
        self.assertTrue(result["suggested_module_signal"]["hard_veto"])

    def test_score_ranges_are_bounded_and_finite(self) -> None:
        for bad in (-0.1, 5.1, float("inf"), float("nan"), True, int("1" + "0" * 400)):
            payload = complete_payload()
            payload["factors"]["technical"]["score"] = bad
            with self.subTest(bad=bad):
                result = entry_score.compile_entry_score(payload, now=NOW)
                self.assertFalse(result["ok"])
                self.assertIn("invalid_score:technical", result["validation_errors"])

    def test_cex_identity_rejects_non_spot_category_and_wallet_fields(self) -> None:
        cases = {
            "wrong_type": {"instrument_type": "SWAP"},
            "wrong_category": {"instrument_category": "1"},
            "wallet_mix": {
                "chain_id": "1",
                "token_contract": "0x0000000000000000000000000000000000000001",
                "provider": "okx_dex",
            },
            "wallet_underlying_mix": {"underlying_symbol": "MU.US"},
        }
        for name, updates in cases.items():
            payload = complete_payload()
            payload["product_identity"].update(updates)
            with self.subTest(name=name):
                result = entry_score.compile_entry_score(payload, now=NOW)
                self.assertFalse(result["ok"])
                self.assertIsNone(result["entry_score_100"])

    def test_cex_identity_rejects_unsupported_cex_channel(self) -> None:
        payload = complete_payload()
        payload["product_identity"]["channel"] = "cex_margin"

        result = entry_score.compile_entry_score(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn(
            "unsupported_product_identity_channel:cex_margin",
            result["validation_errors"],
        )

    def test_wallet_identity_rejects_cex_fields(self) -> None:
        payload = complete_payload()
        payload["product_identity"] = {
            "venue": "okx",
            "channel": "wallet_dex",
            "chain_id": "1",
            "token_contract": "0x0000000000000000000000000000000000000001",
            "provider": "okx_dex",
            "mapping_scope": "exact_chain_token",
            "state": "live",
            "mapping_verified": True,
            "region_eligible": True,
            "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
            "stale_after": (NOW + timedelta(hours=1)).isoformat(),
            "evidence_refs": ["EID-OKX-WALLET"],
            "instrument_id": "XMU-USDT",
        }
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("product_identity_channel_mix", result["validation_errors"])

    def test_wallet_identity_requires_underlying_symbol(self) -> None:
        payload = complete_payload()
        payload["product_identity"] = {
            "venue": "okx",
            "channel": "wallet_dex",
            "chain_id": "1",
            "token_contract": "0x0000000000000000000000000000000000000001",
            "provider": "okx_dex",
            "mapping_scope": "exact_chain_token",
            "state": "live",
            "mapping_verified": True,
            "region_eligible": True,
            "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
            "stale_after": (NOW + timedelta(hours=1)).isoformat(),
            "evidence_refs": ["EID-OKX-WALLET"],
        }

        result = entry_score.compile_entry_score(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertIsNone(result["entry_score_100"])
        self.assertIn("missing_product_identity_underlying_symbol", result["validation_errors"])

    def test_sensitive_fields_are_rejected_before_compilation(self) -> None:
        for key in (
            "api_key", "api.key", "api key", "secret_key", "passphrase",
            "access_token", "private_key", "token", "authorization", "cookie",
            "mnemonic", "seed-phrase", "bearer token", "OK-ACCESS-KEY",
            "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP", "ok_access_key", "ok_access_sign", "headers",
            "signature", "sign", "credential", "ｔｏｋｅｎ", "sеcret", "tοken",
        ):
            payload = complete_payload()
            payload[key] = "synthetic-test-value"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "sensitive field"):
                entry_score.compile_entry_score(payload, now=NOW)

    def test_high_confidence_secret_value_is_rejected_under_safe_key(self) -> None:
        payload = complete_payload()
        payload["note"] = "sk-" + "A" * 24
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            entry_score.compile_entry_score(payload, now=NOW)

    def test_embedded_secret_value_is_rejected_inside_factor_reason(self) -> None:
        payload = complete_payload()
        payload["factors"]["technical"]["reason"] = (
            "operator diagnostic contains " + "sk-" + "A" * 24
        )
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            entry_score.compile_entry_score(payload, now=NOW)

    def test_private_key_header_variants_are_rejected_after_nfkc(self) -> None:
        def pem_header(key_type: str, dash: str = "-") -> str:
            return dash * 5 + "BE" + "GIN " + key_type + " PRIV" + "ATE K" + "EY" + dash * 5

        for key_type in ("RSA", "EC", "OPENSSH", "DSA"):
            payload = complete_payload()
            payload["factors"]["technical"]["reason"] = (
                "diagnostic " + pem_header(key_type) + " tail"
            )
            with self.subTest(key_type=key_type), self.assertRaisesRegex(ValueError, "sensitive value"):
                entry_score.compile_entry_score(payload, now=NOW)

        payload = complete_payload()
        payload["factors"]["technical"]["reason"] = (
            "diagnostic " + pem_header("RSA", "－") + " tail"
        )
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            entry_score.compile_entry_score(payload, now=NOW)

    def test_public_token_contract_identifier_is_not_treated_as_a_secret(self) -> None:
        payload = complete_payload()
        payload["metadata"] = {
            "token_contract": "0x0000000000000000000000000000000000000001",
            "credential_scope": "read_only",
            "credentials_used": False,
        }
        result = entry_score.compile_entry_score(payload, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["product_identity"]["mapping_scope"], "exact_exchange_instrument_only")


if __name__ == "__main__":
    unittest.main()
