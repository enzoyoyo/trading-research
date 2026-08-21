#!/usr/bin/env python3
"""Publish a local, read-only OKX supervision dashboard.

The dashboard consumes only ``okx_execution_supervision.v1`` output, fetches a
relative JSON file every 30 seconds, and contains no trading controls, remote
URLs, authentication, or process-management behavior.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "okx_execution_supervision.v1"
REFRESH_SECONDS = 30
SENSITIVE_KEYS = {
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
FACTOR_WEIGHTS: dict[str, float] = {
    "technical": 0.20,
    "capital_flow": 0.20,
    "sentiment": 0.20,
    "fundamentals": 0.25,
    "macro": 0.15,
}
FACTOR_NAMES = set(FACTOR_WEIGHTS)
LAST_GOOD_FIELDS = {
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
SENSITIVE_VALUE_MARKERS = (
    "secret", "passphrase", "password", "api_key", "apikey",
    "private_key", "authorization", "bearer ", "-----begin private key-----",
)


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
    return any(_normalized_key(marker) in normalized for marker in SENSITIVE_KEYS)


def _reject_sensitive_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_sensitive_key(key):
                raise ValueError(f"sensitive field rejected at {path}.{key}")
            _reject_sensitive_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_fields(child, f"{path}[{index}]")
    elif isinstance(value, str):
        _reject_sensitive_value(value, path)


def _reject_sensitive_value(value: str, path: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    lowered = normalized.lower()
    if any(marker in lowered for marker in SENSITIVE_VALUE_MARKERS):
        raise ValueError(f"sensitive value rejected at {path}")
    if re.search(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", normalized, flags=re.IGNORECASE):
        raise ValueError(f"sensitive value rejected at {path}")
    if re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", normalized):
        raise ValueError(f"sensitive value rejected at {path}")
    if re.search(r"(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}", normalized, flags=re.IGNORECASE):
        raise ValueError(f"sensitive value rejected at {path}")
    if re.search(r"AKIA[0-9A-Z]{16}", normalized):
        raise ValueError(f"sensitive value rejected at {path}")
    return value


def _finite_number(value: Any, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a finite number")
    try:
        finite = math.isfinite(float(value))
    except OverflowError as exc:
        raise ValueError(f"{path} must be a finite number") from exc
    if not finite:
        raise ValueError(f"{path} must be a finite number")
    return value


def _iso_text(value: Any, path: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{path} must be RFC3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{path} must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _enum(value: Any, allowed: set[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{path} is invalid")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be boolean")
    return value


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value):
        raise ValueError(f"{path} is invalid")
    return _reject_sensitive_value(value, path)


def _project_last_good_value(feed_name: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"freshness.{feed_name}.last_good_value must be an object")
    projected: dict[str, Any] = {}
    for key in sorted(LAST_GOOD_FIELDS[feed_name]):
        if key not in value:
            continue
        item = value[key]
        path = f"freshness.{feed_name}.last_good_value.{key}"
        if key in LAST_GOOD_NUMERIC_FIELDS:
            projected[key] = _finite_number(item, path)
        elif key in LAST_GOOD_INTEGER_FIELDS:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"{path} must be a non-negative integer")
            projected[key] = item
        elif key in LAST_GOOD_TIMESTAMP_FIELDS:
            projected[key] = _iso_text(item, path, required=True)
        elif key == "currency":
            if not isinstance(item, str):
                raise ValueError(f"{path} is invalid")
            currency = item.strip().upper()
            if not (2 <= len(currency) <= 12 and currency.isascii() and currency.isalnum()):
                raise ValueError(f"{path} is invalid")
            projected[key] = _reject_sensitive_value(currency, path)
    return projected


def _project_instrument(channel: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("instrument is required")
    identity_label = "cex" if channel == "cex_spot" else "wallet"
    if value.get("mapping_verified") is not True or str(value.get("state") or "") != "live":
        raise ValueError(f"{identity_label} instrument identity is invalid")
    region = value.get("region_eligible")
    if region is not None and not isinstance(region, bool):
        raise ValueError(f"{identity_label} instrument identity is invalid")

    if channel == "cex_spot":
        if any(value.get(key) not in (None, "") for key in (
            "underlying_symbol", "chain_id", "token_contract", "provider",
        )):
            raise ValueError("cex instrument identity contains wallet fields")
        if (
            str(value.get("inst_type") or "") != "SPOT"
            or str(value.get("inst_category") or "") != "3"
            or str(value.get("mapping_scope") or "") != "exact_exchange_instrument_only"
        ):
            raise ValueError("cex instrument identity is invalid")
        projected = {
            "inst_id": _identifier(value.get("inst_id"), "instrument.inst_id"),
            "inst_type": "SPOT",
            "inst_category": "3",
            "mapping_scope": "exact_exchange_instrument_only",
            "mapping_verified": True,
            "region_eligible": region,
            "state": "live",
        }
        for key in ("tick_size", "lot_size", "min_size"):
            number = _finite_number(value.get(key), f"instrument.{key}")
            if number <= 0:
                raise ValueError("cex instrument identity is invalid")
            projected[key] = number
        return projected

    if any(value.get(key) not in (None, "") for key in (
        "inst_id", "inst_type", "inst_category", "tick_size", "lot_size", "min_size",
    )):
        raise ValueError("wallet instrument identity contains cex fields")
    if (
        str(value.get("mapping_scope") or "") != "exact_chain_token"
        or any(not str(value.get(key) or "").strip() for key in (
            "underlying_symbol", "chain_id", "token_contract", "provider",
        ))
    ):
        raise ValueError("wallet instrument identity is invalid")
    return {
        "underlying_symbol": _identifier(value.get("underlying_symbol"), "instrument.underlying_symbol"),
        "chain_id": _identifier(value.get("chain_id"), "instrument.chain_id"),
        "token_contract": _identifier(value.get("token_contract"), "instrument.token_contract"),
        "provider": _identifier(value.get("provider"), "instrument.provider"),
        "mapping_scope": "exact_chain_token",
        "mapping_verified": True,
        "region_eligible": region,
        "state": "live",
    }


def _project_entry_score(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("entry_score is required")
    status = _enum(
        value.get("status"),
        {"complete", "insufficient_data", "unavailable"},
        "entry_score.status",
    )
    conflict = _boolean(value.get("unresolved_conflict"), "entry_score.unresolved_conflict")
    confidence = _enum(value.get("confidence"), {"high", "medium", "low"}, "entry_score.confidence")
    raw_ceiling = value.get("entry_permission_ceiling")
    if raw_ceiling is not None and raw_ceiling != "BLOCK":
        raise ValueError("entry_score.entry_permission_ceiling is invalid")
    raw_score = value.get("entry_score_100")
    score = _finite_number(raw_score, "entry_score.entry_score_100") if raw_score is not None else None
    coverage = _finite_number(value.get("factor_coverage"), "entry_score.factor_coverage")
    if not 0 <= coverage <= 1 or (score is not None and not 0 <= score <= 100):
        raise ValueError("entry_score numeric range is invalid")

    raw_factors = value.get("factors")
    if not isinstance(raw_factors, dict):
        raise ValueError("entry_score.factors is required")
    factors: dict[str, dict[str, int | float]] = {}
    for name, weight in FACTOR_WEIGHTS.items():
        row = raw_factors.get(name)
        if row is None:
            continue
        if not isinstance(row, dict):
            raise ValueError(f"entry_score.factors.{name} must be an object")
        factor_score = _finite_number(row.get("score"), f"entry_score.factors.{name}.score")
        contribution = _finite_number(
            row.get("contribution_points_100"),
            f"entry_score.factors.{name}.contribution_points_100",
        )
        expected = round(factor_score * weight * 20, 2)
        if not 0 <= factor_score <= 5 or not math.isclose(float(contribution), expected, abs_tol=1e-9):
            raise ValueError(f"entry_score.factors.{name} formula is invalid")
        factors[name] = {
            "score": factor_score,
            "contribution_points_100": contribution,
        }

    if status == "complete":
        if (
            score is None
            or not math.isclose(float(coverage), 1.0, abs_tol=1e-9)
            or set(factors) != FACTOR_NAMES
            or not math.isclose(
                sum(float(row["contribution_points_100"]) for row in factors.values()),
                float(score),
                abs_tol=1e-9,
            )
        ):
            raise ValueError("entry_score complete projection is invalid")
    elif (
        score is not None
        or factors
        or conflict
        or (status == "insufficient_data" and raw_ceiling != "BLOCK")
        or (status == "unavailable" and (raw_ceiling is not None or coverage != 0))
    ):
        raise ValueError(f"entry_score {status} projection is invalid")

    return {
        "status": status,
        "entry_score_100": score,
        "factor_coverage": coverage,
        "confidence": confidence,
        "unresolved_conflict": conflict,
        "entry_permission_ceiling": raw_ceiling,
        "factors": factors,
    }


def _validate_permission_matrix(snapshot: dict[str, Any]) -> None:
    mode = snapshot["mode"]
    expected = {
        "public": ("none", "disabled"),
        "read_only": ("read_only", "disabled"),
        "demo": ("read_only", "paper"),
        "live": ("read_only", "live_approved"),
    }
    expected_scope, expected_permission = expected[mode]
    if snapshot["credential_scope"] != expected_scope or snapshot["execution_permission"] != expected_permission:
        raise ValueError("permission matrix invalid for mode/scope/execution_permission")
    if snapshot["analysis_only"] is not (mode in {"public", "read_only"}):
        raise ValueError("permission matrix invalid for analysis_only")

    connection = snapshot["connection"]
    strategy = snapshot["strategy"]
    if mode == "public":
        if (
            connection["private_ws_connected"] is not None
            or connection["private_transport"] is not None
            or connection["rest_polling_healthy"] is not None
            or connection["poll_interval_seconds"] is not None
            or connection["rest_baseline_complete"] is not False
        ):
            raise ValueError("permission matrix invalid for public private state")
        if strategy["status"] != "not_available":
            raise ValueError("permission matrix invalid for public strategy state")
    elif strategy["status"] == "not_available":
        raise ValueError("permission matrix invalid for authenticated strategy state")

    blockers = snapshot["blockers"]
    if mode in {"demo", "live"} and bool(blockers) != snapshot["pause_required"]:
        raise ValueError("permission matrix invalid for blockers/pause_required")
    if mode in {"public", "read_only"} and snapshot["pause_required"]:
        raise ValueError("permission matrix invalid for analysis-only pause_required")
    if snapshot["status"] == "healthy" and (blockers or snapshot["pause_required"]):
        raise ValueError("permission matrix invalid for healthy status")
    if snapshot["status"] == "blocked" and not blockers:
        raise ValueError("permission matrix invalid for blocked status")

    if snapshot["new_entries_allowed"]:
        freshness = snapshot["freshness"]
        feeds_ready = all(
            isinstance(freshness.get(name), dict)
            and freshness[name].get("status") == "live"
            and freshness[name].get("stale") is False
            for name in ("market", "account", "orders")
        )
        orders_ready = isinstance(freshness.get("orders"), dict) and freshness["orders"].get("rest_baseline_complete") is True
        entry_score = snapshot["entry_score"]
        entry_score_ready = (
            entry_score.get("status") == "complete"
            and entry_score.get("confidence") == "high"
            and entry_score.get("factor_coverage") == 1.0
            and set(entry_score.get("factors") or {}) == FACTOR_NAMES
            and entry_score.get("unresolved_conflict") is False
            and entry_score.get("entry_permission_ceiling") is None
        )
        instrument = snapshot["instrument"]
        identity_ready = (
            instrument.get("mapping_verified") is True
            and instrument.get("region_eligible") is True
            and instrument.get("state") == "live"
        )
        demo_rest_polling_ready = (
            mode == "demo"
            and connection["private_transport"] == "rest_polling"
            and connection["rest_polling_healthy"] is True
            and isinstance(connection["poll_interval_seconds"], (int, float))
            and 0 < connection["poll_interval_seconds"] <= 30
        )
        private_connection_ready = connection["private_ws_connected"] is True or demo_rest_polling_ready
        if (
            mode not in {"demo", "live"}
            or snapshot["status"] != "healthy"
            or snapshot["pause_required"]
            or snapshot["pause_effective"]
            or snapshot["market_analysis_allowed"] is not True
            or not private_connection_ready
            or connection["rest_baseline_complete"] is not True
            or strategy["status"] != "running"
            or snapshot["position_reconciliation_status"] != "matched"
            or snapshot["order_state_status"] != "known"
            or not feeds_ready
            or not orders_ready
            or not entry_score_ready
            or not identity_ready
        ):
            raise ValueError("permission matrix invalid for new_entries_allowed")


def _project_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "as_of": _iso_text(snapshot.get("as_of"), "as_of", required=True),
        "status": _enum(snapshot.get("status"), {"healthy", "blocked"}, "status"),
        "venue": _enum(snapshot.get("venue"), {"okx"}, "venue"),
        "channel": _enum(snapshot.get("channel"), {"cex_spot", "wallet_dex"}, "channel"),
        "mode": _enum(snapshot.get("mode"), {"public", "read_only", "demo", "live"}, "mode"),
        "credential_scope": _enum(snapshot.get("credential_scope"), {"none", "read_only"}, "credential_scope"),
        "execution_permission": _enum(snapshot.get("execution_permission"), {"disabled", "paper", "live_approved"}, "execution_permission"),
        "pause_required": _boolean(snapshot.get("pause_required"), "pause_required"),
        "pause_effective": _boolean(snapshot.get("pause_effective"), "pause_effective"),
        "new_entries_allowed": _boolean(snapshot.get("new_entries_allowed"), "new_entries_allowed"),
        "analysis_only": _boolean(snapshot.get("analysis_only"), "analysis_only"),
        "market_analysis_allowed": _boolean(snapshot.get("market_analysis_allowed"), "market_analysis_allowed"),
        "position_reconciliation_status": _enum(snapshot.get("position_reconciliation_status"), {"matched", "drift", "pending"}, "position_reconciliation_status"),
        "order_state_status": _enum(snapshot.get("order_state_status"), {"known", "unknown", "partial_failure"}, "order_state_status"),
        "no_order_execution": True,
    }

    projected["instrument"] = _project_instrument(projected["channel"], snapshot.get("instrument"))

    raw_connection = snapshot.get("connection")
    if not isinstance(raw_connection, dict):
        raise ValueError("connection is required")
    private_ws_connected = raw_connection.get("private_ws_connected")
    if private_ws_connected is None and projected["mode"] == "public":
        projected_private_ws_connected = None
    else:
        projected_private_ws_connected = _boolean(private_ws_connected, "connection.private_ws_connected")
    projected["connection"] = {
        "private_ws_connected": projected_private_ws_connected,
        "private_transport": (
            _enum(raw_connection.get("private_transport"), {"private_ws", "rest_polling"}, "connection.private_transport")
            if raw_connection.get("private_transport") is not None else None
        ),
        "rest_polling_healthy": (
            _boolean(raw_connection.get("rest_polling_healthy"), "connection.rest_polling_healthy")
            if raw_connection.get("rest_polling_healthy") is not None else None
        ),
        "poll_interval_seconds": (
            _finite_number(raw_connection.get("poll_interval_seconds"), "connection.poll_interval_seconds")
            if raw_connection.get("poll_interval_seconds") is not None else None
        ),
        "rest_baseline_complete": _boolean(raw_connection.get("rest_baseline_complete"), "connection.rest_baseline_complete"),
    }

    raw_freshness = snapshot.get("freshness")
    if not isinstance(raw_freshness, dict):
        raise ValueError("freshness is required")
    freshness: dict[str, Any] = {}
    for feed_name in ("market", "account", "orders"):
        row = raw_freshness.get(feed_name)
        if row is None:
            continue
        if not isinstance(row, dict):
            raise ValueError(f"freshness.{feed_name} must be an object")
        status = _enum(row.get("status"), {"live", "stale", "error", "unavailable", "missing"}, f"freshness.{feed_name}.status")
        projected_row: dict[str, Any] = {
            "status": status,
            "stale": _boolean(row["stale"], f"freshness.{feed_name}.stale") if "stale" in row else status != "live",
            "last_good_value": _project_last_good_value(feed_name, row.get("last_good_value")),
        }
        for key in ("source_ts", "last_good_at"):
            if key in row:
                projected_row[key] = _iso_text(row[key], f"freshness.{feed_name}.{key}")
        for key in ("age_seconds", "max_age_seconds"):
            if key in row and row[key] is not None:
                projected_row[key] = _finite_number(row[key], f"freshness.{feed_name}.{key}")
        if "rest_baseline_complete" in row and row["rest_baseline_complete"] is not None:
            projected_row["rest_baseline_complete"] = _boolean(row["rest_baseline_complete"], f"freshness.{feed_name}.rest_baseline_complete")
        freshness[feed_name] = projected_row
    projected["freshness"] = freshness

    raw_strategy = snapshot.get("strategy")
    if not isinstance(raw_strategy, dict):
        raise ValueError("strategy is required")
    strategy_projection: dict[str, Any] = {
        "status": _enum(
            raw_strategy.get("status"),
            {"running", "paused", "stopped", "faulted", "missing", "not_available"},
            "strategy.status",
        ),
    }
    if "stop_new_entries" in raw_strategy:
        strategy_projection["stop_new_entries"] = _boolean(raw_strategy["stop_new_entries"], "strategy.stop_new_entries")
    projected["strategy"] = strategy_projection

    projected["entry_score"] = _project_entry_score(snapshot.get("entry_score"))

    raw_blockers = snapshot.get("blockers")
    if not isinstance(raw_blockers, list):
        raise ValueError("blockers must be a list")
    blockers: list[str] = []
    for index, value in enumerate(raw_blockers):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", value):
            raise ValueError(f"blockers[{index}] is invalid")
        blockers.append(_reject_sensitive_value(value, f"blockers[{index}]"))
    projected["blockers"] = blockers
    raw_live_controls = snapshot.get("live_controls")
    if projected["mode"] == "live":
        if not isinstance(raw_live_controls, dict):
            raise ValueError("permission matrix invalid for live_controls")
        if projected["new_entries_allowed"] and (
            raw_live_controls.get("approval_verified") is not True
            or raw_live_controls.get("ip_allowlist_verified") is not True
            or raw_live_controls.get("withdrawal_enabled") is not False
            or raw_live_controls.get("credentials_rotated") is not True
        ):
            raise ValueError("permission matrix invalid for live_controls")
    elif raw_live_controls is not None:
        raise ValueError("permission matrix invalid for non-live live_controls")
    _validate_permission_matrix(projected)
    return projected


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if snapshot.get("no_order_execution") is not True:
        raise ValueError("no_order_execution must be true")
    _reject_sensitive_fields(snapshot)
    required = {
        "pause_required": bool,
        "pause_effective": bool,
        "new_entries_allowed": bool,
        "analysis_only": bool,
        "market_analysis_allowed": bool,
        "position_reconciliation_status": str,
        "order_state_status": str,
        "connection": dict,
        "entry_score": dict,
    }
    for field, expected_type in required.items():
        if not isinstance(snapshot.get(field), expected_type):
            raise ValueError(f"{field} is required by {SCHEMA_VERSION}")
    if snapshot["position_reconciliation_status"] not in {"matched", "drift", "pending"}:
        raise ValueError("position_reconciliation_status is invalid")
    if snapshot["order_state_status"] not in {"known", "unknown", "partial_failure"}:
        raise ValueError("order_state_status is invalid")
    for legacy_field in ("pause_new_entries_required", "order_state"):
        if legacy_field in snapshot:
            raise ValueError(f"legacy field is forbidden: {legacy_field}")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>OKX 只读监督</title>
  <style>
    :root {
      color-scheme: light;
      --paper: #faf8f3;
      --ink: #1a1a1a;
      --body: #2d2d2d;
      --muted: #6b6258;
      --quiet: #a09888;
      --rule: #d6d2c8;
      --highlight: #f5ead4;
      --warning: #96630e;
      --danger: #8a3026;
    }
    * { box-sizing: border-box; }
    html { background: var(--paper); }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--paper);
      color: var(--body);
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", sans-serif;
      line-height: 1.55;
    }
    main { width: min(1080px, calc(100% - 48px)); margin: 0 auto; padding: 56px 0 72px; }
    header { display: grid; grid-template-columns: 1fr auto; gap: 24px; align-items: end; border-bottom: 1px solid var(--ink); padding-bottom: 22px; }
    .eyebrow { margin: 0 0 8px; color: var(--muted); font-size: 12px; letter-spacing: .16em; text-transform: uppercase; }
    h1, h2 { font-family: Georgia, "Songti SC", "STSong", serif; color: var(--ink); font-weight: 500; }
    h1 { margin: 0; font-size: clamp(34px, 5vw, 58px); line-height: 1.05; }
    h2 { margin: 0 0 18px; font-size: 25px; }
    .timestamp { text-align: right; color: var(--muted); font-size: 13px; }
    .lede { margin: 28px 0 0; max-width: 760px; font-family: Georgia, "Songti SC", "STSong", serif; font-size: clamp(20px, 3vw, 30px); color: var(--ink); }
    section { margin-top: 54px; padding-top: 22px; border-top: 1px solid var(--rule); }
    .facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 24px 32px; }
    .fact dt { margin: 0 0 6px; color: var(--muted); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
    .fact dd { margin: 0; color: var(--ink); font-family: Georgia, "Songti SC", serif; font-size: 23px; overflow-wrap: anywhere; }
    .status-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .status-table th, .status-table td { padding: 14px 10px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--rule); }
    .status-table th { padding-top: 0; color: var(--muted); font-size: 12px; font-weight: 500; letter-spacing: .06em; }
    .status-table th:first-child, .status-table td:first-child { padding-left: 0; }
    .status-table th:last-child, .status-table td:last-child { padding-right: 0; }
    .state { font-weight: 650; color: var(--ink); }
    .state[data-level="blocked"], .state[data-level="stale"], .state[data-level="unknown"], .state[data-level="mismatch"], .state[data-level="drift"], .state[data-level="pending"], .state[data-level="partial_failure"] { color: var(--danger); }
    .state[data-level="warning"] { color: var(--warning); }
    .last-good { color: var(--muted); font-variant-numeric: tabular-nums; }
    .blockers { margin: 0; padding: 0; list-style: none; }
    .blockers li { padding: 10px 0; border-bottom: 1px solid var(--rule); overflow-wrap: anywhere; }
    .blockers li::before { content: "■"; margin-right: 10px; color: var(--warning); font-size: 8px; vertical-align: 2px; }
    .boundary { margin-top: 54px; padding: 18px 0; border-top: 1px solid var(--ink); border-bottom: 1px solid var(--ink); color: var(--muted); font-size: 13px; }
    .error { color: var(--danger); }
    @media (max-width: 720px) {
      main { width: min(100% - 28px, 680px); padding-top: 32px; }
      header { grid-template-columns: 1fr; align-items: start; }
      .timestamp { text-align: left; }
      .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .status-table, .status-table tbody, .status-table tr, .status-table td { display: block; width: 100%; }
      .status-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
      .status-table tr { padding: 14px 0; border-bottom: 1px solid var(--rule); }
      .status-table td { padding: 4px 0; border: 0; }
      .status-table td::before { content: attr(data-label); display: inline-block; min-width: 104px; margin-right: 8px; color: var(--muted); font-size: 12px; }
    }
  </style>
</head>
<body data-refresh-ms="30000">
  <main>
    <header>
      <div>
        <p class="eyebrow">只读执行监督</p>
        <h1>OKX 只读监督</h1>
      </div>
      <div class="timestamp"><span id="as-of">等待快照</span><br>每 30 秒刷新 · America/New_York</div>
    </header>
    <p class="lede" id="summary">正在读取本地监督快照。页面不认证、不下单、不修改策略。</p>

    <section aria-labelledby="identity-title">
      <h2 id="identity-title">运行身份</h2>
      <dl class="facts">
        <div class="fact"><dt>交易场所</dt><dd id="venue">—</dd></div>
        <div class="fact"><dt>产品通道</dt><dd id="channel">—</dd></div>
        <div class="fact"><dt>运行模式</dt><dd id="mode">—</dd></div>
        <div class="fact"><dt>交易标的</dt><dd id="instrument">—</dd></div>
      </dl>
    </section>

    <section aria-labelledby="score-title">
      <h2 id="score-title">入场评分（解释性）</h2>
      <dl class="facts">
        <div class="fact"><dt>总分</dt><dd id="entry-score">—</dd></div>
        <div class="fact"><dt>证据覆盖</dt><dd id="factor-coverage">—</dd></div>
        <div class="fact"><dt>置信度</dt><dd id="score-confidence">—</dd></div>
        <div class="fact"><dt>因子冲突</dt><dd id="score-conflict">—</dd></div>
      </dl>
      <table class="status-table">
        <thead><tr><th>因子</th><th>评分</th><th>贡献分</th><th>用途</th></tr></thead>
        <tbody id="factor-body"></tbody>
      </table>
    </section>

    <section aria-labelledby="state-title">
      <h2 id="state-title">状态与对账</h2>
      <table class="status-table">
        <thead><tr><th>对象</th><th>状态</th><th>最后有效值</th><th>影响</th></tr></thead>
        <tbody id="status-body"></tbody>
      </table>
    </section>

    <section aria-labelledby="blocker-title">
      <h2 id="blocker-title">阻断原因</h2>
      <ul class="blockers" id="blockers"><li>等待本地快照</li></ul>
    </section>

    <p class="boundary" id="boundary">no_order_execution=true · pause_required 不等于 pause_effective · stale 时保留 last-good，不写 0。</p>
  </main>
  <script>
    "use strict";
    const REFRESH_MS = Number(document.body.dataset.refreshMs);
    const SNAPSHOT_URL = "supervision.json";
    const text = (id, value) => { document.getElementById(id).textContent = value ?? "—"; };
    const statusLevel = value => String(value ?? "unknown").toLowerCase();
    const STATE_LABELS = {
      live: "正常", running: "运行中", stopped: "已停止", paused: "已暂停",
      stale: "已过期", unknown: "未知", known: "已知", matched: "一致",
      drift: "漂移", pending: "待对账", partial_failure: "部分失败",
      unavailable: "不可用", complete: "完整", insufficient_data: "数据不足",
      high: "高", medium: "中", low: "低",
    };
    const CHANNEL_LABELS = {cex_spot: "交易所现货", wallet_dex: "链上钱包 / DEX"};
    const MODE_LABELS = {public: "公开数据", read_only: "账户只读", demo: "模拟盘", live: "实盘监督"};
    const FEED_LABELS = {market: "市场行情", account: "账户", orders: "订单"};
    const FACTOR_LABELS = {technical: "技术面", capital_flow: "资金流", sentiment: "情绪", fundamentals: "基本面", macro: "宏观"};
    const stateText = value => STATE_LABELS[statusLevel(value)] || String(value ?? "未知");
    const etTime = value => {
      if (!value) return "时间未知";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "时间无效";
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      }).format(date) + " ET";
    };
    const compact = value => {
      if (value === null || value === undefined) return "—";
      if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key}=${item}`).join(" · ");
      return String(value);
    };
    const row = (label, state, lastGood, impact, labels = ["对象", "状态", "最后有效值", "影响"]) => {
      const tr = document.createElement("tr");
      [label, state, lastGood, impact].forEach((value, index) => {
        const td = document.createElement("td");
        td.dataset.label = labels[index];
        td.textContent = index === 1 ? stateText(value) : compact(value);
        if (index === 1) { td.className = "state"; td.dataset.level = statusLevel(value); }
        if (index === 2) td.className = "last-good";
        tr.appendChild(td);
      });
      return tr;
    };
    function render(snapshot) {
      text("as-of", etTime(snapshot.as_of));
      text("venue", snapshot.venue === "okx" ? "OKX" : snapshot.venue);
      text("channel", CHANNEL_LABELS[snapshot.channel] || snapshot.channel);
      text("mode", MODE_LABELS[snapshot.mode] || snapshot.mode);
      text("instrument", snapshot.instrument?.inst_id);
      const blocked = snapshot.new_entries_allowed !== true;
      text("summary", blocked
        ? "新仓已阻断。监督层只报告状态，未声称暂停已经生效。"
        : "监督状态健康；仍须由 Decision Compiler 和独立执行门决定动作。");

      const score = snapshot.entry_score || {};
      text("entry-score", score.entry_score_100 ?? "数据不足");
      text("factor-coverage", Number.isFinite(score.factor_coverage) ? `${Math.round(score.factor_coverage * 100)}%` : "未知");
      text("score-confidence", stateText(score.confidence));
      text("score-conflict", score.unresolved_conflict === true ? "有冲突" : "无冲突");
      const factorBody = document.getElementById("factor-body");
      factorBody.replaceChildren();
      for (const [name, factor] of Object.entries(score.factors || {})) {
        factorBody.appendChild(row(
          FACTOR_LABELS[name] || name,
          factor.score,
          factor.contribution_points_100,
          "仅作解释，不授权动作",
          ["因子", "评分", "贡献分", "用途"],
        ));
      }

      const body = document.getElementById("status-body");
      body.replaceChildren();
      const freshness = snapshot.freshness || {};
      for (const [name, feed] of Object.entries(freshness)) {
        body.appendChild(row(FEED_LABELS[name] || name, feed.stale ? "stale" : (feed.status || "unknown"), feed.last_good_value, feed.stale ? "阻断新仓" : "可继续监督"));
      }
      body.appendChild(row("策略", snapshot.strategy?.status, `pause_effective=${snapshot.pause_effective === true}`, snapshot.pause_required ? "需要暂停新仓" : "无暂停请求"));
      const connection = snapshot.connection || {};
      const demoRestPolling = snapshot.mode === "demo"
        && connection.private_transport === "rest_polling"
        && connection.rest_polling_healthy === true
        && Number.isFinite(connection.poll_interval_seconds)
        && connection.poll_interval_seconds > 0
        && connection.poll_interval_seconds <= 30;
      const privateStateReady = connection.private_ws_connected === true || demoRestPolling;
      body.appendChild(row(
        demoRestPolling ? "私有状态通道（REST 轮询）" : "私有状态通道（长连接）",
        privateStateReady ? "live" : "unknown",
        demoRestPolling ? `每 ${connection.poll_interval_seconds} 秒` : "—",
        privateStateReady ? (demoRestPolling ? "Demo 监督可用；Live 不适用" : "连接正常") : "阻断新仓",
      ));
      body.appendChild(row("全量对账基线", snapshot.connection?.rest_baseline_complete === true ? "known" : "unknown", "—", snapshot.connection?.rest_baseline_complete === true ? "已完成" : "等待全量对账"));
      body.appendChild(row("订单状态", snapshot.order_state_status, "—", snapshot.order_state_status === "known" ? "状态已知" : "等待全量对账"));
      body.appendChild(row("持仓对账", snapshot.position_reconciliation_status, "—", snapshot.position_reconciliation_status === "matched" ? "账实一致" : "阻断新仓"));

      const list = document.getElementById("blockers");
      list.replaceChildren();
      const blockers = Array.isArray(snapshot.blockers) ? snapshot.blockers : [];
      for (const value of blockers.length ? blockers : ["无阻断项"]) {
        const li = document.createElement("li");
        li.textContent = String(value);
        list.appendChild(li);
      }
      text("boundary", `no_order_execution=${snapshot.no_order_execution === true} · pause_required=${snapshot.pause_required === true} · pause_effective=${snapshot.pause_effective === true} · 本页无交易控制。`);
    }
    async function loadSnapshot() {
      try {
        const response = await fetch(SNAPSHOT_URL, {cache: "no-store", credentials: "omit"});
        if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
        render(await response.json());
      } catch (error) {
        const summary = document.getElementById("summary");
        summary.className = "lede error";
        summary.textContent = `本地快照读取失败：${error.message}。现有页面值不清零，新仓状态按阻断处理。`;
      }
    }
    loadSnapshot();
    setInterval(loadSnapshot, REFRESH_MS);
  </script>
</body>
</html>
"""


def publish_dashboard(snapshot: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    _validate_snapshot(snapshot)
    projected_snapshot = _project_snapshot(snapshot)
    output_dir = output_dir.expanduser().resolve()
    _atomic_write(output_dir / "supervision.json", json.dumps(projected_snapshot, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(output_dir / "index.html", render_dashboard_html())
    return {
        "ok": True,
        "schema_version": "okx_monitor_dashboard_publish.v1",
        "output_dir": str(output_dir),
        "html": str(output_dir / "index.html"),
        "snapshot": str(output_dir / "supervision.json"),
        "refresh_seconds": REFRESH_SECONDS,
        "bind_policy": "loopback_only_if_served",
        "no_order_execution": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a local read-only OKX supervision dashboard")
    parser.add_argument("--snapshot", required=True, help="Path to okx_execution_supervision.v1 JSON")
    parser.add_argument("--output-dir", required=True, help="Directory for index.html and supervision.json")
    args = parser.parse_args()
    try:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot root must be an object")
        result = publish_dashboard(snapshot, Path(args.output_dir))
    except (OSError, OverflowError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "no_order_execution": True}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
