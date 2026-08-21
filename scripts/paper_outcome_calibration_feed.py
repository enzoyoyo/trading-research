#!/usr/bin/env python3
"""Read-only System A paper-outcome calibration feed for trading-research.

Translates paper-trading closed outcomes into a separate calibration bucket.
It never writes to System A, never mixes paper samples into the skill decision
memory result chain, and never makes paper samples materiality-eligible.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from trading_memory_core import db_path, connect, now_iso, jdump

HOME = Path.home()
DEFAULT_PAPER_ROOT = HOME / ".hermes" / "longbridge-paper-trading"
DEFAULT_OUTCOMES = DEFAULT_PAPER_ROOT / "journal" / "paper_outcomes.jsonl"
DEFAULT_ORDERS = DEFAULT_PAPER_ROOT / "journal" / "paper_orders.jsonl"
DEFAULT_SNAPSHOTS = DEFAULT_PAPER_ROOT / "journal" / "paper_position_snapshots.jsonl"
TABLE = "calibration_samples_paper"
PENDING_TABLE = "calibration_pending_paper_predictions"
# 2026-07-26 P0 repair (paper-calibration-loop-stalled-reported-as-pending):
# a pending prediction whose symbol has no position left in the latest broker
# snapshot can never pair (System A's own lifecycle bookkeeping has no
# evidence to close it either -- that's the root outage this repairs). Rather
# than let it sit forever silently inflating pending_prediction_count, it is
# marked orphaned and excluded from the "awaiting close" count -- but it is
# never deleted and never turned into a fabricated win/loss sample.
ORPHAN_REASON_POSITION_GONE = "position_not_in_latest_snapshot"
BUNDLED_LIFECYCLE_MODULE = Path(__file__).resolve().with_name("paper_trade_lifecycle.py")
DEFAULT_LIFECYCLE_MODULE = BUNDLED_LIFECYCLE_MODULE
LIFECYCLE_BINDING_ENV = "PAPER_TRADE_LIFECYCLE_MODULE"
_LIFECYCLE_MODULE: Any | None = None


def lifecycle_module() -> Any:
    global _LIFECYCLE_MODULE
    configured = os.environ.get(LIFECYCLE_BINDING_ENV, str(DEFAULT_LIFECYCLE_MODULE))
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise RuntimeError("paper lifecycle binding must be an absolute candidate-local path")
    if path != BUNDLED_LIFECYCLE_MODULE or path.is_symlink():
        raise RuntimeError(
            "paper lifecycle binding must be the bundled candidate-local module; "
            "external and symlink modules are forbidden"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"bundled paper lifecycle module unavailable: {exc}") from exc
    if resolved != BUNDLED_LIFECYCLE_MODULE or not resolved.is_file():
        raise RuntimeError("paper lifecycle binding escaped the bundled candidate path")
    if _LIFECYCLE_MODULE is not None:
        return _LIFECYCLE_MODULE
    spec = importlib.util.spec_from_file_location(
        "trading_research_bundled_paper_trade_lifecycle", resolved
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load bundled paper lifecycle module: {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _LIFECYCLE_MODULE = module
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def latest_position_snapshot_symbols(path: Path) -> set[str] | None:
    """Symbols holding a nonzero quantity in the most recent broker position
    snapshot. Returns None (not an empty set) when no readable snapshot
    exists -- callers must treat that as "unknown" and must never orphan a
    pending prediction on the strength of a snapshot we couldn't read.
    """
    if not path.exists():
        return None
    latest: dict[str, Any] | None = None
    latest_ts = ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("event_type") != "paper_position_snapshot":
            continue
        ts = str(obj.get("timestamp_utc") or "")
        if latest is None or ts >= latest_ts:
            latest, latest_ts = obj, ts
    if latest is None:
        return None
    symbols: set[str] = set()
    for pos in latest.get("positions") or []:
        if not isinstance(pos, dict) or not pos.get("symbol"):
            continue
        qty = to_float(pos.get("quantity"))
        if qty is not None and qty > 0:
            symbols.add(str(pos["symbol"]).upper())
    return symbols


def to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except Exception:
        return None


def prediction_from(obj: Any) -> tuple[float | None, str | None]:
    """Find explicit probability fields only; do not map text confidence."""
    paths = [
        ("factors.estimated_win_rate", ["factors", "estimated_win_rate"]),
        ("estimated_win_rate", ["estimated_win_rate"]),
        ("predicted_probability", ["predicted_probability"]),
        ("predicted_p", ["predicted_p"]),
        ("win_rate_proxy", ["win_rate_proxy"]),
        ("decision_fusion.win_rate_proxy", ["decision_fusion", "win_rate_proxy"]),
        ("metadata.decision_fusion.win_rate_proxy", ["metadata", "decision_fusion", "win_rate_proxy"]),
    ]
    for label, path in paths:
        cur = obj
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        val = to_float(cur)
        if val is not None and 0.0 <= val <= 1.0:
            return val, label
    if isinstance(obj, dict):
        for value in obj.values():
            if isinstance(value, dict):
                found, source = prediction_from(value)
                if found is not None:
                    return found, source
            elif isinstance(value, list):
                for item in value[:20]:
                    found, source = prediction_from(item)
                    if found is not None:
                        return found, source
    return None, None



def read_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def iter_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_dicts(value)


def build_prediction_index(paper_root: Path) -> tuple[dict[str, dict[str, Any]], Counter]:
    """Index explicit proposal probabilities from System A artifacts.

    This scans proposal/decision packet JSON files only. It never infers from
    text labels like confidence=medium_high; it only accepts numeric probability
    fields such as metadata.decision_fusion.win_rate_proxy.
    """
    index: dict[str, dict[str, Any]] = {}
    stats: Counter = Counter()
    for sub in ("proposals", "decision_packets"):
        base = paper_root / sub
        if not base.exists():
            continue
        for path in base.rglob("*.json"):
            obj = read_json_file(path)
            if obj is None:
                stats[f"{sub}_json_unreadable"] += 1
                continue
            for d in iter_dicts(obj):
                pred, field = prediction_from(d)
                if pred is None:
                    continue
                ids = [d.get("id"), d.get("proposal_id")]
                # LongBridge executor rows often wrap the proposal under `proposal`.
                prop = d.get("proposal") if isinstance(d.get("proposal"), dict) else None
                if prop:
                    ids.extend([prop.get("id"), prop.get("proposal_id")])
                for raw in ids:
                    if not raw:
                        continue
                    key = str(raw)
                    index.setdefault(key, {"predicted_p": pred, "prediction_field": field, "source_file": str(path.relative_to(paper_root))})
                    stats["indexed_predictions"] += 1
    stats["unique_prediction_ids"] = len(index)
    return index, stats


def lookup_prediction(row: dict[str, Any], index: dict[str, dict[str, Any]]) -> tuple[float | None, str | None, dict[str, Any] | None]:
    pred, field = prediction_from(row)
    if pred is not None:
        return pred, field, None
    for raw in (row.get("proposal_id"), row.get("id"), row.get("order_id")):
        if not raw:
            continue
        hit = index.get(str(raw))
        if hit:
            return float(hit["predicted_p"]), str(hit["prediction_field"]), hit
    return None, None, None


def outcome_label(row: dict[str, Any]) -> int | None:
    if str(row.get("outcome") or "").lower() == "success":
        return 1
    if str(row.get("outcome") or "").lower() == "failure":
        return 0
    r_multiple = to_float(row.get("r_multiple"))
    if r_multiple is not None:
        return 1 if r_multiple > 0 else 0
    ret = to_float(row.get("return_pct"))
    if ret is not None:
        return 1 if ret > 0 else 0
    return None


def pair_samples(
    outcomes: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    prediction_index: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], Counter]:
    del orders  # Predictions are already indexed from order/proposal artifacts.
    prediction_index = prediction_index or {}
    aggregation = lifecycle_module().aggregate_lifecycles(outcomes)
    skipped: Counter = Counter()
    samples: list[dict[str, Any]] = []
    skipped["unmatched_exit"] = int(aggregation.get("unmatched_exit_count") or 0)

    for trade in aggregation.get("complete_lifecycles") or []:
        actual = outcome_label(trade)
        if actual is None:
            skipped["no_actual_outcome"] += 1
            continue
        entry_payload = trade.get("entry_payload") if isinstance(trade.get("entry_payload"), dict) else {}
        predicted, field, pred_meta = lookup_prediction(entry_payload, prediction_index)
        if predicted is None:
            predicted, field, pred_meta = lookup_prediction(
                {"proposal_id": trade.get("entry_proposal_id")}, prediction_index
            )
        if predicted is None:
            skipped["no_prediction_field"] += 1
            continue
        trade_lifecycle_id = str(trade.get("trade_lifecycle_id"))
        samples.append(
            {
                "sample_id": f"paper-lifecycle:{trade_lifecycle_id}",
                "trade_lifecycle_id": trade_lifecycle_id,
                "decision_ref": str(trade.get("entry_proposal_id") or trade_lifecycle_id),
                "symbol": str(trade.get("symbol") or "").upper(),
                "predicted_p": float(predicted),
                "outcome": int(actual),
                "source": "paper",
                "outcome_time": trade.get("completed_at"),
                "prediction_field": field,
                "source_payload": {
                    "entry": entry_payload,
                    "exits": trade.get("exits") or [],
                    "aggregate_r_multiple": trade.get("r_multiple"),
                    "prediction_meta": pred_meta,
                },
            }
        )

    for trade in aggregation.get("lifecycles") or []:
        if trade.get("status") == "closed":
            continue
        entry_payload = trade.get("entry_payload") if isinstance(trade.get("entry_payload"), dict) else {}
        predicted, _field, _meta = lookup_prediction(entry_payload, prediction_index)
        if predicted is None:
            predicted, _field, _meta = lookup_prediction(
                {"proposal_id": trade.get("entry_proposal_id")}, prediction_index
            )
        if predicted is not None:
            skipped["open_not_closed_yet"] += 1
    return samples, skipped




def collect_pending_predictions(
    outcomes: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    prediction_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect one explicit prediction per incomplete trade lifecycle."""
    del orders
    aggregation = lifecycle_module().aggregate_lifecycles(outcomes)
    pending: dict[str, dict[str, Any]] = {}
    for trade in aggregation.get("lifecycles") or []:
        if trade.get("status") == "closed":
            continue
        entry_payload = trade.get("entry_payload") if isinstance(trade.get("entry_payload"), dict) else {}
        pred, field, meta = lookup_prediction(entry_payload, prediction_index)
        if pred is None:
            pred, field, meta = lookup_prediction(
                {"proposal_id": trade.get("entry_proposal_id")}, prediction_index
            )
        proposal_ref = str(trade.get("entry_proposal_id") or "")
        trade_lifecycle_id = str(trade.get("trade_lifecycle_id") or "")
        if pred is None or not proposal_ref or not trade_lifecycle_id:
            continue
        meta = meta or {}
        pending[trade_lifecycle_id] = {
            "proposal_ref": proposal_ref,
            "trade_lifecycle_id": trade_lifecycle_id,
            "symbol": str(trade.get("symbol") or "").upper(),
            "predicted_p": float(pred),
            "prediction_field": field,
            "opened_at": trade.get("opened_at"),
            "source_file": meta.get("source_file") if isinstance(meta, dict) else None,
            "source_payload": entry_payload,
        }
    return sorted(pending.values(), key=lambda x: (x.get("opened_at") or "", x["trade_lifecycle_id"]))


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            sample_id TEXT PRIMARY KEY,
            trade_lifecycle_id TEXT,
            decision_ref TEXT,
            symbol TEXT NOT NULL,
            predicted_p REAL NOT NULL,
            outcome INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'paper',
            recorded_at TEXT NOT NULL,
            outcome_time TEXT,
            prediction_field TEXT,
            source_payload_json TEXT NOT NULL
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({TABLE})").fetchall()}
    if "trade_lifecycle_id" not in columns:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN trade_lifecycle_id TEXT")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_symbol_time ON {TABLE}(symbol, outcome_time DESC)")
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_lifecycle ON {TABLE}(trade_lifecycle_id) WHERE trade_lifecycle_id IS NOT NULL")
    conn.commit()


def write_samples(conn: sqlite3.Connection, samples: list[dict[str, Any]]) -> tuple[int, int]:
    ensure_table(conn)
    inserted = existing = 0
    for s in samples:
        try:
            conn.execute(
                f"""INSERT INTO {TABLE} (
                    sample_id, trade_lifecycle_id, decision_ref, symbol, predicted_p,
                    outcome, source, recorded_at, outcome_time, prediction_field,
                    source_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s["sample_id"],
                    s.get("trade_lifecycle_id"),
                    s["decision_ref"],
                    s["symbol"],
                    s["predicted_p"],
                    s["outcome"],
                    "paper",
                    now_iso(),
                    s.get("outcome_time"),
                    s.get("prediction_field"),
                    jdump(s.get("source_payload", {})),
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            existing += 1
    conn.commit()
    return inserted, existing


def ensure_pending_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PENDING_TABLE} (
            proposal_ref TEXT PRIMARY KEY,
            trade_lifecycle_id TEXT,
            symbol TEXT NOT NULL,
            predicted_p REAL NOT NULL,
            prediction_field TEXT,
            opened_at TEXT,
            source_file TEXT,
            recorded_at TEXT NOT NULL,
            source_payload_json TEXT NOT NULL
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({PENDING_TABLE})").fetchall()}
    if "trade_lifecycle_id" not in columns:
        conn.execute(f"ALTER TABLE {PENDING_TABLE} ADD COLUMN trade_lifecycle_id TEXT")
    # 2026-07-26 P0 repair: orphaned = the position backing this pending
    # prediction is gone from the latest broker snapshot but no fill evidence
    # ever closed the lifecycle. Additive columns, defaulted for old rows.
    if "orphaned" not in columns:
        conn.execute(f"ALTER TABLE {PENDING_TABLE} ADD COLUMN orphaned INTEGER NOT NULL DEFAULT 0")
    if "orphaned_at" not in columns:
        conn.execute(f"ALTER TABLE {PENDING_TABLE} ADD COLUMN orphaned_at TEXT")
    if "orphaned_reason" not in columns:
        conn.execute(f"ALTER TABLE {PENDING_TABLE} ADD COLUMN orphaned_reason TEXT")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{PENDING_TABLE}_symbol_time ON {PENDING_TABLE}(symbol, opened_at DESC)")
    conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{PENDING_TABLE}_lifecycle ON {PENDING_TABLE}(trade_lifecycle_id) WHERE trade_lifecycle_id IS NOT NULL")
    conn.commit()


def write_pending_predictions(
    conn: sqlite3.Connection,
    pending: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    orphaned_refs: set[str] | None = None,
) -> dict[str, int]:
    ensure_pending_table(conn)
    orphaned_refs = orphaned_refs or set()
    closed_refs = {str(s.get("decision_ref")) for s in samples if s.get("decision_ref")}
    active_refs = {str(row["proposal_ref"]) for row in pending}
    existing_refs = {
        str(row[0]) for row in conn.execute(f"SELECT proposal_ref FROM {PENDING_TABLE}").fetchall()
    }
    stale_refs = existing_refs - active_refs
    if stale_refs:
        conn.executemany(
            f"DELETE FROM {PENDING_TABLE} WHERE proposal_ref=?",
            [(ref,) for ref in sorted(stale_refs)],
        )
    upserted = 0
    for row in pending:
        if row["proposal_ref"] in closed_refs:
            continue
        is_orphaned = row["proposal_ref"] in orphaned_refs
        conn.execute(
            f"""INSERT OR REPLACE INTO {PENDING_TABLE} (
                proposal_ref, trade_lifecycle_id, symbol, predicted_p,
                prediction_field, opened_at, source_file, recorded_at,
                source_payload_json, orphaned, orphaned_at, orphaned_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["proposal_ref"],
                row.get("trade_lifecycle_id"),
                row["symbol"],
                row["predicted_p"],
                row.get("prediction_field"),
                row.get("opened_at"),
                row.get("source_file"),
                now_iso(),
                jdump(row.get("source_payload", {})),
                1 if is_orphaned else 0,
                now_iso() if is_orphaned else None,
                ORPHAN_REASON_POSITION_GONE if is_orphaned else None,
            ),
        )
        upserted += 1
    total = conn.execute(f"SELECT count(*) FROM {PENDING_TABLE}").fetchone()[0]
    orphaned_total = conn.execute(f"SELECT count(*) FROM {PENDING_TABLE} WHERE orphaned=1").fetchone()[0]
    conn.commit()
    return {
        "pending_upserted": upserted,
        "pending_removed": len(stale_refs),
        "pending_table_total": int(total),
        "orphaned_predictions": int(orphaned_total),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    paper_root = Path(args.paper_root).expanduser()
    outcomes = read_jsonl(Path(args.outcomes).expanduser())
    orders = read_jsonl(Path(args.orders).expanduser())
    prediction_index, index_stats = build_prediction_index(paper_root)
    samples, skipped = pair_samples(outcomes, orders, prediction_index)
    pending = collect_pending_predictions(outcomes, orders, prediction_index)

    snapshots_arg = getattr(args, "snapshots", None)
    snapshots_path = Path(snapshots_arg).expanduser() if snapshots_arg else (paper_root / "journal" / "paper_position_snapshots.jsonl")
    snapshot_symbols = latest_position_snapshot_symbols(snapshots_path)
    # snapshot_symbols is None when the snapshot file is missing/unreadable --
    # fail-safe: treat nothing as orphaned rather than guess.
    orphaned_refs: set[str] = set()
    if snapshot_symbols is not None:
        orphaned_refs = {row["proposal_ref"] for row in pending if row["symbol"] not in snapshot_symbols}

    dry_run = bool(getattr(args, "dry_run", False))
    if dry_run:
        inserted = 0
        existing = 0
        total = None
        pending_stats = {
            "pending_upserted": 0,
            "pending_removed": 0,
            "pending_table_total": None,
            "orphaned_predictions": len(orphaned_refs),
        }
    else:
        conn = connect(db_path(args))
        try:
            inserted, existing = write_samples(conn, samples)
            pending_stats = write_pending_predictions(conn, pending, samples, orphaned_refs)
            total = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        finally:
            conn.close()
    pending_examples = [
        {
            k: row.get(k)
            for k in (
                "proposal_ref",
                "trade_lifecycle_id",
                "symbol",
                "predicted_p",
                "prediction_field",
                "source_file",
            )
        }
        for row in pending
        if row["proposal_ref"] not in orphaned_refs
    ][:5]
    orphaned_examples = [
        {
            k: row.get(k)
            for k in (
                "proposal_ref",
                "trade_lifecycle_id",
                "symbol",
                "predicted_p",
                "prediction_field",
                "source_file",
            )
        }
        for row in pending
        if row["proposal_ref"] in orphaned_refs
    ][:5]
    return {
        "ok": True,
        "source": "paper",
        "materiality_eligible": False,
        "dry_run": dry_run,
        "outcomes_scanned": len(outcomes),
        "orders_scanned": len(orders),
        "prediction_index": dict(index_stats),
        "paired_samples": len(samples),
        "would_write_samples": len(samples),
        "inserted_count": inserted,
        "existing_count": existing,
        "table_total": total,
        # Genuinely awaiting a real close -- orphaned rows are reported
        # separately via pending_stats["orphaned_predictions"] and are
        # excluded here so this count reflects the pairing pool honestly
        # (2026-07-26 P0 repair: paper-calibration-loop-stalled-reported-as-pending).
        "pending_prediction_count": len(pending) - len(orphaned_refs),
        **pending_stats,
        "pending_examples": pending_examples,
        "orphaned_examples": orphaned_examples,
        "skipped": dict(skipped),
        "no_order_execution": True,
    }


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        outcomes = root / "paper_outcomes.jsonl"
        orders = root / "paper_orders.jsonl"
        outcomes.write_text("\n".join([
            json.dumps({"status": "open", "symbol": "ABC.US", "side": "Buy", "proposal_id": "open-1", "timestamp_utc": "2026-01-01T10:00:00Z", "decision_fusion": {"win_rate_proxy": 0.62}}),
            json.dumps({"status": "closed", "symbol": "ABC.US", "side": "Sell", "proposal_id": "exit-1", "timestamp_utc": "2026-01-03T10:00:00Z", "r_multiple": "1.20"}),
        ]) + "\n", encoding="utf-8")
        orders.write_text("", encoding="utf-8")
        db = root / "memory.sqlite"
        args = argparse.Namespace(paper_root=str(root), outcomes=str(outcomes), orders=str(orders), db=str(db))
        first = run(args)
        second = run(args)
        assert first["inserted_count"] == 1, first
        assert first["paired_samples"] == 1, first
        assert second["inserted_count"] == 0 and second["existing_count"] == 1, second
        return {"ok": True, "self_test": "passed", "first": first, "second": second}


def main() -> int:
    ap = argparse.ArgumentParser(description="Translate System A paper outcomes into an isolated calibration bucket")
    ap.add_argument("--db", help="SQLite DB path; default TRADING_MEMORY_DB or core default")
    ap.add_argument("--paper-root", default=str(DEFAULT_PAPER_ROOT))
    ap.add_argument("--outcomes", default=str(DEFAULT_OUTCOMES))
    ap.add_argument("--orders", default=str(DEFAULT_ORDERS))
    ap.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOTS), help="latest broker position snapshot journal, used to orphan pending predictions whose position is gone")
    ap.add_argument("--dry-run", action="store_true", help="compute lifecycle samples without writing the memory DB")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    out = self_test() if args.self_test else run(args)
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.json or args.self_test else None))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
