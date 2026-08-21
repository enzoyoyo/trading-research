#!/usr/bin/env python3
"""Fail-closed multi-horizon prediction ledger on Decision Memory SQLite.

This records falsifiable forecasts, settles only observable price events, and
never changes Decision Compiler output or executes an order.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from memory_store import connect, db_path, event, make_id, parse_time, read_payload

HORIZONS = {"intraday", "overnight_cto", "swing_days", "position_months", "theme_years"}
DIRECTIONS = {"up", "down"}
EID_RE = re.compile(r"^E[A-Za-z0-9:_-]+$")
REGISTRATION_CLOCK_SKEW_SECONDS = 300
MAX_SETTLEMENT_LAG_SECONDS = {
    "intraday": 15 * 60,
    "overnight_cto": 15 * 60,
    "swing_days": 6 * 3600,
    "position_months": 24 * 3600,
    "theme_years": 24 * 3600,
}
SENSITIVE_KEY_FRAGMENTS = ("password", "secret", "token", "credential", "api_key", "authorization")


def _finite_number(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _aware_time(value: Any) -> datetime | None:
    parsed = parse_time(value) if isinstance(value, str) else None
    if parsed is None or parsed.utcoffset() is None:
        return None
    return parsed


def _has_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            canonical = str(key).lower().replace("-", "_")
            if any(fragment in canonical for fragment in SENSITIVE_KEY_FRAGMENTS):
                return True
            if _has_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_key(child) for child in value)
    return False


def canonical_bucket(payload: dict[str, Any]) -> str | None:
    resolution = payload.get("resolution")
    threshold = resolution.get("threshold_pct") if isinstance(resolution, dict) else None
    number = _finite_number(threshold, positive=True)
    required = (
        payload.get("symbol"),
        payload.get("horizon_id"),
        payload.get("regime"),
        payload.get("direction"),
    )
    if not all(required) or number is None:
        return None
    return "×".join([*(str(item) for item in required), f"{number:g}pct"])


def validate_prediction(
    payload: dict[str, Any], registered_at: datetime | None = None
) -> list[str]:
    errors: list[str] = []
    registered_at = registered_at or datetime.now(timezone.utc)
    horizon = payload.get("horizon_id")
    if horizon not in HORIZONS:
        errors.append("invalid_horizon_id")
    if payload.get("direction") not in DIRECTIONS:
        errors.append("direction_must_be_up_or_down")
    probability = _finite_number(payload.get("probability"))
    if probability is None or not 0.0 < probability < 1.0:
        errors.append("probability_must_be_strictly_between_0_and_1")
    as_of = _aware_time(payload.get("as_of"))
    due_at = _aware_time(payload.get("due_at"))
    if as_of is None:
        errors.append("as_of_requires_timezone")
    if due_at is None:
        errors.append("due_at_requires_timezone")
    if as_of is not None and due_at is not None and due_at <= as_of:
        errors.append("due_at_must_follow_as_of")
    if as_of is not None and (
        as_of - registered_at
    ).total_seconds() > REGISTRATION_CLOCK_SKEW_SECONDS:
        errors.append("as_of_must_not_be_in_the_future")
    if due_at is not None and due_at <= registered_at:
        errors.append("due_at_must_be_in_the_future")
    if not str(payload.get("symbol") or "").strip():
        errors.append("symbol_required")
    if not str(payload.get("market") or "").strip():
        errors.append("market_required")
    if not str(payload.get("longbridge_symbol") or "").strip():
        errors.append("verified_longbridge_symbol_required")
    if not str(payload.get("regime") or "").strip():
        errors.append("regime_required")
    expected_bucket = canonical_bucket(payload)
    if payload.get("calibration_bucket") != expected_bucket:
        errors.append("calibration_bucket_must_include_symbol_horizon_regime_direction_threshold")
    for field in ("invalidation", "no_trade_if"):
        values = payload.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            errors.append(f"{field}_requires_nonempty_list")
    drivers = payload.get("drivers")
    if (
        not isinstance(drivers, list)
        or any(
            not isinstance(item, str)
            or not item.strip()
            or EID_RE.fullmatch(item) is None
            for item in drivers
        )
        or len(set(drivers)) < 3
    ):
        errors.append("drivers_require_3_distinct_eids")
    consensus = payload.get("consensus")
    if not isinstance(consensus, dict) or any(
        not str(consensus.get(field) or "").strip()
        for field in ("consensus_view", "price_discounts", "variant_view")
    ):
        errors.append("consensus_requires_view_discounts_and_variant")
    premortem = payload.get("premortem")
    if (
        not isinstance(premortem, list)
        or len(premortem) < 3
        or any(
            not isinstance(item, dict)
            or not str(item.get("failure") or "").strip()
            or not str(item.get("indicator") or "").strip()
            for item in premortem
        )
    ):
        errors.append("premortem_requires_3_failures_with_indicators")
    resolution = payload.get("resolution")
    if not isinstance(resolution, dict) or resolution.get("metric") != "directional_return":
        errors.append("resolution_metric_must_be_directional_return")
    elif _finite_number(resolution.get("threshold_pct"), positive=True) is None:
        errors.append("resolution_threshold_pct_must_be_positive")
    if _finite_number(payload.get("reference_price"), positive=True) is None:
        errors.append("reference_price_must_be_positive")
    if _has_sensitive_key(payload):
        errors.append("sensitive_field_forbidden")
    return sorted(set(errors))


def register_prediction(
    conn, payload: dict[str, Any], registered_at: datetime | None = None
) -> dict[str, Any]:
    registered_at = registered_at or datetime.now(timezone.utc)
    errors = validate_prediction(payload, registered_at)
    if errors:
        return {
            "ok": False,
            "status": "blocked",
            "errors": errors,
            "expected_calibration_bucket": canonical_bucket(payload),
            "prediction_id": None,
            "no_order_execution": True,
        }
    prediction_id = str(payload.get("prediction_id") or make_id("PR", str(payload["symbol"])))
    values = (
        prediction_id,
        payload.get("decision_id"),
        registered_at.astimezone(timezone.utc).isoformat(),
        str(payload["symbol"]),
        str(payload["market"]),
        payload.get("longbridge_symbol"),
        str(payload["horizon_id"]),
        str(payload["direction"]),
        float(payload["probability"]),
        str(payload["as_of"]),
        str(payload["due_at"]),
        str(payload["regime"]),
        str(payload["calibration_bucket"]),
        float(payload["reference_price"]),
        "open",
        json.dumps(payload["resolution"], ensure_ascii=False, sort_keys=True),
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    try:
        conn.execute(
            "INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        event(conn, "prediction_registered", "prediction", prediction_id, payload)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        return {
            "ok": False,
            "status": "blocked",
            "errors": ["duplicate_or_invalid_prediction"],
            "prediction_id": None,
            "no_order_execution": True,
        }
    except Exception:
        conn.rollback()
        raise
    return {
        "ok": True,
        "status": "registered",
        "prediction_id": prediction_id,
        "horizon_id": payload["horizon_id"],
        "calibration_bucket": payload["calibration_bucket"],
        "no_order_execution": True,
    }


def due_predictions(conn, now: datetime, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM predictions WHERE status='open' ORDER BY due_at ASC"
    ).fetchall()
    due: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        item = dict(row)
        due_at = _aware_time(item.get("due_at"))
        if due_at is not None and due_at <= now:
            due.append((due_at, item))
    due.sort(key=lambda item: item[0])
    return [item for _due_at, item in due[:limit]]


def quote_symbol(row: dict[str, Any]) -> str:
    explicit = str(row.get("longbridge_symbol") or "").strip()
    return explicit


def settle_due_predictions(
    conn,
    now: datetime,
    quote_fn: Callable[[str], Any],
    normalize_quote_fn: Callable[[Any], tuple[float | None, str | None]],
    limit: int = 200,
    dry_run: bool = False,
) -> dict[str, Any]:
    settled: list[dict[str, Any]] = []
    missing_quote: list[str] = []
    identity_mismatch: list[str] = []
    stale_settlement: list[str] = []
    already_settled: list[str] = []
    lock_timeouts: list[str] = []
    invalid_contract_rows: list[str] = []
    due = due_predictions(conn, now, limit)
    quotes: dict[str, tuple[float | None, str | None, str | None]] = {}
    for row in due:
        due_at = _aware_time(row["due_at"])
        lag = (now - due_at).total_seconds() if due_at is not None else math.inf
        lag_cap = MAX_SETTLEMENT_LAG_SECONDS.get(row["horizon_id"])
        if lag_cap is None:
            invalid_contract_rows.append(row["prediction_id"])
            continue
        if lag > lag_cap:
            if dry_run:
                stale_settlement.append(row["prediction_id"])
            else:
                detail = {
                    "prediction_id": row["prediction_id"],
                    "horizon_id": row["horizon_id"],
                    "due_at": row["due_at"],
                    "checked_at": now.astimezone(timezone.utc).isoformat(),
                    "reason": "settlement_lag_exceeded_no_point_in_time_quote",
                    "no_order_execution": True,
                }
                try:
                    conn.execute(
                        "UPDATE predictions SET status='unresolved' "
                        "WHERE prediction_id=? AND status='open'",
                        (row["prediction_id"],),
                    )
                    event(
                        conn,
                        "prediction_unresolved",
                        "prediction",
                        row["prediction_id"],
                        detail,
                    )
                    conn.commit()
                    stale_settlement.append(row["prediction_id"])
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    if "locked" in str(exc).lower():
                        lock_timeouts.append(row["prediction_id"])
                        continue
                    raise
            continue
        try:
            raw_quote = quote_fn(quote_symbol(row))
            observed, source = normalize_quote_fn(raw_quote)
            resolved_symbol = None
            if isinstance(raw_quote, dict):
                resolved_symbol = raw_quote.get("symbol") or raw_quote.get("canonical_symbol")
            quotes[row["prediction_id"]] = (
                observed,
                source,
                str(resolved_symbol).upper() if resolved_symbol else None,
            )
        except Exception:
            missing_quote.append(row["prediction_id"])
    for row in due:
        if row["prediction_id"] in stale_settlement or row["prediction_id"] in invalid_contract_rows:
            continue
        if row["prediction_id"] not in quotes:
            continue
        try:
            observed, source, resolved_symbol = quotes[row["prediction_id"]]
            if observed is None or observed <= 0:
                missing_quote.append(row["prediction_id"])
                continue
            if resolved_symbol != quote_symbol(row).upper():
                identity_mismatch.append(row["prediction_id"])
                continue
            resolution = json.loads(row["resolution_json"])
            raw_return = (observed - float(row["reference_price"])) / float(row["reference_price"]) * 100.0
            threshold = float(resolution["threshold_pct"])
            actual = int(raw_return >= threshold) if row["direction"] == "up" else int(raw_return <= -threshold)
            item = {
                "prediction_id": row["prediction_id"],
                "symbol": row["symbol"],
                "horizon_id": row["horizon_id"],
                "actual": actual,
                "outcome": "success" if actual else "failure",
                "return_pct": round(raw_return, 6),
                "quote_source": source or "unknown",
                "settlement_lag_seconds": round(
                    (now - _aware_time(row["due_at"])).total_seconds()
                ),
                "dry_run": dry_run,
            }
            if not dry_run:
                outcome_id = make_id("PO", str(row["symbol"]))
                conn.execute(
                    "INSERT INTO prediction_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        outcome_id,
                        row["prediction_id"],
                        now.astimezone(timezone.utc).isoformat(),
                        observed,
                        actual,
                        item["outcome"],
                        source or "unknown",
                        json.dumps(item, ensure_ascii=False, sort_keys=True),
                    ),
                )
                conn.execute(
                    "UPDATE predictions SET status='settled' WHERE prediction_id=? AND status='open'",
                    (row["prediction_id"],),
                )
                event(conn, "prediction_settled", "prediction", row["prediction_id"], item)
                conn.commit()
            settled.append(item)
        except sqlite3.IntegrityError:
            if not dry_run:
                conn.rollback()
            already_settled.append(row["prediction_id"])
        except sqlite3.OperationalError as exc:
            if not dry_run:
                conn.rollback()
            if "locked" in str(exc).lower():
                lock_timeouts.append(row["prediction_id"])
                continue
            raise
        except Exception:
            if not dry_run:
                conn.rollback()
            raise
    status = "no_due"
    if settled:
        status = "settled"
    if missing_quote or identity_mismatch or stale_settlement or lock_timeouts or invalid_contract_rows:
        status = "partial" if settled else "blocked"
    return {
        "ok": True,
        "status": status,
        "due_count": len(due),
        "settled_count": len(settled),
        "settled": settled,
        "pending_missing_quote": missing_quote,
        "identity_mismatch": identity_mismatch,
        "stale_settlement_excluded": stale_settlement,
        "already_settled": already_settled,
        "skipped_lock_timeout": lock_timeouts,
        "invalid_contract_rows": invalid_contract_rows,
        "fail_closed": bool(
            missing_quote
            or identity_mismatch
            or stale_settlement
            or lock_timeouts
            or invalid_contract_rows
        ),
        "no_order_execution": True,
    }


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        conn = connect(Path(td) / "memory.sqlite")
        valid = {
            "symbol": "TEST",
            "market": "US",
            "longbridge_symbol": "TEST.US",
            "horizon_id": "swing_days",
            "direction": "up",
            "probability": 0.62,
            "as_of": "2026-01-01T00:00:00+00:00",
            "due_at": "2026-01-05T00:00:00+00:00",
            "regime": "neutral",
            "calibration_bucket": "TEST×swing_days×neutral×up×1pct",
            "reference_price": 100.0,
            "drivers": ["E1", "E2", "E3"],
            "invalidation": ["guidance withdrawn"],
            "no_trade_if": ["spread unavailable"],
            "consensus": {
                "consensus_view": "flat",
                "price_discounts": "no growth",
                "variant_view": "positive revision",
            },
            "premortem": [
                {"failure": "demand miss", "indicator": "orders"},
                {"failure": "margin miss", "indicator": "gross margin"},
                {"failure": "risk-off", "indicator": "credit spread"},
            ],
            "resolution": {"metric": "directional_return", "threshold_pct": 1.0},
        }
        registered = register_prediction(
            conn, valid, registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        assert registered["ok"], registered
        invalid = dict(valid)
        invalid["prediction_id"] = "BAD"
        invalid["probability"] = 1.0
        assert not register_prediction(
            conn, invalid, registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )["ok"]
        settled = settle_due_predictions(
            conn,
            datetime(2026, 1, 5, 1, tzinfo=timezone.utc),
            quote_fn=lambda symbol: {"price": 103.0, "source": "fixture", "symbol": symbol},
            normalize_quote_fn=lambda value: (value["price"], value["source"]),
        )
        assert settled["settled_count"] == 1 and settled["settled"][0]["actual"] == 1, settled
        assert settle_due_predictions(
            conn,
            datetime(2026, 1, 5, 1, tzinfo=timezone.utc),
            quote_fn=lambda _symbol: None,
            normalize_quote_fn=lambda _value: (None, None),
        )["settled_count"] == 0
        conn.close()
    return {"ok": True, "self_test": "passed", "registered": 1, "settled": 1}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prediction ledger; no order execution")
    parser.add_argument("--db")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    register = sub.add_parser("register")
    register.add_argument("--payload", required=True)
    register.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    if args.command != "register":
        parser.error("register or --self-test is required")
    conn = connect(db_path(args))
    try:
        output = register_prediction(conn, read_payload(args.payload))
    finally:
        conn.close()
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if output["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
