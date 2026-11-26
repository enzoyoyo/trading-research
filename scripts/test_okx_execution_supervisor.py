#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import okx_execution_supervisor  # noqa: E402
import okx_monitor_dashboard  # noqa: E402
import decision_compiler  # noqa: E402


NOW = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)


def iso(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def healthy_payload() -> dict:
    return {
        "schema_version": "okx_execution_snapshot.v1",
        "as_of": NOW.isoformat(),
        "venue": "okx",
        "channel": "cex_spot",
        "mode": "demo",
        "credential_scope": "read_only",
        "execution_permission": "paper",
        "no_order_execution": True,
        "instrument": {
            "inst_id": "XMU-USDT",
            "inst_type": "SPOT",
            "inst_category": "3",
            "mapping_scope": "exact_exchange_instrument_only",
            "mapping_verified": True,
            "region_eligible": True,
            "state": "live",
            "tick_size": 0.01,
            "lot_size": 0.000001,
            "min_size": 0.001,
        },
        "evidence_refs": ["EID-OKX-INST", "EID-OKX-STATE"],
        "connection": {"private_ws_connected": True},
        "feeds": {
            "market": {
                "status": "live",
                "source_ts": iso(10),
                "last_good_at": iso(10),
                "max_age_seconds": 60,
                "last_good_value": {"last": 100.0, "bid": 99.9, "ask": 100.1, "spread_bps": 20.0},
            },
            "account": {
                "status": "live",
                "source_ts": iso(15),
                "last_good_at": iso(15),
                "max_age_seconds": 90,
                "last_good_value": {"equity": 10000.0, "available": 9000.0},
            },
            "orders": {
                "status": "live",
                "source_ts": iso(8),
                "last_good_at": iso(8),
                "max_age_seconds": 90,
                "rest_baseline_complete": True,
                "last_good_value": {"open_order_count": 1},
            },
        },
        "strategy": {
            "strategy_id": "semi-shakeout-v1",
            "spec_version": "1.0.0",
            "status": "running",
            "heartbeat_at": iso(20),
            "max_heartbeat_age_seconds": 120,
            "stop_new_entries": False,
            "allowed_instruments": ["XMU-USDT"],
        },
        "venue_state": {
            "positions": [{"instrument_id": "XMU-USDT", "quantity": 1.5}],
            "open_orders": [{"order_id": "o-1", "instrument_id": "XMU-USDT", "state": "live", "quantity": 1.0}],
            "fills": [{
                "fill_id": "f-1", "order_id": "o-1", "instrument_id": "XMU-USDT",
                "side": "buy", "quantity": 0.5, "price": 99.5, "fill_ts": iso(5),
            }],
        },
        "local_state": {
            "positions": [{"instrument_id": "XMU-USDT", "quantity": 1.5}],
            "open_orders": [{"order_id": "o-1", "instrument_id": "XMU-USDT", "state": "live", "quantity": 1.0}],
            "fills": [{
                "fill_id": "f-1", "order_id": "o-1", "instrument_id": "XMU-USDT",
                "side": "buy", "quantity": 0.5, "price": 99.5, "fill_ts": iso(5),
            }],
        },
        "execution_results": [],
        "risk": {"account_hard_redline": False, "reason": ""},
        "policy": {"max_spread_bps": 50.0},
        "entry_score": {
            "schema_version": "entry_score_compilation.v1",
            "status": "complete",
            "entry_score_100": 72.0,
            "factor_coverage": 1.0,
            "confidence": "high",
            "unresolved_conflict": False,
            "entry_permission_ceiling": None,
            "factors": {
                "technical": {"score": 4.0, "contribution_points_100": 16.0},
                "capital_flow": {"score": 3.5, "contribution_points_100": 14.0},
                "sentiment": {"score": 3.0, "contribution_points_100": 12.0},
                "fundamentals": {"score": 4.5, "contribution_points_100": 22.5},
                "macro": {"score": 2.5, "contribution_points_100": 7.5},
            },
        },
    }


def healthy_wallet_payload() -> dict:
    payload = healthy_payload()
    instrument_id = "solana:synthetic-contract"
    payload["channel"] = "wallet_dex"
    payload["instrument"] = {
        "chain_id": "solana",
        "token_contract": "synthetic-contract",
        "provider": "xstocks",
        "underlying_symbol": "MU.US",
        "mapping_scope": "exact_chain_token",
        "mapping_verified": True,
        "region_eligible": True,
        "state": "live",
    }
    payload["strategy"]["allowed_instruments"] = [instrument_id]
    for state in (payload["venue_state"], payload["local_state"]):
        state["positions"][0]["instrument_id"] = instrument_id
        state["open_orders"][0]["instrument_id"] = instrument_id
        state["fills"][0]["instrument_id"] = instrument_id
    return payload


class OkxExecutionSupervisorTests(unittest.TestCase):
    def test_healthy_demo_snapshot_emits_existing_modules_only(self) -> None:
        result = okx_execution_supervisor.compile_supervision(healthy_payload(), now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["market_analysis_allowed"])
        self.assertTrue(result["new_entries_allowed"])
        self.assertFalse(result["pause_required"])
        self.assertEqual(result["position_reconciliation_status"], "matched")
        self.assertEqual(result["order_state_status"], "known")
        self.assertEqual(result["instrument"]["inst_id"], "XMU-USDT")
        self.assertTrue(result["instrument"]["mapping_verified"])
        self.assertEqual(result["module_signals"], [])
        self.assertEqual(result["credential_scope"], "read_only")
        self.assertEqual(result["execution_permission"], "paper")
        self.assertTrue(result["connection"]["private_ws_connected"])
        self.assertTrue(result["connection"]["rest_baseline_complete"])
        self.assertEqual(result["entry_score"]["entry_score_100"], 72.0)
        self.assertTrue(result["no_order_execution"])

    def test_demo_rest_polling_at_30_seconds_is_accepted_as_read_only_projection(self) -> None:
        payload = healthy_payload()
        payload["source_credential_scope"] = "read_trade"
        payload["snapshot_projection_read_only"] = True
        payload["connection"] = {
            "private_ws_connected": False,
            "private_transport": "rest_polling",
            "rest_polling_healthy": True,
            "poll_interval_seconds": 30,
        }

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["new_entries_allowed"])
        self.assertEqual(result["source_credential_scope"], "read_trade")
        self.assertTrue(result["snapshot_projection_read_only"])
        self.assertFalse(result["connection"]["private_ws_connected"])
        self.assertTrue(result["connection"]["rest_polling_healthy"])
        self.assertEqual(result["connection"]["poll_interval_seconds"], 30.0)
        self.assertIn("demo_rest_polling_instead_of_private_ws", result["warnings"])

    def test_live_mode_never_accepts_rest_polling_as_private_connection(self) -> None:
        payload = healthy_payload()
        payload["mode"] = "live"
        payload["execution_permission"] = "live_approved"
        payload["live_controls"] = {
            "approval_verified": True,
            "ip_allowlist_verified": True,
            "withdrawal_enabled": False,
            "credentials_rotated": True,
        }
        payload["connection"] = {
            "private_ws_connected": False,
            "private_transport": "rest_polling",
            "rest_polling_healthy": True,
            "poll_interval_seconds": 30,
        }

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertIn("private_ws_disconnected", result["blockers"])

    def test_read_trade_source_requires_explicit_read_only_projection(self) -> None:
        payload = healthy_payload()
        payload["source_credential_scope"] = "read_trade"

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertIn("read_trade_source_requires_read_only_projection", result["blockers"])

    def test_stale_market_retains_last_good_value_and_blocks_new_entries(self) -> None:
        payload = healthy_payload()
        payload["feeds"]["market"]["source_ts"] = iso(300)
        payload["feeds"]["market"]["last_good_at"] = iso(300)
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["new_entries_allowed"])
        self.assertTrue(result["pause_required"])
        self.assertTrue(result["freshness"]["market"]["stale"])
        self.assertEqual(result["freshness"]["market"]["last_good_value"]["last"], 100.0)
        self.assertNotEqual(result["freshness"]["market"]["last_good_value"]["last"], 0)
        data_signal = next(row for row in result["module_signals"] if row["module"] == "data_quality")
        self.assertTrue(data_signal["hard_veto"])
        self.assertTrue(data_signal["tighten_only"])
        self.assertEqual(data_signal["holding_directive"], "HOLD")

    def test_position_mismatch_requires_pause_but_does_not_claim_pause_succeeded(self) -> None:
        payload = healthy_payload()
        payload["venue_state"]["positions"][0]["quantity"] = 1.6
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["position_reconciliation_status"], "drift")
        self.assertTrue(result["pause_required"])
        self.assertFalse(result["pause_effective"])
        self.assertIn("position_mismatch:XMU-USDT", result["blockers"])
        conflict = next(row for row in result["module_signals"] if row["module"] == "conflict_ledger")
        self.assertTrue(conflict["hard_veto"])
        self.assertEqual(conflict["holding_directive"], "HOLD")

    def test_position_drift_of_exactly_one_lot_is_not_tolerated_by_default(self) -> None:
        payload = healthy_payload()
        payload["venue_state"]["positions"][0]["quantity"] = (
            payload["local_state"]["positions"][0]["quantity"]
            + payload["instrument"]["lot_size"]
        )
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["position_reconciliation_status"], "drift")
        self.assertIn("position_mismatch:XMU-USDT", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_position_tolerance_cannot_hide_exactly_one_lot_drift(self) -> None:
        payload = healthy_payload()
        lot_size = payload["instrument"]["lot_size"]
        payload["venue_state"]["positions"][0]["quantity"] = (
            payload["local_state"]["positions"][0]["quantity"] + lot_size
        )
        payload["policy"]["position_tolerance"] = lot_size

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertIn("position_tolerance_not_below_lot_size", result["blockers"])
        self.assertEqual(result["position_reconciliation_status"], "drift")
        self.assertIn("position_mismatch:XMU-USDT", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_matching_negative_spot_positions_are_invalid_not_reconciled(self) -> None:
        payload = healthy_payload()
        for state in (payload["venue_state"], payload["local_state"]):
            state["positions"][0]["quantity"] = -1.5

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["new_entries_allowed"])
        self.assertEqual(result["position_reconciliation_status"], "pending")
        self.assertEqual(result["reconciliation"]["status"], "unknown")
        self.assertIn("invalid_position_row:venue:0", result["reconciliation"]["validation_errors"])
        self.assertIn("invalid_position_row:local:0", result["reconciliation"]["validation_errors"])
        self.assertIn("reconciliation_state_invalid", result["blockers"])

    def test_matching_negative_zero_positions_are_invalid_for_cex_and_wallet(self) -> None:
        for channel, factory in (
            ("cex_spot", healthy_payload),
            ("wallet_dex", healthy_wallet_payload),
        ):
            baseline = okx_execution_supervisor.compile_supervision(factory(), now=NOW)
            self.assertTrue(baseline["new_entries_allowed"], baseline["blockers"])
            for representation, quantity in (
                ("float", -0.0),
                ("string", "-0"),
            ):
                with self.subTest(channel=channel, representation=representation):
                    payload = factory()
                    for state in (payload["venue_state"], payload["local_state"]):
                        state["positions"][0]["quantity"] = quantity

                    result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

                    self.assertFalse(result["ok"])
                    self.assertEqual(result["status"], "blocked")
                    self.assertFalse(result["new_entries_allowed"])
                    self.assertEqual(result["position_reconciliation_status"], "pending")
                    self.assertEqual(result["reconciliation"]["status"], "unknown")
                    self.assertIn("invalid_position_row:venue:0", result["reconciliation"]["validation_errors"])
                    self.assertIn("invalid_position_row:local:0", result["reconciliation"]["validation_errors"])
                    self.assertIn("reconciliation_state_invalid", result["blockers"])

    def test_raw_json_numeric_negative_zero_is_preserved_and_rejected(self) -> None:
        marker = "__LEXICAL_NEGATIVE_ZERO__"
        for channel, factory in (
            ("cex_spot", healthy_payload),
            ("wallet_dex", healthy_wallet_payload),
        ):
            with self.subTest(channel=channel):
                payload = factory()
                for state in (payload["venue_state"], payload["local_state"]):
                    state["positions"][0]["quantity"] = marker
                text = json.dumps(payload)
                self.assertEqual(text.count(json.dumps(marker)), 2)
                text = text.replace(json.dumps(marker), "-0")
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = Path(temp_dir) / "snapshot.json"
                    path.write_text(text, encoding="utf-8")
                    parsed = okx_execution_supervisor._load_payload(str(path))

                quantities = [
                    parsed[state]["positions"][0]["quantity"]
                    for state in ("venue_state", "local_state")
                ]
                self.assertTrue(all(math.copysign(1.0, float(value)) < 0 for value in quantities))

                result = okx_execution_supervisor.compile_supervision(parsed, now=NOW)

                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["new_entries_allowed"])
                self.assertEqual(result["reconciliation"]["status"], "unknown")
                self.assertIn("invalid_position_row:venue:0", result["reconciliation"]["validation_errors"])
                self.assertIn("invalid_position_row:local:0", result["reconciliation"]["validation_errors"])

    def test_raw_json_positive_zero_remains_an_integer_and_valid_position(self) -> None:
        marker = "__LEXICAL_POSITIVE_ZERO__"
        for channel, factory in (
            ("cex_spot", healthy_payload),
            ("wallet_dex", healthy_wallet_payload),
        ):
            with self.subTest(channel=channel):
                payload = factory()
                for state in (payload["venue_state"], payload["local_state"]):
                    state["positions"][0]["quantity"] = marker
                text = json.dumps(payload).replace(json.dumps(marker), "0")
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = Path(temp_dir) / "snapshot.json"
                    path.write_text(text, encoding="utf-8")
                    parsed = okx_execution_supervisor._load_payload(str(path))

                quantities = [
                    parsed[state]["positions"][0]["quantity"]
                    for state in ("venue_state", "local_state")
                ]
                self.assertTrue(all(type(value) is int and value == 0 for value in quantities))

                result = okx_execution_supervisor.compile_supervision(parsed, now=NOW)

                self.assertTrue(result["ok"], result["blockers"])
                self.assertTrue(result["new_entries_allowed"])
                self.assertEqual(result["reconciliation"]["status"], "matched")

    def test_huge_integer_positions_fail_closed_without_traceback_or_echo(self) -> None:
        huge = int("1" + "0" * 400)
        for channel, factory in (
            ("cex_spot", healthy_payload),
            ("wallet_dex", healthy_wallet_payload),
        ):
            with self.subTest(channel=channel):
                payload = factory()
                payload["venue_state"]["positions"][0]["quantity"] = huge

                result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

                self.assertFalse(result["ok"])
                self.assertFalse(result["new_entries_allowed"])
                self.assertIn(
                    "invalid_position_row:venue:0",
                    result["reconciliation"]["validation_errors"],
                )
                self.assertNotIn(str(huge), json.dumps(result))

        payload = healthy_payload()
        payload["venue_state"]["positions"][0]["quantity"] = huge
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("okx_execution_supervisor.py")), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "")
        cli_result = json.loads(completed.stdout)
        self.assertFalse(cli_result["ok"])
        self.assertTrue(cli_result["no_order_execution"])
        self.assertNotIn(str(huge), completed.stdout)

    def test_huge_integer_last_good_value_is_an_invalid_feed_not_a_crash(self) -> None:
        payload = healthy_payload()
        payload["feeds"]["market"]["last_good_value"]["last"] = int("1" + "0" * 400)

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertFalse(result["new_entries_allowed"])
        self.assertIn("feed_invalid_last_good_value:market:last", result["blockers"])

    def test_matching_positive_zero_positions_remain_valid_for_cex_and_wallet(self) -> None:
        for channel, factory in (
            ("cex_spot", healthy_payload),
            ("wallet_dex", healthy_wallet_payload),
        ):
            with self.subTest(channel=channel):
                payload = factory()
                for state in (payload["venue_state"], payload["local_state"]):
                    state["positions"][0]["quantity"] = 0.0

                result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

                self.assertTrue(result["ok"], result["blockers"])
                self.assertEqual(result["status"], "healthy")
                self.assertTrue(result["new_entries_allowed"])
                self.assertEqual(result["position_reconciliation_status"], "matched")

    def test_matching_duplicate_spot_position_rows_are_invalid_not_aggregated(self) -> None:
        payload = healthy_payload()
        for state in (payload["venue_state"], payload["local_state"]):
            state["positions"].append({"instrument_id": "XMU-USDT", "quantity": 0.5})

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertFalse(result["new_entries_allowed"])
        self.assertEqual(result["position_reconciliation_status"], "pending")
        self.assertEqual(result["reconciliation"]["status"], "unknown")
        self.assertIn(
            "duplicate_position_instrument:venue:XMU-USDT",
            result["reconciliation"]["validation_errors"],
        )
        self.assertIn(
            "duplicate_position_instrument:local:XMU-USDT",
            result["reconciliation"]["validation_errors"],
        )

    def test_same_order_id_detail_drift_is_not_treated_as_matched(self) -> None:
        payload = healthy_payload()
        payload["venue_state"]["open_orders"][0].update({
            "instrument_id": "XSKHY-USDT",
            "state": "partially_filled",
            "quantity": 2.0,
        })
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["order_state_status"], "unknown")
        self.assertIn("open_order_detail_mismatch", result["blockers"])
        self.assertEqual(result["reconciliation"]["order_detail_mismatches"][0]["order_id"], "o-1")
        self.assertTrue(result["pause_required"])

    def test_same_fill_id_detail_drift_is_not_treated_as_matched(self) -> None:
        payload = healthy_payload()
        payload["venue_state"]["fills"][0]["order_id"] = "o-other"
        payload["venue_state"]["fills"][0]["quantity"] = 0.75
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["order_state_status"], "unknown")
        self.assertIn("fill_detail_mismatch", result["blockers"])
        self.assertEqual(result["reconciliation"]["fill_detail_mismatches"][0]["fill_id"], "f-1")
        self.assertFalse(result["new_entries_allowed"])

    def test_strategy_heartbeat_failure_is_fail_closed(self) -> None:
        payload = healthy_payload()
        payload["strategy"]["heartbeat_at"] = iso(600)
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("strategy_heartbeat_stale", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_orders_websocket_without_rest_baseline_is_unknown(self) -> None:
        payload = healthy_payload()
        payload["feeds"]["orders"]["rest_baseline_complete"] = False
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("orders_rest_baseline_missing", result["blockers"])
        self.assertEqual(result["order_state_status"], "unknown")
        self.assertFalse(result["connection"]["rest_baseline_complete"])

    def test_batch_item_failure_is_not_hidden_by_top_level_success(self) -> None:
        payload = healthy_payload()
        payload["execution_results"] = [
            {"ordId": "o-2", "sCode": "0", "sMsg": ""},
            {"ordId": "", "sCode": "51008", "sMsg": "insufficient balance"},
        ]
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("execution_partial_failure", result["blockers"])
        self.assertEqual(result["order_state_status"], "partial_failure")
        self.assertEqual(result["execution_result_summary"]["failed_items"], 1)

    def test_wallet_and_cex_identity_fields_cannot_be_mixed(self) -> None:
        payload = healthy_payload()
        payload["channel"] = "wallet_dex"
        payload["instrument"].update({"chain_id": "solana", "token_contract": "synthetic-contract", "provider": "xstocks"})
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("wallet_identity_contains_cex_inst_id", result["blockers"])
        self.assertFalse(result["market_analysis_allowed"])

    def test_cex_identity_rejects_wallet_only_underlying_symbol(self) -> None:
        payload = healthy_payload()
        payload["instrument"]["underlying_symbol"] = "MU.US"
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("cex_identity_contains_wallet_fields", result["blockers"])
        self.assertFalse(result["market_analysis_allowed"])
        self.assertFalse(result["new_entries_allowed"])

    def test_public_mode_allows_market_analysis_but_never_claims_execution_ready(self) -> None:
        payload = healthy_payload()
        payload["mode"] = "public"
        payload["credential_scope"] = "none"
        payload["execution_permission"] = "disabled"
        payload["instrument"]["region_eligible"] = None
        payload["feeds"] = {"market": payload["feeds"]["market"]}
        payload.pop("venue_state")
        payload.pop("local_state")
        payload.pop("connection")
        payload.pop("strategy")
        payload.pop("risk")
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertTrue(result["market_analysis_allowed"])
        self.assertFalse(result["new_entries_allowed"])
        self.assertTrue(result["analysis_only"])
        self.assertIn("private_state_unavailable:public_mode", result["blockers"])

    def test_public_supervision_is_publishable_by_read_only_dashboard(self) -> None:
        payload = healthy_payload()
        payload["mode"] = "public"
        payload["credential_scope"] = "none"
        payload["execution_permission"] = "disabled"
        payload["instrument"]["region_eligible"] = None
        payload["feeds"] = {"market": payload["feeds"]["market"]}
        for key in ("venue_state", "local_state", "connection", "strategy", "risk"):
            payload.pop(key)

        supervision = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        with tempfile.TemporaryDirectory() as temp_dir:
            published = okx_monitor_dashboard.publish_dashboard(supervision, Path(temp_dir))

        self.assertTrue(published["ok"])
        self.assertFalse(supervision["new_entries_allowed"])
        self.assertIsNone(supervision["connection"]["private_ws_connected"])

    def test_healthy_demo_supervision_is_publishable_by_read_only_dashboard(self) -> None:
        supervision = okx_execution_supervisor.compile_supervision(healthy_payload(), now=NOW)
        with tempfile.TemporaryDirectory() as temp_dir:
            published = okx_monitor_dashboard.publish_dashboard(supervision, Path(temp_dir))

        self.assertTrue(published["ok"])
        self.assertTrue(supervision["new_entries_allowed"])

    def test_demo_rest_polling_projection_is_publishable_by_dashboard(self) -> None:
        payload = healthy_payload()
        payload["source_credential_scope"] = "read_trade"
        payload["snapshot_projection_read_only"] = True
        payload["connection"] = {
            "private_ws_connected": False,
            "private_transport": "rest_polling",
            "rest_polling_healthy": True,
            "poll_interval_seconds": 30,
        }
        supervision = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        with tempfile.TemporaryDirectory() as temp_dir:
            published = okx_monitor_dashboard.publish_dashboard(supervision, Path(temp_dir))
            html = (Path(temp_dir) / "index.html").read_text(encoding="utf-8")

        self.assertTrue(supervision["new_entries_allowed"])
        self.assertTrue(published["ok"])
        self.assertIn("REST 轮询", html)

    def test_account_hard_redline_emits_market_risk_veto(self) -> None:
        payload = healthy_payload()
        payload["risk"] = {"account_hard_redline": True, "reason": "margin ratio below policy floor"}
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        signal = next(row for row in result["module_signals"] if row["module"] == "account")
        self.assertTrue(signal["hard_veto"])
        self.assertEqual(signal["holding_directive"], "EXIT")
        self.assertFalse(result["new_entries_allowed"])

    def test_sensitive_fields_are_rejected_recursively(self) -> None:
        for key in (
            "api_key", "api.key", "api key", "secret_key", "passphrase",
            "access_token", "private_key", "token", "authorization", "cookie",
            "mnemonic", "seed-phrase", "bearer token", "OK-ACCESS-KEY",
            "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP", "ok_access_key", "ok_access_sign", "headers",
            "signature", "sign", "credential", "ｔｏｋｅｎ", "sеcret", "tοken",
        ):
            payload = healthy_payload()
            payload["nested"] = {key: "synthetic-test-value"}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "sensitive field"):
                okx_execution_supervisor.compile_supervision(payload, now=NOW)

    def test_high_confidence_secret_value_is_rejected_under_safe_key(self) -> None:
        payload = healthy_payload()
        payload["note"] = "sk-" + "A" * 24
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            okx_execution_supervisor.compile_supervision(payload, now=NOW)

    def test_embedded_secret_value_is_rejected_inside_risk_reason(self) -> None:
        payload = healthy_payload()
        payload["risk"]["reason"] = "diagnostic contains " + "sk-" + "A" * 24
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            okx_execution_supervisor.compile_supervision(payload, now=NOW)

    def test_private_key_header_variants_are_rejected_after_nfkc(self) -> None:
        def pem_header(key_type: str, dash: str = "-") -> str:
            return dash * 5 + "BE" + "GIN " + key_type + " PRIV" + "ATE K" + "EY" + dash * 5

        for key_type in ("RSA", "EC", "OPENSSH", "DSA"):
            payload = healthy_payload()
            payload["risk"]["reason"] = "diag " + pem_header(key_type) + " tail"
            with self.subTest(key_type=key_type), self.assertRaisesRegex(ValueError, "sensitive value"):
                okx_execution_supervisor.compile_supervision(payload, now=NOW)

        payload = healthy_payload()
        payload["risk"]["reason"] = "diag " + pem_header("RSA", "－") + " tail"
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            okx_execution_supervisor.compile_supervision(payload, now=NOW)

    def test_last_good_value_rejects_wrong_types_without_echoing_values(self) -> None:
        marker = "SYNTHETIC_SECRET_VALUE_DO_NOT_USE"
        payload = healthy_payload()
        payload["feeds"]["market"]["last_good_value"]["last"] = marker
        payload["feeds"]["account"]["last_good_value"]["currency"] = marker

        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)

        self.assertFalse(result["ok"])
        self.assertIn("feed_invalid_last_good_value:market:last", result["blockers"])
        self.assertIn("feed_invalid_last_good_value:account:currency", result["blockers"])
        self.assertNotIn(marker, str(result))

    def test_last_good_values_are_projected_through_feed_allowlists(self) -> None:
        payload = healthy_payload()
        payload["feeds"]["market"]["last_good_value"]["internal_note"] = "must-not-persist"
        payload["feeds"]["account"]["last_good_value"]["account_id"] = "must-not-persist"
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertNotIn("internal_note", result["freshness"]["market"]["last_good_value"])
        self.assertNotIn("account_id", result["freshness"]["account"]["last_good_value"])
        self.assertEqual(result["freshness"]["market"]["last_good_value"]["last"], 100.0)

    def test_public_token_contract_identifier_is_not_treated_as_a_secret(self) -> None:
        payload = healthy_payload()
        payload["metadata"] = {
            "token_contract": "0x0000000000000000000000000000000000000001",
            "credential_scope": "read_only",
            "credentials_used": False,
        }
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertTrue(result["ok"])

    def test_missing_no_order_execution_contract_blocks(self) -> None:
        payload = healthy_payload()
        payload.pop("no_order_execution")
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("no_order_execution_required", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_future_as_of_feed_and_heartbeat_are_fail_closed(self) -> None:
        payload = healthy_payload()
        payload["as_of"] = (NOW + timedelta(days=1)).isoformat()
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("future_as_of", result["blockers"])

        payload = healthy_payload()
        payload["feeds"]["market"]["source_ts"] = (NOW + timedelta(hours=1)).isoformat()
        payload["feeds"]["market"]["last_good_at"] = (NOW + timedelta(hours=1)).isoformat()
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("feed_future_timestamp:market", result["blockers"])
        self.assertTrue(result["freshness"]["market"]["stale"])

        payload = healthy_payload()
        payload["strategy"]["heartbeat_at"] = (NOW + timedelta(hours=1)).isoformat()
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("strategy_heartbeat_future", result["blockers"])

    def test_mapping_verified_requires_strict_boolean(self) -> None:
        payload = healthy_payload()
        payload["instrument"]["mapping_verified"] = 1
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("underlying_mapping_unverified", result["blockers"])

    def test_documented_fill_mismatch_is_detected(self) -> None:
        payload = healthy_payload()
        venue_fill = copy.deepcopy(payload["venue_state"]["fills"][0])
        local_fill = copy.deepcopy(payload["local_state"]["fills"][0])
        venue_fill["fill_id"] = "venue-only"
        local_fill["fill_id"] = "local-only"
        payload["venue_state"]["fills"] = [venue_fill]
        payload["local_state"]["fills"] = [local_fill]
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["order_state_status"], "unknown")
        self.assertIn("fill_mismatch", result["blockers"])

    def test_malformed_position_state_is_pending_not_matched(self) -> None:
        payload = healthy_payload()
        payload["venue_state"]["positions"] = [{"instrument_id": "XMU-USDT", "quantity": "not-a-number"}]
        payload["local_state"]["positions"] = []
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["position_reconciliation_status"], "pending")
        self.assertIn("reconciliation_state_invalid", result["blockers"])

    def test_unknown_open_order_item_blocks_even_when_ids_match(self) -> None:
        payload = healthy_payload()
        unknown = {"order_id": "o-unknown", "instrument_id": "XMU-USDT", "state": "unknown", "quantity": 1.0}
        payload["venue_state"]["open_orders"] = [copy.deepcopy(unknown)]
        payload["local_state"]["open_orders"] = [copy.deepcopy(unknown)]
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["order_state_status"], "unknown")
        self.assertIn("unknown_open_order_state", result["blockers"])

    def test_string_false_does_not_claim_pause_is_effective(self) -> None:
        payload = healthy_payload()
        payload["feeds"]["market"]["source_ts"] = iso(300)
        payload["strategy"]["stop_new_entries"] = "false"
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertTrue(result["pause_required"])
        self.assertFalse(result["pause_effective"])
        self.assertIn("invalid_strategy_stop_new_entries", result["blockers"])

    def test_active_stop_new_entries_is_effective_and_blocks(self) -> None:
        payload = healthy_payload()
        payload["strategy"]["stop_new_entries"] = True
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("strategy_stop_new_entries_active", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])
        self.assertTrue(result["pause_required"])
        self.assertTrue(result["pause_effective"])

    def test_disconnected_private_ws_blocks(self) -> None:
        payload = healthy_payload()
        payload["connection"]["private_ws_connected"] = False
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("private_ws_disconnected", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_read_only_scope_never_reports_new_entries_allowed(self) -> None:
        payload = healthy_payload()
        payload["mode"] = "read_only"
        payload["execution_permission"] = "disabled"
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result["analysis_only"])
        self.assertFalse(result["new_entries_allowed"])

    def test_live_mode_requires_external_safety_attestations(self) -> None:
        payload = healthy_payload()
        payload["mode"] = "live"
        payload["execution_permission"] = "live_approved"
        payload["live_controls"] = {
            "approval_verified": True,
            "ip_allowlist_verified": True,
            "withdrawal_enabled": False,
            "credentials_rotated": True,
        }
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result["new_entries_allowed"])

        payload["live_controls"]["withdrawal_enabled"] = True
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertIn("live_withdrawal_must_be_disabled", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_canonical_output_fields_are_present_without_private_aliases(self) -> None:
        result = okx_execution_supervisor.compile_supervision(healthy_payload(), now=NOW)
        self.assertIn("pause_required", result)
        self.assertIn("position_reconciliation_status", result)
        self.assertIn("order_state_status", result)
        self.assertNotIn("pause_new_entries_required", result)
        self.assertNotIn("order_state", result)

    def test_invalid_entry_score_projection_is_explicitly_unavailable(self) -> None:
        payload = healthy_payload()
        payload["entry_score"]["entry_score_100"] = 101
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(result["entry_score"]["status"], "unavailable")
        self.assertIn("entry_score_projection_invalid", result["warnings"])
        self.assertIn("entry_score_projection_invalid", result["blockers"])
        self.assertFalse(result["new_entries_allowed"])

    def test_unhashable_entry_score_ceiling_fails_closed_without_crashing(self) -> None:
        payload = healthy_payload()
        payload["entry_score"]["entry_permission_ceiling"] = ["BLOCK"]
        result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertFalse(result["new_entries_allowed"])
        self.assertIn("entry_score_projection_invalid", result["blockers"])

    def test_demo_and_live_require_actionable_complete_entry_score(self) -> None:
        cases: list[tuple[str, dict, str]] = []

        missing = healthy_payload()
        missing.pop("entry_score")
        cases.append(("missing", missing, "entry_score_not_ready:unavailable"))

        insufficient = healthy_payload()
        insufficient["entry_score"].update({
            "status": "insufficient_data", "entry_score_100": None,
            "factor_coverage": 0.0, "confidence": "low",
            "entry_permission_ceiling": "BLOCK", "factors": {},
        })
        cases.append(("insufficient", insufficient, "entry_score_not_ready:insufficient_data"))

        blocked = healthy_payload()
        blocked["entry_score"]["entry_permission_ceiling"] = "BLOCK"
        cases.append(("blocked", blocked, "entry_score_entry_blocked"))

        conflict = healthy_payload()
        conflict["entry_score"]["unresolved_conflict"] = True
        conflict["entry_score"]["entry_permission_ceiling"] = "BLOCK"
        cases.append(("conflict", conflict, "entry_score_unresolved_conflict"))

        for name, payload, expected_blocker in cases:
            with self.subTest(name=name):
                result = okx_execution_supervisor.compile_supervision(payload, now=NOW)
                self.assertIn(expected_blocker, result["blockers"])
                self.assertFalse(result["new_entries_allowed"])
                self.assertTrue(result["pause_required"])

    def test_input_is_not_mutated(self) -> None:
        payload = healthy_payload()
        original = copy.deepcopy(payload)
        okx_execution_supervisor.compile_supervision(payload, now=NOW)
        self.assertEqual(payload, original)

    def test_healthy_supervisor_emits_no_action_authorizing_signal(self) -> None:
        supervision = okx_execution_supervisor.compile_supervision(healthy_payload(), now=NOW)
        self.assertEqual(supervision["module_signals"], [])
        self.assertTrue(supervision["new_entries_allowed"])
        self.assertTrue(supervision["no_order_execution"])

    def test_stale_supervision_compiles_to_epistemic_entry_block(self) -> None:
        payload = healthy_payload()
        payload["feeds"]["market"]["source_ts"] = iso(300)
        supervision = okx_execution_supervisor.compile_supervision(payload, now=NOW)
        modules = [row["module"] for row in supervision["module_signals"]]
        baseline_signals = [
            {
                "module": module,
                "max_action_level": "L3",
                "position_multiplier": 1.0,
                "hard_veto": False,
                "evidence_refs": [f"EID-{module}"],
                "observed_at": iso(5),
                "stale_after": (NOW + timedelta(minutes=5)).isoformat(),
            }
            for module in ("risk_regime", "portfolio_risk_budget")
        ]
        compiled = decision_compiler.compile_payload({
            "schema_version": "decision_request.v2",
            "decision_context": {
                "query_tier": "T2",
                "intent": "open",
                "has_position": False,
                "as_of": NOW.isoformat(),
                "required_modules": ["risk_regime", "portfolio_risk_budget", *modules],
            },
            "module_signals": baseline_signals + supervision["module_signals"],
        }, now=NOW)
        self.assertTrue(compiled["ok"])
        self.assertEqual(compiled["entry_permission"], "BLOCK")
        self.assertTrue(compiled["epistemic_veto"])
        self.assertEqual(compiled["final_position_multiplier"], 0.0)


if __name__ == "__main__":
    unittest.main()
