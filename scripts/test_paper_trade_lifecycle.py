#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import paper_trade_lifecycle as lifecycle  # noqa: E402


class PaperTradeLifecycleTests(unittest.TestCase):
    def test_partial_exits_remain_one_incomplete_lifecycle(self) -> None:
        rows = [
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 100, "timestamp_utc": "2026-01-01T10:00:00Z"},
            {"status": "partial_closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 40, "r_multiple": "-1.0", "timestamp_utc": "2026-01-02T10:00:00Z"},
            {"status": "partial_closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-2", "quantity": 30, "r_multiple": "0.5", "timestamp_utc": "2026-01-03T10:00:00Z"},
            {"status": "reduce_submitted", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-unfilled", "quantity": 30, "r_multiple": "99", "timestamp_utc": "2026-01-03T11:00:00Z"},
        ]
        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(len(result["lifecycles"]), 1)
        trade = result["lifecycles"][0]
        self.assertEqual(trade["trade_lifecycle_id"], "paper:ABC.US:entry-1")
        self.assertEqual(trade["status"], "partial")
        self.assertEqual(trade["remaining_quantity"], 30.0)
        self.assertEqual(len(trade["exits"]), 2)
        self.assertEqual(result["complete_lifecycle_count"], 0)

    def test_final_close_completes_once_and_weights_partial_r(self) -> None:
        rows = [
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 100, "timestamp_utc": "2026-01-01T10:00:00Z"},
            {"status": "partial_closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 40, "r_multiple": "-1.0", "timestamp_utc": "2026-01-02T10:00:00Z"},
            {"status": "closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-2", "quantity": 60, "r_multiple": "0.5", "timestamp_utc": "2026-01-03T10:00:00Z"},
        ]
        result = lifecycle.aggregate_lifecycles(rows)
        trade = result["lifecycles"][0]
        self.assertEqual(result["complete_lifecycle_count"], 1)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["remaining_quantity"], 0.0)
        self.assertAlmostEqual(trade["r_multiple"], -0.1)
        self.assertEqual(trade["outcome"], "failure")

    def test_fifo_maps_two_entries_without_double_counting(self) -> None:
        rows = [
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 10, "timestamp_utc": "2026-01-01T10:00:00Z"},
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-2", "quantity": 20, "timestamp_utc": "2026-01-01T11:00:00Z"},
            {"status": "closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 10, "r_multiple": "1", "timestamp_utc": "2026-01-02T10:00:00Z"},
            {"status": "closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-2", "quantity": 20, "r_multiple": "-1", "timestamp_utc": "2026-01-03T10:00:00Z"},
        ]
        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(result["complete_lifecycle_count"], 2)
        self.assertEqual([row["entry_proposal_id"] for row in result["complete_lifecycles"]], ["entry-1", "entry-2"])

    def test_unmatched_exit_is_diagnostic_not_materiality(self) -> None:
        rows = [
            {"status": "closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 10, "r_multiple": "1", "timestamp_utc": "2026-01-02T10:00:00Z"},
        ]
        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(result["complete_lifecycle_count"], 0)
        self.assertEqual(result["unmatched_exit_count"], 1)

    def test_voided_order_self_heals_when_real_fill_arrives_later(self) -> None:
        """A stale reconciliation_not_filled void must not permanently bury a
        real confirmed fill for the same order_id that shows up afterwards.

        This is the exact 2026-07-26 deep-audit shape: a reduce_submitted exit
        gets voided from a status snapshot taken before the fill posted, then a
        real fill_confirmed=True row for the same order_id arrives later. Before
        the self-heal fix, aggregate_lifecycles() would drop the real fill
        forever (`oid in voided` short-circuit), leaving the lifecycle open with
        its full entry_quantity still "remaining" despite the real -1.94R exit.
        """
        rows = [
            {"status": "open", "symbol": "MU.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 80, "timestamp_utc": "2026-06-22T13:41:13Z"},
            {"status": "reduce_submitted", "symbol": "MU.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 80, "r_multiple": "-1.94", "fill_confirmed": False, "timestamp_utc": "2026-07-06T13:30:28Z"},
            {"status": "reconciliation_not_filled", "symbol": "MU.US", "order_id": "not-filled-999", "voids_order_id": "999", "timestamp_utc": "2026-07-06T13:31:15Z"},
            {"status": "partial_closed", "symbol": "MU.US", "side": "Sell", "order_id": "999", "proposal_id": "exit-1", "quantity": 80, "r_multiple": "-1.94", "fill_confirmed": True, "timestamp_utc": "2026-07-06T13:35:02Z"},
        ]

        # Pre-fix behavior (what this test guards against regressing to): the
        # unhealed voided set buries the real fill and the trade stays open.
        buggy_voided = {
            str(row.get("voids_order_id"))
            for row in rows
            if str(row.get("status") or "").lower() in {"reconciliation_void", "reconciliation_not_filled"}
            and row.get("voids_order_id")
        }
        self.assertIn("999", buggy_voided, "sanity check: order 999 is voided pre-heal")

        healed = lifecycle._voided_order_ids(rows)
        self.assertNotIn("999", healed, "self-heal must un-void an order with a later confirmed fill")

        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(len(result["lifecycles"]), 1)
        trade = result["lifecycles"][0]
        self.assertEqual(trade["status"], "closed", "real fill must close the lifecycle, not leave it phantom-open")
        self.assertEqual(trade["remaining_quantity"], 0.0)
        self.assertAlmostEqual(trade["r_multiple"], -1.94)
        self.assertEqual(len(trade["exits"]), 1)

    def test_voided_order_stays_voided_without_later_confirmed_fill(self) -> None:
        """Self-heal must not un-void orders that never actually filled -- only
        a later fill_confirmed=True row for the same order_id proves the void
        was wrong. A plain resubmit under a different order_id must not heal
        the original void.
        """
        rows = [
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 10, "timestamp_utc": "2026-01-01T10:00:00Z"},
            {"status": "reduce_submitted", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 10, "r_multiple": "-1.0", "fill_confirmed": False, "timestamp_utc": "2026-01-02T10:00:00Z"},
            {"status": "reconciliation_not_filled", "symbol": "ABC.US", "order_id": "not-filled-111", "voids_order_id": "111", "timestamp_utc": "2026-01-02T10:01:00Z"},
        ]
        healed = lifecycle._voided_order_ids(rows)
        self.assertIn("111", healed, "an order with no later confirmed fill must remain voided")

        result = lifecycle.aggregate_lifecycles(rows)
        trade = result["lifecycles"][0]
        self.assertEqual(trade["status"], "open")
        self.assertEqual(trade["remaining_quantity"], 10.0)

    def test_confirmed_fill_followed_by_later_void_still_self_heals(self) -> None:
        """Companion to the self-heal test above with the two reconciliation
        events swapped in time: a real confirmed fill happens first, and a
        void for the same order_id arrives afterwards. Self-heal is
        presence-based, not chronological -- an adversarial review rejected
        the earlier time-ordered design because the void row's timestamp_utc
        is this process's local clock while the confirm row's timestamp_utc
        is the broker's clock, and those two clocks are not comparable (on a
        stale read the local void timestamp can land after the broker's true
        fill time, which is exactly backwards). So a confirmed fill for the
        order_id heals it regardless of whether the void row appears before
        or after it in the rows. The real -1.94R exit must close the
        lifecycle and be counted, exactly like the void-then-confirm case.
        """
        rows = [
            {"status": "open", "symbol": "MU.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 80, "timestamp_utc": "2026-06-22T13:41:13Z"},
            {"status": "partial_closed", "symbol": "MU.US", "side": "Sell", "order_id": "777", "proposal_id": "exit-1", "quantity": 80, "r_multiple": "-1.94", "fill_confirmed": True, "timestamp_utc": "2026-07-06T13:30:28Z"},
            {"status": "reconciliation_void", "symbol": "MU.US", "order_id": "void-777", "voids_order_id": "777", "timestamp_utc": "2026-07-06T13:35:02Z"},
        ]

        healed = lifecycle._voided_order_ids(rows)
        self.assertNotIn("777", healed, "a confirmed fill for the order_id self-heals it regardless of void order")

        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(len(result["lifecycles"]), 1)
        trade = result["lifecycles"][0]
        self.assertEqual(trade["status"], "closed", "the real fill must close the lifecycle even though the void arrived later")
        self.assertEqual(trade["remaining_quantity"], 0.0)
        self.assertAlmostEqual(trade["r_multiple"], -1.94)
        self.assertEqual(len(trade["exits"]), 1)
        self.assertEqual(result["complete_lifecycle_count"], 1)

    def test_void_row_missing_timestamp_still_self_heals_by_presence(self) -> None:
        """A void row with a missing/unparseable timestamp_utc used to sort
        last under parse_dt's datetime.max fallback, which under the old
        time-ordered design would make a garbage-timestamp void permanently
        outrank any confirm -- a broken timestamp effectively winning the
        dispute forever. Presence-based self-heal is immune to this: it never
        looks at timestamps at all, so a confirmed fill for the same
        order_id still heals the void no matter what the void row's
        timestamp field contains.
        """
        rows = [
            {"status": "open", "symbol": "MU.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 80, "timestamp_utc": "2026-06-22T13:41:13Z"},
            {"status": "partial_closed", "symbol": "MU.US", "side": "Sell", "order_id": "888", "proposal_id": "exit-1", "quantity": 80, "r_multiple": "-1.94", "fill_confirmed": True, "timestamp_utc": "2026-07-06T13:30:28Z"},
            {"status": "reconciliation_void", "symbol": "MU.US", "order_id": "void-888", "voids_order_id": "888", "timestamp_utc": None},
        ]

        healed = lifecycle._voided_order_ids(rows)
        self.assertNotIn("888", healed, "a missing/unparseable void timestamp must not defeat presence-based self-heal")

        result = lifecycle.aggregate_lifecycles(rows)
        trade = result["lifecycles"][0]
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(result["complete_lifecycle_count"], 1)

    def test_active_lifecycle_resolver_returns_oldest_open_entry(self) -> None:
        rows = [
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 10, "timestamp_utc": "2026-01-01T10:00:00Z"},
            {"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "entry-2", "quantity": 20, "timestamp_utc": "2026-01-01T11:00:00Z"},
        ]
        active = lifecycle.resolve_active_lifecycle(rows, "ABC.US")
        self.assertEqual(active["entry_proposal_id"], "entry-1")
        self.assertEqual(active["trade_lifecycle_id"], "paper:ABC.US:entry-1")

    def test_closed_unreconciled_row_closes_target_lifecycle_and_is_excluded_from_complete(self) -> None:
        """A closed_unreconciled row (ghost-position close with no order-detail
        or execution record) must terminate the matching open lifecycle without
        fabricating quantity/R evidence, and must never enter complete_lifecycles
        -- win-rate stats and calibration pairing require an evidenced close.
        """
        rows = [
            {"status": "open", "symbol": "XLV.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 50, "timestamp_utc": "2026-07-01T10:00:00Z"},
            {"status": "closed_unreconciled", "symbol": "XLV.US", "proposal_id": "recon-1", "reason": "position vanished from snapshot", "reconciliation_note": "no order-detail match", "timestamp_utc": "2026-07-10T10:00:00Z"},
        ]
        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(len(result["lifecycles"]), 1)
        trade = result["lifecycles"][0]
        self.assertEqual(trade["status"], "closed_unreconciled")
        self.assertEqual(trade["outcome"], "unreconciled")
        # Deliberately untouched -- no fabricated fill evidence.
        self.assertEqual(trade["remaining_quantity"], 50.0)
        self.assertIsNone(trade["r_multiple"])
        self.assertEqual(len(trade["exits"]), 1)
        self.assertEqual(trade["exits"][0]["outcome"], "unreconciled")
        # Excluded from complete_lifecycles / complete_lifecycle_count.
        self.assertEqual(result["complete_lifecycle_count"], 0)
        self.assertEqual(result["complete_lifecycles"], [])
        # Excluded from open_lifecycle_count -- it is terminal, just unevidenced.
        self.assertEqual(result["open_lifecycle_count"], 0)

    def test_closed_unreconciled_row_without_matching_open_lifecycle_becomes_unmatched_exit(self) -> None:
        """When no open lifecycle exists for the symbol (or the explicit
        trade_lifecycle_id doesn't resolve to one), the closed_unreconciled row
        must land in unmatched_exits rather than being silently dropped or
        attached to the wrong trade.
        """
        rows = [
            {"status": "closed_unreconciled", "symbol": "IWM.US", "proposal_id": "recon-1", "reason": "position vanished from snapshot", "timestamp_utc": "2026-07-10T10:00:00Z"},
        ]
        result = lifecycle.aggregate_lifecycles(rows)
        self.assertEqual(len(result["lifecycles"]), 0)
        self.assertEqual(result["unmatched_exit_count"], 1)
        self.assertEqual(result["unmatched_exits"][0]["symbol"], "IWM.US")
        self.assertEqual(result["unmatched_exits"][0]["status"], "closed_unreconciled")

        # Also verify the case where every candidate lifecycle for the symbol
        # is already terminal (closed) -- the unreconciled row must not reopen
        # or re-target a closed trade.
        rows_with_already_closed = [
            {"status": "open", "symbol": "SOXX.US", "side": "Buy", "proposal_id": "entry-1", "quantity": 20, "timestamp_utc": "2026-07-01T10:00:00Z"},
            {"status": "closed", "symbol": "SOXX.US", "side": "Sell", "proposal_id": "exit-1", "quantity": 20, "r_multiple": "1.0", "timestamp_utc": "2026-07-05T10:00:00Z"},
            {"status": "closed_unreconciled", "symbol": "SOXX.US", "proposal_id": "recon-2", "reason": "stale reconciliation", "timestamp_utc": "2026-07-10T10:00:00Z"},
        ]
        result2 = lifecycle.aggregate_lifecycles(rows_with_already_closed)
        self.assertEqual(result2["complete_lifecycle_count"], 1)
        self.assertEqual(result2["lifecycles"][0]["status"], "closed")
        self.assertEqual(result2["unmatched_exit_count"], 1)
        self.assertEqual(result2["unmatched_exits"][0]["status"], "closed_unreconciled")


if __name__ == "__main__":
    unittest.main()
