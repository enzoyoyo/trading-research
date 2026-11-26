#!/usr/bin/env python3
"""Compile the canonical five-factor research score into an auditable 0-100 view.

This script is a research/readiness layer. It never places, cancels, or amends
orders, and its score never replaces the Decision Compiler.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "entry_score.v1"
SCORE_VERSION = "five_factor_v1"
FACTOR_WEIGHTS: dict[str, float] = {
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
                raise ValueError(f"sensitive field is forbidden in entry-score payload: {path}.{key}")
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


def _valid_factor_evidence_refs(refs: list[str]) -> bool:
    return bool(refs) and all(ref.startswith("EID-") and len(ref) > 4 for ref in refs)


def _valid_identity_evidence_refs(refs: list[str]) -> bool:
    return bool(refs) and all(
        (ref.startswith("EID-") and len(ref) > 4)
        or (ref.startswith("OKX-PUBLIC-") and len(ref) > 11)
        for ref in refs
    )


def _validate_product_identity(
    value: Any,
    *,
    symbol: str,
    as_of: datetime,
    evaluation_at: datetime,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        return {}, ["missing_product_identity"]

    errors: list[str] = []
    venue = str(value.get("venue") or "").strip().lower()
    channel = str(value.get("channel") or "").strip().lower()
    instrument_id = str(value.get("instrument_id") or "").strip().upper()
    instrument_type = str(value.get("instrument_type") or "").strip().upper()
    instrument_category = str(value.get("instrument_category") or "").strip()
    mapping_scope = str(value.get("mapping_scope") or "").strip().lower()
    state = str(value.get("state") or "").strip().lower()
    evidence_refs_raw = value.get("evidence_refs")
    evidence_refs = [
        str(ref).strip()
        for ref in evidence_refs_raw
        if isinstance(ref, str) and str(ref).strip()
    ] if isinstance(evidence_refs_raw, list) else []
    observed_at = _parse_dt(value.get("observed_at"))
    stale_after = _parse_dt(value.get("stale_after"))

    if venue != "okx":
        errors.append("product_identity_venue_not_okx")
    if not channel:
        errors.append("missing_product_identity_channel")
    if channel == "cex_spot":
        if not instrument_id:
            errors.append("missing_product_identity_instrument_id")
        elif symbol and instrument_id != symbol:
            errors.append("product_identity_symbol_mismatch")
        if instrument_type != "SPOT":
            errors.append("product_identity_instrument_type_not_spot")
        if instrument_category != "3":
            errors.append("product_identity_instrument_category_not_3")
        if mapping_scope != "exact_exchange_instrument_only":
            errors.append("product_identity_mapping_scope_invalid")
        if any(str(value.get(key) or "").strip() for key in ("chain_id", "token_contract", "provider", "underlying_symbol")):
            errors.append("product_identity_channel_mix")
    elif channel == "wallet_dex":
        if not str(value.get("chain_id") or "").strip():
            errors.append("missing_product_identity_chain_id")
        if not str(value.get("token_contract") or "").strip():
            errors.append("missing_product_identity_token_contract")
        if not str(value.get("provider") or "").strip():
            errors.append("missing_product_identity_provider")
        if not str(value.get("underlying_symbol") or "").strip():
            errors.append("missing_product_identity_underlying_symbol")
        if mapping_scope != "exact_chain_token":
            errors.append("product_identity_mapping_scope_invalid")
        if any(str(value.get(key) or "").strip() for key in ("instrument_id", "instrument_type", "instrument_category")):
            errors.append("product_identity_channel_mix")
    else:
        errors.append(f"unsupported_product_identity_channel:{channel or 'missing'}")
    if value.get("mapping_verified") is not True:
        errors.append("product_identity_mapping_unverified")
    if state != "live":
        errors.append(f"product_identity_not_live:{state or 'missing'}")
    if not evidence_refs:
        errors.append("missing_product_identity_evidence_refs")
    elif not _valid_identity_evidence_refs(evidence_refs):
        errors.append("invalid_product_identity_evidence_refs")
    if observed_at is None:
        errors.append("invalid_product_identity_observed_at")
    elif observed_at > as_of or observed_at > evaluation_at:
        errors.append("future_product_identity_observed_at")
    if stale_after is None:
        errors.append("invalid_product_identity_stale_after")
    elif stale_after <= evaluation_at:
        errors.append("stale_product_identity")
    region_eligible = value.get("region_eligible")
    if region_eligible is not True and region_eligible is not False and region_eligible is not None:
        errors.append("invalid_region_eligible")

    allowed_keys = (
        "venue", "channel", "instrument_id", "instrument_type",
        "instrument_category", "mapping_scope", "mapping_verified", "state", "region_eligible",
        "underlying_symbol", "chain_id", "token_contract", "provider",
    )
    identity = {key: value.get(key) for key in allowed_keys if key in value}
    identity["evidence_refs"] = evidence_refs
    identity["observed_at"] = observed_at.isoformat() if observed_at else None
    identity["stale_after"] = stale_after.isoformat() if stale_after else None
    return identity, errors


def _valid_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 5.0:
        return None
    return number


def _band(weighted_score: float) -> tuple[str, str, str]:
    if weighted_score >= 4.0:
        return "L2-L3", "ADD", "actionable_with_caveats"
    if weighted_score >= 3.0:
        return "L1", "TEST", "working_view"
    if weighted_score >= 2.0:
        return "L0", "WATCH", "watch_only"
    return "L4-L5", "BLOCK", "not_actionable"


def _insufficient_result(
    symbol: str,
    errors: list[str],
    as_of: datetime,
    *,
    product_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signal = {
        "module": "data_quality",
        "sub_framework": "multi_factor_entry_score",
        "max_action_level": "L0",
        "entry_permission": "BLOCK",
        "holding_directive": "HOLD",
        "position_multiplier": 0.0,
        "hard_veto": True,
        "evidence_refs": ["DATA-GAP-entry-score"],
        "observed_at": as_of.isoformat(),
        "stale_after": as_of.isoformat(),
        "reason": ";".join(list(dict.fromkeys(errors))),
        "repair_signal": "collect_missing_or_fresh_factor_evidence",
        "tighten_only": True,
        "cannot_raise_upstream": True,
        "no_order_execution": True,
    }
    return {
        "ok": False,
        "schema_version": "entry_score_compilation.v1",
        "score_version": SCORE_VERSION,
        "symbol": symbol,
        "product_identity": product_identity or {},
        "as_of": as_of.isoformat(),
        "status": "insufficient_data",
        "entry_score_100": None,
        "weighted_score_0_5": None,
        "factor_coverage": 0.0,
        "validation_errors": list(dict.fromkeys(errors)),
        "unresolved_conflict": False,
        "entry_permission_ceiling": "BLOCK",
        "factor_weights": dict(FACTOR_WEIGHTS),
        "confidence": "low",
        "risk_adjustments": ["score_not_published_due_to_validation_failure"],
        "suggested_module_signal": signal,
        "suggested_module_signals": [signal],
        "calculation_formula": "sum(factor_score_0_5 * canonical_weight) * 20",
        "compiler_effect": "block_only",
        "cannot_authorize_action": True,
        "no_order_execution": True,
    }


def compile_entry_score(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Validate and compile an ``entry_score.v1`` payload.

    Missing, stale, future, or untraceable factor evidence produces no numeric
    total. A wide cross-factor conflict may publish the arithmetic score but
    blocks decision use until the conflict is resolved.
    """
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    _reject_sensitive_fields(payload)

    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reference_now = reference_now.astimezone(timezone.utc)
    parsed_as_of = _parse_dt(payload.get("as_of"))
    errors: list[str] = []
    if parsed_as_of is None:
        errors.append("invalid_as_of")
    elif parsed_as_of > reference_now:
        errors.append("future_as_of")
    as_of = parsed_as_of if parsed_as_of is not None and parsed_as_of <= reference_now else reference_now
    symbol = str(payload.get("symbol") or "").strip().upper()
    raw_factors = payload.get("factors")
    factors: dict[str, Any] = raw_factors if isinstance(raw_factors, dict) else {}
    product_identity, identity_errors = _validate_product_identity(
        payload.get("product_identity"),
        symbol=symbol,
        as_of=as_of,
        evaluation_at=reference_now,
    )
    errors.extend(identity_errors)

    if payload.get("schema_version") != SCHEMA:
        errors.append(f"unsupported_schema:{payload.get('schema_version')}")
    if not symbol:
        errors.append("missing_symbol")
    for name in sorted(set(factors) - set(FACTOR_WEIGHTS)):
        errors.append(f"unexpected_factor:{name}")

    normalized: dict[str, dict[str, Any]] = {}
    for name, weight in FACTOR_WEIGHTS.items():
        row = factors.get(name)
        if not isinstance(row, dict):
            errors.append(f"missing_factor:{name}")
            continue
        score = _valid_score(row.get("score"))
        if score is None:
            errors.append(f"invalid_score:{name}")
        refs = row.get("evidence_refs")
        clean_refs = [str(ref).strip() for ref in refs if isinstance(ref, str) and str(ref).strip()] if isinstance(refs, list) else []
        if not clean_refs:
            errors.append(f"missing_evidence_refs:{name}")
        elif not _valid_factor_evidence_refs(clean_refs):
            errors.append(f"invalid_evidence_refs:{name}")
        reason = str(row.get("reason") or "").strip()
        if not reason:
            errors.append(f"missing_reason:{name}")
        observed_at = _parse_dt(row.get("observed_at"))
        stale_after = _parse_dt(row.get("stale_after"))
        if observed_at is None:
            errors.append(f"invalid_observed_at:{name}")
        elif observed_at > as_of or observed_at > reference_now:
            errors.append(f"future_observed_at:{name}")
        if stale_after is None:
            errors.append(f"invalid_stale_after:{name}")
        elif stale_after <= reference_now:
            errors.append(f"stale_factor:{name}")
        if score is not None and observed_at is not None and stale_after is not None:
            normalized[name] = {
                "score": score,
                "weight": weight,
                "weight_pct": int(round(weight * 100)),
                "weighted_contribution_0_5": round(score * weight, 4),
                "contribution_points_100": round(score * weight * 20, 2),
                "reason": reason,
                "evidence_refs": clean_refs,
                "observed_at": observed_at,
                "stale_after": stale_after,
            }

    if errors:
        result = _insufficient_result(
            symbol,
            errors,
            as_of,
            product_identity=product_identity,
        )
        result["factor_coverage"] = round(len(normalized) / len(FACTOR_WEIGHTS), 4)
        return result

    weighted_score = round(sum(row["score"] * row["weight"] for row in normalized.values()), 4)
    score_100 = round(weighted_score * 20, 2)
    base_action_range, _diagnostic_entry_ceiling, readiness_level = _band(weighted_score)
    entry_ceiling: str | None = None
    ordered = sorted(normalized.items(), key=lambda item: (item[1]["score"], item[0]))
    lowest_name, lowest = ordered[0]
    dispersion = round(max(row["score"] for row in normalized.values()) - min(row["score"] for row in normalized.values()), 4)
    unresolved_conflict = dispersion > 2.5
    all_refs = list(dict.fromkeys(ref for row in normalized.values() for ref in row["evidence_refs"]))
    observed_at = max(row["observed_at"] for row in normalized.values())
    stale_after = min(row["stale_after"] for row in normalized.values())

    if unresolved_conflict:
        entry_ceiling = "BLOCK"
        signal = {
            "module": "conflict_ledger",
            "sub_framework": "multi_factor_entry_score",
            "max_action_level": "L0",
            "entry_permission": "BLOCK",
            "holding_directive": "HOLD",
            "position_multiplier": 0.0,
            "hard_veto": True,
            "unresolved_conflict": True,
            "evidence_refs": all_refs,
            "observed_at": observed_at.isoformat(),
            "stale_after": stale_after.isoformat(),
            "reason": f"cross_factor_dispersion={dispersion}>2.5",
            "repair_signal": "resolve_cross_factor_conflict",
            "tighten_only": True,
            "cannot_raise_upstream": True,
            "no_order_execution": True,
        }
    else:
        signal = None

    signals = [signal] if signal is not None else []
    risk_adjustments: list[str] = []
    confidence = "low" if unresolved_conflict else "high"
    region_eligible = product_identity.get("region_eligible")
    identity_refs = list(product_identity.get("evidence_refs") or [])
    identity_observed_at = str(product_identity.get("observed_at") or as_of.isoformat())
    identity_stale_after = str(product_identity.get("stale_after") or as_of.isoformat())
    if region_eligible is None:
        entry_ceiling = "BLOCK"
        confidence = "low" if unresolved_conflict else "medium"
        risk_adjustments.append("region_eligibility_unknown")
        signals.append({
            "module": "data_quality",
            "sub_framework": "okx_region_eligibility",
            "max_action_level": "L0",
            "entry_permission": "BLOCK",
            "holding_directive": "HOLD",
            "position_multiplier": 0.0,
            "hard_veto": True,
            "unresolved_conflict": True,
            "evidence_refs": identity_refs,
            "observed_at": identity_observed_at,
            "stale_after": identity_stale_after,
            "reason": "region_eligibility_unknown",
            "repair_signal": "verify_account_and_region_eligibility",
            "tighten_only": True,
            "cannot_raise_upstream": True,
            "no_order_execution": True,
        })
    elif region_eligible is False:
        entry_ceiling = "BLOCK"
        confidence = "low"
        risk_adjustments.append("region_ineligible")
        signals.append({
            "module": "account",
            "sub_framework": "okx_region_eligibility",
            "max_action_level": "L0",
            "entry_permission": "BLOCK",
            "holding_directive": "HOLD",
            "position_multiplier": 0.0,
            "hard_veto": True,
            "evidence_refs": identity_refs,
            "observed_at": identity_observed_at,
            "stale_after": identity_stale_after,
            "reason": "region_or_account_ineligible",
            "repair_signal": "do_not_trade_product",
            "tighten_only": True,
            "cannot_raise_upstream": True,
            "no_order_execution": True,
        })

    factor_output = {
        name: {
            key: value
            for key, value in row.items()
            if key not in {"observed_at", "stale_after", "weight"}
        }
        | {"observed_at": row["observed_at"].isoformat(), "stale_after": row["stale_after"].isoformat()}
        for name, row in normalized.items()
    }
    return {
        "ok": True,
        "schema_version": "entry_score_compilation.v1",
        "score_version": SCORE_VERSION,
        "symbol": symbol,
        "product_identity": product_identity,
        "as_of": as_of.isoformat(),
        "status": "complete",
        "entry_score_100": score_100,
        "weighted_score_0_5": weighted_score,
        "factor_coverage": 1.0,
        "factor_weights": dict(FACTOR_WEIGHTS),
        "factors": factor_output,
        "base_action_range": base_action_range,
        "entry_permission_ceiling": entry_ceiling,
        "lowest_factor": {"name": lowest_name, "score": lowest["score"], "reason": lowest["reason"]},
        "cross_factor_dispersion": dispersion,
        "unresolved_conflict": unresolved_conflict,
        "confidence": confidence,
        "risk_adjustments": risk_adjustments,
        "validation_errors": [],
        "readiness_level": readiness_level,
        "suggested_module_signal": signals[-1] if signals else None,
        "suggested_module_signals": signals,
        "calculation_formula": "sum(factor_score_0_5 * canonical_weight) * 20",
        "score_is_not_final_action": True,
        "compiler_effect": "tighten_only" if signals else "none",
        "cannot_authorize_action": True,
        "no_order_execution": True,
    }


def _load_payload(path: str | None) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile a five-factor entry score without executing orders")
    parser.add_argument("path", nargs="?", help="Input JSON file; stdin when omitted")
    args = parser.parse_args(argv)
    try:
        result = compile_entry_score(_load_payload(args.path))
    except (OSError, OverflowError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "no_order_execution": True}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
