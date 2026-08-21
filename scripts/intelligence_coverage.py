#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "intelligence_coverage.v1"
CAPABILITY_STATES = {
    "live",
    "delayed",
    "cached",
    "unsupported",
    "auth_missing",
    "rate_limited",
    "error",
    "unknown",
}
USABLE_STATES = {"live", "delayed", "cached"}
CRITICALITY_ORDER = {"high": 0, "medium": 1, "low": 2}
COVERAGE_SIGNAL_TTL_MINUTES = 15


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty RFC3339 string")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def validate_payload(payload: dict[str, Any]) -> tuple[datetime, list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    if payload.get("schema_version") != SCHEMA:
        raise ValueError(f"schema_version must be {SCHEMA}")
    as_of = parse_time(payload.get("as_of"), "as_of")

    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("targets must be a non-empty list")
    targets: list[str] = []
    seen_targets: set[str] = set()
    for index, value in enumerate(raw_targets):
        target = require_string(value, f"targets[{index}]").upper()
        if target not in seen_targets:
            targets.append(target)
            seen_targets.add(target)

    raw_requirements = payload.get("requirements")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ValueError("requirements must be a non-empty list")
    requirements: list[dict[str, Any]] = []
    seen_dimensions: set[str] = set()
    for index, raw in enumerate(raw_requirements):
        if not isinstance(raw, dict):
            raise ValueError(f"requirements[{index}] must be an object")
        dimension = require_string(raw.get("dimension"), f"requirements[{index}].dimension").lower()
        if dimension in seen_dimensions:
            raise ValueError(f"duplicate requirement dimension: {dimension}")
        criticality = str(raw.get("criticality", "medium")).lower()
        if criticality not in CRITICALITY_ORDER:
            raise ValueError(f"invalid criticality for {dimension}")
        minimum = raw.get("min_independent_sources", 1)
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            raise ValueError(f"min_independent_sources for {dimension} must be >= 1")
        requirements.append(
            {
                "dimension": dimension,
                "criticality": criticality,
                "min_independent_sources": minimum,
            }
        )
        seen_dimensions.add(dimension)

    raw_observations = payload.get("observations", [])
    if not isinstance(raw_observations, list):
        raise ValueError("observations must be a list")
    observations: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_observations):
        if not isinstance(raw, dict):
            raise ValueError(f"observations[{index}] must be an object")
        target = require_string(raw.get("target"), f"observations[{index}].target").upper()
        dimension = require_string(raw.get("dimension"), f"observations[{index}].dimension").lower()
        source = require_string(raw.get("source"), f"observations[{index}].source")
        family = require_string(raw.get("provider_family"), f"observations[{index}].provider_family").lower()
        status = str(raw.get("status", "unknown")).lower()
        if target not in seen_targets:
            raise ValueError(f"observation target not declared: {target}")
        if dimension not in seen_dimensions:
            raise ValueError(f"observation dimension not required: {dimension}")
        if status not in CAPABILITY_STATES:
            raise ValueError(f"invalid capability state: {status}")
        data_present = raw.get("data_present", False)
        if not isinstance(data_present, bool):
            raise ValueError(f"observations[{index}].data_present must be boolean")
        observed_at = raw.get("observed_at")
        stale_after = raw.get("stale_after")
        fallback_level = raw.get("fallback_level")
        if status in {"delayed", "cached"}:
            fallback_level = require_string(fallback_level, f"observations[{index}].fallback_level")
        if status in USABLE_STATES and data_present:
            observed_time = parse_time(observed_at, f"observations[{index}].observed_at")
            stale_time = parse_time(stale_after, f"observations[{index}].stale_after")
            if observed_time > as_of:
                raise ValueError(f"observations[{index}].observed_at cannot be after as_of")
            if stale_time <= observed_time:
                raise ValueError(f"observations[{index}].stale_after must be after observed_at")
        observations.append(
            {
                "target": target,
                "dimension": dimension,
                "source": source,
                "provider_family": family,
                "status": status,
                "observed_at": observed_at,
                "stale_after": stale_after,
                "data_present": data_present,
                "fallback_level": fallback_level,
            }
        )
    return as_of, targets, requirements, observations


def classify_cell(
    target: str,
    requirement: dict[str, Any],
    rows: list[dict[str, Any]],
    as_of: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    usable: list[dict[str, Any]] = []
    stale_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["status"] not in USABLE_STATES or not row["data_present"]:
            continue
        stale_after = parse_time(row["stale_after"], "stale_after")
        if stale_after <= as_of:
            stale_rows.append(row)
        else:
            usable.append(row)

    families = sorted({row["provider_family"] for row in usable})
    live_families = sorted({row["provider_family"] for row in usable if row["status"] == "live"})
    minimum = requirement["min_independent_sources"]
    reason_code: str | None = None
    if len(families) >= minimum:
        state = "covered_live" if len(live_families) >= minimum else "covered_reference"
    elif usable:
        state = "unavailable"
        reason_code = "insufficient_independent_sources"
    elif stale_rows:
        state = "stale"
        reason_code = "stale"
    elif rows and all(row["status"] == "unsupported" for row in rows):
        state = "unsupported"
        reason_code = "unsupported"
    elif rows:
        state = "unavailable"
        priority = ("auth_missing", "rate_limited", "error", "unknown")
        reason_code = next((status for status in priority if any(row["status"] == status for row in rows)), "missing")
    else:
        state = "missing"
        reason_code = "missing"

    fallback_levels = sorted({str(row["fallback_level"]) for row in usable if row.get("fallback_level")})
    cell = {
        "target": target,
        "dimension": requirement["dimension"],
        "criticality": requirement["criticality"],
        "coverage_state": state,
        "required_independent_sources": minimum,
        "independent_provider_count": len(families),
        "live_independent_provider_count": len(live_families),
        "provider_families": families,
        "fallback_levels": fallback_levels,
    }
    if reason_code is None:
        return cell, None
    gap = {
        "target": target,
        "dimension": requirement["dimension"],
        "severity": requirement["criticality"],
        "reason_code": reason_code,
        "coverage_state": state,
        "required_independent_sources": minimum,
        "available_independent_sources": len(families),
        "impact": "coverage requirement not met; cannot treat the dimension as decision-ready",
        "fallback": "collect the next independent, current source or keep the action cap tightened",
    }
    return cell, gap


def fair_queue(targets: list[str], gaps: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for severity in ("high", "medium", "low"):
        per_target = {
            target: deque(sorted(
                (gap for gap in gaps if gap["target"] == target and gap["severity"] == severity),
                key=lambda row: row["dimension"],
            ))
            for target in targets
        }
        while len(output) < max_items and any(per_target[target] for target in targets):
            for target in targets:
                if len(output) >= max_items:
                    break
                if not per_target[target]:
                    continue
                gap = per_target[target].popleft()
                output.append(
                    {
                        "target": target,
                        "dimension": gap["dimension"],
                        "priority": gap["severity"],
                        "reason_code": gap["reason_code"],
                        "next_action": "collect_independent_current_source",
                    }
                )
        if len(output) >= max_items:
            break
    return output


def suggested_signal(coverage: list[dict[str, Any]], gaps: list[dict[str, Any]], coverage_id: str, as_of: datetime, stale_after: datetime) -> dict[str, Any]:
    severities = {gap["severity"] for gap in gaps}
    reference_only = any(cell["coverage_state"] == "covered_reference" for cell in coverage)
    if "high" in severities:
        level, multiplier, reason = "L0", 0.0, "high_criticality_coverage_gap"
    elif "medium" in severities:
        level, multiplier, reason = "L1", 0.5, "medium_criticality_coverage_gap"
    elif "low" in severities or reference_only:
        level, multiplier, reason = "L2", 0.75, "reference_or_low_criticality_coverage_gap"
    else:
        level, multiplier, reason = "L3", 1.0, "coverage_gate_neutral"
    return {
        "module": "data_quality",
        "sub_framework": "source_coverage",
        "max_action_level": level,
        "position_multiplier": multiplier,
        "hard_veto": False,
        "reason": reason,
        "evidence_refs": [coverage_id],
        "observed_at": as_of.isoformat().replace("+00:00", "Z"),
        "stale_after": stale_after.isoformat().replace("+00:00", "Z"),
        "tighten_only": True,
        "cannot_raise_upstream": True,
    }


def compile_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    as_of, targets, requirements, observations = validate_payload(payload)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[(row["target"], row["dimension"])].append(row)

    coverage: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for target in targets:
        for requirement in requirements:
            cell, gap = classify_cell(target, requirement, grouped[(target, requirement["dimension"])], as_of)
            coverage.append(cell)
            if gap:
                gaps.append(gap)

    covered = sum(cell["coverage_state"].startswith("covered_") for cell in coverage)
    ratio = round(covered / len(coverage), 4) if coverage else 0.0
    max_queue = payload.get("max_queue", len(gaps))
    if not isinstance(max_queue, int) or isinstance(max_queue, bool) or max_queue < 0:
        raise ValueError("max_queue must be a non-negative integer")
    canonical = json.dumps(
        {"schema_version": SCHEMA, "as_of": as_of.isoformat(), "targets": targets, "requirements": requirements, "observations": observations},
        sort_keys=True,
        separators=(",", ":"),
    )
    coverage_id = "ICOV-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]
    signal_deadline = as_of + timedelta(minutes=COVERAGE_SIGNAL_TTL_MINUTES)
    source_deadlines = [
        deadline
        for row in observations
        if row["status"] in USABLE_STATES and row["data_present"]
        for deadline in [parse_time(row["stale_after"], "stale_after")]
        if deadline > as_of
    ]
    if source_deadlines:
        signal_deadline = min([signal_deadline, *source_deadlines])
    return {
        "ok": True,
        "schema_version": SCHEMA,
        "coverage_id": coverage_id,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "targets": targets,
        "requirements": requirements,
        "coverage": coverage,
        "coverage_ratio": ratio,
        "covered_cells": covered,
        "total_cells": len(coverage),
        "critical_gap_count": sum(gap["severity"] == "high" for gap in gaps),
        "data_gaps": gaps,
        "fair_collection_queue": fair_queue(targets, gaps, max_queue),
        "suggested_module_signal": suggested_signal(coverage, gaps, coverage_id, as_of, signal_deadline),
        "allowed_use": ["data_quality", "evidence_collection_plan", "research_readiness", "watch_priority"],
        "forbidden_use": ["direction_signal", "verified_fact", "position_cap_increase", "order_execution"],
        "no_order_execution": True,
    }


def self_test() -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA,
        "as_of": "2026-07-12T00:00:00Z",
        "targets": ["AAA.US", "BBB.US"],
        "requirements": [
            {"dimension": "quote", "criticality": "high", "min_independent_sources": 1},
            {"dimension": "news", "criticality": "medium", "min_independent_sources": 2},
        ],
        "observations": [
            {"target": "AAA.US", "dimension": "quote", "source": "q", "provider_family": "p1", "status": "live", "observed_at": "2026-07-11T23:59:00Z", "stale_after": "2026-07-12T00:05:00Z", "data_present": True, "fallback_level": "T1"},
            {"target": "AAA.US", "dimension": "news", "source": "n", "provider_family": "p1", "status": "live", "observed_at": "2026-07-11T23:00:00Z", "stale_after": "2026-07-12T01:00:00Z", "data_present": True, "fallback_level": "T2"},
            {"target": "BBB.US", "dimension": "quote", "source": "q", "provider_family": "p1", "status": "unsupported", "data_present": False},
        ],
    }
    result = compile_coverage(payload)
    assert result["coverage_ratio"] == 0.25
    assert result["critical_gap_count"] == 1
    assert result["suggested_module_signal"]["max_action_level"] == "L0"
    assert [row["target"] for row in result["fair_collection_queue"][:2]] == ["BBB.US", "AAA.US"]
    assert [row["priority"] for row in result["fair_collection_queue"][:2]] == ["high", "medium"]
    assert result["no_order_execution"] is True
    return {"ok": True, "self_test": "passed", "coverage_ratio": result["coverage_ratio"]}


def read_payload(path: str | None) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile per-target intelligence coverage without network or order side effects")
    parser.add_argument("--input", help="JSON input path; stdin when omitted")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true", help="Compatibility flag; output is always JSON")
    args = parser.parse_args()
    try:
        output = self_test() if args.self_test else compile_coverage(read_payload(args.input))
        code = 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        output = {"ok": False, "schema_version": SCHEMA, "error": str(exc), "no_order_execution": True}
        code = 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
