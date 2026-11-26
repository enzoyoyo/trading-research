#!/usr/bin/env python3
"""Validate KOL method cards embedded in research_provenance.v1."""
from __future__ import annotations

from datetime import datetime
from typing import Any

JsonDict = dict[str, Any]
Issue = dict[str, str]

CANONICAL_CLAIM_TYPES = {
    "fact", "reported_metric", "guidance", "forecast", "assumption", "opinion",
    "market_pricing", "derived_calculation", "rumor_signal",
}
KOL_LIFECYCLE_STAGES = (
    "public_claim", "falsifiable_thesis", "universe_selection", "entry", "add_reduce",
    "stop_invalidation", "take_profit", "exit", "sizing_risk", "post_trade_calibration",
)
EVIDENCE_STATUSES = {"supported", "partial", "unknown", "not_applicable"}
INFERENCE_LABELS = {
    "direct_assertion", "framework_inference", "self_reported_performance", "not_found_publicly",
}
PROVENANCE_MODES = {"direct_quote", "source_summary", "framework_inference", "unverified"}
ACCESS_STATES = {"public", "partial", "subscriber_only", "blocked"}
TIME_PRECISIONS = {"exact", "minute", "date_only"}
ALLOWED_EFFECTS = {"tighten", "refresh_source", "request_manual_review"}
FORBIDDEN_EFFECTS = {
    "raise_action_level", "raise_position_cap", "raise_reliability", "revive_l0", "order_execution",
}


def _issue(code: str, path: str, message: str) -> Issue:
    return {"code": code, "path": path, "message": message}


def _aware_timestamp(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _iso_date(value: Any) -> bool:
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _validate_source(
    document_id: str,
    document: JsonDict,
    card_author: str,
    card_handle: str,
    path: str,
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    source_path = f"{path}.source_document_ids[{document_id}]"
    author = str(document.get("author") or "").strip()
    if not author:
        errors.append(_issue("kol_source_author_missing", source_path, "KOL source requires author"))
    elif author not in {card_author, card_handle}:
        errors.append(_issue(
            "kol_source_author_mismatch", source_path,
            "KOL source author must match method-card author or handle",
        ))

    published_at = str(document.get("published_at") or "").strip()
    precision = str(document.get("published_time_precision") or "").strip()
    if not published_at:
        errors.append(_issue("kol_published_at_missing", source_path, "KOL source requires published_at"))
    elif _iso_date(published_at):
        if precision != "date_only":
            errors.append(_issue(
                "kol_published_time_precision_invalid", source_path,
                "date-only values require published_time_precision=date_only",
            ))
    elif _aware_timestamp(published_at):
        if precision not in {"exact", "minute"}:
            errors.append(_issue(
                "kol_published_time_precision_invalid", source_path,
                "timestamp values require exact or minute precision",
            ))
    else:
        errors.append(_issue(
            "kol_published_at_invalid", source_path,
            "published_at must be an ISO date or timezone-aware timestamp",
        ))
    if precision not in TIME_PRECISIONS:
        errors.append(_issue(
            "kol_published_time_precision_invalid", source_path,
            "invalid or missing published_time_precision",
        ))

    retrieved_at = document.get("retrieved_at")
    if not retrieved_at:
        errors.append(_issue("kol_retrieved_at_missing", source_path, "KOL source requires retrieved_at"))
    elif not _aware_timestamp(retrieved_at):
        errors.append(_issue(
            "kol_retrieved_at_invalid", source_path,
            "retrieved_at must include an explicit timezone",
        ))

    access_state = str(document.get("access_state") or "").strip()
    if not access_state:
        errors.append(_issue("kol_access_state_missing", source_path, "KOL source requires access_state"))
    elif access_state not in ACCESS_STATES:
        errors.append(_issue("kol_access_state_invalid", source_path, "invalid KOL access_state"))
    elif access_state == "partial":
        warnings.append(_issue(
            "kol_partial_access", source_path,
            "partial source access caps provenance at partial/L1",
        ))
    elif access_state in {"subscriber_only", "blocked"}:
        warnings.append(_issue(
            "kol_restricted_access_gap", source_path,
            "restricted source is a gap and cannot support a lifecycle claim",
        ))


def _validate_stage_lists(stage: JsonDict, path: str, errors: list[Issue]) -> None:
    for field in ("source_document_ids", "framework_claim_ids", "unknowns"):
        if field not in stage or not _string_list(stage.get(field)):
            errors.append(_issue(
                "kol_stage_list_invalid", f"{path}.{field}",
                f"{field} must be an explicit array of non-empty strings",
            ))
    for field in ("supporting_eids", "contradicting_eids"):
        if field not in stage or not _string_list(stage.get(field)):
            errors.append(_issue(
                "kol_eid_handoff_missing", f"{path}.{field}",
                f"{field} must be an explicit EvidenceItem ID array",
            ))


def _validate_mapping(
    stage: JsonDict,
    path: str,
    quote_by_id: dict[str, JsonDict],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    label = str(stage.get("inference_label") or "").strip()
    mode = str(stage.get("provenance_mode") or "").strip()
    claim_type = str(stage.get("claim_type") or "").strip()
    status = str(stage.get("evidence_status") or "").strip()
    source_ids = stage.get("source_document_ids") if isinstance(stage.get("source_document_ids"), list) else []
    quote_ids = stage.get("quote_anchor_ids") if isinstance(stage.get("quote_anchor_ids"), list) else []
    framework_ids = stage.get("framework_claim_ids") if isinstance(stage.get("framework_claim_ids"), list) else []
    unknowns = stage.get("unknowns") if isinstance(stage.get("unknowns"), list) else []

    if not label:
        errors.append(_issue("kol_inference_label_missing", path, "lifecycle stage requires inference_label"))
        return
    if label not in INFERENCE_LABELS:
        errors.append(_issue("kol_inference_label_invalid", path, "invalid KOL inference_label"))
        return
    if claim_type not in CANONICAL_CLAIM_TYPES:
        errors.append(_issue(
            "kol_noncanonical_claim_type", path,
            "KOL aliases must map to the canonical claim_type enum at the boundary",
        ))
    if label == "direct_assertion":
        if mode not in {"direct_quote", "source_summary"} or not source_ids:
            errors.append(_issue(
                "kol_inference_mapping_invalid", path,
                "direct_assertion requires a source and direct_quote/source_summary provenance",
            ))
        elif mode == "direct_quote":
            if not _string_list(quote_ids) or not quote_ids or any(item not in quote_by_id for item in quote_ids):
                errors.append(_issue(
                    "kol_direct_quote_anchor_missing", path,
                    "direct_quote requires at least one existing QuoteAnchor",
                ))
            elif any(
                str(quote_by_id[item].get("document_id") or "") not in source_ids
                for item in quote_ids
            ):
                errors.append(_issue(
                    "kol_direct_quote_source_mismatch", path,
                    "every direct-quote anchor must belong to the stage source documents",
                ))
    elif label == "framework_inference" and (
        mode != "framework_inference" or claim_type not in {"assumption", "opinion"} or not framework_ids
    ):
        errors.append(_issue(
            "kol_inference_mapping_invalid", path,
            "framework inference requires canonical provenance and framework IDs",
        ))
    elif label == "self_reported_performance":
        if mode != "unverified" or claim_type != "opinion" or status not in {"partial", "unknown"}:
            errors.append(_issue(
                "kol_self_reported_performance_mapping", path,
                "self-reported performance must remain unverified opinion",
            ))
        if not source_ids:
            errors.append(_issue(
                "kol_self_reported_source_missing", path,
                "self-reported performance requires a traceable source; otherwise use not_found_publicly",
            ))
        else:
            warnings.append(_issue(
                "kol_self_reported_performance_unverified", path,
                "self-reported performance is unverified and caps provenance at partial/L1",
            ))
    elif label == "not_found_publicly" and (
        mode != "unverified" or claim_type != "assumption" or status != "unknown"
        or source_ids or framework_ids or not unknowns
    ):
        errors.append(_issue(
            "kol_unknown_mapping_invalid", path,
            "not_found_publicly must be an explicit unverified assumption without source/framework IDs",
        ))


def _validate_stage(
    stage_name: str,
    stage: JsonDict,
    path: str,
    card_source_ids: set[str],
    document_by_id: dict[str, JsonDict],
    quote_by_id: dict[str, JsonDict],
    framework_by_id: dict[str, JsonDict],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    if not str(stage.get("summary") or "").strip():
        errors.append(_issue("kol_stage_summary_missing", path, "lifecycle stage requires a summary"))
    status = str(stage.get("evidence_status") or "").strip()
    if status not in EVIDENCE_STATUSES:
        errors.append(_issue("kol_evidence_status_invalid", path, "invalid evidence_status"))
    elif status == "partial":
        warnings.append(_issue(
            "kol_partial_evidence_stage", path,
            "partial lifecycle evidence caps provenance at partial/L1",
        ))
    _validate_stage_lists(stage, path, errors)
    _validate_mapping(stage, path, quote_by_id, errors, warnings)

    source_ids = stage.get("source_document_ids") if isinstance(stage.get("source_document_ids"), list) else []
    if any(item not in card_source_ids or item not in document_by_id for item in source_ids):
        errors.append(_issue(
            "kol_stage_source_missing", path,
            "stage references unknown or undeclared source documents",
        ))
    stage_framework_ids = stage.get("framework_claim_ids") if isinstance(stage.get("framework_claim_ids"), list) else []
    if any(item not in framework_by_id for item in stage_framework_ids):
        errors.append(_issue("kol_stage_framework_missing", path, "stage references an unknown framework"))
    if stage.get("inference_label") == "framework_inference":
        unusable = [
            item for item in stage_framework_ids
            if item in framework_by_id
            and framework_by_id[item].get("status") not in {"supported", "mixed"}
        ]
        if unusable:
            errors.append(_issue(
                "kol_stage_framework_not_usable",
                path,
                "framework inference requires supported or mixed FrameworkClaim rows",
            ))

    access_states = {str(document_by_id[item].get("access_state") or "") for item in source_ids if item in document_by_id}
    if access_states & {"subscriber_only", "blocked"}:
        errors.append(_issue(
            "kol_restricted_source_used", path,
            "subscriber-only or blocked material cannot support a lifecycle stage",
        ))
    if "partial" in access_states and status != "partial":
        errors.append(_issue(
            "kol_partial_source_overstated", path,
            "a stage using partial access must remain evidence_status=partial",
        ))
    if stage_name in {"falsifiable_thesis", "stop_invalidation"} and status in {"supported", "partial"}:
        if not str(stage.get("falsifier") or "").strip():
            errors.append(_issue(
                "kol_falsifier_missing", path,
                "supported thesis/invalidation stages require an explicit falsifier",
            ))


def _validate_gap_list(
    card: JsonDict,
    field: str,
    path: str,
    card_source_ids: set[str],
    document_by_id: dict[str, JsonDict],
    errors: list[Issue],
    warnings: list[Issue],
) -> set[str]:
    referenced_source_ids: set[str] = set()
    value = card.get(field)
    if not isinstance(value, list):
        errors.append(_issue("kol_gap_list_missing", f"{path}.{field}", f"{field} must be an explicit array"))
        return referenced_source_ids
    for index, gap in enumerate(value):
        gap_path = f"{path}.{field}[{index}]"
        if not isinstance(gap, dict) or not str(gap.get("reason") or "").strip():
            errors.append(_issue("kol_gap_invalid", gap_path, "gap entries require a reason"))
            continue
        source_ids = gap.get("source_document_ids")
        if not _string_list(source_ids) or not source_ids or any(item not in card_source_ids for item in source_ids):
            errors.append(_issue("kol_gap_source_missing", gap_path, "gap source IDs must belong to the card"))
            continue
        referenced_source_ids.update(source_ids)
        if field == "subscriber_gaps" and any(
            str(document_by_id[item].get("access_state") or "") not in {"partial", "subscriber_only", "blocked"}
            for item in source_ids if item in document_by_id
        ):
            errors.append(_issue(
                "kol_subscriber_gap_access_mismatch", gap_path,
                "subscriber gaps must reference restricted or partial sources",
            ))
        if field != "subscriber_gaps" and any(
            str(document_by_id[item].get("access_state") or "") in {"subscriber_only", "blocked"}
            for item in source_ids if item in document_by_id
        ):
            errors.append(_issue(
                "kol_restricted_source_outside_subscriber_gaps", gap_path,
                "subscriber-only or blocked sources may appear only in subscriber_gaps",
            ))
        if field == "promotion_gaps":
            label = gap.get("inference_label")
            mode = gap.get("provenance_mode")
            claim_type = gap.get("claim_type")
            mapping_valid = (
                label in INFERENCE_LABELS
                and mode in PROVENANCE_MODES
                and claim_type in CANONICAL_CLAIM_TYPES
            )
            if mapping_valid and label == "direct_assertion":
                mapping_valid = mode in {"direct_quote", "source_summary"}
            elif mapping_valid and label == "framework_inference":
                mapping_valid = mode == "framework_inference" and claim_type in {"assumption", "opinion"}
            elif mapping_valid and label == "self_reported_performance":
                mapping_valid = mode == "unverified" and claim_type == "opinion"
            elif mapping_valid and label == "not_found_publicly":
                mapping_valid = mode == "unverified" and claim_type == "assumption"
            if not mapping_valid:
                errors.append(_issue(
                    "kol_promotion_gap_mapping_invalid",
                    gap_path,
                    "promotion-gap mapping must use the existing inference/provenance/claim enums",
                ))
            if gap.get("can_raise_reliability") is not False:
                errors.append(_issue(
                    "kol_promotion_gap_reliability_raise_forbidden",
                    gap_path,
                    "promotion gaps must set can_raise_reliability=false",
                ))
            if mapping_valid and label == "self_reported_performance":
                warnings.append(_issue(
                    "kol_self_reported_performance_unverified",
                    gap_path,
                    "self-reported performance is unverified and caps provenance at partial/L1",
                ))
    return referenced_source_ids


def _validate_boundary(card: JsonDict, path: str, errors: list[Issue]) -> None:
    boundary = card.get("decision_boundary")
    valid = isinstance(boundary, dict)
    if valid:
        allowed = boundary.get("allowed_effects")
        forbidden = boundary.get("forbidden_effects")
        valid = (
            _string_list(allowed) and set(allowed) == ALLOWED_EFFECTS
            and _string_list(forbidden) and set(forbidden) == FORBIDDEN_EFFECTS
            and not isinstance(boundary.get("position_multiplier"), bool)
            and boundary.get("position_multiplier") == 0.0
            and boundary.get("self_reported_performance_can_raise_reliability") is False
            and boundary.get("no_order_execution") is True
        )
    if not valid:
        errors.append(_issue(
            "kol_decision_boundary_invalid", f"{path}.decision_boundary",
            "KOL handoff may only tighten, refresh sources, or request manual review",
        ))


def _validate_card(
    index: int,
    card: JsonDict,
    document_by_id: dict[str, JsonDict],
    quote_by_id: dict[str, JsonDict],
    framework_by_id: dict[str, JsonDict],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    path = f"kol_method_cards[{index}]"
    author = str(card.get("author") or "").strip()
    handle = str(card.get("author_handle") or "").strip()
    if not card.get("method_card_id") or not author or not handle:
        errors.append(_issue(
            "kol_card_identity_missing", path,
            "method_card_id, author, and author_handle are required",
        ))
    for field in ("market_scope", "time_horizon", "portable_parts", "do_not_port", "unknowns"):
        if field not in card or not _string_list(card.get(field)):
            errors.append(_issue(
                "kol_card_list_invalid", f"{path}.{field}",
                f"{field} must be an explicit array of non-empty strings",
            ))

    source_ids_value = card.get("source_document_ids")
    if not _string_list(source_ids_value) or not source_ids_value:
        errors.append(_issue(
            "kol_card_sources_missing", f"{path}.source_document_ids",
            "method cards require at least one source document",
        ))
        card_source_ids: set[str] = set()
    else:
        card_source_ids = set(source_ids_value)
    for document_id in sorted(card_source_ids):
        document = document_by_id.get(document_id)
        if not document:
            errors.append(_issue("kol_card_source_missing", path, f"unknown source document: {document_id}"))
            continue
        _validate_source(document_id, document, author, handle, path, errors, warnings)

    lifecycle = card.get("lifecycle")
    if not isinstance(lifecycle, dict):
        errors.append(_issue("kol_lifecycle_invalid", f"{path}.lifecycle", "lifecycle must be an object"))
    else:
        missing = [stage for stage in KOL_LIFECYCLE_STAGES if stage not in lifecycle]
        if missing:
            errors.append(_issue(
                "kol_lifecycle_missing_stage", f"{path}.lifecycle",
                "missing lifecycle stages: " + ",".join(missing),
            ))
        unknown = sorted(set(lifecycle) - set(KOL_LIFECYCLE_STAGES))
        if unknown:
            errors.append(_issue(
                "kol_lifecycle_unknown_stage", f"{path}.lifecycle",
                "unknown lifecycle stages: " + ",".join(unknown),
            ))
        for stage_name in KOL_LIFECYCLE_STAGES:
            stage = lifecycle.get(stage_name)
            if stage is None:
                continue
            if not isinstance(stage, dict):
                errors.append(_issue(
                    "kol_lifecycle_stage_invalid", f"{path}.lifecycle.{stage_name}",
                    "lifecycle stage must be an object",
                ))
                continue
            _validate_stage(
                stage_name, stage, f"{path}.lifecycle.{stage_name}", card_source_ids,
                document_by_id, quote_by_id, framework_by_id, errors, warnings,
            )

    _validate_gap_list(card, "promotion_gaps", path, card_source_ids, document_by_id, errors, warnings)
    subscriber_gap_source_ids = _validate_gap_list(
        card, "subscriber_gaps", path, card_source_ids, document_by_id, errors, warnings,
    )
    restricted_source_ids = {
        item for item in card_source_ids
        if item in document_by_id
        and str(document_by_id[item].get("access_state") or "") in {"subscriber_only", "blocked"}
    }
    uncovered_restricted_sources = sorted(restricted_source_ids - subscriber_gap_source_ids)
    if uncovered_restricted_sources:
        errors.append(_issue(
            "kol_restricted_source_gap_missing", f"{path}.subscriber_gaps",
            "restricted card sources require subscriber-gap coverage: "
            + ",".join(uncovered_restricted_sources),
        ))
    _validate_boundary(card, path, errors)


def validate_kol_cards(
    cards: list[JsonDict],
    documents: list[JsonDict],
    quotes: list[JsonDict],
    frameworks: list[JsonDict],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    """Validate optional cards without creating a parallel action or truth system."""
    document_by_id = {str(row.get("document_id") or ""): row for row in documents}
    quote_by_id = {str(row.get("quote_id") or ""): row for row in quotes}
    framework_by_id = {str(row.get("framework_claim_id") or ""): row for row in frameworks}
    for index, card in enumerate(cards):
        _validate_card(index, card, document_by_id, quote_by_id, framework_by_id, errors, warnings)
