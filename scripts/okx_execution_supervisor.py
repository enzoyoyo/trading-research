#!/usr/bin/env python3
"""Compile a read-only OKX supervision snapshot into existing risk modules.

The supervisor does not authenticate, connect to OKX, mutate a strategy, create a
STOP file, or execute orders. It only evaluates an already-normalized snapshot
and emits tighten-only module signals for the existing Decision Compiler.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "okx_execution_snapshot.v1"
ALLOWED_CHANNELS = {"cex_spot", "wallet_dex"}
ALLOWED_MODES = {"public", "read_only", "demo", "live"}
ENTRY_FACTOR_WEIGHTS: dict[str, float] = {
    "technical": 0.20,
    "capital_flow": 0.20,
    "sentiment": 0.20,
    "fundamentals": 0.25,
    "macro": 0.15,
}
SENSITIVE_FIELD_NAMES = {
    "api_key",
    "secret",
    "passphrase",
    "access_token",
    "refresh_token",
    "bearer_token",
    "auth_token",
    "session_token",
    "id_token",
    "api_token",
    "private_key",
    "password",
    "authorization",
    "cookie",
    "mnemonic",
    "seed_phrase",
    "access_key",
    "access_sign",
    "signature",
}
SENSITIVE_EXACT_KEYS = {
    "token", "auth", "credential", "credentials", "header", "headers", "sign",
}
PUBLIC_IDENTIFIER_KEYS = {"token_contract", "token_address"}
EXISTING_MODULES = {
    "data_quality",
    "conflict_ledger",
    "account",
    "liquidity",
    "execution_window",
}
LAST_GOOD_VALUE_FIELDS = {
    "market": {
        "last", "bid", "ask", "spread_bps", "midpoint",
        "top5_bid_notional", "top5_ask_notional", "order_book_ts", "source_ts",
    },
    "account": {
        "equity", "available", "available_balance", "margin_ratio",
        "maintenance_margin", "unrealized_pnl", "currency",
    },
    "orders": {"open_order_count", "fill_count", "last_order_ts", "last_fill_ts"},
}
LAST_GOOD_NUMERIC_FIELDS = {
    "last", "bid", "ask", "spread_bps", "midpoint",
    "top5_bid_notional", "top5_ask_notional", "equity", "available",
    "available_balance", "margin_ratio", "maintenance_margin", "unrealized_pnl",
}
LAST_GOOD_INTEGER_FIELDS = {"open_order_count", "fill_count"}
LAST_GOOD_TIMESTAMP_FIELDS = {"order_book_ts", "source_ts", "last_order_ts", "last_fill_ts"}


def _normalized_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return "".join(character for character in normalized if character.isalnum())


def _is_sensitive_key(key: Any) -> bool:
    canonical = unicodedata.normalize("NFKC", str(key))
    if any(character.isalnum() and not character.isascii() for character in canonical):
        return True
    normalized = _normalized_key(key)
    public_identifiers = {_normalized_key(key) for key in PUBLIC_IDENTIFIER_KEYS}
    if normalized in public_identifiers:
        return False
    if normalized.startswith("okaccess"):
        return True
    if normalized in {_normalized_key(key) for key in SENSITIVE_EXACT_KEYS}:
        return True
    return any(_normalized_key(marker) in normalized for marker in SENSITIVE_FIELD_NAMES)


def _reject_sensitive_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_sensitive_key(key):
                raise ValueError(f"sensitive field is forbidden in supervision snapshot: {path}.{key}")
            _reject_sensitive_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_fields(child, f"{path}[{index}]")
    elif isinstance(value, str) and _is_high_confidence_secret_value(value):
        raise ValueError(f"sensitive value rejected at {path}")


def _is_high_confidence_secret_value(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    if re.search(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", normalized, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(r"(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}", normalized, flags=re.IGNORECASE)
        or re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", normalized)
        or re.search(r"Bearer\s+\S{12,}", normalized, flags=re.IGNORECASE)
        or re.search(r"AKIA[0-9A-Z]{16}", normalized)
    )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_negative_zero(value: float) -> bool:
    return value == 0.0 and math.copysign(1.0, value) < 0.0


def _project_last_good_value(name: str, value: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(value, dict):
        return None, []
    projected: dict[str, Any] = {}
    invalid: list[str] = []
    for key in sorted(LAST_GOOD_VALUE_FIELDS.get(name, set())):
        if key not in value:
            continue
        item = value.get(key)
        numeric_item = _number(item) if not isinstance(item, bool) and isinstance(item, (int, float)) else None
        if key in LAST_GOOD_NUMERIC_FIELDS and numeric_item is not None:
            projected[key] = item
        elif key in LAST_GOOD_INTEGER_FIELDS and not isinstance(item, bool) and isinstance(item, int) and item >= 0:
            projected[key] = item
        elif key in LAST_GOOD_TIMESTAMP_FIELDS:
            parsed = _parse_dt(item)
            if parsed is not None:
                projected[key] = parsed.isoformat()
            else:
                invalid.append(key)
        elif key == "currency" and isinstance(item, str):
            currency = item.strip().upper()
            if 2 <= len(currency) <= 12 and currency.isascii() and currency.isalnum():
                projected[key] = currency
            else:
                invalid.append(key)
        else:
            invalid.append(key)
    return projected, invalid


def _feed_snapshot(name: str, row: Any, evaluated_at: datetime) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    if not isinstance(row, dict):
        return {
            "status": "missing", "source_ts": None, "last_good_at": None,
            "age_seconds": None, "max_age_seconds": None, "stale": True,
            "last_good_value": None, "rest_baseline_complete": False if name == "orders" else None,
        }, [f"feed_missing:{name}"]

    status = str(row.get("status") or "missing").strip().lower()
    source_ts = _parse_dt(row.get("source_ts"))
    last_good_at = _parse_dt(row.get("last_good_at"))
    max_age = _number(row.get("max_age_seconds"))
    invalid_clock = False
    if row.get("source_ts") is not None and source_ts is None:
        blockers.append(f"feed_invalid_source_ts:{name}")
        invalid_clock = True
    if row.get("last_good_at") is not None and last_good_at is None:
        blockers.append(f"feed_invalid_last_good_at:{name}")
        invalid_clock = True
    if max_age is None or max_age <= 0:
        blockers.append(f"feed_invalid_max_age:{name}")
        invalid_clock = True
        max_age = None
    if any(timestamp is not None and timestamp > evaluated_at for timestamp in (source_ts, last_good_at)):
        blockers.append(f"feed_future_timestamp:{name}")
        invalid_clock = True
    freshness_anchor = source_ts or last_good_at
    age = (evaluated_at - freshness_anchor).total_seconds() if freshness_anchor and not invalid_clock else None
    stale = invalid_clock or age is None or max_age is None or age > max_age
    if status != "live":
        blockers.append(f"feed_unavailable:{name}:{status}")
    last_good_value, invalid_last_good_fields = _project_last_good_value(name, row.get("last_good_value"))
    for field in invalid_last_good_fields:
        blockers.append(f"feed_invalid_last_good_value:{name}:{field}")
        stale = True
    if last_good_value is None:
        blockers.append(f"feed_last_good_missing:{name}")
        stale = True
    if stale:
        blockers.append(f"feed_stale:{name}")
    return {
        "status": status,
        "source_ts": source_ts.isoformat() if source_ts else None,
        "last_good_at": last_good_at.isoformat() if last_good_at else None,
        "age_seconds": round(age, 3) if age is not None and age >= 0 else None,
        "max_age_seconds": max_age,
        "stale": stale,
        "last_good_value": last_good_value,
        "rest_baseline_complete": row.get("rest_baseline_complete") is True if name == "orders" else None,
    }, list(dict.fromkeys(blockers))


def _identity_blockers(channel: str, instrument: Any, *, require_region: bool) -> list[str]:
    if not isinstance(instrument, dict):
        return ["instrument_identity_missing"]
    blockers: list[str] = []
    if instrument.get("mapping_verified") is not True:
        blockers.append("underlying_mapping_unverified")
    if require_region and instrument.get("region_eligible") is not True:
        blockers.append("region_eligibility_unverified")
    if str(instrument.get("state") or "") != "live":
        blockers.append("instrument_not_live")
    if channel == "cex_spot":
        if not str(instrument.get("inst_id") or "").strip():
            blockers.append("cex_inst_id_missing")
        if str(instrument.get("inst_type") or "") != "SPOT":
            blockers.append("cex_inst_type_not_spot")
        if str(instrument.get("inst_category") or "") != "3":
            blockers.append("cex_inst_category_not_tokenized_stock")
        if str(instrument.get("mapping_scope") or "") != "exact_exchange_instrument_only":
            blockers.append("cex_mapping_scope_invalid")
        for field in ("tick_size", "lot_size", "min_size"):
            value = _number(instrument.get(field))
            if value is None or value <= 0:
                blockers.append(f"cex_invalid_{field}")
        if any(instrument.get(key) not in (None, "") for key in ("chain_id", "token_contract", "provider", "underlying_symbol")):
            blockers.append("cex_identity_contains_wallet_fields")
    elif channel == "wallet_dex":
        if any(instrument.get(key) not in (None, "") for key in (
            "inst_id", "inst_type", "inst_category", "tick_size", "lot_size", "min_size",
        )):
            blockers.append("wallet_identity_contains_cex_inst_id")
        if str(instrument.get("mapping_scope") or "") != "exact_chain_token":
            blockers.append("wallet_mapping_scope_invalid")
        for key in ("chain_id", "token_contract", "provider", "underlying_symbol"):
            if not str(instrument.get(key) or "").strip():
                blockers.append(f"wallet_identity_missing:{key}")
    return blockers


def _position_map(rows: Any, side: str) -> tuple[dict[str, float], list[str]]:
    output: dict[str, float] = {}
    errors: list[str] = []
    if not isinstance(rows, list):
        return output, [f"invalid_positions:{side}"]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"invalid_position_row:{side}:{index}")
            continue
        instrument_id = str(row.get("instrument_id") or "").strip()
        quantity = _number(row.get("quantity"))
        if not instrument_id or quantity is None or quantity < 0 or _is_negative_zero(quantity):
            errors.append(f"invalid_position_row:{side}:{index}")
            continue
        if instrument_id in output:
            errors.append(f"duplicate_position_instrument:{side}:{instrument_id}")
            continue
        output[instrument_id] = quantity
    return output, errors


def _open_order_map(rows: Any, side: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    output: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not isinstance(rows, list):
        return output, [f"invalid_open_orders:{side}"]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"invalid_open_order_row:{side}:{index}")
            continue
        order_id = str(row.get("order_id") or "").strip()
        instrument_id = str(row.get("instrument_id") or "").strip()
        state = str(row.get("state") or "").strip().lower()
        quantity = _number(row.get("quantity"))
        if not order_id or not instrument_id or quantity is None or quantity <= 0:
            errors.append(f"invalid_open_order_row:{side}:{index}")
            continue
        if state not in {"live", "open", "partially_filled", "pending_cancel"}:
            errors.append(f"unknown_open_order_state:{side}:{order_id}")
        if order_id in output:
            errors.append(f"duplicate_open_order_id:{side}:{order_id}")
        output[order_id] = {
            "instrument_id": instrument_id,
            "state": state,
            "quantity": quantity,
        }
    return output, errors


def _fill_map(rows: Any, side: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    output: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not isinstance(rows, list):
        return output, [f"invalid_fills:{side}"]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"invalid_fill_row:{side}:{index}")
            continue
        fill_id = str(row.get("fill_id") or "").strip()
        order_id = str(row.get("order_id") or "").strip()
        instrument_id = str(row.get("instrument_id") or "").strip()
        fill_side = str(row.get("side") or "").strip().lower()
        quantity = _number(row.get("quantity"))
        price = _number(row.get("price"))
        fill_ts = _parse_dt(row.get("fill_ts"))
        if (
            not fill_id or not order_id or not instrument_id
            or fill_side not in {"buy", "sell"}
            or quantity is None or quantity <= 0
            or price is None or price <= 0
            or fill_ts is None
        ):
            errors.append(f"invalid_fill_row:{side}:{index}")
            continue
        if fill_id in output:
            errors.append(f"duplicate_fill_id:{side}:{fill_id}")
        canonical = {
            "order_id": order_id,
            "instrument_id": instrument_id,
            "side": fill_side,
            "quantity": quantity,
            "price": price,
            "fill_ts": fill_ts.isoformat(),
        }
        if "fee" in row:
            fee = _number(row.get("fee"))
            if fee is None:
                errors.append(f"invalid_fill_fee:{side}:{fill_id}")
                continue
            canonical["fee"] = fee
        if "fee_currency" in row:
            fee_currency = str(row.get("fee_currency") or "").strip().upper()
            if not fee_currency or not fee_currency.isascii() or not fee_currency.isalnum() or len(fee_currency) > 12:
                errors.append(f"invalid_fill_fee_currency:{side}:{fill_id}")
                continue
            canonical["fee_currency"] = fee_currency
        output[fill_id] = canonical
    return output, errors


def _reconcile(venue_state: Any, local_state: Any, tolerance: float) -> dict[str, Any]:
    empty = {
        "status": "unknown", "position_mismatches": [],
        "venue_only_order_ids": [], "local_only_order_ids": [],
        "order_detail_mismatches": [],
        "venue_only_fill_ids": [], "local_only_fill_ids": [],
        "fill_detail_mismatches": [],
        "validation_errors": [],
    }
    if not isinstance(venue_state, dict) or not isinstance(local_state, dict):
        return empty | {"validation_errors": ["missing_reconciliation_state"]}
    venue_positions, venue_position_errors = _position_map(venue_state.get("positions"), "venue")
    local_positions, local_position_errors = _position_map(local_state.get("positions"), "local")
    venue_orders, venue_order_errors = _open_order_map(venue_state.get("open_orders"), "venue")
    local_orders, local_order_errors = _open_order_map(local_state.get("open_orders"), "local")
    venue_fills, venue_fill_errors = _fill_map(venue_state.get("fills"), "venue")
    local_fills, local_fill_errors = _fill_map(local_state.get("fills"), "local")
    validation_errors = list(dict.fromkeys(
        venue_position_errors + local_position_errors
        + venue_order_errors + local_order_errors
        + venue_fill_errors + local_fill_errors
    ))
    position_mismatches: list[dict[str, Any]] = []
    for instrument_id in sorted(set(venue_positions) | set(local_positions)):
        venue_qty = venue_positions.get(instrument_id, 0.0)
        local_qty = local_positions.get(instrument_id, 0.0)
        if abs(venue_qty - local_qty) > tolerance:
            position_mismatches.append({
                "instrument_id": instrument_id,
                "venue_quantity": venue_qty,
                "local_quantity": local_qty,
                "difference": round(venue_qty - local_qty, 12),
            })
    venue_order_ids = set(venue_orders)
    local_order_ids = set(local_orders)
    venue_only_orders = sorted(venue_order_ids - local_order_ids)
    local_only_orders = sorted(local_order_ids - venue_order_ids)
    order_detail_mismatches: list[dict[str, Any]] = []
    for order_id in sorted(venue_order_ids & local_order_ids):
        venue_order = venue_orders[order_id]
        local_order = local_orders[order_id]
        if (
            venue_order["instrument_id"] != local_order["instrument_id"]
            or venue_order["state"] != local_order["state"]
            or abs(venue_order["quantity"] - local_order["quantity"]) > tolerance
        ):
            order_detail_mismatches.append({
                "order_id": order_id,
                "venue": copy.deepcopy(venue_order),
                "local": copy.deepcopy(local_order),
            })
    venue_fill_ids = set(venue_fills)
    local_fill_ids = set(local_fills)
    venue_only_fills = sorted(venue_fill_ids - local_fill_ids)
    local_only_fills = sorted(local_fill_ids - venue_fill_ids)
    fill_detail_mismatches: list[dict[str, Any]] = []
    for fill_id in sorted(venue_fill_ids & local_fill_ids):
        if venue_fills[fill_id] != local_fills[fill_id]:
            fill_detail_mismatches.append({
                "fill_id": fill_id,
                "venue": copy.deepcopy(venue_fills[fill_id]),
                "local": copy.deepcopy(local_fills[fill_id]),
            })
    mismatch = bool(
        position_mismatches or venue_only_orders or local_only_orders
        or order_detail_mismatches or venue_only_fills or local_only_fills
        or fill_detail_mismatches
    )
    return {
        "status": "unknown" if validation_errors else "mismatch" if mismatch else "matched",
        "position_mismatches": position_mismatches,
        "venue_only_order_ids": venue_only_orders,
        "local_only_order_ids": local_only_orders,
        "order_detail_mismatches": order_detail_mismatches,
        "venue_only_fill_ids": venue_only_fills,
        "local_only_fill_ids": local_only_fills,
        "fill_detail_mismatches": fill_detail_mismatches,
        "validation_errors": validation_errors,
    }


def _project_entry_score(value: Any) -> tuple[dict[str, Any], bool]:
    unavailable = {
        "status": "unavailable", "entry_score_100": None, "factor_coverage": 0.0,
        "confidence": "low", "unresolved_conflict": False,
        "entry_permission_ceiling": None, "factors": {},
    }
    if value is None:
        return unavailable, False
    if not isinstance(value, dict) or value.get("schema_version") != "entry_score_compilation.v1":
        return unavailable, True
    status = str(value.get("status") or "").strip()
    score = _number(value.get("entry_score_100")) if value.get("entry_score_100") is not None else None
    coverage = _number(value.get("factor_coverage"))
    confidence = str(value.get("confidence") or "low").strip().lower()
    conflict = value.get("unresolved_conflict")
    raw_ceiling = value.get("entry_permission_ceiling")
    ceiling = raw_ceiling if raw_ceiling is None or raw_ceiling == "BLOCK" else None
    if (
        status not in {"complete", "insufficient_data"}
        or (status == "complete" and (score is None or not 0 <= score <= 100))
        or (status == "insufficient_data" and score is not None)
        or coverage is None or not 0 <= coverage <= 1
        or confidence not in {"low", "medium", "high"}
        or not isinstance(conflict, bool)
        or (raw_ceiling is not None and raw_ceiling != "BLOCK")
        or (status == "complete" and not math.isclose(coverage, 1.0, abs_tol=1e-9))
        or (status == "insufficient_data" and ceiling != "BLOCK")
    ):
        return unavailable, True
    factors: dict[str, dict[str, float]] = {}
    raw_factors = value.get("factors")
    if isinstance(raw_factors, dict):
        for name, weight in ENTRY_FACTOR_WEIGHTS.items():
            row = raw_factors.get(name)
            if not isinstance(row, dict):
                continue
            factor_score = _number(row.get("score"))
            contribution = _number(row.get("contribution_points_100"))
            expected_contribution = round(factor_score * weight * 20, 2) if factor_score is not None else None
            if (
                factor_score is None or not 0 <= factor_score <= 5
                or contribution is None or contribution < 0
                or expected_contribution is None
                or not math.isclose(contribution, expected_contribution, abs_tol=1e-9)
            ):
                return unavailable, True
            factors[name] = {"score": factor_score, "contribution_points_100": contribution}
    if status == "complete" and (
        set(factors) != set(ENTRY_FACTOR_WEIGHTS)
        or score is None
        or not math.isclose(sum(row["contribution_points_100"] for row in factors.values()), score, abs_tol=1e-9)
    ):
        return unavailable, True
    if status == "insufficient_data" and factors:
        return unavailable, True
    return {
        "status": status,
        "entry_score_100": score,
        "factor_coverage": coverage,
        "confidence": confidence,
        "unresolved_conflict": conflict,
        "entry_permission_ceiling": ceiling,
        "factors": factors,
    }, False


def _entry_score_blockers(entry_score: dict[str, Any], invalid: bool, mode: str) -> list[str]:
    if mode not in {"demo", "live"}:
        return []
    blockers: list[str] = []
    if invalid:
        blockers.append("entry_score_projection_invalid")
    status = str(entry_score.get("status") or "unavailable")
    if status != "complete":
        blockers.append(f"entry_score_not_ready:{status}")
        return blockers
    if entry_score.get("unresolved_conflict") is True:
        blockers.append("entry_score_unresolved_conflict")
    if entry_score.get("entry_permission_ceiling") == "BLOCK":
        blockers.append("entry_score_entry_blocked")
    if entry_score.get("confidence") != "high":
        blockers.append("entry_score_confidence_not_high")
    return blockers


def _permission_blockers(payload: dict[str, Any], mode: str) -> tuple[list[str], dict[str, Any]]:
    blockers: list[str] = []
    credential_scope = str(payload.get("credential_scope") or "").strip().lower()
    execution_permission = str(payload.get("execution_permission") or "").strip().lower()
    expected_scope = "none" if mode == "public" else "read_only"
    expected_permission = {
        "public": "disabled", "read_only": "disabled", "demo": "paper", "live": "live_approved",
    }.get(mode)
    if credential_scope != expected_scope:
        blockers.append(f"invalid_credential_scope:{credential_scope or 'missing'}")
    if expected_permission is not None and execution_permission != expected_permission:
        blockers.append(f"invalid_execution_permission:{execution_permission or 'missing'}")
    raw_source_scope = payload.get("source_credential_scope")
    source_credential_scope = (
        str(raw_source_scope).strip().lower() if raw_source_scope is not None else None
    )
    raw_projection = payload.get("snapshot_projection_read_only")
    snapshot_projection_read_only = raw_projection if isinstance(raw_projection, bool) else None
    if source_credential_scope is not None:
        if source_credential_scope != "read_trade":
            blockers.append(f"invalid_source_credential_scope:{source_credential_scope or 'missing'}")
        if mode != "demo":
            blockers.append("read_trade_source_projection_demo_only")
        if snapshot_projection_read_only is not True or credential_scope != "read_only":
            blockers.append("read_trade_source_requires_read_only_projection")
    elif raw_projection is not None:
        blockers.append("snapshot_projection_without_source_scope")
    raw_controls = payload.get("live_controls")
    controls: dict[str, Any] = raw_controls if isinstance(raw_controls, dict) else {}
    if mode == "live":
        if controls.get("approval_verified") is not True:
            blockers.append("live_approval_unverified")
        if controls.get("ip_allowlist_verified") is not True:
            blockers.append("live_ip_allowlist_unverified")
        if controls.get("withdrawal_enabled") is not False:
            blockers.append("live_withdrawal_must_be_disabled")
        if controls.get("credentials_rotated") is not True:
            blockers.append("live_credentials_not_rotated")
    return blockers, {
        "credential_scope": credential_scope,
        "source_credential_scope": source_credential_scope,
        "snapshot_projection_read_only": snapshot_projection_read_only,
        "execution_permission": execution_permission,
        "live_controls": {
            key: controls.get(key) if isinstance(controls.get(key), bool) else None
            for key in ("approval_verified", "ip_allowlist_verified", "withdrawal_enabled", "credentials_rotated")
        } if mode == "live" else None,
    }


def _signal(
    *,
    module: str,
    sub_framework: str,
    as_of: datetime,
    evidence_refs: list[str],
    level: str,
    entry: str,
    multiplier: float,
    hard_veto: bool,
    reason: str,
    holding: str = "HOLD",
    repair: str = "",
    unresolved: bool = False,
) -> dict[str, Any]:
    if module not in EXISTING_MODULES:
        raise ValueError(f"unregistered supervision mapping: {module}")
    return {
        "module": module,
        "sub_framework": sub_framework,
        "max_action_level": level,
        "entry_permission": entry,
        "holding_directive": holding,
        "position_multiplier": multiplier,
        "hard_veto": hard_veto,
        "unresolved_conflict": unresolved,
        "evidence_refs": evidence_refs,
        "observed_at": as_of.isoformat(),
        "stale_after": (as_of + timedelta(seconds=30)).isoformat(),
        "reason": reason,
        "repair_signal": repair,
        "tighten_only": True,
        "cannot_raise_upstream": True,
        "no_order_execution": True,
    }


def compile_supervision(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    _reject_sensitive_fields(payload)
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reference_now = reference_now.astimezone(timezone.utc)
    blockers: list[str] = []
    warnings: list[str] = []
    parsed_as_of = _parse_dt(payload.get("as_of"))
    if parsed_as_of is None:
        blockers.append("invalid_as_of")
    elif parsed_as_of > reference_now:
        blockers.append("future_as_of")
    as_of = parsed_as_of if parsed_as_of is not None and parsed_as_of <= reference_now else reference_now

    if payload.get("schema_version") != SCHEMA:
        blockers.append(f"unsupported_schema:{payload.get('schema_version')}")
    if payload.get("no_order_execution") is not True:
        blockers.append("no_order_execution_required")
    if str(payload.get("venue") or "").lower() != "okx":
        blockers.append("venue_must_be_okx")
    channel = str(payload.get("channel") or "").strip().lower()
    if channel not in ALLOWED_CHANNELS:
        blockers.append(f"unsupported_channel:{channel or 'missing'}")
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in ALLOWED_MODES:
        blockers.append(f"unsupported_mode:{mode or 'missing'}")
    permission_blockers, permission_projection = _permission_blockers(payload, mode)
    blockers.extend(permission_blockers)
    if channel in ALLOWED_CHANNELS:
        blockers.extend(_identity_blockers(channel, payload.get("instrument"), require_region=mode != "public"))

    raw_refs = payload.get("evidence_refs")
    evidence_refs = [str(ref).strip() for ref in raw_refs if isinstance(ref, str) and str(ref).strip()] if isinstance(raw_refs, list) else []
    if not evidence_refs:
        evidence_refs = ["DATA-GAP-okx-supervision"]
        blockers.append("missing_evidence_refs")
    elif any(not ref.startswith("EID-") for ref in evidence_refs):
        blockers.append("invalid_evidence_refs")

    raw_feeds = payload.get("feeds")
    feeds: dict[str, Any] = raw_feeds if isinstance(raw_feeds, dict) else {}
    freshness: dict[str, Any] = {}
    required_feeds = ["market"] if mode == "public" else ["market", "account", "orders"]
    feed_blockers: list[str] = []
    for name in required_feeds:
        snapshot, errors = _feed_snapshot(name, feeds.get(name), reference_now)
        freshness[name] = snapshot
        feed_blockers.extend(errors)
    blockers.extend(feed_blockers)

    raw_strategy = payload.get("strategy")
    strategy: dict[str, Any] = raw_strategy if isinstance(raw_strategy, dict) else {}
    heartbeat = _parse_dt(strategy.get("heartbeat_at"))
    heartbeat_limit = _number(strategy.get("max_heartbeat_age_seconds"))
    heartbeat_age = (reference_now - heartbeat).total_seconds() if heartbeat and heartbeat <= reference_now else None
    strategy_status = str(strategy.get("status") or "missing").strip().lower()
    raw_stop_new_entries = strategy.get("stop_new_entries")
    stop_new_entries = raw_stop_new_entries if isinstance(raw_stop_new_entries, bool) else None
    private_ws_connected: bool | None = None
    private_transport: str | None = None
    rest_polling_healthy: bool | None = None
    poll_interval_seconds: float | None = None
    if mode != "public":
        if strategy_status != "running":
            blockers.append(f"strategy_not_running:{strategy_status}")
        if heartbeat is not None and heartbeat > reference_now:
            blockers.append("strategy_heartbeat_future")
        if heartbeat_limit is None or heartbeat_limit <= 0:
            blockers.append("strategy_heartbeat_limit_invalid")
        if heartbeat_age is None or heartbeat_limit is None or heartbeat_age > heartbeat_limit:
            blockers.append("strategy_heartbeat_stale")
        if not isinstance(raw_stop_new_entries, bool):
            blockers.append("invalid_strategy_stop_new_entries")
        elif stop_new_entries is True:
            blockers.append("strategy_stop_new_entries_active")
        raw_connection = payload.get("connection")
        connection: dict[str, Any] = raw_connection if isinstance(raw_connection, dict) else {}
        raw_private_ws_connected = connection.get("private_ws_connected")
        private_ws_connected = raw_private_ws_connected if isinstance(raw_private_ws_connected, bool) else None
        raw_private_transport = connection.get("private_transport")
        private_transport = str(raw_private_transport).strip().lower() if raw_private_transport is not None else None
        raw_rest_polling_healthy = connection.get("rest_polling_healthy")
        rest_polling_healthy = raw_rest_polling_healthy if isinstance(raw_rest_polling_healthy, bool) else None
        poll_interval_seconds = _number(connection.get("poll_interval_seconds"))
        demo_rest_polling = (
            mode == "demo"
            and private_transport == "rest_polling"
            and rest_polling_healthy is True
            and poll_interval_seconds is not None
            and 0 < poll_interval_seconds <= 30
        )
        if private_ws_connected is not True and not demo_rest_polling:
            blockers.append("private_ws_disconnected" if private_ws_connected is False else "private_ws_state_missing")
        elif demo_rest_polling and private_ws_connected is not True:
            warnings.append("demo_rest_polling_instead_of_private_ws")
    else:
        blockers.append("private_state_unavailable:public_mode")
        strategy_status = "not_available"
        heartbeat = None
        heartbeat_limit = None
        heartbeat_age = None
        stop_new_entries = False

    raw_instrument = payload.get("instrument")
    instrument: dict[str, Any] = raw_instrument if isinstance(raw_instrument, dict) else {}
    inst_id = str(instrument.get("inst_id") or "").strip()
    raw_allowed = strategy.get("allowed_instruments")
    allowed: list[Any] = raw_allowed if isinstance(raw_allowed, list) else []
    if mode != "public" and channel == "cex_spot" and inst_id and inst_id not in {str(item) for item in allowed}:
        blockers.append(f"instrument_not_allowlisted:{inst_id}")

    entry_score, invalid_entry_score = _project_entry_score(payload.get("entry_score"))
    if invalid_entry_score:
        warnings.append("entry_score_projection_invalid")
    blockers.extend(_entry_score_blockers(entry_score, invalid_entry_score, mode))

    raw_policy = payload.get("policy")
    policy: dict[str, Any] = raw_policy if isinstance(raw_policy, dict) else {}
    tolerance = _number(policy.get("position_tolerance"))
    if policy.get("position_tolerance") is not None and (tolerance is None or tolerance < 0):
        blockers.append("invalid_position_tolerance")
    lot_size = _number(instrument.get("lot_size"))
    effective_lot_size = lot_size if lot_size is not None and lot_size > 0 else None
    if tolerance is not None and tolerance >= 0 and effective_lot_size is not None and tolerance >= effective_lot_size:
        blockers.append("position_tolerance_not_below_lot_size")
        tolerance = 0.0
    epsilon = min(1e-12, effective_lot_size / 10) if effective_lot_size is not None else 1e-12
    tolerance = max(tolerance if tolerance is not None and tolerance >= 0 else 0.0, epsilon)
    if mode == "public":
        reconciliation = {
            "status": "unknown",
            "position_mismatches": [],
            "venue_only_order_ids": [],
            "local_only_order_ids": [],
            "order_detail_mismatches": [],
            "venue_only_fill_ids": [],
            "local_only_fill_ids": [],
            "fill_detail_mismatches": [],
            "validation_errors": [],
        }
    else:
        reconciliation = _reconcile(payload.get("venue_state"), payload.get("local_state"), tolerance)
        if reconciliation["status"] == "unknown":
            blockers.append("reconciliation_state_invalid")
            if any(error.startswith("unknown_open_order_state:") for error in reconciliation["validation_errors"]):
                blockers.append("unknown_open_order_state")
        if reconciliation["status"] != "matched":
            for item in reconciliation["position_mismatches"]:
                blockers.append(f"position_mismatch:{item['instrument_id']}")
            if reconciliation["venue_only_order_ids"] or reconciliation["local_only_order_ids"]:
                blockers.append("open_order_mismatch")
            if reconciliation["order_detail_mismatches"]:
                blockers.append("open_order_detail_mismatch")
            if reconciliation["venue_only_fill_ids"] or reconciliation["local_only_fill_ids"]:
                blockers.append("fill_mismatch")
            if reconciliation["fill_detail_mismatches"]:
                blockers.append("fill_detail_mismatch")

    position_reconciliation_status = (
        "pending" if reconciliation["status"] == "unknown"
        else "drift" if reconciliation["position_mismatches"]
        else "matched"
    )
    order_state_status = "unknown" if mode == "public" or reconciliation["status"] == "unknown" else "known"
    if reconciliation["venue_only_order_ids"] or reconciliation["local_only_order_ids"]:
        order_state_status = "unknown"
    if reconciliation["order_detail_mismatches"]:
        order_state_status = "unknown"
    if reconciliation["venue_only_fill_ids"] or reconciliation["local_only_fill_ids"]:
        order_state_status = "unknown"
    if reconciliation["fill_detail_mismatches"]:
        order_state_status = "unknown"
    raw_orders_feed = feeds.get("orders")
    orders_feed: dict[str, Any] = raw_orders_feed if isinstance(raw_orders_feed, dict) else {}
    rest_baseline_complete = orders_feed.get("rest_baseline_complete") is True if mode != "public" else False
    if mode != "public" and not rest_baseline_complete:
        blockers.append("orders_rest_baseline_missing")
        order_state_status = "unknown"
    if any(item.startswith("feed_") and ":orders" in item for item in blockers):
        order_state_status = "unknown"

    raw_results = payload.get("execution_results")
    execution_results = raw_results if isinstance(raw_results, list) else []
    if raw_results is not None and not isinstance(raw_results, list):
        blockers.append("execution_results_invalid")
    failed_items = [
        row for row in execution_results
        if not isinstance(row, dict) or str(row.get("sCode") or "") != "0"
    ]
    if failed_items:
        partial_failure = len(failed_items) < len(execution_results)
        blockers.append("execution_partial_failure" if partial_failure else "execution_failure")
        order_state_status = "partial_failure" if partial_failure and order_state_status == "known" else "unknown"
    execution_summary = {
        "total_items": len(execution_results),
        "failed_items": len(failed_items),
        "failure_codes": sorted({str(row.get("sCode") or "invalid") for row in failed_items if isinstance(row, dict)}),
    }

    max_spread = _number(policy.get("max_spread_bps"))
    market_last_good = freshness.get("market", {}).get("last_good_value")
    spread = _number(market_last_good.get("spread_bps")) if isinstance(market_last_good, dict) else None
    liquidity_blocked = False
    if mode != "public" and (max_spread is None or max_spread <= 0):
        blockers.append("max_spread_policy_invalid")
        liquidity_blocked = True
    elif max_spread is not None and spread is not None and spread > max_spread:
        blockers.append("spread_exceeds_policy")
        liquidity_blocked = True
    elif max_spread is not None and spread is None:
        blockers.append("spread_unknown")
        liquidity_blocked = True

    raw_risk = payload.get("risk")
    risk: dict[str, Any] = raw_risk if isinstance(raw_risk, dict) else {}
    raw_hard_redline = risk.get("account_hard_redline")
    hard_redline = raw_hard_redline is True
    if mode != "public" and not isinstance(raw_hard_redline, bool):
        blockers.append("account_hard_redline_state_invalid")
    if hard_redline:
        blockers.append("account_hard_redline")

    blockers = list(dict.fromkeys(blockers))
    identity_blocked = any(
        item.startswith(("instrument_", "cex_", "wallet_", "underlying_", "region_", "venue_", "unsupported_channel"))
        for item in blockers
    )
    market_feed_blocked = any(item.startswith("feed_") and ":market" in item for item in blockers)
    market_analysis_allowed = not identity_blocked and not market_feed_blocked
    analysis_only = mode in {"public", "read_only"}
    new_entries_allowed = not blockers and mode in {"demo", "live"}
    pause_required = bool(blockers) and mode in {"demo", "live"}
    pause_effective = stop_new_entries is True or strategy_status == "paused"

    data_related = [
        item for item in blockers
        if item not in {
            "account_hard_redline", "spread_exceeds_policy", "spread_unknown",
            "open_order_mismatch", "open_order_detail_mismatch", "fill_mismatch", "fill_detail_mismatch",
        }
        and not item.startswith("position_mismatch:")
    ]
    signals: list[dict[str, Any]] = []
    if data_related:
        signals.append(_signal(
            module="data_quality",
            sub_framework="okx_execution_supervision",
            as_of=as_of,
            evidence_refs=evidence_refs,
            level="L0",
            entry="BLOCK",
            multiplier=0.0,
            hard_veto=True,
            reason=";".join(data_related),
            repair="refresh_okx_snapshot_and_reconcile",
        ))

    if reconciliation["status"] == "mismatch":
        signals.append(_signal(
            module="conflict_ledger",
            sub_framework="okx_broker_reconciliation",
            as_of=as_of,
            evidence_refs=evidence_refs,
            level="L0",
            entry="BLOCK",
            multiplier=0.0,
            hard_veto=True,
            reason="venue and local state do not match",
            repair="reconcile_venue_and_local_state",
            unresolved=True,
        ))

    if hard_redline:
        signals.append(_signal(
            module="account",
            sub_framework="okx_account_risk",
            as_of=as_of,
            evidence_refs=evidence_refs,
            level="L5",
            entry="BLOCK",
            multiplier=0.0,
            hard_veto=True,
            holding="EXIT",
            reason=str(risk.get("reason") or "account hard redline active"),
            repair="manual_account_risk_review",
        ))

    if liquidity_blocked:
        signals.append(_signal(
            module="liquidity",
            sub_framework="okx_tokenized_stock_execution_quality",
            as_of=as_of,
            evidence_refs=evidence_refs,
            level="L0",
            entry="BLOCK",
            multiplier=0.0,
            hard_veto=False,
            reason=f"spread_bps={spread};policy_max={max_spread}",
            repair="wait_for_liquidity_or_reduce_proposed_size",
        ))

    return {
        "ok": not blockers,
        "schema_version": "okx_execution_supervision.v1",
        "as_of": as_of.isoformat(),
        "venue": "okx",
        "channel": channel,
        "mode": mode,
        "credential_scope": permission_projection["credential_scope"],
        "source_credential_scope": permission_projection["source_credential_scope"],
        "snapshot_projection_read_only": permission_projection["snapshot_projection_read_only"],
        "execution_permission": permission_projection["execution_permission"],
        "live_controls": permission_projection["live_controls"],
        "instrument": {
            key: instrument.get(key)
            for key in (
                "inst_id", "inst_type", "inst_category", "underlying_symbol",
                "mapping_scope", "mapping_verified", "region_eligible", "state",
                "tick_size", "lot_size", "min_size",
                "chain_id", "token_contract", "provider",
            )
            if key in instrument
        },
        "status": "healthy" if not blockers else "blocked",
        "analysis_only": analysis_only,
        "market_analysis_allowed": market_analysis_allowed,
        "new_entries_allowed": new_entries_allowed,
        "pause_required": pause_required,
        "pause_effective": pause_effective,
        "pause_semantics": "stops new entries only; does not cancel open orders or close positions",
        "connection": {
            "private_ws_connected": private_ws_connected,
            "private_transport": private_transport,
            "rest_polling_healthy": rest_polling_healthy,
            "poll_interval_seconds": poll_interval_seconds,
            "rest_baseline_complete": rest_baseline_complete,
        },
        "freshness": freshness,
        "strategy": {
            "status": strategy_status,
            "heartbeat_at": heartbeat.isoformat() if heartbeat else None,
            "heartbeat_age_seconds": round(heartbeat_age, 3) if heartbeat_age is not None else None,
            "max_heartbeat_age_seconds": heartbeat_limit,
            "stop_new_entries": stop_new_entries,
        },
        "reconciliation": reconciliation,
        "position_reconciliation_status": position_reconciliation_status,
        "order_state_status": order_state_status,
        "execution_result_summary": execution_summary,
        "entry_score": entry_score,
        "blockers": blockers,
        "warnings": warnings,
        "module_signals": signals,
        "existing_module_mapping_only": True,
        "compiler_effect": "tighten_only" if signals else "none",
        "supervisor_did_not_pause_strategy": pause_required and not pause_effective,
        "no_order_execution": True,
    }


def _parse_json_integer(value: str) -> int | float:
    parsed = int(value)
    return -0.0 if parsed == 0 and value.startswith("-") else parsed


def _load_payload(path: str | None) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    parsed = json.loads(text, parse_int=_parse_json_integer)
    if not isinstance(parsed, dict):
        raise ValueError("snapshot must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile an OKX supervision snapshot without side effects")
    parser.add_argument("path", nargs="?", help="Input JSON file; stdin when omitted")
    args = parser.parse_args(argv)
    try:
        result = compile_supervision(_load_payload(args.path))
    except (OSError, OverflowError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "no_order_execution": True}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
