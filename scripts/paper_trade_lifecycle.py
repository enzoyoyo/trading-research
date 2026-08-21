#!/usr/bin/env python3
"""Candidate-local reconstruction of unique paper-trade lifecycles.

Pure/read-only helpers. ``reduce_submitted`` and unfilled diagnostics are never
materiality events. Partial exits stay attached to their entry until the
lifecycle is fully closed. Release validation imports this bundled module only.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

OPEN_STATUSES = {"open", "filled_open"}
EXIT_STATUSES = {"partial_closed", "closed", "completed"}
CLOSED_STATUSES = {"closed", "completed"}
# Terminal state for a lifecycle whose symbol vanished from the broker position
# snapshot with no order-detail or execution record to confirm how it closed
# (see paper_fill_reconciler.make_closed_unreconciled_outcome). It is a real
# terminal state -- excluded from "open" counts -- but it is deliberately kept
# out of CLOSED_STATUSES/EXIT_STATUSES so nothing downstream (win-rate stats,
# calibration pairing) mistakes it for a normal, evidenced close.
UNRECONCILED_CLOSE_STATUS = "closed_unreconciled"
# Any status a trade can end up in that means "no longer open" for the purposes
# of counting/filtering, without implying a real, evidenced close.
TERMINAL_STATUSES = CLOSED_STATUSES | {UNRECONCILED_CLOSE_STATUS}
ORDER_ID_RE = re.compile(r'"order_id"\s*:\s*"?([0-9A-Za-z_-]+)"?')
# Statuses that constitute real, confirmed fill evidence -- mirrors
# paper_fill_reconciler.CONFIRMED_STATUSES exactly (open-side ∪ exit-side).
# Used only for the voided-order self-heal check below.
_CONFIRMED_FILL_STATUSES = OPEN_STATUSES | EXIT_STATUSES


def row_order_id(row: dict[str, Any]) -> str | None:
    if row.get("order_id"):
        return str(row["order_id"])
    result = (
        row.get("executor_result")
        if isinstance(row.get("executor_result"), dict)
        else row.get("result")
    )
    if not isinstance(result, dict):
        return None
    stdout = result.get("stdout")
    match = ORDER_ID_RE.search(stdout) if isinstance(stdout, str) else None
    return match.group(1) if match else None


def parse_dt(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(Decimal(str(value).replace(",", "")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def lifecycle_id(symbol: str, entry_ref: Any) -> str:
    return f"paper:{normalize_symbol(symbol)}:{str(entry_ref)}"


def row_ref(row: dict[str, Any]) -> str:
    return str(
        row.get("proposal_id")
        or row_order_id(row)
        or row.get("timestamp_utc")
        or "unknown"
    )


def is_open_fill(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status") or "").lower() in OPEN_STATUSES
        and str(row.get("side") or "").lower() in {"buy", "long", "open"}
    )


def is_reconciled_exit(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status") or "").lower() in EXIT_STATUSES
        and str(row.get("side") or "").lower() in {"sell", "short", "reduce", "exit"}
    )


def is_unreconciled_close(row: dict[str, Any]) -> bool:
    """A closed_unreconciled row carries no ``side`` (it isn't a fill), so it
    can't be recognized by is_reconciled_exit -- it needs its own check.
    """
    return str(row.get("status") or "").lower() == UNRECONCILED_CLOSE_STATUS


def _event_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row_order_id(row) or row.get("proposal_id") or ""),
        str(row.get("status") or "").lower(),
        str(row.get("timestamp_utc") or ""),
    )


def _voided_order_ids(rows: list[dict[str, Any]]) -> set[str]:
    """Order ids voided by a reconciliation_void/reconciliation_not_filled row,
    minus any that are self-healed by a real confirmed fill for that same
    order_id existing *anywhere* in rows -- presence, not chronological order.

    Candidate-local copy, kept in lockstep with System A's
    paper_trade_lifecycle.py. This is a deliberate reversal of an earlier
    time-ordered design that an adversarial review rejected: the void row's
    timestamp_utc is this process's local utc_now() at write time, while the
    confirm row's timestamp_utc is the broker's updated_at -- two different
    clocks that are not comparable, and on a stale read the void's local
    write time can land *after* the broker's true fill time, which is exactly
    the case the ordering was supposed to detect. Across the 25 real
    self-healed orders in the 2026-07-26 audit sample the confirm-to-void gap
    was as small as 7 seconds, well inside ordinary NTP/clock drift -- small
    enough that the two clocks being off by a few seconds flips which one
    looks "later". A time-ordered rule is also not actually permanent: a
    void arriving after the healing confirm re-voids an order that had
    already been healed, so the same order can flap voided/healed as more
    rows land, which is not how a real confirmed fill should ever behave.

    Presence-based is the correct semantics because it matches the layer
    below: paper_fill_reconciler.voided_order_ids() is itself presence-based
    (any confirmed fill for the order_id heals it, and confirmed fills are
    never re-sent/re-voided once written), and lifecycle aggregation must not
    be more aggressive than reconciliation -- if lifecycle voided something
    reconciliation had already healed, that is unrecoverable data loss (the
    row is still in rows, but the trade permanently vanishes from the
    lifecycle view). fill_confirmed=True is only ever written by the
    reconciler's broker-backfill path off real executions, so its presence is
    stronger evidence than any status-field inference, and it is safe to let
    it win regardless of where it falls in the row order.

    Without this, aggregate_lifecycles() permanently drops real fills whose
    order_id happened to be voided from a stale read -- the exact bug behind
    the 2026-07-26 deep-audit finding that misclassified five real donchian
    losses (SOXX/MU/AMD/IWM/XLV) as ghost-position closes, plus knock-on
    AAPL/MU-x-signal lifecycle tangles.
    """
    voided: set[str] = set()
    self_healed: set[str] = set()
    for row in rows:
        status = str(row.get("status") or "").lower()
        voids_id = row.get("voids_order_id")
        if (
            status in {"reconciliation_void", "reconciliation_not_filled"}
            and voids_id
        ):
            voided.add(str(voids_id))
        oid = row_order_id(row)
        if (
            oid
            and row.get("fill_confirmed") is True
            and status in _CONFIRMED_FILL_STATUSES
        ):
            self_healed.add(oid)
    return voided - self_healed


def _new_lifecycle(row: dict[str, Any]) -> dict[str, Any]:
    symbol = normalize_symbol(row.get("symbol"))
    entry_ref = row_ref(row)
    quantity = max(number(row.get("quantity")) or 0.0, 0.0)
    trade_id = str(row.get("trade_lifecycle_id") or lifecycle_id(symbol, entry_ref))
    return {
        "trade_lifecycle_id": trade_id,
        "entry_proposal_id": str(
            row.get("entry_proposal_id") or row.get("proposal_id") or entry_ref
        ),
        "entry_order_id": row_order_id(row),
        "symbol": symbol,
        "strategy": row.get("strategy"),
        "sleeve": row.get("sleeve"),
        "signals": row.get("signals") if isinstance(row.get("signals"), list) else [],
        "source_report": row.get("source_report"),
        "opened_at": row.get("timestamp_utc"),
        "entry_quantity": quantity,
        "entry_price": number(row.get("entry_price")),
        "planned_max_loss_pct": number(row.get("planned_max_loss_pct")),
        "remaining_quantity": quantity,
        "exit_quantity": 0.0,
        "exits": [],
        "status": "open",
        "completed_at": None,
        "r_multiple": None,
        "return_pct": None,
        "outcome": None,
        "entry_matched": True,
        "entry_payload": row,
        "_weighted_r": 0.0,
        "_r_weight": 0.0,
        "_weighted_return": 0.0,
        "_return_weight": 0.0,
    }


def _apply_exit(
    trade: dict[str, Any],
    row: dict[str, Any],
    allocation: float,
    force_close: bool,
) -> None:
    entry_qty = float(trade.get("entry_quantity") or 0.0)
    if allocation <= 0 and entry_qty > 0:
        allocation = float(trade.get("remaining_quantity") or 0.0)
    weight = allocation / entry_qty if entry_qty > 0 else (1.0 if force_close else 0.0)
    r_multiple = number(row.get("r_multiple"))
    return_pct = number(row.get("return_pct"))
    if r_multiple is not None:
        trade["_weighted_r"] += r_multiple * weight
        trade["_r_weight"] += weight
    if return_pct is not None:
        trade["_weighted_return"] += return_pct * weight
        trade["_return_weight"] += weight
    trade["exit_quantity"] = round(
        float(trade.get("exit_quantity") or 0.0) + allocation, 8
    )
    trade["remaining_quantity"] = max(
        round(float(trade.get("remaining_quantity") or 0.0) - allocation, 8), 0.0
    )
    trade["exits"].append(
        {
            "proposal_id": row.get("proposal_id"),
            "order_id": row_order_id(row),
            "timestamp_utc": row.get("timestamp_utc"),
            "status": row.get("status"),
            "quantity": allocation,
            "exit_price": number(row.get("exit_price") or row.get("limit_price")),
            "r_multiple": r_multiple,
            "return_pct": return_pct,
            "outcome": row.get("outcome"),
            "exit_reason": row.get("exit_reason"),
            "signals": (
                row.get("signals") if isinstance(row.get("signals"), list) else []
            ),
            "review_note": row.get("review_note"),
            "repair_note": row.get("repair_note"),
        }
    )
    should_close = (
        force_close
        or trade["remaining_quantity"] <= 1e-9
        or str(row.get("held_after") or "") == "0"
    )
    if should_close:
        trade["status"] = "closed"
        trade["remaining_quantity"] = 0.0
        trade["completed_at"] = row.get("timestamp_utc")
    else:
        trade["status"] = "partial"


def _apply_unreconciled_close(trade: dict[str, Any], row: dict[str, Any]) -> None:
    """Force-close a lifecycle with the closed_unreconciled terminal state.

    Deliberately never touches entry_quantity/remaining_quantity/r_multiple/
    return_pct -- those would imply a fill we have no evidence for. This only
    records that reconciliation gave up attributing a specific exit.
    """
    trade["status"] = UNRECONCILED_CLOSE_STATUS
    trade["completed_at"] = row.get("timestamp_utc")
    trade["outcome"] = "unreconciled"
    trade["exits"].append(
        {
            "proposal_id": row.get("proposal_id"),
            "order_id": row_order_id(row),
            "timestamp_utc": row.get("timestamp_utc"),
            "status": row.get("status"),
            "quantity": None,
            "exit_price": None,
            "r_multiple": None,
            "return_pct": None,
            "outcome": "unreconciled",
            "exit_reason": row.get("reason"),
            "reconciliation_note": row.get("reconciliation_note"),
        }
    )


def _finalize(trade: dict[str, Any]) -> dict[str, Any]:
    if trade["_r_weight"] > 0:
        trade["r_multiple"] = round(float(trade["_weighted_r"]), 4)
    if trade["_return_weight"] > 0:
        trade["return_pct"] = round(float(trade["_weighted_return"]), 4)
    if trade["status"] == "closed":
        if trade["r_multiple"] is not None:
            trade["outcome"] = "success" if trade["r_multiple"] > 0 else "failure"
        else:
            explicit = [
                str(exit_row.get("outcome") or "").lower()
                for exit_row in trade["exits"]
            ]
            if "failure" in explicit:
                trade["outcome"] = "failure"
            elif "success" in explicit:
                trade["outcome"] = "success"
    for key in ["_weighted_r", "_r_weight", "_weighted_return", "_return_weight"]:
        trade.pop(key, None)
    return trade


def aggregate_lifecycles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    voided = _voided_order_ids(rows)
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: (parse_dt(pair[1].get("timestamp_utc")), pair[0]),
    )
    trades: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    active: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmatched_exits: list[dict[str, Any]] = []
    seen_events: set[tuple[str, str, str]] = set()

    for _index, row in ordered:
        oid = str(row_order_id(row) or "")
        if oid and oid in voided:
            continue
        key = _event_key(row)
        if key in seen_events:
            continue
        seen_events.add(key)

        if is_open_fill(row):
            trade = _new_lifecycle(row)
            if trade["trade_lifecycle_id"] in by_id:
                continue
            trades.append(trade)
            by_id[trade["trade_lifecycle_id"]] = trade
            active[trade["symbol"]].append(trade)
            continue

        if is_unreconciled_close(row):
            symbol = normalize_symbol(row.get("symbol"))
            explicit_id = str(row.get("trade_lifecycle_id") or "")
            target = by_id.get(explicit_id) if explicit_id else None
            if target is None:
                open_candidates = [
                    trade
                    for trade in active[symbol]
                    if trade["status"] not in TERMINAL_STATUSES
                ]
                target = open_candidates[0] if open_candidates else None
            if target is None or target["status"] in TERMINAL_STATUSES:
                unmatched_exits.append(
                    {
                        "symbol": symbol,
                        "proposal_id": row.get("proposal_id"),
                        "order_id": row_order_id(row),
                        "timestamp_utc": row.get("timestamp_utc"),
                        "status": row.get("status"),
                    }
                )
                continue
            _apply_unreconciled_close(target, row)
            continue

        if not is_reconciled_exit(row):
            continue

        symbol = normalize_symbol(row.get("symbol"))
        explicit_id = str(row.get("trade_lifecycle_id") or "")
        candidates = (
            [by_id[explicit_id]]
            if explicit_id and explicit_id in by_id
            else [
                trade
                for trade in active[symbol]
                if trade["status"] not in TERMINAL_STATUSES
            ]
        )
        if not candidates:
            unmatched_exits.append(
                {
                    "symbol": symbol,
                    "proposal_id": row.get("proposal_id"),
                    "order_id": row_order_id(row),
                    "timestamp_utc": row.get("timestamp_utc"),
                    "status": row.get("status"),
                }
            )
            continue

        remaining_exit = max(number(row.get("quantity")) or 0.0, 0.0)
        force_close = str(row.get("status") or "").lower() in CLOSED_STATUSES
        for trade in candidates:
            if trade["status"] in TERMINAL_STATUSES:
                continue
            available = float(trade.get("remaining_quantity") or 0.0)
            allocation = (
                min(remaining_exit, available)
                if remaining_exit > 0 and available > 0
                else available
            )
            _apply_exit(
                trade,
                row,
                allocation,
                force_close and (remaining_exit <= available or len(candidates) == 1),
            )
            remaining_exit = max(remaining_exit - allocation, 0.0)
            if remaining_exit <= 1e-9:
                break

    finalized = [_finalize(trade) for trade in trades]
    complete = [
        trade
        for trade in finalized
        if trade["status"] == "closed" and trade["entry_matched"]
    ]
    return {
        "lifecycles": finalized,
        "complete_lifecycles": complete,
        "complete_lifecycle_count": len(complete),
        "open_lifecycle_count": sum(
            1 for trade in finalized if trade["status"] not in TERMINAL_STATUSES
        ),
        "unmatched_exits": unmatched_exits,
        "unmatched_exit_count": len(unmatched_exits),
    }


def resolve_active_lifecycle(
    rows: list[dict[str, Any]], symbol: str
) -> dict[str, Any] | None:
    result = aggregate_lifecycles(rows)
    wanted = normalize_symbol(symbol)
    active = [
        trade
        for trade in result["lifecycles"]
        if trade["symbol"] == wanted and trade["status"] not in TERMINAL_STATUSES
    ]
    return (
        sorted(active, key=lambda trade: parse_dt(trade.get("opened_at")))[0]
        if active
        else None
    )
