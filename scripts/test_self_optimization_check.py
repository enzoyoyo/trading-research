#!/usr/bin/env python3
"""Tests for the v2.46 A3 deep checks in self_optimization_check.py.

See deep_audit_and_repair_plan_20260726.md findings
ledger-health-blind-to-missed-runs and
paper-calibration-loop-stalled-reported-as-pending.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import self_optimization_check as soc  # noqa: E402


def _iso_days_ago(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).date().isoformat()


class LedgerLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        job = unittest.mock.patch.object(soc, "SELF_OPT_CRON_JOB_ID", "synthetic-cron-job")
        job.start()
        self.addCleanup(job.stop)

    def test_missing_cron_configuration_returns_gap_without_opening_database(self) -> None:
        with unittest.mock.patch.object(soc, "SELF_OPT_CRON_JOB_ID", ""), \
             unittest.mock.patch.object(soc.sqlite3, "connect") as connect:
            result = soc._cron_execution_crosscheck(["2026-07-24"])
        self.assertEqual(result, {"status": "unavailable", "reason": "cron_job_id_not_configured"})
        connect.assert_not_called()

    def _write_ledger(self, path: Path, dates: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for d in dates:
                fh.write(json.dumps({"date": d, "status": "no_necessary_upgrade"}) + "\n")

    def _write_cron_executions(self, path: Path, rows: list[tuple[str, str]]) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "CREATE TABLE executions (job_id TEXT NOT NULL, status TEXT NOT NULL, claimed_at TEXT NOT NULL)"
            )
            conn.executemany(
                "INSERT INTO executions (job_id, status, claimed_at) VALUES (?, ?, ?)",
                [(soc.SELF_OPT_CRON_JOB_ID, status, f"{day}T12:40:00+08:00") for day, status in rows],
            )
            conn.commit()
        finally:
            conn.close()

    def test_contiguous_ledger_up_to_yesterday_is_ok(self) -> None:
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            self._write_ledger(ledger_path, [_iso_days_ago(now, d) for d in (3, 2, 1)])

            with unittest.mock.patch.object(soc, "_load_self_optimization_ledger_module") as loader:
                loader.return_value = _StubLedgerModule(ledger_path)
                result = soc.ledger_liveness(now=now)

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("finding", result)
        self.assertEqual(result["missing_trailing_dates"], [])

    def test_two_day_gap_matching_07_24_07_25_incident_alerts(self) -> None:
        # Reproduces the real 2026-07-21~24 incident shape: cron completed
        # 07-23, then failed 07-24 and 07-25 without appending, and this
        # check runs the morning of 07-26 before that day's own append.
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            self._write_ledger(ledger_path, ["2026-07-22", "2026-07-23"])

            with unittest.mock.patch.object(soc, "_load_self_optimization_ledger_module") as loader:
                loader.return_value = _StubLedgerModule(ledger_path)
                result = soc.ledger_liveness(now=now)

        self.assertEqual(result["status"], "gap")
        self.assertEqual(result["missing_trailing_dates"], ["2026-07-24", "2026-07-25"])
        self.assertIn("finding", result)
        self.assertIn("2 consecutive trailing day(s)", result["finding"])

    def test_gap_crosschecks_completed_failed_and_missing_cron_dates(self) -> None:
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            cron_db = Path(tmp) / "executions.db"
            self._write_ledger(ledger_path, ["2026-07-22"])
            self._write_cron_executions(
                cron_db,
                [("2026-07-23", "completed"), ("2026-07-24", "failed")],
            )

            with unittest.mock.patch.object(soc, "_load_self_optimization_ledger_module") as loader:
                loader.return_value = _StubLedgerModule(ledger_path)
                result = soc.ledger_liveness(now=now, cron_db_path=cron_db)

        crosscheck = result["cron_execution_crosscheck"]
        self.assertEqual(crosscheck["status"], "ok")
        self.assertEqual(crosscheck["completed_without_ledger_append_dates"], ["2026-07-23"])
        self.assertEqual(crosscheck["failed_execution_dates"], ["2026-07-24"])
        self.assertEqual(crosscheck["no_execution_record_dates"], ["2026-07-25"])
        self.assertIn("completed without ledger append", result["finding"])
        self.assertIn("failed before append", result["finding"])
        self.assertNotIn("did not complete/append on those dates", result["finding"])

    def test_single_missing_day_is_below_alert_threshold(self) -> None:
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            self._write_ledger(ledger_path, ["2026-07-23", "2026-07-24"])  # missing only 07-25

            with unittest.mock.patch.object(soc, "_load_self_optimization_ledger_module") as loader:
                loader.return_value = _StubLedgerModule(ledger_path)
                result = soc.ledger_liveness(now=now)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["missing_trailing_dates"], ["2026-07-25"])

    def test_older_isolated_gap_does_not_alert_once_trailing_days_present(self) -> None:
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            # 07-21 missing (historical backfill gap) but 07-24/07-25 present.
            self._write_ledger(ledger_path, ["2026-07-20", "2026-07-24", "2026-07-25"])

            with unittest.mock.patch.object(soc, "_load_self_optimization_ledger_module") as loader:
                loader.return_value = _StubLedgerModule(ledger_path)
                result = soc.ledger_liveness(now=now)

        self.assertEqual(result["status"], "ok")

    def test_empty_ledger_reports_missing_not_ok(self) -> None:
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            with unittest.mock.patch.object(soc, "_load_self_optimization_ledger_module") as loader:
                loader.return_value = _StubLedgerModule(ledger_path)
                result = soc.ledger_liveness(now=now)

        self.assertEqual(result["status"], "missing")
        self.assertIn("finding", result)

    def test_reads_via_real_self_optimization_ledger_module(self) -> None:
        # No mocking of the ledger module itself: exercise the real
        # self_optimization_ledger.py read path end to end.
        now = datetime.fromisoformat("2026-07-26T07:15:00+00:00")
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.jsonl"
            self._write_ledger(ledger_path, [_iso_days_ago(now, d) for d in (2, 1)])

            import os

            old_env = os.environ.get("SELF_OPT_LEDGER")
            os.environ["SELF_OPT_LEDGER"] = str(ledger_path)
            try:
                result = soc.ledger_liveness(now=now)
            finally:
                if old_env is None:
                    os.environ.pop("SELF_OPT_LEDGER", None)
                else:
                    os.environ["SELF_OPT_LEDGER"] = old_env

        self.assertEqual(result["status"], "ok")


class _StubLedgerModule:
    """Minimal stand-in exposing the two functions ledger_liveness() calls."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def ledger_path(self) -> Path:
        return self._path

    LEDGER_GAP_MIN_CONSECUTIVE_DAYS = 2

    def read_rows(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def trailing_missing_dates(self, rows: list[dict], today, window_days: int = 10) -> list[str]:
        dates_present = {datetime.fromisoformat(str(row["date"])).date() for row in rows if row.get("date")}
        missing = []
        cursor = today - timedelta(days=1)
        window_start = cursor - timedelta(days=window_days)
        while cursor > window_start:
            if cursor in dates_present:
                break
            missing.append(cursor.isoformat())
            cursor -= timedelta(days=1)
        return sorted(missing)


class PendingPredictionOrphanTests(unittest.TestCase):
    def test_orphaned_flag_true(self) -> None:
        self.assertTrue(soc._pending_prediction_is_orphaned({"orphaned": True}))

    def test_is_orphaned_flag_true(self) -> None:
        self.assertTrue(soc._pending_prediction_is_orphaned({"is_orphaned": 1}))

    def test_status_closed_unreconciled(self) -> None:
        self.assertTrue(soc._pending_prediction_is_orphaned({"status": "closed_unreconciled"}))

    def test_reconciliation_status_orphaned_case_insensitive(self) -> None:
        self.assertTrue(soc._pending_prediction_is_orphaned({"reconciliation_status": "Orphaned"}))

    def test_plain_pending_row_is_not_orphaned(self) -> None:
        self.assertFalse(soc._pending_prediction_is_orphaned({"symbol": "AMD.US", "status": "pending"}))


class LatestPositionSymbolsTests(unittest.TestCase):
    def test_reads_last_line_symbols_uppercased(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper_position_snapshots.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"positions": [{"symbol": "old.us"}]}),
                        json.dumps({"positions": [{"symbol": "aapl.us"}, {"symbol": "MU.US"}]}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            symbols = soc._latest_position_symbols(path)

        self.assertEqual(symbols, {"AAPL.US", "MU.US"})

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(soc._latest_position_symbols(Path("/nonexistent/snapshots.jsonl")))

    def test_empty_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.jsonl"
            path.write_text("", encoding="utf-8")
            self.assertIsNone(soc._latest_position_symbols(path))

    def test_zero_position_snapshot_returns_empty_set_not_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.jsonl"
            path.write_text(json.dumps({"positions": []}) + "\n", encoding="utf-8")
            self.assertEqual(soc._latest_position_symbols(path), set())


class PendingCalibrationRowsTests(unittest.TestCase):
    def test_reads_all_columns_including_orphan_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE calibration_pending_paper_predictions (symbol TEXT, status TEXT)")
                conn.execute(
                    "INSERT INTO calibration_pending_paper_predictions (symbol, status) VALUES (?, ?)",
                    ("AMD.US", "orphaned"),
                )
                conn.commit()
            finally:
                conn.close()

            rows = soc._pending_calibration_rows(db_path)

        self.assertEqual(rows, [{"symbol": "AMD.US", "status": "orphaned"}])

    def test_missing_db_returns_empty(self) -> None:
        self.assertEqual(soc._pending_calibration_rows(Path("/nonexistent/trading_memory.sqlite")), [])

    def test_missing_table_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            sqlite3.connect(db_path).close()
            self.assertEqual(soc._pending_calibration_rows(db_path), [])


class CalibrationLivenessDataLinkTests(unittest.TestCase):
    """Exercises calibration_liveness()'s pitfall-state upgrade with the
    feed-subprocess call mocked at the process boundary (network/subprocess)
    but real sqlite + jsonl files underneath, per project convention of only
    mocking at boundaries."""

    def _seed_pending_table(self, db_path: Path, rows: list[tuple]) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE calibration_pending_paper_predictions (symbol TEXT, status TEXT, opened_at TEXT)"
            )
            conn.executemany(
                "INSERT INTO calibration_pending_paper_predictions (symbol, status, opened_at) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _write_snapshot(self, path: Path, symbols: list[str]) -> None:
        path.write_text(
            json.dumps({"positions": [{"symbol": s} for s in symbols]}) + "\n", encoding="utf-8"
        )

    def test_vanished_position_upgrades_to_data_link_broken_not_waiting_state(self) -> None:
        # Reproduces the 2026-07-21~24 incident shape: AMD/IWM/XLV pending
        # predictions but the latest snapshot only holds AAPL.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            snap_path = Path(tmp) / "paper_position_snapshots.jsonl"
            self._seed_pending_table(
                db_path,
                [
                    ("AMD.US", "pending", "2026-06-30T00:00:00Z"),
                    ("IWM.US", "pending", "2026-07-01T00:00:00Z"),
                    ("XLV.US", "pending", "2026-07-07T00:00:00Z"),
                    ("AAPL.US", "pending", "2026-07-09T00:00:00Z"),
                ],
            )
            self._write_snapshot(snap_path, ["AAPL.US"])

            with unittest.mock.patch.object(
                soc, "run_json", return_value={"_ok": True, "paired_samples": 1, "pending_prediction_count": 4}
            ), unittest.mock.patch.object(
                soc, "DEFAULT_TRADING_MEMORY_DB", db_path
            ), unittest.mock.patch.object(
                soc, "DEFAULT_PAPER_POSITION_SNAPSHOTS", snap_path
            ):
                result = soc.calibration_liveness()

        self.assertEqual(result["status"], "calibration_data_link_broken")
        self.assertEqual(result["broken_pending_count"], 3)
        self.assertIn("AMD.US", result["finding"])
        self.assertIn("state 1", result["finding"])

    def test_orphaned_pending_rows_are_not_reported_as_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            snap_path = Path(tmp) / "paper_position_snapshots.jsonl"
            self._seed_pending_table(
                db_path,
                [
                    ("AMD.US", "orphaned", "2026-06-30T00:00:00Z"),
                    ("AAPL.US", "pending", "2026-07-09T00:00:00Z"),
                ],
            )
            self._write_snapshot(snap_path, ["AAPL.US"])

            with unittest.mock.patch.object(
                soc, "run_json", return_value={"_ok": True, "paired_samples": 1, "pending_prediction_count": 2}
            ), unittest.mock.patch.object(
                soc, "DEFAULT_TRADING_MEMORY_DB", db_path
            ), unittest.mock.patch.object(
                soc, "DEFAULT_PAPER_POSITION_SNAPSHOTS", snap_path
            ):
                result = soc.calibration_liveness()

        self.assertEqual(result["status"], "starved")  # not calibration_data_link_broken
        self.assertEqual(result["orphaned_pending_count"], 1)

    def test_real_orphaned_column_schema_from_calibration_feed_is_not_reported_as_broken(self) -> None:
        # paper_outcome_calibration_feed.py's own 2026-07-26 P0 orphan-triage
        # repair persists `orphaned INTEGER`/`orphaned_at`/`orphaned_reason`
        # columns (not the `status` TEXT shape used elsewhere in this file) --
        # exercise that exact production schema so the cross-repo contract is
        # verified against the real writer, not just an abstract shape.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            snap_path = Path(tmp) / "paper_position_snapshots.jsonl"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE calibration_pending_paper_predictions ("
                    "symbol TEXT, opened_at TEXT, orphaned INTEGER NOT NULL DEFAULT 0, "
                    "orphaned_at TEXT, orphaned_reason TEXT)"
                )
                conn.executemany(
                    "INSERT INTO calibration_pending_paper_predictions "
                    "(symbol, opened_at, orphaned, orphaned_at, orphaned_reason) VALUES (?, ?, ?, ?, ?)",
                    [
                        ("AMD.US", "2026-06-30T00:00:00Z", 1, "2026-07-26T00:00:00Z", "position_gone"),
                        ("IWM.US", "2026-07-01T00:00:00Z", 1, "2026-07-26T00:00:00Z", "position_gone"),
                        ("XLV.US", "2026-07-07T00:00:00Z", 1, "2026-07-26T00:00:00Z", "position_gone"),
                        ("AAPL.US", "2026-07-09T00:00:00Z", 0, None, None),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            self._write_snapshot(snap_path, ["AAPL.US"])

            with unittest.mock.patch.object(
                soc, "run_json", return_value={"_ok": True, "paired_samples": 1, "pending_prediction_count": 4}
            ), unittest.mock.patch.object(
                soc, "DEFAULT_TRADING_MEMORY_DB", db_path
            ), unittest.mock.patch.object(
                soc, "DEFAULT_PAPER_POSITION_SNAPSHOTS", snap_path
            ):
                result = soc.calibration_liveness()

        self.assertEqual(result["status"], "starved")  # not calibration_data_link_broken
        self.assertEqual(result["orphaned_pending_count"], 3)

    def test_pending_with_matching_position_still_reports_starved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            snap_path = Path(tmp) / "paper_position_snapshots.jsonl"
            self._seed_pending_table(db_path, [("AAPL.US", "pending", "2026-07-09T00:00:00Z")])
            self._write_snapshot(snap_path, ["AAPL.US"])

            with unittest.mock.patch.object(
                soc, "run_json", return_value={"_ok": True, "paired_samples": 1, "pending_prediction_count": 1}
            ), unittest.mock.patch.object(
                soc, "DEFAULT_TRADING_MEMORY_DB", db_path
            ), unittest.mock.patch.object(
                soc, "DEFAULT_PAPER_POSITION_SNAPSHOTS", snap_path
            ):
                result = soc.calibration_liveness()

        self.assertEqual(result["status"], "starved")
        self.assertIn("throughput", result["finding"])

    def test_no_starvation_and_no_broken_links_is_plain_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            snap_path = Path(tmp) / "paper_position_snapshots.jsonl"
            self._seed_pending_table(db_path, [])
            self._write_snapshot(snap_path, ["AAPL.US"])

            with unittest.mock.patch.object(
                soc, "run_json", return_value={"_ok": True, "paired_samples": 20, "pending_prediction_count": 0}
            ), unittest.mock.patch.object(
                soc, "DEFAULT_TRADING_MEMORY_DB", db_path
            ), unittest.mock.patch.object(
                soc, "DEFAULT_PAPER_POSITION_SNAPSHOTS", snap_path
            ):
                result = soc.calibration_liveness()

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("finding", result)

    def test_missing_snapshot_skips_link_check_but_keeps_starved_reporting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "trading_memory.sqlite"
            self._seed_pending_table(db_path, [("AMD.US", "pending", "2026-06-30T00:00:00Z")])

            with unittest.mock.patch.object(
                soc, "run_json", return_value={"_ok": True, "paired_samples": 1, "pending_prediction_count": 1}
            ), unittest.mock.patch.object(
                soc, "DEFAULT_TRADING_MEMORY_DB", db_path
            ), unittest.mock.patch.object(
                soc, "DEFAULT_PAPER_POSITION_SNAPSHOTS", Path(tmp) / "nonexistent.jsonl"
            ):
                result = soc.calibration_liveness()

        self.assertEqual(result["status"], "starved")

    def test_feed_error_still_returns_error_status(self) -> None:
        with unittest.mock.patch.object(soc, "run_json", return_value={"_ok": False, "_stderr": "boom"}):
            result = soc.calibration_liveness()
        self.assertEqual(result["status"], "error")


class HypothesisRegistryReconciliationLivenessTests(unittest.TestCase):
    def test_factor_status_drift_surfaces_without_auto_repair(self) -> None:
        responses = [
            {"_ok": True, "count": 2},
            {
                "_ok": True, "ok": True, "read_only": True,
                "drift_count": 1, "no_recent_evidence_count": 0,
                "not_judgeable_count": 0, "ambiguous": [],
            },
        ]
        with unittest.mock.patch.object(soc, "run_json", side_effect=responses):
            result = soc.hypothesis_registry_liveness()
        self.assertEqual(result["status"], "factor_status_drift")
        self.assertTrue(result["reconciliation"]["read_only"])
        self.assertIn("did not auto-update", result["finding"])

    def test_absent_latest_factor_surfaces_as_no_recent_evidence(self) -> None:
        responses = [
            {"_ok": True, "count": 1},
            {
                "_ok": True, "ok": True, "read_only": True,
                "drift_count": 0, "no_recent_evidence_count": 1,
                "not_judgeable_count": 0, "ambiguous": [],
            },
        ]
        with unittest.mock.patch.object(soc, "run_json", side_effect=responses):
            result = soc.hypothesis_registry_liveness()
        self.assertEqual(result["status"], "no_recent_evidence")
        self.assertIn("not status drift", result["finding"])


class LoopLivenessWiringTests(unittest.TestCase):
    def test_ledger_key_present_and_its_finding_surfaces(self) -> None:
        gap_result = {"status": "gap", "finding": "ledger gap finding"}
        with unittest.mock.patch.object(soc, "journal_liveness", return_value={"status": "ok"}), \
            unittest.mock.patch.object(soc, "hypothesis_registry_liveness", return_value={"status": "ok"}), \
            unittest.mock.patch.object(soc, "calibration_liveness", return_value={"status": "ok"}), \
            unittest.mock.patch.object(soc, "git_drift_liveness", return_value={"status": "clean"}), \
            unittest.mock.patch.object(soc, "ledger_liveness", return_value=gap_result):
            result = soc.loop_liveness()

        self.assertEqual(result["ledger"], gap_result)
        self.assertIn("ledger gap finding", result["findings"])

    def test_calibration_data_link_broken_finding_surfaces(self) -> None:
        broken_result = {"status": "calibration_data_link_broken", "finding": "link broken finding"}
        with unittest.mock.patch.object(soc, "journal_liveness", return_value={"status": "ok"}), \
            unittest.mock.patch.object(soc, "hypothesis_registry_liveness", return_value={"status": "ok"}), \
            unittest.mock.patch.object(soc, "calibration_liveness", return_value=broken_result), \
            unittest.mock.patch.object(soc, "git_drift_liveness", return_value={"status": "clean"}), \
            unittest.mock.patch.object(soc, "ledger_liveness", return_value={"status": "ok"}):
            result = soc.loop_liveness()

        self.assertEqual(result["calibration"], broken_result)
        self.assertIn("link broken finding", result["findings"])


if __name__ == "__main__":
    unittest.main()
