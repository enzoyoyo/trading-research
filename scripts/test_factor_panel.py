#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import ModuleType
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import factor_panel as panel


class _FakeFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.columns = {key for row in rows for key in row}

    def iterrows(self):
        return iter(enumerate(self._rows))


class FactorPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rows = [
            {field: ("2026-08-21" if field == "date" else 1.0) for field in panel.REQUIRED_FIELDS}
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def runner(self, calls: list[str], fail: bool = False):
        def run(code: str, timeout: int):
            calls.append(code)
            if fail:
                return {"status": "fail", "error": "mock_network_blocked", "stdout": "", "stderr": ""}
            return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
        return run

    @staticmethod
    def longbridge_runner(calls: list[tuple[str, int]], fail: bool = False):
        def run(symbol: str, count: int):
            calls.append((symbol, count))
            if fail:
                return {"status": "fail", "error": "mock_longbridge_blocked", "rows": []}
            return {
                "status": "pass",
                "rows": [{
                    "time": "2026-08-21 12:00:00", "open": 99, "close": 100,
                    "high": 101, "low": 98, "volume": 123, "turnover": 12345,
                }],
            }
        return run

    @staticmethod
    def akshare_child_runner(
        calls: list[str], endpoint_calls: list[tuple[str, dict[str, object]]],
        rows_by_method: dict[str, list[dict[str, object]]],
        fail_methods: set[str] | None = None,
    ):
        methods = (
            "stock_zh_a_hist", "stock_us_hist", "stock_hk_hist",
            "stock_zh_a_daily", "stock_hk_daily",
        )
        failures = fail_methods or set()

        def run(code: str, timeout: int):
            del timeout
            method = next(name for name in methods if f"ak.{name}(" in code)
            calls.append(method)
            if method in failures:
                return {
                    "status": "fail", "error": "mock_network_blocked",
                    "stdout": "", "stderr": "",
                }
            fake_akshare = ModuleType("akshare")

            def endpoint(**kwargs):
                endpoint_calls.append((method, kwargs))
                return _FakeFrame(rows_by_method.get(method, []))

            setattr(fake_akshare, method, endpoint)
            output = StringIO()
            with mock.patch.dict(sys.modules, {"akshare": fake_akshare}):
                with redirect_stdout(output):
                    exec(code, {})
            return {"status": "pass", "stdout": output.getvalue(), "stderr": ""}

        return run

    def test_cache_write_manifest_and_incremental_skip(self) -> None:
        calls: list[str] = []
        got = panel.fetch_panel("A", ["600519"], force=True, min_interval=1.5,
                                root=self.root, runner=self.runner(calls),
                                sleep_fn=lambda _seconds: None, today=date(2026, 8, 21))
        self.assertEqual((got["fetched"], got["calls_made"]), (1, 2))
        cache = json.loads((self.root / "A" / "600519.json").read_text())
        manifest = json.loads((self.root / "A" / "panel_manifest.json").read_text())
        self.assertEqual(cache["rows"], self.rows)
        self.assertEqual(manifest["calls_made"], 2)
        skipped = panel.fetch_panel("A", ["600519"], min_interval=1.5,
                                    root=self.root, runner=self.runner(calls),
                                    sleep_fn=lambda _seconds: None, today=date(2026, 8, 22))
        self.assertEqual((skipped["skipped_fresh"], skipped["calls_made"], len(calls)), (1, 0, 2))

    def test_default_window_covers_h10_at_realistic_trading_density(self) -> None:
        args = panel.build_parser().parse_args(["fetch", "--market", "A", "--symbols", "600519"])
        self.assertGreaterEqual(args.window_days, 800)

    def test_force_refetches_fresh_cache(self) -> None:
        calls: list[str] = []
        for _ in range(2):
            result = panel.fetch_panel("A", ["600519"], force=True, min_interval=1.5,
                                       root=self.root, runner=self.runner(calls),
                                       sleep_fn=lambda _seconds: None, today=date(2026, 8, 21))
            self.assertEqual(result["fetched"], 1)
        self.assertEqual(len(calls), 4)

    def test_failure_is_gap_never_zero_and_all_failure_non_ok(self) -> None:
        calls: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []
        got = panel.fetch_panel("US", ["105.MSFT"], force=True, min_interval=0,
                                root=self.root, runner=self.runner(calls, fail=True),
                                longbridge_runner=self.longbridge_runner(longbridge_calls, fail=True),
                                today=date(2026, 8, 21))
        self.assertFalse(got["ok"])
        self.assertEqual(got["status"], "failed")
        self.assertEqual(got["failed"], 1)
        self.assertEqual(got["calls_made"], 3)  # AkShare initial+retry, then LongBridge
        self.assertEqual(len(longbridge_calls), 1)
        self.assertEqual(got["data_gaps"][0]["gap"], "akshare_and_longbridge_fetch_failed")
        self.assertEqual(got["data_gaps"][0]["sources_attempted"], ["akshare", "longbridge"])
        self.assertNotIn("rows", got["data_gaps"][0])
        self.assertFalse((self.root / "US" / "105.MSFT.json").exists())

    def test_us_primary_failure_longbridge_takeover_and_labels(self) -> None:
        akshare_calls: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []
        got = panel.fetch_panel(
            "US", ["SOXX"], force=True, min_interval=0, root=self.root,
            runner=self.runner(akshare_calls, fail=True),
            longbridge_runner=self.longbridge_runner(longbridge_calls),
            today=date(2026, 8, 21), window_days=420,
        )
        self.assertTrue(got["ok"])
        self.assertEqual((got["fetched"], got["source"], got["adjust"]), (1, "longbridge", "forward"))
        self.assertEqual(len(akshare_calls), 2)
        self.assertEqual(longbridge_calls, [("SOXX", 420)])
        cache = json.loads((self.root / "US" / "SOXX.json").read_text())
        manifest = json.loads((self.root / "US" / "panel_manifest.json").read_text())
        self.assertEqual((cache["source"], cache["adjust"]), ("longbridge", "forward"))
        self.assertEqual(cache["panel_source"], "longbridge")
        self.assertEqual(cache["adjust_basis"], "forward_snapshot_20260821")
        self.assertEqual((manifest["source"], manifest["adjust"]), ("longbridge", "forward"))
        self.assertEqual(manifest["adjust_basis"], "forward_snapshot_20260821")
        self.assertEqual(
            (manifest["symbols"]["SOXX"]["source"], manifest["symbols"]["SOXX"]["adjust"]),
            ("longbridge", "forward"),
        )
        self.assertEqual(manifest["symbols"]["SOXX"]["panel_source"], "longbridge")
        self.assertEqual(cache["rows"][0]["close"], 100.0)

    def test_explicit_longbridge_skips_akshare(self) -> None:
        akshare_calls: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []
        got = panel.fetch_panel(
            "US", ["SPY"], force=True, min_interval=0, root=self.root,
            runner=self.runner(akshare_calls, fail=True),
            longbridge_runner=self.longbridge_runner(longbridge_calls),
            today=date(2026, 8, 21), source="longbridge",
        )
        self.assertTrue(got["ok"])
        self.assertEqual(akshare_calls, [])
        self.assertEqual(len(longbridge_calls), 1)
        self.assertEqual(got["source_mode"], "longbridge")

    def test_explicit_longbridge_supports_hk_and_rejects_a(self) -> None:
        with self.assertRaisesRegex(ValueError, "only supports markets US/HK"):
            panel.fetch_panel(
                "A", ["600519"], min_interval=1.5, root=self.root,
                runner=self.runner([]), source="longbridge", today=date(2026, 8, 21),
            )
        longbridge_calls: list[tuple[str, int]] = []
        got = panel.fetch_panel(
            "HK", ["00700"], force=True, min_interval=1.5, root=self.root,
            runner=self.runner([], fail=True), sleep_fn=lambda _seconds: None,
            longbridge_runner=self.longbridge_runner(longbridge_calls),
            source="longbridge", today=date(2026, 8, 21),
        )
        self.assertEqual((got["source"], got["em_gate"]), ("longbridge", None))
        self.assertEqual(longbridge_calls, [("00700", 800)])

    def test_source_cli_contract_includes_longbridge(self) -> None:
        args = panel.build_parser().parse_args([
            "fetch", "--market", "US", "--symbols", "SPY", "--source", "longbridge",
        ])
        self.assertEqual(args.source, "longbridge")

    def test_longbridge_symbol_mapping_and_hk_trade_dates(self) -> None:
        us_expected = {
            "SPY": "SPY.US",
            "SPY.US": "SPY.US",
            "105.MSFT": "MSFT.US",
            "105.msft": "MSFT.US",
        }
        for symbol, expected in us_expected.items():
            with self.subTest(market="US", symbol=symbol):
                self.assertEqual(panel._longbridge_symbol(symbol, "US"), expected)
        for symbol, expected in {
            "00700": "700.HK", "00005": "5.HK", "0700.HK": "700.HK",
        }.items():
            with self.subTest(market="HK", symbol=symbol):
                self.assertEqual(panel._longbridge_symbol(symbol, "HK"), expected)
        with self.assertRaisesRegex(ValueError, "hk_symbol_invalid"):
            panel._longbridge_symbol("0700.X", "HK")
        self.assertEqual(panel._lb_trade_date("2026-08-02T16:00:00Z", "HK"), date(2026, 8, 3))
        self.assertEqual(
            panel._lb_trade_date("2026-08-02 16:00:00+00:00", "HK"), date(2026, 8, 3),
        )
        self.assertEqual(panel._lb_trade_date("2026-08-02T16:00:00Z", "US"), date(2026, 8, 2))
        self.assertEqual(panel._lb_trade_date("2026-08-02", "HK"), date(2026, 8, 2))
        self.assertEqual(panel._lb_trade_date("2026-08-02", "US"), date(2026, 8, 2))

        class _Suppress:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        command_calls: list[tuple[object, str, str, int, str]] = []
        fake_longbridge = ModuleType("longbridge_query")
        fake_longbridge.suppress_stdout_fd = _Suppress
        fake_longbridge.get_ctx = lambda: "ctx"

        def cmd_candle(ctx, symbol, period, count, *, adjust):
            command_calls.append((ctx, symbol, period, count, adjust))
            return []

        fake_longbridge.cmd_candle = cmd_candle
        with mock.patch.dict(sys.modules, {"longbridge_query": fake_longbridge}):
            self.assertEqual(panel.run_longbridge("00700", 2, "HK")["status"], "pass")
        self.assertEqual(command_calls, [("ctx", "700.HK", "day", 2, "forward")])

    def test_hk_auto_longbridge_primary_skips_akshare(self) -> None:
        akshare_calls: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []

        def fail_loud(code: str, timeout: int):
            del timeout
            akshare_calls.append(code)
            raise AssertionError("HK LongBridge success must not evaluate the Eastmoney gate")

        got = panel.fetch_panel(
            "HK", ["00700", "00005"], force=True, min_interval=1.5, root=self.root,
            runner=fail_loud, sleep_fn=lambda _seconds: None,
            longbridge_runner=self.longbridge_runner(longbridge_calls), today=date(2026, 8, 21),
        )
        self.assertEqual((got["status"], got["sources_used"]), ("ok", ["longbridge"]))
        self.assertEqual(akshare_calls, [])
        self.assertEqual(longbridge_calls, [("00700", 800), ("00005", 800)])
        manifest = json.loads((self.root / "HK" / "panel_manifest.json").read_text())
        self.assertEqual(manifest["sources_used"], ["longbridge"])
        self.assertEqual(
            manifest["em_gate"],
            {"evaluated": False, "active": True, "reason": "not_evaluated"},
        )
        self.assertTrue(all(
            record["source"] == "longbridge" and record["adjust_basis"].startswith("forward_snapshot_")
            for record in manifest["symbols"].values()
        ))

    def test_hk_auto_probe_failure_skips_eastmoney_and_uses_sina(self) -> None:
        eastmoney_codes: list[str] = []
        sina_codes: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []

        def runner(code: str, timeout: int):
            del timeout
            if "ak.stock_hk_hist(" in code:
                eastmoney_codes.append(code)
                return {"status": "fail", "error": "mock_eastmoney_blocked", "stdout": "", "stderr": ""}
            if "ak.stock_hk_daily(" in code:
                sina_codes.append(code)
                return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
            raise AssertionError("unexpected AkShare endpoint")

        got = panel.fetch_panel(
            "HK", ["00700", "00005"], force=True, min_interval=1.5, root=self.root,
            runner=runner, sleep_fn=lambda _seconds: None,
            longbridge_runner=self.longbridge_runner(longbridge_calls, fail=True),
            today=date(2026, 8, 21),
        )
        manifest = json.loads((self.root / "HK" / "panel_manifest.json").read_text())
        self.assertEqual(got["akshare_calls_made"], 1)
        self.assertEqual(got["akshare_sina_calls_made"], 2)
        self.assertEqual(len(eastmoney_codes), 1)
        self.assertEqual(len(sina_codes), 2)
        self.assertEqual(
            manifest["em_gate"],
            {"evaluated": True, "active": False, "reason": "em_probe_failed"},
        )
        self.assertTrue(all(
            record["sources_attempted"] == ["longbridge", "akshare_sina"]
            for record in manifest["symbols"].values()
        ))
        health = json.loads((self.root / "em_health.json").read_text())
        self.assertEqual(health["status"], "down")
        self.assertGreater(
            datetime.fromisoformat(health["cooldown_until"]),
            datetime.fromisoformat(health["checked_at"]),
        )

    def test_hk_auto_active_cooldown_skips_canary(self) -> None:
        now = datetime.now(timezone.utc)
        panel._atomic_json(self.root / "em_health.json", {
            "status": "down", "checked_at": now.isoformat(),
            "cooldown_until": (now + timedelta(hours=1)).isoformat(),
            "last_error": "prior_failure",
        })
        eastmoney_codes: list[str] = []
        sina_codes: list[str] = []

        def runner(code: str, timeout: int):
            del timeout
            if "ak.stock_hk_hist(" in code:
                eastmoney_codes.append(code)
                raise AssertionError("cooldown must suppress the canary")
            if "ak.stock_hk_daily(" in code:
                sina_codes.append(code)
                return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
            raise AssertionError("unexpected AkShare endpoint")

        got = panel.fetch_panel(
            "HK", ["00700"], force=True, min_interval=1.5, root=self.root,
            runner=runner, sleep_fn=lambda _seconds: None,
            longbridge_runner=self.longbridge_runner([], fail=True), today=date(2026, 8, 21),
        )
        self.assertEqual((got["akshare_calls_made"], got["akshare_sina_calls_made"]), (0, 1))
        self.assertEqual(eastmoney_codes, [])
        self.assertEqual(len(sina_codes), 1)
        self.assertEqual(
            got["em_gate"],
            {"evaluated": True, "active": False, "reason": "em_cooldown_active"},
        )
        manifest = json.loads((self.root / "HK" / "panel_manifest.json").read_text())
        self.assertEqual(
            manifest["symbols"]["00700"]["sources_attempted"], ["longbridge", "akshare_sina"],
        )

    def test_hk_auto_successful_probe_allows_eastmoney_after_longbridge(self) -> None:
        eastmoney_codes: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []

        def runner(code: str, timeout: int):
            del timeout
            eastmoney_codes.append(code)
            return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}

        got = panel.fetch_panel(
            "HK", ["00700"], force=True, min_interval=1.5, root=self.root,
            runner=runner, sleep_fn=lambda _seconds: None,
            longbridge_runner=self.longbridge_runner(longbridge_calls, fail=True),
            today=date(2026, 8, 21),
        )
        self.assertEqual(longbridge_calls, [("00700", 800)])
        self.assertEqual((got["source"], got["akshare_calls_made"]), ("akshare", 2))
        self.assertEqual(got["akshare_sina_calls_made"], 0)
        self.assertEqual(len(eastmoney_codes), 2)  # canary, then the actual symbol request.
        self.assertEqual(
            got["em_gate"],
            {"evaluated": True, "active": True, "reason": None},
        )
        manifest = json.loads((self.root / "HK" / "panel_manifest.json").read_text())
        self.assertEqual(
            manifest["symbols"]["00700"]["sources_attempted"], ["longbridge", "akshare"],
        )

    def test_explicit_akshare_bypasses_em_health_gate(self) -> None:
        eastmoney_codes: list[str] = []

        def failing_runner(code: str, timeout: int):
            del timeout
            eastmoney_codes.append(code)
            return {"status": "fail", "error": "manual_diagnostic_failure", "stdout": "", "stderr": ""}

        got = panel.fetch_panel(
            "A", ["600519"], force=True, min_interval=1.5, root=self.root,
            runner=failing_runner, sleep_fn=lambda _seconds: None,
            source="akshare", today=date(2026, 8, 21),
        )
        self.assertEqual((got["akshare_calls_made"], got["em_gate"]), (2, None))
        self.assertEqual(len(eastmoney_codes), 2)
        self.assertFalse((self.root / "em_health.json").exists())
        manifest = json.loads((self.root / "A" / "panel_manifest.json").read_text())
        self.assertEqual(manifest["symbols"]["600519"]["sources_attempted"], ["akshare"])

    def test_auto_eastmoney_circuit_trips_after_three_failures_and_sina_delivers(self) -> None:
        eastmoney_calls: list[str] = []
        sina_calls: list[str] = []

        def runner(code: str, timeout: int):
            del timeout
            if "ak.stock_zh_a_hist(" in code:
                eastmoney_calls.append(code)
                if len(eastmoney_calls) == 1:
                    return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
                return {"status": "fail", "error": "mock_eastmoney_blocked", "stdout": "", "stderr": ""}
            if "ak.stock_zh_a_daily(" in code:
                sina_calls.append(code)
                return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
            raise AssertionError("unexpected AkShare endpoint")

        symbols = ["600519", "000001", "300750", "688981"]
        got = panel.fetch_panel(
            "A", symbols, force=True, min_interval=1.5, root=self.root, runner=runner,
            sleep_fn=lambda _seconds: None, today=date(2026, 8, 21),
        )
        self.assertEqual(len(eastmoney_calls), 1 + panel.EM_CIRCUIT_MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(len(sina_calls), len(symbols))
        self.assertEqual((got["fetched"], got["failed"]), (len(symbols), 0))
        self.assertEqual(
            got["em_gate"],
            {"evaluated": True, "active": False, "reason": "em_circuit_tripped"},
        )
        manifest = json.loads((self.root / "A" / "panel_manifest.json").read_text())
        self.assertEqual(manifest["em_gate"], got["em_gate"])
        health = json.loads((self.root / "em_health.json").read_text())
        self.assertEqual(health["status"], "down")
        self.assertGreater(
            datetime.fromisoformat(health["cooldown_until"]),
            datetime.fromisoformat(health["checked_at"]),
        )

    def test_auto_eastmoney_circuit_counts_consecutive_not_cumulative_failures(self) -> None:
        outcomes = iter(["pass", "fail", "pass", "fail", "fail", "fail"])
        eastmoney_calls: list[str] = []

        def runner(code: str, timeout: int):
            del timeout
            if "ak.stock_zh_a_hist(" in code:
                eastmoney_calls.append(code)
                outcome = next(outcomes)
                if outcome == "pass":
                    return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
                return {"status": "fail", "error": "mock_eastmoney_blocked", "stdout": "", "stderr": ""}
            if "ak.stock_zh_a_daily(" in code:
                return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
            raise AssertionError("unexpected AkShare endpoint")

        got = panel.fetch_panel(
            "A", ["600519", "000001", "300750"], force=True, min_interval=1.5,
            root=self.root, runner=runner, sleep_fn=lambda _seconds: None,
            today=date(2026, 8, 21),
        )
        self.assertEqual(len(eastmoney_calls), 6)  # canary, then fail-success-fail-fail-fail
        self.assertEqual((got["fetched"], got["failed"]), (3, 0))
        self.assertEqual(
            got["em_gate"],
            {"evaluated": True, "active": False, "reason": "em_circuit_tripped"},
        )

    def test_explicit_akshare_continuous_failures_do_not_trip_or_write_health(self) -> None:
        eastmoney_calls: list[str] = []

        def runner(code: str, timeout: int):
            del timeout
            eastmoney_calls.append(code)
            return {"status": "fail", "error": "manual_diagnostic_failure", "stdout": "", "stderr": ""}

        got = panel.fetch_panel(
            "A", ["600519", "000001"], force=True, min_interval=1.5, root=self.root,
            runner=runner, sleep_fn=lambda _seconds: None, source="akshare", today=date(2026, 8, 21),
        )
        self.assertEqual((len(eastmoney_calls), got["em_gate"]), (4, None))
        self.assertFalse((self.root / "em_health.json").exists())

    def test_fresh_legacy_cache_without_provenance_is_refetched(self) -> None:
        path = self.root / "US" / "SPY.json"
        panel._atomic_json(path, {
            "schema_version": "factor_panel_symbol.v1",
            "fetched_at": "2026-08-21T00:00:00+00:00",
            "rows": self.rows,
        })
        akshare_calls: list[str] = []
        got = panel.fetch_panel(
            "US", ["SPY"], min_interval=0, root=self.root,
            runner=self.runner(akshare_calls), today=date(2026, 8, 21),
        )
        self.assertEqual((got["fetched"], got["skipped_fresh"]), (1, 0))
        self.assertEqual(len(akshare_calls), 1)
        refreshed = json.loads(path.read_text())
        self.assertEqual((refreshed["source"], refreshed["adjust"]), ("akshare", "qfq"))

    def test_fresh_longbridge_cache_with_none_adjust_is_refetched(self) -> None:
        path = self.root / "US" / "SPY.json"
        panel._atomic_json(path, {
            "schema_version": "factor_panel_symbol.v1",
            "fetched_at": "2026-08-21T00:00:00+00:00",
            "source": "longbridge",
            "adjust": "none",
            "rows": self.rows,
        })
        akshare_calls: list[str] = []
        longbridge_calls: list[tuple[str, int]] = []
        got = panel.fetch_panel(
            "US", ["SPY"], min_interval=0, root=self.root, source="longbridge",
            runner=self.runner(akshare_calls, fail=True),
            longbridge_runner=self.longbridge_runner(longbridge_calls),
            today=date(2026, 8, 21),
        )
        self.assertEqual((got["fetched"], got["skipped_fresh"]), (1, 0))
        self.assertEqual(akshare_calls, [])
        self.assertEqual(len(longbridge_calls), 1)
        refreshed = json.loads(path.read_text())
        self.assertEqual((refreshed["source"], refreshed["adjust"]), ("longbridge", "forward"))

    def test_partial_failure_keeps_success_and_gap(self) -> None:
        calls: list[str] = []
        def mixed(code: str, timeout: int):
            calls.append(code)
            if "BAD" in code:
                return {"status": "fail", "error": "blocked", "stdout": "", "stderr": ""}
            return {"status": "pass", "stdout": json.dumps(self.rows), "stderr": ""}
        got = panel.fetch_panel("A", ["600519", "BAD"], force=True, min_interval=1.5,
                                root=self.root, runner=mixed, sleep_fn=lambda _seconds: None,
                                today=date(2026, 8, 21))
        self.assertTrue(got["ok"])
        self.assertEqual((got["status"], got["fetched"], got["failed"]), ("partial", 1, 1))

    def test_prune_window_and_manifest_preserved(self) -> None:
        calls: list[str] = []
        panel.fetch_panel("A", ["600519"], force=True, min_interval=1.5,
                          root=self.root, runner=self.runner(calls),
                          sleep_fn=lambda _seconds: None, today=date(2026, 8, 21))
        path = self.root / "A" / "600519.json"
        payload = json.loads(path.read_text())
        payload["fetched_at"] = "2020-01-01T00:00:00+00:00"
        panel._atomic_json(path, payload)
        got = panel.prune_cache(30, root=self.root, now=datetime(2026, 8, 21, tzinfo=timezone.utc))
        self.assertEqual(got["removed_count"], 1)
        self.assertFalse(path.exists())
        self.assertTrue((self.root / "A" / "panel_manifest.json").exists())

    def test_ahk_throttle_and_symbol_cap_cannot_be_relaxed(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_interval >=1.5"):
            panel.fetch_panel(
                "A", ["600519"], force=True, min_interval=0,
                root=self.root, runner=self.runner([]), today=date(2026, 8, 21),
            )
        with self.assertRaisesRegex(ValueError, "at most 100"):
            panel.fetch_panel(
                "HK", [f"{index:05d}" for index in range(101)], min_interval=1.5,
                root=self.root, runner=self.runner([]), today=date(2026, 8, 21),
            )

    def test_environment_override_and_no_proxy_subprocess_layer(self) -> None:
        with mock.patch.dict(os.environ, {"FACTOR_PANEL_DIR": str(self.root)}):
            self.assertEqual(panel.cache_root(), self.root)
        fake = subprocess.CompletedProcess(["python"], 0, "[]\n", "")
        with mock.patch("factor_panel.subprocess.run", return_value=fake) as run:
            got = panel.run_akshare("print('[]')")
        self.assertEqual(got["status"], "pass")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["no_proxy"], "*")
        self.assertEqual(env["HTTP_PROXY"], "")

    def test_self_test_cli_exact_output_and_no_network(self) -> None:
        proc = subprocess.run([sys.executable, str(Path(panel.__file__)), "--self-test"],
                              capture_output=True, text=True, timeout=3,
                              env={**os.environ, "FACTOR_PANEL_DIR": str(self.root)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"ok": True, "self_test": "passed"})
        self.assertEqual(proc.stdout.strip(), '{"ok": true, "self_test": "passed"}')

    def test_a_auto_eastmoney_failure_sina_takeover_and_derived_fields(self) -> None:
        calls: list[str] = []
        endpoint_calls: list[tuple[str, dict[str, object]]] = []
        sleeps: list[float] = []
        sina_rows = [
            {"date": "2026-08-20", "open": 99, "high": 101, "low": 98,
             "close": 100, "volume": 1000, "amount": 100000,
             "outstanding_share": 100000, "turnover": 0.012},
            {"date": "2026-08-21", "open": 101, "high": 111, "low": 100,
             "close": 110, "volume": 1200, "amount": 132000,
             "outstanding_share": 100000, "turnover": 0.013},
        ]
        runner = self.akshare_child_runner(
            calls, endpoint_calls, {"stock_zh_a_daily": sina_rows},
            fail_methods={"stock_zh_a_hist"},
        )
        with mock.patch("factor_panel.time.monotonic", return_value=0.0):
            got = panel.fetch_panel(
                "A", ["600519"], force=True, min_interval=1.5,
                root=self.root, runner=runner, sleep_fn=sleeps.append,
                today=date(2026, 8, 21),
            )
        self.assertEqual(calls, ["stock_zh_a_hist", "stock_zh_a_daily"])
        self.assertEqual(sleeps, [1.5])
        self.assertEqual((got["calls_made"], got["akshare_calls_made"]), (2, 1))
        self.assertEqual(got["akshare_sina_calls_made"], 1)
        self.assertEqual(got["longbridge_calls_made"], 0)
        self.assertEqual(endpoint_calls[0][1]["symbol"], "sh600519")
        cache = json.loads((self.root / "A" / "600519.json").read_text())
        manifest = json.loads((self.root / "A" / "panel_manifest.json").read_text())
        self.assertEqual((cache["source"], cache["adjust"]), ("akshare_sina", "sina_qfq"))
        self.assertEqual(cache["adjust_basis"], "sina_qfq_snapshot_20260821")
        self.assertEqual(cache["rows"][0]["turnover_rate"], 0.012)
        self.assertIsNone(cache["rows"][0]["pct_change"])
        self.assertAlmostEqual(cache["rows"][1]["pct_change"], 10.0)
        self.assertEqual(
            (manifest["symbols"]["600519"]["source"],
             manifest["symbols"]["600519"]["adjust"]),
            ("akshare_sina", "sina_qfq"),
        )
        self.assertEqual(
            manifest["symbols"]["600519"]["sources_attempted"],
            ["akshare_sina"],
        )
        self.assertEqual(
            manifest["em_gate"],
            {"evaluated": True, "active": False, "reason": "em_probe_failed"},
        )

    def test_explicit_sina_skips_eastmoney_matches_cache_and_rejects_us(self) -> None:
        path = self.root / "A" / "600519.json"
        panel._atomic_json(path, panel.envelope(
            "factor_panel_symbol.v1", market="A", symbol="600519",
            fetched_at="2026-08-21T00:00:00+00:00", source="akshare",
            panel_source="akshare", adjust="qfq",
            adjust_basis="qfq_snapshot_20260821", rows=self.rows,
        ))
        calls: list[str] = []
        endpoint_calls: list[tuple[str, dict[str, object]]] = []
        runner = self.akshare_child_runner(
            calls, endpoint_calls,
            {"stock_zh_a_daily": [{"date": "2026-08-21", "open": 1, "high": 1,
                                    "low": 1, "close": 1, "volume": 1,
                                    "amount": 1, "turnover": 0.01}]},
        )
        got = panel.fetch_panel(
            "A", ["600519"], min_interval=1.5, root=self.root,
            runner=runner, today=date(2026, 8, 21), source="akshare_sina",
        )
        self.assertEqual((got["fetched"], got["skipped_fresh"]), (1, 0))
        self.assertEqual(calls, ["stock_zh_a_daily"])
        explicit_skip = panel.fetch_panel(
            "A", ["600519"], min_interval=1.5, root=self.root,
            runner=runner, today=date(2026, 8, 21), source="akshare_sina",
        )
        auto_skip = panel.fetch_panel(
            "A", ["600519"], min_interval=1.5, root=self.root,
            runner=runner, today=date(2026, 8, 21), source="auto",
        )
        self.assertEqual((explicit_skip["skipped_fresh"], auto_skip["skipped_fresh"]), (1, 1))
        self.assertEqual(calls, ["stock_zh_a_daily"])
        args = panel.build_parser().parse_args([
            "fetch", "--market", "A", "--symbols", "600519",
            "--source", "akshare_sina",
        ])
        self.assertEqual(args.source, "akshare_sina")
        with self.assertRaisesRegex(ValueError, "akshare_sina.*A/HK"):
            panel.fetch_panel(
                "US", ["SPY"], min_interval=0, root=self.root,
                runner=runner, today=date(2026, 8, 21), source="akshare_sina",
            )
        self.assertEqual(calls, ["stock_zh_a_daily"])

    def test_beijing_symbol_is_unsupported_without_sina_call(self) -> None:
        calls: list[str] = []

        def fail_loud(code: str, timeout: int):
            calls.append(code)
            raise AssertionError("Sina runner must not be called for Beijing symbols")

        got = panel.fetch_panel(
            "A", ["830799"], force=True, min_interval=1.5, root=self.root,
            runner=fail_loud, today=date(2026, 8, 21), source="akshare_sina",
        )
        self.assertEqual(calls, [])
        self.assertEqual((got["ok"], got["status"], got["calls_made"]), (False, "failed", 0))
        self.assertEqual(got["data_gaps"][0]["gap"], "sina_symbol_unsupported")
        self.assertFalse((self.root / "A" / "830799.json").exists())

    def test_sina_a_symbol_prefix_mapping(self) -> None:
        expected = {
            "600519": "sh600519", "000001": "sz000001",
            "300750": "sz300750", "688981": "sh688981",
        }
        for symbol, sina_symbol in expected.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(panel._sina_a_symbol(symbol), sina_symbol)

    def test_hk_sina_filters_full_history_window_and_has_no_turnover(self) -> None:
        calls: list[str] = []
        endpoint_calls: list[tuple[str, dict[str, object]]] = []
        hk_rows = [
            {"date": "2026-08-18", "open": 89, "high": 91, "low": 88,
             "close": 90, "volume": 1, "amount": 90},
            {"date": "2026-08-20", "open": 99, "high": 101, "low": 98,
             "close": 100, "volume": 2, "amount": 200},
            {"date": "2026-08-21", "open": 101, "high": 111, "low": 100,
             "close": 110, "volume": 3, "amount": 330},
            {"date": "2026-08-22", "open": 111, "high": 121, "low": 110,
             "close": 120, "volume": 4, "amount": 480},
        ]
        runner = self.akshare_child_runner(
            calls, endpoint_calls, {"stock_hk_daily": hk_rows},
        )
        got = panel.fetch_panel(
            "HK", ["00700"], window_days=2, force=True, min_interval=1.5,
            root=self.root, runner=runner, today=date(2026, 8, 21),
            source="akshare_sina",
        )
        self.assertEqual((got["fetched"], calls), (1, ["stock_hk_daily"]))
        self.assertEqual(endpoint_calls, [
            ("stock_hk_daily", {"symbol": "00700", "adjust": "qfq"}),
        ])
        cache = json.loads((self.root / "HK" / "00700.json").read_text())
        self.assertEqual([row["date"] for row in cache["rows"]], ["2026-08-20", "2026-08-21"])
        self.assertTrue(all(row["turnover_rate"] is None for row in cache["rows"]))
        self.assertIsNone(cache["rows"][0]["pct_change"])
        self.assertAlmostEqual(cache["rows"][1]["pct_change"], 10.0)

    def test_mixed_adjust_basis_from_fresh_cache_and_sina_is_reported_only(self) -> None:
        panel._atomic_json(self.root / "A" / "600519.json", panel.envelope(
            "factor_panel_symbol.v1", market="A", symbol="600519",
            fetched_at="2026-08-21T00:00:00+00:00", source="akshare",
            panel_source="akshare", adjust="qfq",
            adjust_basis="qfq_snapshot_20260821", rows=self.rows,
        ))
        calls: list[str] = []
        endpoint_calls: list[tuple[str, dict[str, object]]] = []
        runner = self.akshare_child_runner(
            calls, endpoint_calls,
            {"stock_zh_a_daily": [{"date": "2026-08-21", "open": 10, "high": 10,
                                    "low": 10, "close": 10, "volume": 10,
                                    "amount": 100, "turnover": 0.02}]},
            fail_methods={"stock_zh_a_hist"},
        )
        got = panel.fetch_panel(
            "A", ["600519", "000001"], min_interval=1.5, root=self.root,
            runner=runner, sleep_fn=lambda _seconds: None,
            today=date(2026, 8, 21), source="auto",
        )
        self.assertEqual((got["status"], got["fetched"], got["skipped_fresh"], got["failed"]),
                         ("ok", 1, 1, 0))
        risk = [gap for gap in got["data_gaps"]
                if gap.get("reason_code") == "mixed_adjust_basis_risk"]
        expected_bases = ["qfq_snapshot_20260821", "sina_qfq_snapshot_20260821"]
        self.assertEqual(risk, [{"reason_code": "mixed_adjust_basis_risk", "bases": expected_bases}])
        manifest = json.loads((self.root / "A" / "panel_manifest.json").read_text())
        self.assertEqual(manifest["adjust_bases_used"], expected_bases)
        self.assertIsNone(manifest["adjust_basis"])
        self.assertEqual(manifest["sources_used"], ["akshare", "akshare_sina"])
        self.assertEqual(manifest["source_aggregate"]["mode"], "mixed")
        self.assertEqual(
            (manifest["symbols"]["600519"]["status"],
             manifest["symbols"]["000001"]["status"]),
            ("skipped_fresh", "fetched"),
        )
        self.assertEqual(calls, ["stock_zh_a_hist", "stock_zh_a_daily"])


if __name__ == "__main__":
    unittest.main()
