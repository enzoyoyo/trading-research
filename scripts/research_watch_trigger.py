#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INPUT_SCHEMA = "research_watch_trigger.v1"
OUTPUT_SCHEMA = "research_watch_trigger_compilation.v1"
ALLOWED_ACTIONS = {"rerun_research", "refresh_source", "request_manual_review"}
TRIGGER_CONDITIONS = {
    "price_cross": {"above", "below", "cross_up", "cross_down"},
    "filing_event": {"occurs"},
    "event_window": {"occurs"},
    "source_stale": {"becomes_stale"},
    "data_gap_recovered": {"recovers"},
    "conflict_resolved": {"recovers"},
}
REARM_RULES = {"manual", "recross", "after_cooldown", "never"}
ALLOWED_FIELDS = {
    "schema_version", "trigger_id", "symbol", "trigger_type", "condition", "threshold",
    "reference_value", "created_at", "expires_at", "cooldown_seconds", "rearm_rule",
    "one_shot", "evidence_refs", "on_trigger", "no_order_execution",
}
REQUIRED_FIELDS = ALLOWED_FIELDS - {"threshold", "reference_value"}


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def compile_trigger(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != INPUT_SCHEMA:
        raise ValueError(f"schema_version must be {INPUT_SCHEMA}")
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        raise ValueError(f"missing required field: {missing[0]}")
    unexpected = sorted(set(payload) - ALLOWED_FIELDS)
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    trigger_id = require_string(payload.get("trigger_id"), "trigger_id")
    symbol = require_string(payload.get("symbol"), "symbol").upper()
    if payload.get("on_trigger") not in ALLOWED_ACTIONS:
        raise ValueError("on_trigger must be rerun_research, refresh_source, or request_manual_review")
    trigger_type = payload.get("trigger_type")
    if trigger_type not in TRIGGER_CONDITIONS:
        raise ValueError("trigger_type is invalid")
    if payload.get("condition") not in TRIGGER_CONDITIONS[trigger_type]:
        raise ValueError("condition is invalid for trigger_type")
    if payload.get("rearm_rule") not in REARM_RULES:
        raise ValueError("rearm_rule is invalid")
    cooldown = payload.get("cooldown_seconds")
    if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
        raise ValueError("cooldown_seconds must be a non-negative integer")
    if not isinstance(payload.get("one_shot"), bool):
        raise ValueError("one_shot must be boolean")
    if trigger_type == "price_cross":
        for field in ("threshold", "reference_value"):
            value = payload.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{field} must be numeric for price_cross")
    if payload.get("no_order_execution") is not True:
        raise ValueError("no_order_execution must be true")
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs or not all(isinstance(ref, str) and ref.strip() for ref in evidence_refs):
        raise ValueError("evidence_refs must contain only non-empty string references")
    normalized_refs = [ref.strip() for ref in evidence_refs]
    created_at = parse_time(payload["created_at"], "created_at")
    expires_at = parse_time(payload["expires_at"], "expires_at")
    if expires_at <= created_at:
        raise ValueError("expires_at must be after created_at")
    normalized = dict(payload)
    normalized.update({"trigger_id": trigger_id, "symbol": symbol, "evidence_refs": normalized_refs})
    return {
        "ok": True,
        "schema_version": OUTPUT_SCHEMA,
        "trigger": normalized,
        "requires_fresh_facts": True,
        "requires_decision_recompile": True,
        "no_external_side_effects": True,
        "no_order_execution": True,
    }


def read_payload(path: str | None) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and compile a side-effect-free research watch trigger")
    parser.add_argument("--input", help="JSON input path; stdin when omitted")
    args = parser.parse_args()
    try:
        output = compile_trigger(read_payload(args.input))
        code = 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "schema_version": OUTPUT_SCHEMA, "error": str(exc), "no_order_execution": True}
        code = 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
