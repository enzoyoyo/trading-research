#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import paper_outcome_calibration_feed as feed  # noqa: E402


class PaperOutcomeLifecycleCalibrationTests(unittest.TestCase):
    def test_self_test_passes_with_empty_home_from_bundled_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "empty-home"
            temp_dir = root / "tmp"
            home.mkdir()
            temp_dir.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "TMPDIR": str(temp_dir),
                    "TRADING_MEMORY_DB": str(root / "memory.sqlite"),
                    feed.LIFECYCLE_BINDING_ENV: str(feed.BUNDLED_LIFECYCLE_MODULE),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "paper_outcome_calibration_feed.py"), "--self-test"],
                cwd=SCRIPTS.parent,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["self_test"], "passed")

    def test_external_mutable_and_symlink_lifecycle_modules_never_execute(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "external-executed"
            external = root / "mutable-paper-trade-lifecycle.py"
            external.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
                "def aggregate_lifecycles(_rows): return {}\n",
                encoding="utf-8",
            )
            symlink = root / "lifecycle-symlink.py"
            symlink.symlink_to(external)
            for candidate in (external, symlink):
                with self.subTest(candidate=candidate.name), mock.patch.dict(
                    os.environ,
                    {feed.LIFECYCLE_BINDING_ENV: str(candidate)},
                    clear=False,
                ):
                    with self.assertRaisesRegex(RuntimeError, "candidate-local"):
                        feed.lifecycle_module()
                self.assertFalse(marker.exists())

    def test_partial_exits_emit_one_sample_only_after_final_close(self) -> None:
        outcomes = [
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 100, "timestamp_utc": "2026-01-01T10:00:00Z", "paper_prediction_contract": {"schema_version": 1, "prediction_id": "synthetic-entry-1", "proposal_id": "entry-1", "symbol": "ABC.US", "p": 0.62, "as_of": "2026-01-01T10:00:00Z", "frozen_at": "2026-01-01T10:00:00Z", "horizon_id": "swing_days", "target": "net_pnl_positive", "settlement": "full_lifecycle", "strategy_version": "synthetic-v1", "model_version": "synthetic-model-v1", "basis": "Synthetic pre-entry forecast fixture, not live evidence", "evidence_refs": ["fixture:prior-training-set"], "probability_kind": "ex_ante_forecast"}},
            {"status": "partial_closed", "net_pnl": 10, "net_pnl_currency": "USD", "costs_included": True, "cost_evidence_refs": ["fixture:broker-fees"], "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 40, "r_multiple": "-1", "timestamp_utc": "2026-01-02T10:00:00Z"},
            {"status": "reduce_submitted", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-unfilled", "quantity": 30, "r_multiple": "99", "timestamp_utc": "2026-01-02T11:00:00Z"},
        ]
        samples, skipped = feed.pair_samples(outcomes, [], {})
        pending = feed.collect_pending_predictions(outcomes, [], {})
        self.assertEqual(samples, [])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["trade_lifecycle_id"], "paper:ABC.US:entry-1")
        self.assertEqual(skipped["open_not_closed_yet"], 1)

        outcomes.append(
            {"status": "closed", "net_pnl": 10, "net_pnl_currency": "USD", "costs_included": True, "cost_evidence_refs": ["fixture:broker-fees"], "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-2", "quantity": 60, "r_multiple": "1", "timestamp_utc": "2026-01-03T10:00:00Z"}
        )
        samples, skipped = feed.pair_samples(outcomes, [], {})
        pending = feed.collect_pending_predictions(outcomes, [], {})
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["decision_ref"], "entry-1")
        self.assertEqual(samples[0]["trade_lifecycle_id"], "paper:ABC.US:entry-1")
        self.assertEqual(samples[0]["sample_id"], "paper-lifecycle:paper:ABC.US:entry-1")
        self.assertEqual(pending, [])
        self.assertNotIn("open_not_closed_yet", skipped)

    def test_dry_run_does_not_create_memory_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outcomes_path = root / "paper_outcomes.jsonl"
            orders_path = root / "paper_orders.jsonl"
            db_path = root / "must-not-exist.sqlite"
            outcomes_path.write_text(
                "\n".join([
                    json.dumps({"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 10, "timestamp_utc": "2026-01-01T10:00:00Z", "paper_prediction_contract": {"schema_version": 1, "prediction_id": "synthetic-entry-1", "proposal_id": "entry-1", "symbol": "ABC.US", "p": 0.62, "as_of": "2026-01-01T10:00:00Z", "frozen_at": "2026-01-01T10:00:00Z", "horizon_id": "swing_days", "target": "net_pnl_positive", "settlement": "full_lifecycle", "strategy_version": "synthetic-v1", "model_version": "synthetic-model-v1", "basis": "Synthetic pre-entry forecast fixture, not live evidence", "evidence_refs": ["fixture:prior-training-set"], "probability_kind": "ex_ante_forecast"}}),
                    json.dumps({"status": "closed", "net_pnl": 10, "net_pnl_currency": "USD", "costs_included": True, "cost_evidence_refs": ["fixture:broker-fees"], "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 10, "r_multiple": "1", "timestamp_utc": "2026-01-02T10:00:00Z"}),
                ]) + "\n",
                encoding="utf-8",
            )
            orders_path.write_text("", encoding="utf-8")
            result = feed.run(argparse.Namespace(
                paper_root=str(root),
                outcomes=str(outcomes_path),
                orders=str(orders_path),
                db=str(db_path),
                dry_run=True,
            ))
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["would_write_samples"], 1)
            self.assertEqual(result["inserted_count"], 0)
            self.assertFalse(db_path.exists())

    def test_unmatched_exit_never_becomes_calibration_sample(self) -> None:
        outcomes = [
            {"status": "closed", "net_pnl": 10, "net_pnl_currency": "USD", "costs_included": True, "cost_evidence_refs": ["fixture:broker-fees"], "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 10, "r_multiple": "1", "predicted_p": 0.9, "timestamp_utc": "2026-01-03T10:00:00Z"}
        ]
        samples, skipped = feed.pair_samples(outcomes, [], {})
        self.assertEqual(samples, [])
        self.assertEqual(skipped["unmatched_exit"], 1)


class LatestPositionSnapshotSymbolsTests(unittest.TestCase):
    def test_missing_snapshot_file_returns_none_not_empty_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "paper_position_snapshots.jsonl"
            self.assertIsNone(feed.latest_position_snapshot_symbols(missing))

    def test_returns_nonzero_quantity_symbols_from_the_latest_snapshot_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "paper_position_snapshots.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({
                            "event_type": "paper_position_snapshot",
                            "timestamp_utc": "2026-07-20T10:00:00Z",
                            "positions": [{"symbol": "AMD.US", "quantity": "5"}, {"symbol": "IWM.US", "quantity": "10"}],
                        }),
                        json.dumps({
                            "event_type": "paper_position_snapshot",
                            "timestamp_utc": "2026-07-24T19:55:23Z",
                            "positions": [{"symbol": "AAPL.US", "quantity": "14"}, {"symbol": "XLV.US", "quantity": "0"}],
                        }),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            symbols = feed.latest_position_snapshot_symbols(path)
            self.assertEqual(symbols, {"AAPL.US"})


class OrphanedPendingPredictionTests(unittest.TestCase):
    """paper-calibration-loop-stalled-reported-as-pending: a pending
    prediction whose symbol has no position left in the broker snapshot must
    be excluded from pending_prediction_count and reported separately,
    without ever being deleted or fabricated into a win/loss sample."""

    def _paper_root_with_snapshot(self, td: Path, held_symbols: list[str]) -> Path:
        root = Path(td)
        journal = root / "journal"
        journal.mkdir(parents=True)
        snapshot = {
            "event_type": "paper_position_snapshot",
            "timestamp_utc": "2026-07-24T19:55:23Z",
            "positions": [{"symbol": s, "quantity": "1"} for s in held_symbols],
        }
        (journal / "paper_position_snapshots.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        return root

    def test_pending_prediction_for_vanished_position_is_orphaned_and_excluded_from_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._paper_root_with_snapshot(td, held_symbols=["AAPL.US"])
            outcomes_path = root / "journal" / "paper_outcomes.jsonl"
            orders_path = root / "journal" / "paper_orders.jsonl"
            outcomes_path.write_text(
                "\n".join(
                    [
                        json.dumps({"status": "open", "symbol": "AMD.US", "side": "Buy", "proposal_id": "entry-amd", "quantity": 50, "timestamp_utc": "2026-06-30T10:00:00Z", "paper_prediction_contract": {"schema_version": 1, "prediction_id": "synthetic-entry-amd", "proposal_id": "entry-amd", "symbol": "AMD.US", "p": 0.62, "as_of": "2026-06-30T10:00:00Z", "frozen_at": "2026-06-30T10:00:00Z", "horizon_id": "swing_days", "target": "net_pnl_positive", "settlement": "full_lifecycle", "strategy_version": "synthetic-v1", "model_version": "synthetic-model-v1", "basis": "Synthetic pre-entry forecast fixture, not live evidence", "evidence_refs": ["fixture:prior-training-set"], "probability_kind": "ex_ante_forecast"}}),
                        json.dumps({"status": "open", "symbol": "AAPL.US", "side": "Buy", "proposal_id": "entry-aapl", "quantity": 14, "timestamp_utc": "2026-07-09T10:00:00Z", "paper_prediction_contract": {"schema_version": 1, "prediction_id": "synthetic-entry-aapl", "proposal_id": "entry-aapl", "symbol": "AAPL.US", "p": 0.62, "as_of": "2026-07-09T10:00:00Z", "frozen_at": "2026-07-09T10:00:00Z", "horizon_id": "swing_days", "target": "net_pnl_positive", "settlement": "full_lifecycle", "strategy_version": "synthetic-v1", "model_version": "synthetic-model-v1", "basis": "Synthetic pre-entry forecast fixture, not live evidence", "evidence_refs": ["fixture:prior-training-set"], "probability_kind": "ex_ante_forecast"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            orders_path.write_text("", encoding="utf-8")
            db_path = root / "memory.sqlite"

            result = feed.run(argparse.Namespace(
                paper_root=str(root),
                outcomes=str(outcomes_path),
                orders=str(orders_path),
                db=str(db_path),
                dry_run=True,
            ))

            self.assertEqual(result["pending_prediction_count"], 1)
            self.assertEqual(result["orphaned_predictions"], 1)
            self.assertEqual([e["symbol"] for e in result["pending_examples"]], ["AAPL.US"])
            self.assertEqual([e["symbol"] for e in result["orphaned_examples"]], ["AMD.US"])

    def test_orphaned_flag_persists_to_pending_table_without_deleting_the_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = self._paper_root_with_snapshot(td, held_symbols=[])
            outcomes_path = root / "journal" / "paper_outcomes.jsonl"
            orders_path = root / "journal" / "paper_orders.jsonl"
            outcomes_path.write_text(
                json.dumps({"status": "open", "symbol": "XLV.US", "side": "Buy", "proposal_id": "entry-xlv", "quantity": 20, "timestamp_utc": "2026-07-07T10:00:00Z", "paper_prediction_contract": {"schema_version": 1, "prediction_id": "synthetic-entry-xlv", "proposal_id": "entry-xlv", "symbol": "XLV.US", "p": 0.62, "as_of": "2026-07-07T10:00:00Z", "frozen_at": "2026-07-07T10:00:00Z", "horizon_id": "swing_days", "target": "net_pnl_positive", "settlement": "full_lifecycle", "strategy_version": "synthetic-v1", "model_version": "synthetic-model-v1", "basis": "Synthetic pre-entry forecast fixture, not live evidence", "evidence_refs": ["fixture:prior-training-set"], "probability_kind": "ex_ante_forecast"}}) + "\n",
                encoding="utf-8",
            )
            orders_path.write_text("", encoding="utf-8")
            db_path = root / "memory.sqlite"
            os.environ["TRADING_MEMORY_DB"] = str(db_path)
            try:
                result = feed.run(argparse.Namespace(
                    paper_root=str(root),
                    outcomes=str(outcomes_path),
                    orders=str(orders_path),
                    db=str(db_path),
                    dry_run=False,
                ))
            finally:
                os.environ.pop("TRADING_MEMORY_DB", None)

            self.assertEqual(result["pending_prediction_count"], 0)
            self.assertEqual(result["orphaned_predictions"], 1)

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            row = conn.execute(
                "SELECT proposal_ref, orphaned, orphaned_reason FROM calibration_pending_paper_predictions WHERE proposal_ref='entry-xlv'"
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row, "orphaned row must still exist, not be deleted")
            self.assertEqual(row[1], 1)
            self.assertEqual(row[2], feed.ORPHAN_REASON_POSITION_GONE)

    def test_missing_snapshot_never_orphans_anything(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)  # no journal/ dir at all -> snapshot file missing
            outcomes_path = root / "paper_outcomes.jsonl"
            orders_path = root / "paper_orders.jsonl"
            outcomes_path.write_text(
                json.dumps({"status": "open", "symbol": "AMD.US", "side": "Buy", "proposal_id": "entry-amd", "quantity": 50, "timestamp_utc": "2026-06-30T10:00:00Z", "paper_prediction_contract": {"schema_version": 1, "prediction_id": "synthetic-entry-amd", "proposal_id": "entry-amd", "symbol": "AMD.US", "p": 0.62, "as_of": "2026-06-30T10:00:00Z", "frozen_at": "2026-06-30T10:00:00Z", "horizon_id": "swing_days", "target": "net_pnl_positive", "settlement": "full_lifecycle", "strategy_version": "synthetic-v1", "model_version": "synthetic-model-v1", "basis": "Synthetic pre-entry forecast fixture, not live evidence", "evidence_refs": ["fixture:prior-training-set"], "probability_kind": "ex_ante_forecast"}}) + "\n",
                encoding="utf-8",
            )
            orders_path.write_text("", encoding="utf-8")
            result = feed.run(argparse.Namespace(
                paper_root=str(root),
                outcomes=str(outcomes_path),
                orders=str(orders_path),
                db=str(root / "memory.sqlite"),
                dry_run=True,
            ))
            self.assertEqual(result["orphaned_predictions"], 0)
            self.assertEqual(result["pending_prediction_count"], 1)


if __name__ == "__main__":
    unittest.main()
