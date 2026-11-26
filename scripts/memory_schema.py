"""trading-research decision memory schema.

Data structures, enums, and validation for the decision-memory substrate:
DB schema (init_db), payload/row validation, and row<->dict shaping. Split out
of trading_memory_core.py (Task 6, skill_optimization_plan_20260705.md) so no
module in the memory substrate exceeds 800 lines. No broker access, no real
trade execution, no external secrets.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB = Path.home() / ".cache/hermes/trading-research/memory/trading_memory.sqlite"
STRATEGY_VERSION = "trading-research-memory-v1"

FAILURE_TAGS = {
    "quick_loss",
    "chase_reversal",
    "fake_breakout",
    "flow_against",
    "news_against",
    "smart_money_against",
    "model_signal_against",
    "low_coverage_loss",
    "execution_risk_high",
    "entry_too_early",
    "direction_invalidated",
    "over_optimistic",
    "over_conservative",
    "take_profit_too_early",
    "risk_warning_insufficient",
    "timeout_failure",
    "watch_missed_opportunity",
    "avoid_missed_opportunity",
    "symbol_drag",
    "regime_misread",
    "data_gap_mispriced",
}


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def jload(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def ensure_unique_result_decisions(conn: sqlite3.Connection) -> None:
    """Migrate the result lookup index to a one-result-per-decision guard.

    Existing duplicate rows are evidence that needs a manual audit.  Refuse the
    migration without deleting or choosing either row; otherwise upgrade the
    legacy non-unique index in one serialized write transaction.

    Schema migration owns its transaction.  An existing caller transaction is
    rejected before the rollback handler so the caller retains full control.
    """
    if conn.in_transaction:
        raise RuntimeError(
            "ensure_unique_result_decisions cannot run inside an active transaction"
        )
    try:
        conn.execute("BEGIN IMMEDIATE")
        duplicates = conn.execute(
            "SELECT decision_id, COUNT(*) AS row_count FROM results "
            "GROUP BY decision_id HAVING COUNT(*) > 1 "
            "ORDER BY decision_id LIMIT 10"
        ).fetchall()
        if duplicates:
            detail = ", ".join(
                f"decision_id={row[0]!r} rows={row[1]}" for row in duplicates
            )
            raise RuntimeError(
                "duplicate results block the unique decision_id migration; "
                f"manual audit required without row deletion: {detail}"
            )

        indexes = {
            row[1]: bool(row[2])
            for row in conn.execute("PRAGMA index_list('results')").fetchall()
        }
        if indexes.get("idx_results_decision") is False:
            conn.execute("DROP INDEX idx_results_decision")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_results_decision "
            "ON results(decision_id)"
        )
        index = next(
            (
                row
                for row in conn.execute("PRAGMA index_list('results')").fetchall()
                if row[1] == "idx_results_decision"
            ),
            None,
        )
        columns = [
            row[2]
            for row in conn.execute("PRAGMA index_info('idx_results_decision')").fetchall()
        ]
        if index is None or not bool(index[2]) or columns != ["decision_id"]:
            raise RuntimeError(
                "results uniqueness migration failed: expected a unique "
                "idx_results_decision(decision_id) index"
            )
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS memory_events (
            op_id TEXT PRIMARY KEY,
            event_time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            analysis_time TEXT NOT NULL,
            agent TEXT,
            skill_version TEXT,
            strategy_version TEXT,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            longbridge_symbol TEXT,
            direction TEXT NOT NULL,
            action_level TEXT NOT NULL,
            action_label TEXT,
            position_cap_pct REAL,
            risk_level TEXT,
            attack_state TEXT,
            manual_aggressive_profile TEXT,
            valid_for_review INTEGER DEFAULT 1,
            scores_json TEXT,
            factors_json TEXT,
            evidence_ids_json TEXT,
            hypothesis_ids_json TEXT,
            conflict_ids_json TEXT,
            data_gaps_json TEXT,
            reasons_json TEXT,
            falsifier TEXT,
            review_clock TEXT,
            candidate_source TEXT,
            confluence_count INTEGER,
            report_path TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_symbol_time ON decisions(symbol, analysis_time DESC);
        CREATE INDEX IF NOT EXISTS idx_decisions_direction_time ON decisions(direction, analysis_time DESC);
        CREATE TABLE IF NOT EXISTS results (
            result_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
            validation_time TEXT NOT NULL,
            price_at_decision REAL,
            price_at_validation REAL,
            return_pct REAL,
            max_favorable_excursion_pct REAL,
            max_adverse_excursion_pct REAL,
            stop_hit INTEGER,
            target_hit INTEGER,
            falsifier_hit INTEGER,
            expired_early INTEGER,
            exit_type TEXT,
            outcome TEXT NOT NULL,
            failure_tags_json TEXT,
            effective_factors_json TEXT,
            ineffective_factors_json TEXT,
            review_evidence_ids_json TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_results_validation_time ON results(validation_time DESC);
        CREATE TABLE IF NOT EXISTS reviews (
            review_id TEXT PRIMARY KEY,
            review_time TEXT NOT NULL,
            window INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            cooldown_minutes INTEGER NOT NULL,
            stats_json TEXT NOT NULL,
            recommendations_json TEXT NOT NULL,
            adjustments_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims (
            claim_id TEXT PRIMARY KEY,
            decision_id TEXT,
            subject TEXT NOT NULL,
            relation TEXT NOT NULL,
            object TEXT NOT NULL,
            polarity TEXT NOT NULL,
            regime TEXT,
            evidence_ids_json TEXT,
            confidence REAL,
            valid_from TEXT,
            valid_to TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_claims_subject_relation ON claims(subject, relation);
        CREATE TABLE IF NOT EXISTS predictions (
            prediction_id TEXT PRIMARY KEY,
            decision_id TEXT,
            registered_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL,
            longbridge_symbol TEXT,
            horizon_id TEXT NOT NULL CHECK (
                horizon_id IN ('intraday','overnight_cto','swing_days','position_months','theme_years')
            ),
            direction TEXT NOT NULL CHECK (direction IN ('up','down')),
            probability REAL NOT NULL CHECK (probability > 0 AND probability < 1),
            as_of TEXT NOT NULL,
            due_at TEXT NOT NULL,
            regime TEXT NOT NULL,
            calibration_bucket TEXT NOT NULL,
            reference_price REAL NOT NULL CHECK (reference_price > 0),
            status TEXT NOT NULL CHECK (status IN ('open','settled','unresolved')),
            resolution_json TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_predictions_due_status
            ON predictions(status, due_at);
        CREATE INDEX IF NOT EXISTS idx_predictions_horizon_regime
            ON predictions(horizon_id, regime, due_at);
        CREATE TABLE IF NOT EXISTS prediction_outcomes (
            outcome_id TEXT PRIMARY KEY,
            prediction_id TEXT NOT NULL UNIQUE REFERENCES predictions(prediction_id),
            settled_at TEXT NOT NULL,
            observed_price REAL NOT NULL,
            actual INTEGER NOT NULL CHECK (actual IN (0,1)),
            outcome TEXT NOT NULL CHECK (outcome IN ('success','failure')),
            quote_source TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_settled
            ON prediction_outcomes(settled_at DESC);
        """
    )
    conn.commit()
    ensure_unique_result_decisions(conn)


def require(payload: dict[str, Any], keys: list[str]) -> None:
    missing = [k for k in keys if payload.get(k) in (None, "")]
    if missing:
        raise SystemExit(f"payload missing required fields: {', '.join(missing)}")


def _numeric(value: Any, *, low: float | None = None, high: float | None = None) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if low is not None and value <= low:
        return False
    if high is not None and value > high:
        return False
    return True


def decision_completeness(payload: dict[str, Any]) -> dict[str, Any]:
    """Non-fatal contract check: a decision is calibration-ready only if it
    carries an honest predicted win-rate and an entry price. Missing either does
    not block recording (we never reject a real decision), but it is surfaced so
    the report can declare ``completeness=partial`` instead of silently shipping
    a decision the loop can never auto-verify or calibrate."""
    factors = payload.get("factors") or {}
    missing: list[str] = []
    if not _numeric(factors.get("estimated_win_rate"), low=0.0, high=1.0):
        missing.append("factors.estimated_win_rate")
    has_price = _numeric(payload.get("price_at_decision"), low=0.0) or _numeric(
        factors.get("price_at_decision"), low=0.0
    )
    if not has_price:
        missing.append("price_at_decision")
    return {"status": "full" if not missing else "partial", "missing": missing}


def bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def row_to_decision(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in [
        "scores_json",
        "factors_json",
        "evidence_ids_json",
        "hypothesis_ids_json",
        "conflict_ids_json",
        "data_gaps_json",
        "reasons_json",
        "payload_json",
    ]:
        target = key.removesuffix("_json")
        d[target] = jload(d.pop(key), [] if key.endswith("ids_json") or key in ("data_gaps_json", "reasons_json") else {})
    return d


def row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    r = dict(row)
    for key in ["failure_tags_json", "effective_factors_json", "ineffective_factors_json", "review_evidence_ids_json", "payload_json"]:
        target = key.removesuffix("_json")
        r[target] = jload(r.pop(key), [])
    return r
