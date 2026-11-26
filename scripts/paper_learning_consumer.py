#!/usr/bin/env python3
"""Pure System B paper-learning triage; daily ledger owns all persistence.

This module never patches a skill, grants order/sizing authority, or writes state.
A repeated sample set is an observation, not another independent experiment.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

EVIDENCE_SCHEMA = "paper_learning_evidence.v1"
CONSUMPTION_SCHEMA = "paper_learning_consumption.v1"
CANDIDATE_SCHEMA = "paper_failure_candidate.v1"
MIN_RESEARCH_SAMPLES = 5
BOUNDARY = {"auto_apply": False, "risk_relaxation_allowed": False,
            "sizing_authority": False, "order_authority": False}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def finite(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def clock(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def read_history(path: Path) -> dict[str, Any]:
    """A damaged consumption history must not reset novelty to 'new'."""
    if not path.exists():
        return {"status": "missing", "rows": []}
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("non-object ledger row")
    except (OSError, UnicodeError, ValueError):
        return {"status": "invalid", "rows": [], "reason": "consumption_history_unreadable_or_invalid"}
    return {"status": "present", "rows": rows}


def real_reviews(packet: dict[str, Any], as_of: datetime) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Use completed, known finite-R packet rows, never OOS or open snapshots."""
    values = packet.get("closed_trade_reviews")
    if not isinstance(values, list):
        return {}, ["closed_trade_reviews_missing"]
    indexed: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    seen_ids: set[str] = set()
    for row in values:
        if not isinstance(row, dict):
            continue
        oid = row.get("trade_lifecycle_id")
        if isinstance(oid, str) and oid:
            if oid in seen_ids:
                duplicate_ids.add(oid)
            seen_ids.add(oid)
        outcome = str(row.get("outcome") or "").lower()
        if not outcome:
            ret = finite(row.get("return_pct"))
            outcome = "unknown" if ret is None else "win" if ret > 0 else "loss" if ret < 0 else "breakeven"
        opened, closed = clock(row.get("opened_at")), clock(row.get("completed_at"))
        if (not isinstance(oid, str) or not oid or outcome not in {"win", "loss", "breakeven", "success", "failure"}
                or finite(row.get("r_multiple")) is None or not opened or not closed
                or not opened <= closed <= as_of):
            continue
        indexed[oid] = row
    for oid in duplicate_ids:
        indexed.pop(oid, None)
    return indexed, (["duplicate_completed_lifecycle_ids"] if duplicate_ids else [])


def previous_candidates(history: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    candidates = []
    for row in history.get("rows", []):
        snapshot = row.get("paper_learning_consumption")
        if snapshot is None:  # Legacy daily rows did not record candidate consumption.
            continue
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != CONSUMPTION_SCHEMA:
            return [], False
        items = snapshot.get("candidates")
        if not isinstance(items, list):
            return [], False
        for item in items:
            if not isinstance(item, dict):
                return [], False
            if item.get("validation_status") != "valid":
                continue
            ids = item.get("real_lifecycle_ids")
            if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids)
                    or len(set(ids)) != len(ids) or item.get("real_sample_set_sha256") != fingerprint(sorted(ids))
                    or not isinstance(item.get("candidate_id"), str)):
                return [], False
            candidates.append(item)
    return candidates, history.get("status") != "invalid"


def consume(packet: dict[str, Any], history: dict[str, Any], *, now: datetime | None = None,
            packet_file: str | None = None) -> dict[str, Any]:
    """Return traceable review dispositions without declaring a patch approved."""
    now = now or datetime.now(timezone.utc)
    block = packet.get("learning_evidence")
    gate = packet.get("materiality_gate") if isinstance(packet.get("materiality_gate"), dict) else {}
    result: dict[str, Any] = {
        "schema_version": CONSUMPTION_SCHEMA, "status": "unknown", "packet_file": packet_file,
        "structured_evidence_present": "learning_evidence" in packet,
        "source_schema_version": block.get("schema_version") if isinstance(block, dict) else None,
        "packet_generated_at_utc": packet.get("generated_at_utc"),
        "research_upgrade_eligible": False, "declared_research_upgrade_eligible": gate.get("enough_for_trading_research_skill_upgrade") is True,
        "policy_sizing_eligible": gate.get("enough_for_policy_sizing_change") is True,
        "history_status": history.get("status"), "candidates": [], "data_gaps": [],
        "novelty_basis": "first_seen_by_versioned_consumer_not_new_market_events",
        "post_change_improvement_evidence": False,
        "new_independent_lifecycle_ids": [], "new_independent_sample_count": 0,
        "new_review_candidate_count": 0, **BOUNDARY,
    }
    if not isinstance(block, dict) or block.get("schema_version") != EVIDENCE_SCHEMA:
        result["data_gaps"] = ["learning_evidence_missing_or_unsupported"]
        return result
    as_of = clock(packet.get("generated_at_utc"))
    if as_of is None or as_of > now:
        result.update(status="invalid", data_gaps=["invalid_or_future_packet_clock"])
        return result
    patterns = block.get("failure_patterns")
    if not isinstance(patterns, list):
        result.update(status="invalid", data_gaps=["failure_patterns_not_list"])
        return result
    reviews, gaps = real_reviews(packet, as_of)
    prior, history_valid = previous_candidates(history)
    result["data_gaps"] = gaps + ([] if history_valid else ["consumption_history_invalid"])
    result["status"] = "present" if history_valid else "blocked"
    result["source_schema_version"] = EVIDENCE_SCHEMA
    result["source_evidence_fingerprint"] = block.get("evidence_fingerprint")
    result["research_upgrade_eligible"] = result["declared_research_upgrade_eligible"] and len(reviews) >= MIN_RESEARCH_SAMPLES
    if result["declared_research_upgrade_eligible"] and not result["research_upgrade_eligible"]:
        result["data_gaps"].append("research_gate_not_supported_by_known_complete_samples")
    observed_keys: set[str] = set()
    observed_ids: set[str] = set()
    for source in patterns:
        normalized = {"candidate_id": source.get("candidate_id") if isinstance(source, dict) else None,
                      "validation_status": "invalid", "review_state": "awaiting_evidence", "reason": "invalid_candidate_contract",
                      "novelty": "unknown", "new_independent_sample_count": 0, "new_lifecycle_ids": [], **BOUNDARY}
        result["candidates"].append(normalized)
        if not isinstance(source, dict) or source.get("schema_version") != CANDIDATE_SCHEMA:
            continue
        strategy, evidence = source.get("strategy"), source.get("evidence")
        if not isinstance(strategy, str) or not strategy or not isinstance(evidence, dict):
            continue
        ids, stats = evidence.get("lifecycle_ids"), evidence.get("real")
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids)
                or len(set(ids)) != len(ids) or not isinstance(stats, dict)):
            continue
        try:
            valid_id = fingerprint({"strategy": strategy, "evidence": evidence})
        except (TypeError, ValueError):
            continue
        if source.get("candidate_id") != valid_id:
            normalized["reason"] = "candidate_id_evidence_mismatch"
            continue
        if (source.get("status") not in {"review_required", "insufficient_samples"}
                or source.get("auto_apply") is not False or source.get("risk_relaxation_allowed") is not False):
            normalized["reason"] = "invalid_candidate_status_or_authority"
            continue
        if (any(i not in reviews or str(reviews[i].get("strategy") or "unattributed") != strategy for i in ids)
                or any(isinstance(stats.get(k), bool) or not isinstance(stats.get(k), int) for k in ("trades", "known_outcomes", "r_sample_count", "unknown_outcomes"))
                or stats.get("trades") != len(ids) or stats.get("known_outcomes") != len(ids)
                or stats.get("r_sample_count") != len(ids) or stats.get("unknown_outcomes") != 0):
            normalized["reason"] = "candidate_real_samples_do_not_match_completed_reviews"
            continue
        selected = [reviews[i] for i in sorted(ids)]
        avg_r = sum((finite(row["r_multiple"]) for row in selected), Decimal(0)) / len(ids)
        if avg_r >= 0 or finite(stats.get("avg_r")) is None or finite(stats["avg_r"]) >= 0:
            normalized["reason"] = "negative_expectancy_not_supported"
            continue
        sample_hash = fingerprint(sorted(ids))
        key = fingerprint({"candidate_id": valid_id, "real_sample_set_sha256": sample_hash})
        matching_prior = [p for p in prior if p.get("strategy") == strategy]
        seen_ids = {i for p in prior for i in p["real_lifecycle_ids"]} | observed_ids
        fresh = sorted(set(ids) - seen_ids) if history_valid else []
        exact_prior = [p for p in matching_prior if p.get("candidate_id") == valid_id and p.get("real_sample_set_sha256") == sample_hash]
        repeated = bool(exact_prior) or key in observed_keys
        if repeated:
            fresh = []
        novelty = ("unknown" if not history_valid else "unchanged" if repeated else "new_samples" if fresh
                   else "revised_candidate_existing_samples")
        eligible = result["research_upgrade_eligible"] and source["status"] == "review_required" and len(ids) >= MIN_RESEARCH_SAMPLES
        reason = ("consumption_history_invalid" if not history_valid else "insufficient_real_samples_or_research_gate"
                  if not eligible else "same_real_evidence_no_new_independent_samples" if not fresh
                  else "method_mapping_and_validation_required")
        normalized.update(
            validation_status="valid", strategy=strategy, pattern=source.get("pattern"),
            source_status=source["status"], policy_version=evidence.get("policy_version"),
            real_lifecycle_ids=sorted(ids), real_sample_set_sha256=sample_hash, consumption_key=key,
            real_evidence_sha256=fingerprint([
                {"trade_lifecycle_id": row["trade_lifecycle_id"], "strategy": strategy,
                 "r_multiple": str(finite(row["r_multiple"])), "outcome": row.get("outcome"),
                 "opened_at": row.get("opened_at"), "completed_at": row.get("completed_at")}
                for row in selected]), real_sample_count=len(ids),
            new_lifecycle_ids=fresh, new_independent_sample_count=len(fresh), novelty=novelty,
            research_review_eligible=eligible, review_state="awaiting_evidence", reason=reason,
            previous_review_state=exact_prior[-1].get("review_state") if exact_prior else None,
            previously_triaged=bool(exact_prior), review_actions=source.get("review_actions") or [],
            attribution_scope="cumulative_diagnostic_not_post_change_performance",
        )
        observed_keys.add(key)
        observed_ids.update(ids)
        if eligible and fresh:
            result["new_review_candidate_count"] += 1
        result["new_independent_lifecycle_ids"].extend(fresh)
    result["new_independent_lifecycle_ids"] = sorted(set(result["new_independent_lifecycle_ids"]))
    result["new_independent_sample_count"] = len(result["new_independent_lifecycle_ids"])
    return result


def rebase_for_append(snapshot: Any, history: dict[str, Any]) -> dict[str, Any] | None:
    """Recompute novelty under the ledger lock, including repeated check files.

    'reviewed_watch' records deterministic triage, not approval of a skill patch.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != CONSUMPTION_SCHEMA:
        return None
    result = json.loads(json.dumps(snapshot, allow_nan=False))
    prior, valid = previous_candidates(history)
    seen_ids = {i for p in prior for i in p["real_lifecycle_ids"]}
    seen_keys = {(p.get("candidate_id"), p.get("real_sample_set_sha256")) for p in prior}
    fresh_all = set()
    result["new_review_candidate_count"] = 0
    if not valid:
        result["status"] = "blocked"
        result["history_status"] = "invalid"
        result["data_gaps"] = sorted(set(result.get("data_gaps", []) + ["consumption_history_invalid"]))
    for item in result.get("candidates") or []:
        if item.get("validation_status") != "valid":
            continue
        ids = item.get("real_lifecycle_ids") or []
        key = (item.get("candidate_id"), item.get("real_sample_set_sha256"))
        consistent = bool(ids) and all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids) and key[1] == fingerprint(sorted(ids))
        if not consistent:
            raise ValueError("invalid candidate consumption snapshot")
        repeated = key in seen_keys
        fresh = sorted(set(ids) - seen_ids) if valid and not repeated else []
        item.update(new_lifecycle_ids=fresh, new_independent_sample_count=len(fresh),
                    novelty="unknown" if not valid else "unchanged" if repeated else "new_samples" if fresh else "revised_candidate_existing_samples",
                    previously_triaged=repeated, review_state="reviewed_watch",
                    review_scope="deterministic_triage_only", **BOUNDARY)
        if not valid:
            item["reason"] = "consumption_history_invalid"
        elif item.get("research_review_eligible") and not fresh:
            item["reason"] = "same_real_evidence_no_new_independent_samples"
        if fresh and item.get("research_review_eligible"):
            result["new_review_candidate_count"] += 1
        seen_ids.update(ids)
        seen_keys.add(key)
        fresh_all.update(fresh)
    result.update(new_independent_lifecycle_ids=sorted(fresh_all), new_independent_sample_count=len(fresh_all), **BOUNDARY)
    return result
