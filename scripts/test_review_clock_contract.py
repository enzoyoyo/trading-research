#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import record_due_results as recorder  # noqa: E402
import memory_review  # noqa: E402
from memory_schema import ensure_unique_result_decisions  # noqa: E402
from trading_memory_core import cmd_record_decision, cmd_record_result, connect  # noqa: E402


class ReviewClockContractTests(unittest.TestCase):
    def record(self, conn, root: Path, payload: dict) -> dict:
        path = root / f"{payload['symbol']}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return cmd_record_decision(conn, argparse.Namespace(payload=str(path), db=str(root / "memory.sqlite")))

    def result_args(self, root: Path, decision_id: str) -> argparse.Namespace:
        path = root / f"result-{decision_id}.json"
        path.write_text(
            json.dumps(
                {
                    "validation_time": "2026-07-10T00:00:00+00:00",
                    "outcome": "success",
                    "effective_factors": ["review_clock_factor"],
                }
            ),
            encoding="utf-8",
        )
        return argparse.Namespace(payload=str(path), decision_id=decision_id, db=None)

    def result_side_effects(self, conn, decision_id: str) -> tuple[int, int, int]:
        return (
            conn.execute(
                "SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)
            ).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM claims WHERE decision_id=? "
                "AND relation='factor_validated_after_review'",
                (decision_id,),
            ).fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE event_type='record_result' "
                "AND json_extract(payload_json, '$.decision_id')=?",
                (decision_id,),
            ).fetchone()[0],
        )

    def locked_due_pair(self, root: Path, *, winner_raises: bool) -> tuple[str, dict, dict]:
        db = root / "memory.sqlite"
        seed = connect(db)
        out = self.record(
            seed,
            root,
            {
                "symbol": "LOCKPAIR",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "price_at_decision": 10.0,
                "review_clock": "2020-01-01T00:00:00+00:00",
            },
        )
        decision_id = out["decision_id"]
        seed.execute(
            "CREATE TRIGGER hold_result_insert BEFORE INSERT ON results "
            "BEGIN SELECT hold_writer(); END"
        )
        seed.commit()
        seed.close()

        ready = threading.Barrier(2)
        hold_started = threading.Event()
        outputs: dict[str, dict] = {}
        errors: dict[str, BaseException] = {}

        def worker(role: str) -> None:
            conn = connect(db)
            if role == "winner":
                def hold_writer() -> int:
                    hold_started.set()
                    time.sleep(0.2)
                    if winner_raises:
                        raise RuntimeError("forced winner rollback")
                    return 0

                conn.create_function("hold_writer", 0, hold_writer)
            else:
                conn.execute("PRAGMA busy_timeout=50")
                conn.create_function("hold_writer", 0, lambda: 0)
            ready.wait(timeout=3)
            if role == "loser":
                hold_started.wait(timeout=3)
            try:
                outputs[role] = recorder.run(
                    conn,
                    datetime(2026, 7, 10, tzinfo=timezone.utc),
                    quote_fn=lambda _symbol: 11.0,
                    deadband_pct=recorder.DEFAULT_DEADBAND_PCT,
                    limit=1,
                    dry_run=False,
                )
            except BaseException as exc:
                errors[role] = exc
            finally:
                conn.close()

        threads = [
            threading.Thread(target=worker, args=("winner",)),
            threading.Thread(target=worker, args=("loser",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(any(thread.is_alive() for thread in threads), "locked due run deadlocked")
        return decision_id, outputs, errors

    def test_actionable_decision_rejects_non_iso_review_clock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            payload = {
                "symbol": "AVGO",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "review_clock": "2026-07-07 after US close",
            }
            with self.assertRaisesRegex(SystemExit, "review_clock must be timezone-aware ISO-8601"):
                self.record(conn, root, payload)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)
            conn.close()

    def test_actionable_decision_normalizes_valid_review_clock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            payload = {
                "symbol": "AVGO",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "review_clock": "2026-07-07T16:00:00-04:00",
            }
            self.record(conn, root, payload)
            stored = conn.execute("SELECT review_clock FROM decisions").fetchone()[0]
            self.assertEqual(stored, "2026-07-07T20:00:00+00:00")
            conn.close()

    def test_non_reviewable_decision_may_omit_review_clock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            payload = {
                "symbol": "CAL",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "valid_for_review": False,
            }
            out = self.record(conn, root, payload)
            self.assertTrue(out["ok"])
            conn.close()

    def test_publication_header_cannot_substitute_for_explicit_review_clock(self) -> None:
        header_variants = (
            {"published_at": "2026-07-31T22:11:43Z"},
            {"as_of": "2026-08-01T00:00:00Z"},
            {"retrieved_at": "2026-08-01T01:00:00Z", "source_review_clock": "2026-08-08T00:00:00Z"},
        )
        for index, header in enumerate(header_variants):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                conn = connect(root / "memory.sqlite")
                payload = {
                    "symbol": f"HDR{index}",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L1",
                    **header,
                }
                with self.assertRaisesRegex(SystemExit, "review_clock must be timezone-aware ISO-8601"):
                    self.record(conn, root, payload)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)
                conn.close()

    def test_due_run_surfaces_legacy_invalid_review_clock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            conn = connect(root / "memory.sqlite")
            payload = {
                "symbol": "100",
                "market": "HK",
                "direction": "long",
                "action_level": "L2",
                "price_at_decision": 10.0,
                "review_clock": "2026-07-09T08:00:00+08:00",
            }
            out = self.record(conn, root, payload)
            conn.execute(
                "UPDATE decisions SET review_clock=? WHERE decision_id=?",
                ("2026-07-09 HK close", out["decision_id"]),
            )
            conn.commit()
            result = recorder.run(
                conn,
                datetime(2026, 7, 10, tzinfo=timezone.utc),
                quote_fn=lambda _symbol: 11.0,
                deadband_pct=recorder.DEFAULT_DEADBAND_PCT,
                limit=50,
                dry_run=True,
            )
            self.assertEqual(result["recorded_count"], 0)
            self.assertEqual(
                result["invalid_review_clocks"],
                [{"decision_id": out["decision_id"], "symbol": "100", "review_clock": "2026-07-09 HK close"}],
            )
            conn.close()

    def test_due_limit_still_surfaces_later_invalid_clock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            due = self.record(
                conn,
                root,
                {
                    "symbol": "FIRST",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "price_at_decision": 10.0,
                    "analysis_time": "2026-01-01T00:00:00+00:00",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            invalid = self.record(
                conn,
                root,
                {
                    "symbol": "LATER",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "price_at_decision": 10.0,
                    "analysis_time": "2026-01-02T00:00:00+00:00",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            conn.execute(
                "UPDATE decisions SET review_clock='after US close' WHERE decision_id=?",
                (invalid["decision_id"],),
            )
            conn.commit()
            expected_invalid = [{
                "decision_id": invalid["decision_id"],
                "symbol": "LATER",
                "review_clock": "after US close",
            }]

            dry = recorder.run(
                conn,
                datetime(2026, 7, 10, tzinfo=timezone.utc),
                quote_fn=lambda _symbol: 11.0,
                deadband_pct=recorder.DEFAULT_DEADBAND_PCT,
                limit=1,
                dry_run=True,
            )
            self.assertEqual(dry["recorded_count"], 1)
            self.assertEqual(dry["recorded"][0]["decision_id"], due["decision_id"])
            self.assertEqual(dry["invalid_review_clocks"], expected_invalid)
            self.assertEqual(self.result_side_effects(conn, due["decision_id"]), (0, 0, 0))

            live = recorder.run(
                conn,
                datetime(2026, 7, 10, tzinfo=timezone.utc),
                quote_fn=lambda _symbol: 11.0,
                deadband_pct=recorder.DEFAULT_DEADBAND_PCT,
                limit=1,
                dry_run=False,
            )
            self.assertEqual(live["recorded_count"], 1)
            self.assertEqual(live["recorded"][0]["decision_id"], due["decision_id"])
            self.assertEqual(live["invalid_review_clocks"], expected_invalid)
            self.assertEqual(self.result_side_effects(conn, due["decision_id"]), (1, 0, 1))
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM results WHERE decision_id=?",
                    (invalid["decision_id"],),
                ).fetchone()[0],
                0,
            )
            conn.close()

    def test_outer_transaction_success_remains_caller_owned(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            out = self.record(
                conn,
                root,
                {
                    "symbol": "OUTEROK",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            conn.execute("CREATE TABLE caller_state (value TEXT)")
            conn.commit()
            conn.execute("BEGIN")
            conn.execute("INSERT INTO caller_state VALUES ('caller-pending')")

            result = cmd_record_result(conn, self.result_args(root, out["decision_id"]))
            self.assertTrue(result["recorded"])
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 1)
            self.assertEqual(self.result_side_effects(conn, out["decision_id"]), (1, 1, 1))

            observer = sqlite3.connect(db)
            try:
                self.assertEqual(observer.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 0)
                self.assertEqual(observer.execute("SELECT COUNT(*) FROM results").fetchone()[0], 0)
            finally:
                observer.close()
            conn.rollback()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 0)
            self.assertEqual(self.result_side_effects(conn, out["decision_id"]), (0, 0, 0))
            conn.close()

    def test_outer_transaction_failure_rolls_back_only_result_savepoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            out = self.record(
                conn,
                root,
                {
                    "symbol": "OUTERERR",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            conn.execute("CREATE TABLE caller_state (value TEXT)")
            conn.execute(
                "CREATE TRIGGER force_result_event_failure BEFORE INSERT ON memory_events "
                "WHEN NEW.event_type='record_result' BEGIN "
                "SELECT RAISE(ABORT, 'forced result event failure'); END"
            )
            conn.commit()
            conn.execute("BEGIN")
            conn.execute("INSERT INTO caller_state VALUES ('caller-pending')")

            with self.assertRaisesRegex(sqlite3.IntegrityError, "forced result event failure"):
                cmd_record_result(conn, self.result_args(root, out["decision_id"]))
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 1)
            self.assertEqual(self.result_side_effects(conn, out["decision_id"]), (0, 0, 0))
            conn.commit()
            conn.close()

            audit = sqlite3.connect(db)
            try:
                self.assertEqual(audit.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 1)
                self.assertEqual(audit.execute("SELECT COUNT(*) FROM results").fetchone()[0], 0)
            finally:
                audit.close()

    def test_migration_rejects_outer_transaction_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            conn.execute("CREATE TABLE caller_state (value TEXT)")
            conn.commit()
            conn.execute("BEGIN")
            conn.execute("INSERT INTO caller_state VALUES ('caller-pending')")
            observed = None
            try:
                ensure_unique_result_decisions(conn)
            except BaseException as exc:
                observed = exc
            self.assertIsInstance(observed, RuntimeError)
            self.assertIn("active transaction", str(observed))
            self.assertTrue(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 1)
            conn.commit()
            conn.close()

            audit = sqlite3.connect(db)
            try:
                self.assertEqual(audit.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0], 1)
            finally:
                audit.close()

    def test_slow_winner_returns_deterministic_loser_skip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            decision_id, outputs, errors = self.locked_due_pair(root, winner_raises=False)
            self.assertEqual(errors, {})
            self.assertEqual(sorted(row["recorded_count"] for row in outputs.values()), [0, 1])
            self.assertEqual(outputs["loser"]["skipped_already_processed"], [decision_id])
            audit = sqlite3.connect(root / "memory.sqlite")
            try:
                self.assertEqual(
                    audit.execute("SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    audit.execute(
                        "SELECT COUNT(*) FROM memory_events WHERE event_type='record_result'"
                    ).fetchone()[0],
                    1,
                )
            finally:
                audit.close()

    def test_winner_rollback_allows_loser_to_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            decision_id, outputs, errors = self.locked_due_pair(root, winner_raises=True)
            self.assertIn("winner", errors)
            self.assertNotIn("loser", errors)
            self.assertEqual(outputs["loser"]["recorded_count"], 1)
            self.assertEqual(outputs["loser"]["skipped_already_processed"], [])
            audit = sqlite3.connect(root / "memory.sqlite")
            try:
                self.assertEqual(
                    audit.execute("SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    audit.execute(
                        "SELECT COUNT(*) FROM memory_events WHERE event_type='record_result'"
                    ).fetchone()[0],
                    1,
                )
            finally:
                audit.close()

    def test_exhausted_lock_wait_returns_structured_skip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            seed = connect(db)
            out = self.record(
                seed,
                root,
                {
                    "symbol": "TIMEOUT",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "price_at_decision": 10.0,
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            decision_id = out["decision_id"]
            seed.close()

            ready = threading.Event()
            start = threading.Event()
            output: dict[str, dict] = {}
            errors: list[BaseException] = []
            sentinel = object()
            previous = getattr(memory_review, "RESULT_LOCK_WAIT_SECONDS", sentinel)
            memory_review.RESULT_LOCK_WAIT_SECONDS = 0.12

            def loser() -> None:
                conn = connect(db)
                conn.execute("PRAGMA busy_timeout=25")
                ready.set()
                start.wait(timeout=2)
                try:
                    output["value"] = recorder.run(
                        conn,
                        datetime(2026, 7, 10, tzinfo=timezone.utc),
                        quote_fn=lambda _symbol: 11.0,
                        deadband_pct=recorder.DEFAULT_DEADBAND_PCT,
                        limit=1,
                        dry_run=False,
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    conn.close()

            thread = threading.Thread(target=loser)
            thread.start()
            ready.wait(timeout=2)
            locker = connect(db)
            locker.execute("BEGIN IMMEDIATE")
            start.set()
            thread.join(timeout=2)
            locker.rollback()
            locker.close()
            if previous is sentinel:
                delattr(memory_review, "RESULT_LOCK_WAIT_SECONDS")
            else:
                memory_review.RESULT_LOCK_WAIT_SECONDS = previous

            self.assertFalse(thread.is_alive(), "bounded lock wait did not finish")
            self.assertEqual(errors, [])
            result = output["value"]
            self.assertEqual(result["recorded_count"], 0)
            self.assertEqual(result["skipped_already_processed"], [])
            self.assertEqual(result["skipped_lock_timeout"], [decision_id])
            audit = sqlite3.connect(db)
            try:
                self.assertEqual(
                    audit.execute("SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    audit.execute(
                        "SELECT COUNT(*) FROM memory_events WHERE event_type='record_result'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                audit.close()

    def test_concurrent_due_runs_record_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            seed = connect(db)
            out = self.record(
                seed,
                root,
                {
                    "symbol": "RACE",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "price_at_decision": 10.0,
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            decision_id = out["decision_id"]
            seed.close()

            barrier = threading.Barrier(2)
            outputs: list[dict | None] = [None, None]
            errors: list[BaseException] = []

            def worker(index: int) -> None:
                conn = connect(db)
                try:
                    def overlapping_quote(_symbol: str) -> float:
                        barrier.wait(timeout=10)
                        return 11.0

                    outputs[index] = recorder.run(
                        conn,
                        datetime(2026, 7, 10, tzinfo=timezone.utc),
                        quote_fn=overlapping_quote,
                        deadband_pct=recorder.DEFAULT_DEADBAND_PCT,
                        limit=50,
                        dry_run=False,
                    )
                except BaseException as exc:  # surfaced in the test thread below
                    errors.append(exc)
                finally:
                    conn.close()

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertFalse(any(thread.is_alive() for thread in threads), "concurrent due run deadlocked")
            if errors:
                raise errors[0]

            audit = sqlite3.connect(db)
            try:
                self.assertEqual(
                    audit.execute("SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    audit.execute(
                        "SELECT COUNT(*) FROM memory_events WHERE event_type='record_result' "
                        "AND json_extract(payload_json, '$.decision_id')=?",
                        (decision_id,),
                    ).fetchone()[0],
                    1,
                )
            finally:
                audit.close()

            completed = [item for item in outputs if item is not None]
            self.assertEqual(sorted(item["recorded_count"] for item in completed), [0, 1])
            loser = next(item for item in completed if item["recorded_count"] == 0)
            self.assertEqual(loser["skipped_already_processed"], [decision_id])

    def test_manual_duplicate_result_has_one_claim_and_event_set(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            out = self.record(
                conn,
                root,
                {
                    "symbol": "MANUAL",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            decision_id = out["decision_id"]
            result_path = root / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "validation_time": "2026-07-10T00:00:00+00:00",
                        "outcome": "success",
                        "effective_factors": ["manual_factor"],
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                payload=str(result_path), decision_id=decision_id, db=str(db)
            )
            first = cmd_record_result(conn, args)
            second = cmd_record_result(conn, args)

            self.assertTrue(first["recorded"])
            self.assertFalse(first["already_processed"])
            self.assertFalse(second["recorded"])
            self.assertTrue(second["already_processed"])
            self.assertEqual(second["result_id"], first["result_id"])
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM claims WHERE decision_id=? "
                    "AND relation='factor_validated_after_review'",
                    (decision_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM memory_events WHERE event_type='record_result' "
                    "AND json_extract(payload_json, '$.decision_id')=?",
                    (decision_id,),
                ).fetchone()[0],
                1,
            )
            conn.close()

    def test_legacy_single_result_index_migrates_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            out = self.record(
                conn,
                root,
                {
                    "symbol": "MIGRATE",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            decision_id = out["decision_id"]
            conn.close()

            legacy = sqlite3.connect(db)
            legacy.execute("DROP INDEX idx_results_decision")
            legacy.execute("CREATE INDEX idx_results_decision ON results(decision_id)")
            legacy.execute(
                "INSERT INTO results "
                "(result_id, decision_id, validation_time, outcome, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-result", decision_id, "2026-07-10T00:00:00+00:00", "success", "{}", "2026-07-10T00:00:00+00:00"),
            )
            legacy.commit()
            legacy.close()

            migrated = connect(db)
            try:
                result = migrated.execute(
                    "SELECT result_id, decision_id FROM results"
                ).fetchone()
                self.assertEqual(tuple(result), ("legacy-result", decision_id))
                index = next(
                    row
                    for row in migrated.execute("PRAGMA index_list('results')").fetchall()
                    if row[1] == "idx_results_decision"
                )
                self.assertEqual(index[2], 1)
            finally:
                migrated.close()

    def test_legacy_duplicate_result_migration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "memory.sqlite"
            conn = connect(db)
            out = self.record(
                conn,
                root,
                {
                    "symbol": "LEGACY",
                    "market": "US",
                    "direction": "long",
                    "action_level": "L2",
                    "review_clock": "2020-01-01T00:00:00+00:00",
                },
            )
            decision_id = out["decision_id"]
            conn.close()

            legacy = sqlite3.connect(db)
            legacy.execute("DROP INDEX idx_results_decision")
            for result_id in ("legacy-result-1", "legacy-result-2"):
                legacy.execute(
                    "INSERT INTO results "
                    "(result_id, decision_id, validation_time, outcome, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (result_id, decision_id, "2026-07-10T00:00:00+00:00", "success", "{}", "2026-07-10T00:00:00+00:00"),
                )
            legacy.commit()
            legacy.close()

            try:
                migrated = connect(db)
            except RuntimeError as exc:
                self.assertRegex(str(exc), r"duplicate results.*decision_id")
            else:
                migrated.close()
                self.fail("legacy duplicate results were not rejected")

            audit = sqlite3.connect(db)
            try:
                self.assertEqual(
                    audit.execute("SELECT COUNT(*) FROM results WHERE decision_id=?", (decision_id,)).fetchone()[0],
                    2,
                )
            finally:
                audit.close()


if __name__ == "__main__":
    unittest.main()
