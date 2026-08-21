#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import okx_monitor_dashboard  # noqa: E402


class OkxMonitorDashboardTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "schema_version": "okx_execution_supervision.v1",
            "as_of": "2026-07-27T16:00:00+00:00",
            "status": "blocked",
            "venue": "okx",
            "channel": "cex_spot",
            "mode": "demo",
            "credential_scope": "read_only",
            "execution_permission": "paper",
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
            "connection": {"private_ws_connected": True, "rest_baseline_complete": True},
            "freshness": {
                "market": {
                    "status": "stale",
                    "source_ts": "2026-07-27T15:55:00+00:00",
                    "last_good_value": {"last": 100.0, "bid": 99.9, "ask": 100.1},
                }
            },
            "strategy": {"status": "stopped", "stop_new_entries": False},
            "order_state_status": "unknown",
            "position_reconciliation_status": "drift",
            "reconciliation": {"status": "mismatch"},
            "pause_required": True,
            "pause_effective": False,
            "new_entries_allowed": False,
            "analysis_only": False,
            "market_analysis_allowed": True,
            "entry_score": {
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
            "blockers": ["strategy_not_running:stopped", "position_mismatch:XMU-USDT"],
            "no_order_execution": True,
        }

    def ready_demo_payload(self) -> dict:
        payload = self.payload()
        payload.update({
            "status": "healthy",
            "pause_required": False,
            "pause_effective": False,
            "new_entries_allowed": True,
            "position_reconciliation_status": "matched",
            "order_state_status": "known",
            "blockers": [],
        })
        payload["strategy"] = {"status": "running", "stop_new_entries": False}
        payload["freshness"] = {
            "market": {"status": "live", "stale": False},
            "account": {"status": "live", "stale": False},
            "orders": {"status": "live", "stale": False, "rest_baseline_complete": True},
        }
        return payload

    def test_html_is_local_read_only_mobile_and_refreshes_every_30_seconds(self) -> None:
        html = okx_monitor_dashboard.render_dashboard_html()
        lower = html.lower()
        self.assertIn('name="viewport"', lower)
        self.assertIn('data-refresh-ms="30000"', lower)
        self.assertIn("setinterval(loadsnapshot, refresh_ms)", lower)
        self.assertIn("snapshot.pause_required", lower)
        self.assertIn("snapshot.order_state_status", lower)
        self.assertIn("snapshot.position_reconciliation_status", lower)
        self.assertIn("snapshot.entry_score", lower)
        self.assertIn("snapshot.connection", lower)
        self.assertIn('["因子", "评分", "贡献分", "用途"]', html)
        self.assertIn("pause_effective=", lower)
        self.assertIn("fetch(snapshot_url", lower)
        self.assertIn("content-security-policy", lower)
        self.assertNotIn("pause_new_entries_required", lower)
        self.assertNotIn("<button", lower)
        self.assertNotIn("<form", lower)
        self.assertNotIn("contenteditable", lower)
        self.assertNotIn("http://", lower)
        self.assertNotIn("https://", lower)
        self.assertNotIn("websocket", lower)

    def test_html_follows_warm_editorial_no_card_design(self) -> None:
        html = okx_monitor_dashboard.render_dashboard_html().lower()
        self.assertIn("#faf8f3", html)
        self.assertIn("#1a1a1a", html)
        self.assertNotIn("#2563eb", html)
        self.assertNotIn("linear-gradient", html)
        self.assertNotIn("box-shadow", html)
        self.assertIn("font-family: georgia", html)
        self.assertIn("@media (max-width: 720px)", html)
        self.assertIn("交易场所", html)
        self.assertIn("产品通道", html)
        self.assertIn("运行模式", html)
        self.assertIn("交易标的", html)
        self.assertNotIn(">venue<", html)

    def test_publish_writes_html_and_sanitized_snapshot_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dashboard"
            result = okx_monitor_dashboard.publish_dashboard(self.payload(), output_dir)
            self.assertTrue(result["ok"])
            self.assertEqual(result["refresh_seconds"], 30)
            self.assertTrue((output_dir / "index.html").exists())
            saved = json.loads((output_dir / "supervision.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["freshness"]["market"]["last_good_value"]["last"], 100.0)
            self.assertTrue(saved["no_order_execution"])

    def test_publish_projects_schema_and_drops_unknown_values(self) -> None:
        marker = "UNTRUSTED_VALUE_DO_NOT_PERSIST"
        payload = self.payload()
        payload["operator_note"] = marker
        payload["freshness"]["market"]["unknown_metadata"] = marker
        payload["entry_score"]["factors"]["technical"]["comment"] = marker

        with tempfile.TemporaryDirectory() as temp_dir:
            okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))
            saved_text = (Path(temp_dir) / "supervision.json").read_text(encoding="utf-8")
            saved = json.loads(saved_text)

        self.assertNotIn(marker, saved_text)
        self.assertNotIn("operator_note", saved)
        self.assertNotIn("unknown_metadata", saved["freshness"]["market"])
        self.assertNotIn("comment", saved["entry_score"]["factors"]["technical"])

    def test_publish_rejects_wrong_type_in_allowed_last_good_field(self) -> None:
        payload = self.payload()
        payload["freshness"]["market"]["last_good_value"]["last"] = (
            "SYNTHETIC_SECRET_VALUE_DO_NOT_USE"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "last_good_value.last"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_publish_rejects_huge_integer_without_overflow_or_output(self) -> None:
        payload = self.payload()
        payload["freshness"]["market"]["last_good_value"]["last"] = int("1" + "0" * 400)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "dashboard"
            with self.assertRaisesRegex(ValueError, "last_good_value.last"):
                okx_monitor_dashboard.publish_dashboard(payload, output_dir)
            self.assertFalse((output_dir / "supervision.json").exists())
            self.assertFalse((output_dir / "index.html").exists())

    def test_cli_reports_huge_integer_as_structured_no_order_error(self) -> None:
        payload = self.payload()
        payload["freshness"]["market"]["last_good_value"]["last"] = int("1" + "0" * 400)

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "snapshot.json"
            output_dir = Path(temp_dir) / "dashboard"
            snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("okx_monitor_dashboard.py")),
                    "--snapshot",
                    str(snapshot_path),
                    "--output-dir",
                    str(output_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            self.assertFalse(result["ok"])
            self.assertTrue(result["no_order_execution"])
            self.assertFalse((output_dir / "supervision.json").exists())
            self.assertFalse((output_dir / "index.html").exists())

    def test_sensitive_fields_are_rejected(self) -> None:
        for key in (
            "api_key", "api.key", "api key", "secret_key", "passphrase",
            "access_token", "private_key", "token", "authorization", "cookie",
            "mnemonic", "seed-phrase", "bearer token", "OK-ACCESS-KEY",
            "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP", "ok_access_key", "ok_access_sign", "headers",
            "signature", "sign", "credential", "ｔｏｋｅｎ", "sеcret", "tοken",
        ):
            payload = self.payload()
            payload["nested"] = {key: "synthetic-test-value"}
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(ValueError, "sensitive field"):
                    okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_high_confidence_secret_value_is_rejected_under_safe_key(self) -> None:
        payload = self.payload()
        payload["note"] = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "sensitive value"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_embedded_secret_values_are_rejected_inside_blockers(self) -> None:
        values = (
            "diag:" + "sk-" + "A" * 24,
            "diag:" + "AKIA" + "B" * 16,
        )
        for value in values:
            payload = self.payload()
            payload["blockers"] = [value]
            with self.subTest(value_type=value[:4]), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(ValueError, "sensitive value"):
                    okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_private_key_header_variants_are_rejected_after_nfkc(self) -> None:
        def pem_header(key_type: str, dash: str = "-") -> str:
            return dash * 5 + "BE" + "GIN " + key_type + " PRIV" + "ATE K" + "EY" + dash * 5

        for key_type, dash in (("RSA", "-"), ("EC", "-"), ("OPENSSH", "-"), ("RSA", "－")):
            payload = self.payload()
            value = "diag " + pem_header(key_type, dash) + " tail"
            payload["blockers"] = [value]
            with self.subTest(value=value[:24]), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(ValueError, "sensitive value"):
                    okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_wallet_snapshot_requires_complete_wallet_identity(self) -> None:
        payload = self.ready_demo_payload()
        payload["channel"] = "wallet_dex"
        payload["instrument"] = {
            "underlying_symbol": "MU.US",
            "mapping_scope": "exact_chain_token",
            "mapping_verified": True,
            "region_eligible": True,
            "state": "live",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "wallet instrument identity"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_cex_snapshot_rejects_wallet_only_underlying_symbol(self) -> None:
        payload = self.ready_demo_payload()
        payload["instrument"]["underlying_symbol"] = "MU.US"
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "cex instrument identity"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_new_entries_reject_entry_score_block_ceiling(self) -> None:
        payload = self.ready_demo_payload()
        payload["entry_score"]["entry_permission_ceiling"] = "BLOCK"
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "permission matrix"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_unhashable_entry_score_ceiling_is_rejected_as_invalid_input(self) -> None:
        payload = self.payload()
        payload["entry_score"]["entry_permission_ceiling"] = {"value": "BLOCK"}
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "entry_permission_ceiling"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_unavailable_entry_score_is_displayable_only_when_entries_are_blocked(self) -> None:
        payload = self.payload()
        payload["entry_score"] = {
            "status": "unavailable", "entry_score_100": None,
            "factor_coverage": 0.0, "confidence": "low",
            "unresolved_conflict": False, "entry_permission_ceiling": None,
            "factors": {},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result = okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))
        self.assertTrue(result["ok"])

    def test_public_supervision_projection_accepts_canonical_unavailable_private_state(self) -> None:
        payload = self.payload()
        payload.update({
            "mode": "public",
            "credential_scope": "none",
            "execution_permission": "disabled",
            "status": "blocked",
            "pause_required": False,
            "pause_effective": False,
            "new_entries_allowed": False,
            "analysis_only": True,
            "market_analysis_allowed": True,
        })
        payload["connection"] = {
            "private_ws_connected": None,
            "rest_baseline_complete": False,
        }
        payload["strategy"] = {"status": "not_available", "stop_new_entries": False}
        payload["position_reconciliation_status"] = "pending"
        payload["order_state_status"] = "unknown"
        payload["blockers"] = ["private_state_unavailable:public_mode"]

        with tempfile.TemporaryDirectory() as temp_dir:
            result = okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))
            saved = json.loads((Path(temp_dir) / "supervision.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertIsNone(saved["connection"]["private_ws_connected"])
        self.assertEqual(saved["strategy"]["status"], "not_available")
        self.assertFalse(saved["new_entries_allowed"])

    def test_permission_matrix_rejects_impossible_execution_claims(self) -> None:
        cases = (
            {
                "mode": "public",
                "credential_scope": "read_only",
                "execution_permission": "live_approved",
                "new_entries_allowed": True,
            },
            {
                "mode": "read_only",
                "credential_scope": "read_only",
                "execution_permission": "disabled",
                "new_entries_allowed": True,
            },
            {
                "mode": "demo",
                "credential_scope": "read_only",
                "execution_permission": "paper",
                "analysis_only": True,
            },
        )
        for changes in cases:
            payload = self.payload()
            payload.update(changes)
            with self.subTest(mode=changes["mode"]), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(ValueError, "permission matrix"):
                    okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_live_new_entries_require_attestations_freshness_and_entry_score(self) -> None:
        payload = self.payload()
        payload.update({
            "mode": "live",
            "execution_permission": "live_approved",
            "status": "healthy",
            "pause_required": False,
            "pause_effective": False,
            "new_entries_allowed": True,
            "analysis_only": False,
            "market_analysis_allowed": True,
            "position_reconciliation_status": "matched",
            "order_state_status": "known",
            "blockers": [],
        })
        payload["strategy"] = {"status": "running", "stop_new_entries": False}
        payload["freshness"] = {
            "market": {"status": "live", "stale": False},
            "account": {"status": "live", "stale": False},
            "orders": {"status": "live", "stale": False, "rest_baseline_complete": True},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "permission matrix"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

        payload["live_controls"] = {
            "approval_verified": True,
            "ip_allowlist_verified": True,
            "withdrawal_enabled": False,
            "credentials_rotated": True,
        }
        payload["freshness"]["market"] = {"status": "stale", "stale": True}
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "permission matrix"):
                okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

        payload["freshness"]["market"] = {"status": "live", "stale": False}
        with tempfile.TemporaryDirectory() as temp_dir:
            result = okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))
        self.assertTrue(result["ok"])

    def test_public_token_contract_identifier_is_not_treated_as_a_secret(self) -> None:
        payload = self.payload()
        payload["metadata"] = {
            "token_contract": "0x0000000000000000000000000000000000000001",
            "credential_scope": "read_only",
            "credentials_used": False,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result = okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))
        self.assertTrue(result["no_order_execution"])

    def test_wrong_schema_is_rejected(self) -> None:
        payload = self.payload()
        payload["schema_version"] = "unknown.v1"
        with tempfile.TemporaryDirectory() as temp_dir, self.assertRaisesRegex(ValueError, "schema_version"):
            okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))

    def test_missing_canonical_supervision_fields_are_rejected(self) -> None:
        for field in (
            "pause_required", "position_reconciliation_status", "order_state_status",
            "connection", "entry_score",
        ):
            payload = self.payload()
            payload.pop(field)
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(ValueError, field):
                    okx_monitor_dashboard.publish_dashboard(payload, Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
