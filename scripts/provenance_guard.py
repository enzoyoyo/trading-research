#!/usr/bin/env python3
"""Validate source-grounded research provenance bundles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from kol_method_card import validate_kol_cards

SCHEMA_VERSION = "research_provenance.v1"
PROVENANCE_MODES = {"direct_quote", "source_summary", "framework_inference", "unverified"}
FRAMEWORK_STATUSES = {"supported", "mixed", "rejected", "needs_verification"}
BEHAVIOR_RESULTS = {"supports", "contradicts", "mixed", "not_enough_evidence"}
DECISION_USES = {"context_only", "watch_only", "supports_conclusion"}
QUOTE_USES = {"internal_evidence", "public_excerpt"}
COUNTEREVIDENCE_STATUSES = {"not_checked", "searched_none_found", "supporting_only", "mixed", "contradicted"}
LICENSE_SCOPES = {"public", "user_provided", "paid_restricted", "vendor_restricted", "unknown"}
STORAGE_SCOPES = {"transient", "private", "tracked_allowed"}
REDISTRIBUTION_SCOPES = {"yes", "no", "derived_only", "unknown"}
ACCESS_STATES = {"public", "partial", "subscriber_only", "blocked"}
SOURCE_ROLES = {"semantic", "nonsemantic"}
BUNDLE_PURPOSE_KINDS = {"report_evidence", "kol_method_handoff"}
CANONICAL_EID_PATTERN = re.compile(r"E\d+")
MAX_PUBLIC_EXCERPT_CHARS = 300
JsonDict = dict[str, Any]
Issue = dict[str, str]


def canonical_sha256(bundle: JsonDict) -> str:
    payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_aware_timestamp(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _parse_source_time(value: Any, *, allow_date_only: bool) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if allow_date_only and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _trusted_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(UTC)


def _issue(code: str, path: str, message: str) -> Issue:
    return {"code": code, "path": path, "message": message}


def _sorted_ids(rows: list[JsonDict], key: str) -> list[str]:
    return sorted(str(row.get(key)) for row in rows if row.get(key))


def _check_unique_ids(rows: list[JsonDict], key: str, section: str, errors: list[Issue]) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows):
        value = str(row.get(key) or "").strip()
        if not value:
            errors.append(_issue("stable_id_missing", f"{section}[{index}].{key}", "stable ID must be non-empty"))
            continue
        if value in seen:
            errors.append(_issue("duplicate_id", f"{section}[{index}].{key}", f"duplicate stable ID: {value}"))
        seen.add(value)


def _is_explicit_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _missing_text_fields(row: JsonDict, fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if not isinstance(row.get(field), str) or not row[field].strip()]


def _invalid_list_fields(row: JsonDict, fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if field not in row or not _is_explicit_string_list(row.get(field))]


def _validate_top_level(
    bundle: JsonDict,
    sections: list[tuple[list[JsonDict], str, str]],
    errors: list[Issue],
    *,
    now: datetime,
) -> None:
    if bundle.get("schema_version") != SCHEMA_VERSION:
        errors.append(_issue("unsupported_schema_version", "schema_version", f"expected {SCHEMA_VERSION}"))
    if not _is_aware_timestamp(bundle.get("as_of")):
        errors.append(_issue("invalid_as_of", "as_of", "as_of must include an explicit RFC3339/ISO timezone"))
    else:
        parsed_as_of = _parse_source_time(bundle.get("as_of"), allow_date_only=False)
        if parsed_as_of is not None and parsed_as_of > now:
            errors.append(_issue("future_as_of", "as_of", "as_of cannot be later than the trusted runtime clock"))
    if bundle.get("no_order_execution") is not True:
        errors.append(_issue(
            "order_execution_boundary_missing",
            "no_order_execution",
            "provenance bundles are research-only and must set no_order_execution=true",
        ))
    for rows, key, section in sections:
        _check_unique_ids(rows, key, section, errors)


def _secure_open_capability_error() -> str | None:
    missing = [name for name in ("O_NOFOLLOW", "O_DIRECTORY") if not getattr(os, name, 0)]
    if missing:
        return "missing secure open flags: " + ",".join(missing)
    if os.open not in getattr(os, "supports_dir_fd", set()):
        return "os.open does not support descriptor-relative paths"
    return None


def _local_path_parts(local_path: Any) -> tuple[str, ...] | None:
    if not isinstance(local_path, str) or not local_path or "\x00" in local_path:
        return None
    windows_path = PureWindowsPath(local_path)
    if (
        PurePosixPath(local_path).is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or "\\" in local_path
    ):
        return None
    parts = tuple(local_path.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def _base_directory_fd(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(root, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("base_dir is not a directory")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_anchored_regular_file(base_fd: int, parts: tuple[str, ...]) -> bytes:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    file_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    directory_fd = base_fd
    owned_directories: list[int] = []
    file_fd: int | None = None
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            owned_directories.append(next_fd)
            directory_fd = next_fd
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise OSError("local_path component is not a directory")
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise OSError("local source capture is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(owned_directories):
            os.close(descriptor)


def _capture_source(
    index: int,
    document: JsonDict,
    base_fd: int | None,
    secure_open_error: str | None,
    errors: list[Issue],
    warnings: list[Issue],
) -> tuple[str, str | None]:
    path = f"source_documents[{index}]"
    required = ("document_id", "source_type", "title", "publisher", "published_at", "original_url")
    missing = [field for field in required if not str(document.get(field) or "").strip()]
    if missing:
        errors.append(_issue("source_metadata_missing", path, "missing traceability fields: " + ",".join(missing)))
    governance = {
        "license_scope": (LICENSE_SCOPES, "invalid_license_scope"),
        "storage_scope": (STORAGE_SCOPES, "invalid_storage_scope"),
        "redistribution_allowed": (REDISTRIBUTION_SCOPES, "invalid_redistribution_allowed"),
    }
    missing_governance: list[str] = []
    for field, (allowed, code) in governance.items():
        if field not in document:
            missing_governance.append(field)
            continue
        value = document.get(field)
        if not isinstance(value, str) or value not in allowed:
            errors.append(_issue(code, f"{path}.{field}", f"invalid {field}"))
    if missing_governance:
        warnings.append(_issue(
            "source_governance_missing",
            path,
            "legacy source omits governance fields: " + ",".join(missing_governance),
        ))
    original_url = str(document.get("original_url") or "")
    if original_url and not original_url.startswith(("http://", "https://")):
        errors.append(_issue("invalid_source_url", path, "original_url must use http or https"))
    document_id = str(document.get("document_id") or "")
    local_path = document.get("local_path")
    if "local_path" not in document or local_path is None:
        warnings.append(_issue(
            "source_capture_missing",
            path,
            "source has metadata but no local capture/hash; exact quotation is unavailable",
        ))
        return document_id, None
    parts = _local_path_parts(local_path)
    if parts is None:
        errors.append(_issue(
            "source_path_unsafe",
            path,
            "local_path must be a non-empty portable relative path without dot or empty components",
        ))
        return document_id, None
    if secure_open_error is not None:
        errors.append(_issue(
            "source_secure_open_unavailable",
            path,
            "secure no-follow source capture is unavailable: " + secure_open_error,
        ))
        return document_id, None
    if base_fd is None:
        errors.append(_issue("source_path_unsafe", path, "base_dir could not be opened securely"))
        return document_id, None
    try:
        source_bytes = _read_anchored_regular_file(base_fd, parts)
    except (OSError, ValueError):
        errors.append(_issue(
            "source_path_unsafe",
            path,
            "local source capture failed anchored no-follow regular-file validation",
        ))
        return document_id, None
    if str(document.get("content_sha256") or "") != hashlib.sha256(source_bytes).hexdigest():
        errors.append(_issue("source_hash_mismatch", path, "content_sha256 does not match captured bytes"))
    return document_id, source_bytes.decode("utf-8", errors="replace")


def _semantic_source_ids(
    documents: list[JsonDict],
    quotes: list[JsonDict],
    claims: list[JsonDict],
) -> set[str]:
    ids = {
        str(source_id)
        for claim in claims
        for source_id in (claim.get("source_document_ids") or [])
        if str(source_id).strip()
    }
    ids.update(
        str(quote.get("document_id"))
        for quote in quotes
        if str(quote.get("document_id") or "").strip()
    )
    return ids & {str(row.get("document_id")) for row in documents}


def _validate_source_time_access(
    index: int,
    document: JsonDict,
    *,
    bundle_as_of: datetime | None,
    enforce_time_order: bool,
    semantic_source_ids: set[str],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    path = f"source_documents[{index}]"
    published_at = _parse_source_time(document.get("published_at"), allow_date_only=True)
    if published_at is None:
        errors.append(_issue(
            "source_published_at_invalid",
            f"{path}.published_at",
            "published_at must be YYYY-MM-DD or a timezone-aware timestamp",
        ))
    retrieved_at: datetime | None = None
    if "retrieved_at" in document:
        retrieved_at = _parse_source_time(document.get("retrieved_at"), allow_date_only=False)
        if retrieved_at is None:
            errors.append(_issue(
                "source_retrieved_at_invalid",
                f"{path}.retrieved_at",
                "retrieved_at must be a timezone-aware timestamp",
            ))
    if enforce_time_order and published_at is not None and retrieved_at is not None and published_at > retrieved_at:
        errors.append(_issue(
            "source_time_order_invalid",
            path,
            "published_at must not be later than retrieved_at",
        ))
    last_source_time = retrieved_at or published_at
    if enforce_time_order and bundle_as_of is not None and last_source_time is not None and last_source_time > bundle_as_of:
        errors.append(_issue(
            "source_time_order_invalid",
            path,
            "source publication/retrieval time must not be later than bundle as_of",
        ))

    if "access_state" not in document:
        return
    access_state = str(document.get("access_state") or "").strip()
    if access_state not in ACCESS_STATES:
        errors.append(_issue(
            "source_access_state_invalid",
            f"{path}.access_state",
            "access_state must be public, partial, subscriber_only, or blocked",
        ))
    elif access_state == "partial":
        warnings.append(_issue(
            "source_access_partial",
            f"{path}.access_state",
            "partial access caps provenance readiness and cannot be material evidence",
        ))
    elif access_state in {"subscriber_only", "blocked"} and str(document.get("document_id")) in semantic_source_ids:
        errors.append(_issue(
            "source_access_blocked",
            f"{path}.access_state",
            "subscriber-only or blocked sources cannot support semantic claims or quotes",
        ))


def _validate_sources(
    documents: list[JsonDict],
    quotes: list[JsonDict],
    claims: list[JsonDict],
    bundle_as_of: datetime | None,
    enforce_time_order: bool,
    root: Path,
    errors: list[Issue],
    warnings: list[Issue],
) -> dict[str, str]:
    source_text_by_id: dict[str, str] = {}
    secure_open_error = _secure_open_capability_error()
    base_fd: int | None = None
    if secure_open_error is None and any(document.get("local_path") for document in documents):
        try:
            base_fd = _base_directory_fd(root)
        except (OSError, ValueError):
            base_fd = None
    try:
        semantic_source_ids = _semantic_source_ids(documents, quotes, claims)
        for index, document in enumerate(documents):
            _validate_source_time_access(
                index,
                document,
                bundle_as_of=bundle_as_of,
                enforce_time_order=enforce_time_order,
                semantic_source_ids=semantic_source_ids,
                errors=errors,
                warnings=warnings,
            )
            document_id, source_text = _capture_source(
                index,
                document,
                base_fd,
                secure_open_error,
                errors,
                warnings,
            )
            if source_text is not None:
                source_text_by_id[document_id] = source_text
    finally:
        if base_fd is not None:
            os.close(base_fd)
    return source_text_by_id


def _validate_public_quote(
    quote: JsonDict,
    document: JsonDict,
    verbatim: str,
    path: str,
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    if quote.get("quote_use") != "public_excerpt":
        return
    if document.get("redistribution_allowed") != "yes":
        warnings.append(_issue(
            "redistribution_not_confirmed",
            path,
            "public excerpt lacks confirmed redistribution permission; keep it internal or shorten/attribute",
        ))
    if len(verbatim) > MAX_PUBLIC_EXCERPT_CHARS:
        errors.append(_issue(
            "public_excerpt_too_long",
            path,
            f"public excerpts must be <= {MAX_PUBLIC_EXCERPT_CHARS} characters",
        ))


def _validate_locator(quote: JsonDict, source_text: str, verbatim: str, path: str, errors: list[Issue]) -> None:
    locator = quote.get("locator") or {}
    line_start = locator.get("line_start")
    line_end = locator.get("line_end", line_start)
    lines = source_text.splitlines()
    valid = (
        isinstance(line_start, int)
        and isinstance(line_end, int)
        and 1 <= line_start <= line_end <= len(lines)
    )
    excerpt = ""
    if valid and isinstance(line_start, int) and isinstance(line_end, int):
        excerpt = "\n".join(lines[line_start - 1:line_end])
    if not valid or _normalize_whitespace(verbatim) not in _normalize_whitespace(excerpt):
        errors.append(_issue("quote_locator_mismatch", path, "locator line range must contain verbatim_text"))


def _validate_quote(
    index: int,
    quote: JsonDict,
    document_by_id: dict[Any, JsonDict],
    source_text_by_id: dict[str, str],
    attested_quote_ids: set[str],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    path = f"quote_anchors[{index}]"
    missing = _missing_text_fields(quote, ("document_id", "verbatim_text", "speaker"))
    if not isinstance(quote.get("locator"), dict):
        missing.append("locator")
    if missing:
        errors.append(_issue(
            "quote_required_field_missing",
            path,
            "missing or invalid quote fields: " + ",".join(missing),
        ))
    document_id = str(quote.get("document_id") or "")
    document = document_by_id.get(document_id)
    if not document:
        errors.append(_issue("quote_document_missing", path, "quote references an unknown source document"))
        return
    if not document.get("local_path"):
        quote_id = str(quote.get("quote_id") or "")
        if quote_id in attested_quote_ids:
            verbatim = str(quote.get("verbatim_text") or "")
            digest = str(quote.get("verbatim_sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != hashlib.sha256(
                verbatim.encode("utf-8")
            ).hexdigest():
                errors.append(_issue(
                    "quote_attestation_hash_mismatch",
                    path,
                    "attested quote hash must match the exact installed verbatim_text",
                ))
            if quote.get("quote_use") not in QUOTE_USES:
                errors.append(_issue(
                    "invalid_quote_use",
                    path,
                    "quote_use must be internal_evidence or public_excerpt",
                ))
            _validate_public_quote(quote, document, verbatim, path, errors, warnings)
            if not _nonempty_locator(quote.get("locator")):
                errors.append(_issue(
                    "quote_attestation_locator_missing",
                    path,
                    "attested quote requires a nonblank review-index/DOM locator",
                ))
            warnings.append(_issue(
                "quote_private_capture_attested",
                path,
                "quote is hash-bound to a reviewed private capture; bytes are not distributed and readiness stays partial",
            ))
            return
        errors.append(_issue("quote_source_not_captured", path, "direct quote requires a local source capture"))
        return
    source_text = source_text_by_id.get(document_id)
    if source_text is None:
        return
    verbatim = str(quote.get("verbatim_text") or "")
    if quote.get("quote_use") not in QUOTE_USES:
        errors.append(_issue("invalid_quote_use", path, "quote_use must be internal_evidence or public_excerpt"))
    _validate_public_quote(quote, document, verbatim, path, errors, warnings)
    if not verbatim or _normalize_whitespace(verbatim) not in _normalize_whitespace(source_text):
        errors.append(_issue("quote_not_exact", path, "stitched, changed, or ellipsized quotes are forbidden"))
        return
    _validate_locator(quote, source_text, verbatim, path, errors)


def _validate_quotes(
    quotes: list[JsonDict],
    documents: list[JsonDict],
    source_text_by_id: dict[str, str],
    attested_quote_ids: set[str],
    errors: list[Issue],
    warnings: list[Issue],
) -> None:
    document_by_id = {row.get("document_id"): row for row in documents}
    for index, quote in enumerate(quotes):
        _validate_quote(
            index,
            quote,
            document_by_id,
            source_text_by_id,
            attested_quote_ids,
            errors,
            warnings,
        )


def _validate_framework(index: int, framework: JsonDict, quote_ids: set[Any], errors: list[Issue]) -> None:
    path = f"framework_claims[{index}]"
    missing = _missing_text_fields(framework, ("statement", "derivation_note", "scope"))
    invalid_lists = _invalid_list_fields(framework, ("quote_anchor_ids", "counterevidence_refs"))
    if missing or invalid_lists:
        errors.append(_issue(
            "framework_required_field_missing",
            path,
            "missing or invalid framework fields: " + ",".join(missing + invalid_lists),
        ))
    if framework.get("status") not in FRAMEWORK_STATUSES:
        errors.append(_issue("invalid_framework_status", path, "invalid framework status"))
    quote_refs = framework.get("quote_anchor_ids") or []
    if framework.get("status") in {"supported", "mixed"}:
        if not quote_refs or any(item not in quote_ids for item in quote_refs):
            errors.append(_issue(
                "framework_quote_missing",
                path,
                "supported or mixed framework claims require an existing QuoteAnchor",
            ))
    counter_status = framework.get("counterevidence_status")
    if counter_status not in COUNTEREVIDENCE_STATUSES:
        errors.append(_issue("invalid_counterevidence_status", path, "invalid counterevidence_status"))
    if counter_status in {None, "", "not_checked"}:
        errors.append(_issue(
            "framework_counterevidence_not_checked",
            path,
            "framework claims require a documented disconfirming-evidence search",
        ))
    if framework.get("status") == "supported" and counter_status == "contradicted":
        errors.append(_issue(
            "framework_status_conflict",
            path,
            "a contradicted framework must be mixed, rejected, or needs_verification",
        ))


def _validate_frameworks(frameworks: list[JsonDict], quote_ids: set[Any], errors: list[Issue]) -> None:
    for index, framework in enumerate(frameworks):
        _validate_framework(index, framework, quote_ids, errors)


def _validate_conclusion_claim(claim: JsonDict, path: str, errors: list[Issue]) -> None:
    if claim.get("decision_use") != "supports_conclusion":
        return
    if not str(claim.get("falsifier") or "").strip():
        errors.append(_issue("analysis_falsifier_missing", path, "conclusion claims require an explicit falsifier"))
    if claim.get("counterevidence_status") in {None, "", "not_checked"}:
        errors.append(_issue(
            "analysis_counterevidence_not_checked",
            path,
            "conclusion claims require a documented disconfirming-evidence search",
        ))
    if claim.get("counterevidence_status") in {"mixed", "contradicted"}:
        errors.append(_issue(
            "analysis_counterevidence_conflict",
            path,
            "mixed or contradicted counterevidence cannot support a durable conclusion",
        ))


def _validate_analysis_refs(
    claim: JsonDict,
    path: str,
    document_ids: set[Any],
    quote_ids: set[Any],
    errors: list[Issue],
) -> None:
    mode = claim.get("provenance_mode")
    if mode not in PROVENANCE_MODES:
        errors.append(_issue("invalid_provenance_mode", path, "invalid provenance_mode"))
    if claim.get("decision_use") not in DECISION_USES:
        errors.append(_issue("invalid_decision_use", path, "invalid decision_use"))
    counter_status = claim.get("counterevidence_status")
    if counter_status is not None and counter_status not in COUNTEREVIDENCE_STATUSES:
        errors.append(_issue("invalid_counterevidence_status", path, "invalid counterevidence_status"))
    if mode == "source_summary":
        refs = claim.get("source_document_ids") or []
        if not refs or any(item not in document_ids for item in refs):
            errors.append(_issue("analysis_source_missing", path, "source_summary requires a SourceDocument"))
    if mode == "direct_quote":
        refs = claim.get("quote_anchor_ids") or []
        if not refs or any(item not in quote_ids for item in refs):
            errors.append(_issue("analysis_quote_missing", path, "direct_quote requires an exact QuoteAnchor"))
    if mode == "unverified" and claim.get("decision_use") == "supports_conclusion":
        errors.append(_issue("unverified_claim_supports_conclusion", path, "unverified claims cannot support a conclusion"))


def _validate_framework_inference(
    claim: JsonDict,
    path: str,
    framework_by_id: dict[Any, JsonDict],
    errors: list[Issue],
) -> None:
    if claim.get("provenance_mode") != "framework_inference":
        return
    refs = claim.get("framework_claim_ids") or []
    if not refs or any(item not in framework_by_id for item in refs):
        errors.append(_issue("analysis_framework_missing", path, "framework inference requires an existing claim"))
        return
    unusable = [item for item in refs if framework_by_id[item].get("status") in {"rejected", "needs_verification"}]
    if unusable:
        errors.append(_issue("analysis_framework_not_usable", path, "rejected/unverified framework cannot support inference"))


def _validate_analysis_claims(
    claims: list[JsonDict],
    documents: list[JsonDict],
    quotes: list[JsonDict],
    frameworks: list[JsonDict],
    errors: list[Issue],
) -> None:
    document_ids = {row.get("document_id") for row in documents}
    quote_ids = {row.get("quote_id") for row in quotes}
    framework_by_id = {row.get("framework_claim_id"): row for row in frameworks}
    for index, claim in enumerate(claims):
        path = f"analysis_claims[{index}]"
        missing = _missing_text_fields(claim, ("statement",))
        invalid_lists = _invalid_list_fields(claim, (
            "source_document_ids",
            "quote_anchor_ids",
            "framework_claim_ids",
            "evidence_refs",
            "counterevidence_refs",
        ))
        if missing or invalid_lists:
            errors.append(_issue(
                "analysis_required_field_missing",
                path,
                "missing or invalid analysis fields: " + ",".join(missing + invalid_lists),
            ))
        _validate_conclusion_claim(claim, path, errors)
        _validate_analysis_refs(claim, path, document_ids, quote_ids, errors)
        _validate_framework_inference(claim, path, framework_by_id, errors)


def _validate_behavior(index: int, check: JsonDict, analysis_ids: set[Any], errors: list[Issue]) -> None:
    path = f"behavior_cross_checks[{index}]"
    missing = _missing_text_fields(check, ("statement_claim_id",))
    invalid_lists = _invalid_list_fields(check, ("behavior_evidence_refs",))
    if missing or invalid_lists:
        errors.append(_issue(
            "behavior_required_field_missing",
            path,
            "missing or invalid behavior fields: " + ",".join(missing + invalid_lists),
        ))
    if check.get("result") not in BEHAVIOR_RESULTS:
        errors.append(_issue("invalid_behavior_result", path, "invalid behavior cross-check result"))
    if check.get("statement_claim_id") not in analysis_ids:
        errors.append(_issue("behavior_statement_missing", path, "behavior check requires an analysis claim"))
    if not check.get("behavior_evidence_refs"):
        errors.append(_issue("behavior_evidence_missing", path, "behavior check requires disclosed behavior evidence"))
    if check.get("causality_claimed") is not False:
        errors.append(_issue(
            "behavior_causality_forbidden",
            path,
            "disclosed behavior may corroborate/contradict but cannot prove causality",
        ))
    if check.get("result") not in {"supports", "contradicts", "mixed"}:
        return
    aligned = (
        check.get("subject_match") == "confirmed"
        and check.get("temporal_alignment") in {"aligned", "lagged"}
        and check.get("tenure_alignment") in {"confirmed", "not_applicable"}
    )
    if not aligned:
        errors.append(_issue(
            "behavior_alignment_insufficient",
            path,
            "supports/contradicts/mixed requires subject, time, and tenure alignment",
        ))


def _validate_behaviors(checks: list[JsonDict], claims: list[JsonDict], errors: list[Issue]) -> None:
    analysis_ids = {row.get("claim_id") for row in claims}
    for index, check in enumerate(checks):
        _validate_behavior(index, check, analysis_ids, errors)


def _duplicate_values(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _coverage_list(
    coverage: JsonDict,
    field: str,
    errors: list[Issue],
) -> list[str]:
    value = coverage.get(field)
    if field not in coverage or not _is_explicit_string_list(value):
        errors.append(_issue(
            "claim_coverage_field_invalid",
            f"claim_coverage.{field}",
            f"{field} must be an explicit array of non-empty stable IDs",
        ))
        return []
    normalized = [str(item).strip() for item in value]
    duplicates = _duplicate_values(normalized)
    if duplicates:
        errors.append(_issue(
            "claim_coverage_duplicate_id",
            f"claim_coverage.{field}",
            "coverage ID arrays cannot contain duplicates: " + ",".join(sorted(duplicates)),
        ))
    return normalized


def _framework_source_closure(
    framework: JsonDict,
    quote_by_id: dict[str, JsonDict],
) -> set[str]:
    source_ids = {
        str(value)
        for value in (framework.get("evidence_source_document_ids") or [])
        if str(value).strip()
    }
    source_ids.update(
        str(quote_by_id.get(str(quote_id), {}).get("document_id"))
        for quote_id in (framework.get("quote_anchor_ids") or [])
        if str(quote_by_id.get(str(quote_id), {}).get("document_id") or "").strip()
    )
    return source_ids


def _claim_source_closure(
    claim: JsonDict,
    quote_by_id: dict[str, JsonDict],
    framework_by_id: dict[str, JsonDict],
) -> set[str]:
    source_ids = {
        str(value)
        for value in (claim.get("source_document_ids") or [])
        if str(value).strip()
    }
    source_ids.update(
        str(quote_by_id.get(str(quote_id), {}).get("document_id"))
        for quote_id in (claim.get("quote_anchor_ids") or [])
        if str(quote_by_id.get(str(quote_id), {}).get("document_id") or "").strip()
    )
    for framework_id in claim.get("framework_claim_ids") or []:
        framework = framework_by_id.get(str(framework_id))
        if framework is not None:
            source_ids.update(_framework_source_closure(framework, quote_by_id))
    return source_ids


def _validate_bundle_purpose_and_coverage(
    bundle: JsonDict,
    documents: list[JsonDict],
    quotes: list[JsonDict],
    frameworks: list[JsonDict],
    claims: list[JsonDict],
    checks: list[JsonDict],
    cards: list[JsonDict],
    errors: list[Issue],
    warnings: list[Issue],
) -> JsonDict | None:
    purpose_present = "bundle_purpose" in bundle
    coverage_present = "claim_coverage" in bundle
    if not purpose_present and not coverage_present:
        return None
    if not purpose_present:
        errors.append(_issue(
            "bundle_purpose_missing",
            "bundle_purpose",
            "claim_coverage requires an explicit bundle_purpose",
        ))
        return None
    if not coverage_present:
        errors.append(_issue(
            "claim_coverage_missing",
            "claim_coverage",
            "bundle_purpose requires bound claim_coverage",
        ))
        return None
    purpose = bundle.get("bundle_purpose")
    coverage = bundle.get("claim_coverage")
    if not isinstance(purpose, dict):
        errors.append(_issue("bundle_purpose_invalid", "bundle_purpose", "bundle_purpose must be an object"))
        return None
    if not isinstance(coverage, dict):
        errors.append(_issue("claim_coverage_invalid", "claim_coverage", "claim_coverage must be an object"))
        return None
    purpose_kind = str(purpose.get("kind") or "").strip()
    if purpose_kind not in BUNDLE_PURPOSE_KINDS:
        errors.append(_issue(
            "bundle_purpose_kind_invalid",
            "bundle_purpose.kind",
            "purpose kind must be report_evidence or kol_method_handoff",
        ))
    target_ids = purpose.get("target_ids")
    if not _is_explicit_string_list(target_ids) or not target_ids:
        errors.append(_issue(
            "bundle_purpose_target_missing",
            "bundle_purpose.target_ids",
            "bundle purpose requires one or more target IDs",
        ))
        normalized_targets: list[str] = []
    else:
        normalized_targets = [str(item).strip() for item in target_ids]
        duplicates = _duplicate_values(normalized_targets)
        if duplicates:
            errors.append(_issue(
                "bundle_purpose_target_duplicate",
                "bundle_purpose.target_ids",
                "target IDs must be unique: " + ",".join(sorted(duplicates)),
            ))
    target_binding = str(coverage.get("target_binding") or "").strip()
    if not target_binding or target_binding not in normalized_targets:
        errors.append(_issue(
            "claim_coverage_target_mismatch",
            "claim_coverage.target_binding",
            "target_binding must match exactly one bundle_purpose target ID",
        ))
    card_by_id = {
        str(row.get("method_card_id")): row
        for row in cards
        if row.get("method_card_id")
    }
    card_ids = set(card_by_id)
    if purpose_kind == "kol_method_handoff":
        if len(normalized_targets) != 1 or target_binding not in card_ids:
            errors.append(_issue(
                "kol_target_binding_missing",
                "claim_coverage.target_binding",
                "KOL handoff target_binding must name exactly one installed method card",
            ))

    field_names = (
        "source_document_ids", "framework_claim_ids", "analysis_claim_ids",
        "accepted_claim_ids", "watch_only_claim_ids", "context_only_claim_ids",
        "rejected_claim_ids", "canonical_eids",
    )
    values = {field: _coverage_list(coverage, field, errors) for field in field_names}
    if purpose_kind == "kol_method_handoff" and target_binding in card_by_id:
        target_source_ids = {
            str(value)
            for value in (card_by_id[target_binding].get("source_document_ids") or [])
            if str(value).strip()
        }
        if set(values["source_document_ids"]) != target_source_ids:
            errors.append(_issue(
                "kol_target_source_binding_mismatch",
                "claim_coverage.source_document_ids",
                "KOL claim coverage must bind exactly the target method card's source documents",
            ))
    covered_claim_ids = values["analysis_claim_ids"]
    if not covered_claim_ids:
        errors.append(_issue(
            "claim_coverage_empty",
            "claim_coverage.analysis_claim_ids",
            "purpose-bound claim coverage must include at least one analysis claim",
        ))

    document_ids = {str(row.get("document_id")) for row in documents}
    framework_ids = {str(row.get("framework_claim_id")) for row in frameworks}
    claim_by_id = {str(row.get("claim_id")): row for row in claims}
    quote_by_id = {str(row.get("quote_id")): row for row in quotes}
    framework_by_id = {str(row.get("framework_claim_id")): row for row in frameworks}
    missing_documents = set(values["source_document_ids"]) - document_ids
    if missing_documents:
        errors.append(_issue(
            "claim_coverage_source_missing",
            "claim_coverage.source_document_ids",
            "coverage references unknown source documents: " + ",".join(sorted(missing_documents)),
        ))
    missing_frameworks = set(values["framework_claim_ids"]) - framework_ids
    if missing_frameworks:
        errors.append(_issue(
            "claim_coverage_framework_missing",
            "claim_coverage.framework_claim_ids",
            "coverage references unknown framework claims: " + ",".join(sorted(missing_frameworks)),
        ))
    missing_claims = set(covered_claim_ids) - set(claim_by_id)
    if missing_claims:
        errors.append(_issue(
            "claim_coverage_claim_missing",
            "claim_coverage.analysis_claim_ids",
            "coverage references unknown analysis claims: " + ",".join(sorted(missing_claims)),
        ))

    covered_existing = {claim_id for claim_id in covered_claim_ids if claim_id in claim_by_id}
    covered_claims = [claim_by_id[claim_id] for claim_id in covered_existing]
    required_source_ids: set[str] = set()
    required_framework_ids: set[str] = set()
    for claim in covered_claims:
        required_source_ids.update(_claim_source_closure(claim, quote_by_id, framework_by_id))
        required_framework_ids.update(
            str(value) for value in (claim.get("framework_claim_ids") or []) if str(value).strip()
        )
    declared_source_ids = set(values["source_document_ids"])
    if required_source_ids - declared_source_ids:
        errors.append(_issue(
            "claim_coverage_source_incomplete",
            "claim_coverage.source_document_ids",
            "coverage omits source documents reachable from covered claims: "
            + ",".join(sorted(required_source_ids - declared_source_ids)),
        ))
    if declared_source_ids - required_source_ids:
        errors.append(_issue(
            "claim_coverage_source_unbound",
            "claim_coverage.source_document_ids",
            "coverage includes source documents unreachable from covered claims: "
            + ",".join(sorted(declared_source_ids - required_source_ids)),
        ))
    if required_framework_ids - set(values["framework_claim_ids"]):
        errors.append(_issue(
            "claim_coverage_framework_unbound",
            "claim_coverage.framework_claim_ids",
            "coverage omits framework claims referenced by covered analysis claims",
        ))
    expected_watch = {
        claim_id for claim_id in covered_existing
        if claim_by_id[claim_id].get("decision_use") == "watch_only"
    }
    expected_context = {
        claim_id for claim_id in covered_existing
        if claim_by_id[claim_id].get("decision_use") == "context_only"
    }
    declared_watch = set(values["watch_only_claim_ids"])
    declared_context = set(values["context_only_claim_ids"])
    if declared_watch != expected_watch or declared_context != expected_context:
        errors.append(_issue(
            "claim_coverage_decision_use_mismatch",
            "claim_coverage",
            "watch_only/context_only coverage must exactly match AnalysisClaim decision_use",
        ))

    accepted = set(values["accepted_claim_ids"])
    rejected = set(values["rejected_claim_ids"])
    if accepted & rejected:
        errors.append(_issue(
            "claim_coverage_accept_reject_overlap",
            "claim_coverage",
            "accepted and rejected claim IDs must be disjoint",
        ))
    if (accepted | rejected | declared_watch | declared_context) - set(covered_claim_ids):
        errors.append(_issue(
            "claim_coverage_classification_unbound",
            "claim_coverage",
            "classified claim IDs must be included in analysis_claim_ids",
        ))
    invalid_accepted = {
        claim_id for claim_id in accepted
        if claim_id not in claim_by_id or claim_by_id[claim_id].get("decision_use") != "supports_conclusion"
    }
    if invalid_accepted:
        errors.append(_issue(
            "claim_coverage_acceptance_invalid",
            "claim_coverage.accepted_claim_ids",
            "only supports_conclusion claims may be accepted: " + ",".join(sorted(invalid_accepted)),
        ))
    purpose_decision_use = str(purpose.get("decision_use") or "").strip()
    covered_uses = {
        str(claim_by_id[claim_id].get("decision_use") or "") for claim_id in covered_existing
    }
    expected_purpose_use = (
        "supports_conclusion" if "supports_conclusion" in covered_uses
        else ("watch_only" if "watch_only" in covered_uses else "context_only")
    )
    if purpose_decision_use not in DECISION_USES or purpose_decision_use != expected_purpose_use:
        errors.append(_issue(
            "bundle_purpose_decision_use_mismatch",
            "bundle_purpose.decision_use",
            "purpose decision_use must match the strongest covered AnalysisClaim decision_use",
        ))
    accepted_eids = {
        str(value)
        for claim_id in accepted
        for value in (claim_by_id.get(claim_id, {}).get("evidence_refs") or [])
        if CANONICAL_EID_PATTERN.fullmatch(str(value))
    }
    if set(values["canonical_eids"]) != accepted_eids:
        errors.append(_issue(
            "claim_coverage_canonical_eid_unbound",
            "claim_coverage.canonical_eids",
            "canonical EIDs must exactly equal canonical evidence_refs on accepted claims",
        ))
    if purpose_kind == "kol_method_handoff" and (
        values["accepted_claim_ids"] or values["canonical_eids"]
    ):
        errors.append(_issue(
            "kol_claim_promotion_forbidden",
            "claim_coverage",
            "KOL handoff cannot carry accepted claims or canonical EIDs",
        ))
    accepted_framework_ids = {
        str(framework_id)
        for claim_id in accepted
        for framework_id in (claim_by_id.get(claim_id, {}).get("framework_claim_ids") or [])
    }
    if any(
        framework_by_id.get(framework_id, {}).get("status") == "mixed"
        or framework_by_id.get(framework_id, {}).get("counterevidence_status") == "mixed"
        for framework_id in accepted_framework_ids
    ):
        warnings.append(_issue(
            "claim_coverage_framework_mixed",
            "claim_coverage.accepted_claim_ids",
            "accepted conclusion depends on mixed framework evidence and remains nonmaterial",
        ))
    accepted_behavior_results = {
        str(check.get("result") or "")
        for check in checks
        if str(check.get("statement_claim_id") or "") in accepted
    }
    if "contradicts" in accepted_behavior_results:
        errors.append(_issue(
            "claim_coverage_behavior_conflict",
            "claim_coverage.accepted_claim_ids",
            "accepted conclusion has a contradictory behavior cross-check",
        ))
    elif "mixed" in accepted_behavior_results:
        warnings.append(_issue(
            "claim_coverage_behavior_mixed",
            "claim_coverage.accepted_claim_ids",
            "accepted conclusion has mixed behavior evidence and remains nonmaterial",
        ))
    expected_conclusion = {
        claim_id for claim_id in covered_existing
        if claim_by_id[claim_id].get("decision_use") == "supports_conclusion"
    }
    if expected_conclusion - (accepted | rejected):
        errors.append(_issue(
            "claim_coverage_conclusion_unclassified",
            "claim_coverage",
            "every covered supports_conclusion claim must be accepted or rejected",
        ))
    return coverage


def _nonempty_locator(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value) and all(str(item).strip() for item in value.values())
    return False


def _validate_source_capture_manifest(
    rows: list[JsonDict],
    documents: list[JsonDict],
    quotes: list[JsonDict],
    frameworks: list[JsonDict],
    claims: list[JsonDict],
    purpose: JsonDict | None,
    coverage: JsonDict | None,
    errors: list[Issue],
) -> set[str]:
    document_by_id = {str(row.get("document_id")): row for row in documents}
    claim_by_id = {str(row.get("claim_id")): row for row in claims}
    quote_by_id = {str(row.get("quote_id")): row for row in quotes}
    framework_by_id = {
        str(row.get("framework_claim_id")): row for row in frameworks
    }
    covered_claim_ids = set(coverage.get("analysis_claim_ids") or []) if coverage else set(claim_by_id)
    seen_documents: set[str] = set()
    attested_quote_ids: set[str] = set()
    for index, row in enumerate(rows):
        path = f"source_capture_manifest[{index}]"
        document_id = str(row.get("document_id") or "").strip()
        if not document_id or document_id not in document_by_id:
            errors.append(_issue(
                "capture_manifest_document_missing",
                f"{path}.document_id",
                "manifest row must bind an existing SourceDocument",
            ))
            continue
        if document_id in seen_documents:
            errors.append(_issue(
                "capture_manifest_document_duplicate",
                f"{path}.document_id",
                "each SourceDocument may have at most one capture manifest row",
            ))
        seen_documents.add(document_id)
        document = document_by_id[document_id]
        digest = str(row.get("content_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest != str(document.get("content_sha256") or ""):
            errors.append(_issue(
                "capture_manifest_hash_mismatch",
                f"{path}.content_sha256",
                "manifest hash must be a lowercase SHA-256 matching SourceDocument",
            ))
        media_type = str(row.get("media_type") or "").strip().lower()
        if not media_type or "/" not in media_type:
            errors.append(_issue(
                "capture_manifest_media_type_missing",
                f"{path}.media_type",
                "manifest row requires an explicit media type",
            ))
        source_role = str(row.get("source_role") or "").strip()
        if source_role not in SOURCE_ROLES:
            errors.append(_issue(
                "capture_manifest_source_role_invalid",
                f"{path}.source_role",
                "source_role must be semantic or nonsemantic",
            ))
        if not str(row.get("disposition") or "").strip():
            errors.append(_issue(
                "capture_manifest_disposition_missing",
                f"{path}.disposition",
                "manifest row requires an explicit disposition",
            ))
        supported = row.get("supported_claim_ids")
        if not _is_explicit_string_list(supported):
            errors.append(_issue(
                "capture_manifest_claims_invalid",
                f"{path}.supported_claim_ids",
                "supported_claim_ids must be an explicit array of stable claim IDs",
            ))
            supported_ids: list[str] = []
        else:
            supported_ids = [str(item).strip() for item in supported]
        if source_role == "semantic" and not supported_ids:
            errors.append(_issue(
                "capture_manifest_semantic_claims_missing",
                f"{path}.supported_claim_ids",
                "semantic captures require at least one supported claim",
            ))
        if source_role == "nonsemantic" and supported_ids:
            errors.append(_issue(
                "capture_manifest_nonsemantic_claims_forbidden",
                f"{path}.supported_claim_ids",
                "nonsemantic captures cannot support analysis claims",
            ))
        unknown_claims = set(supported_ids) - set(claim_by_id)
        unbound_claims = set(supported_ids) - covered_claim_ids
        if unknown_claims or unbound_claims:
            errors.append(_issue(
                "capture_manifest_claim_missing",
                f"{path}.supported_claim_ids",
                "manifest claim IDs must exist and be included in claim_coverage",
            ))
        source_mismatches = {
            claim_id
            for claim_id in supported_ids
            if claim_id in claim_by_id
            and document_id not in _claim_source_closure(
                claim_by_id[claim_id], quote_by_id, framework_by_id
            )
        }
        if source_mismatches:
            errors.append(_issue(
                "capture_manifest_claim_source_mismatch",
                f"{path}.supported_claim_ids",
                "manifest document is not reachable from supported claims: "
                + ",".join(sorted(source_mismatches)),
            ))
        if source_role == "semantic" and not _nonempty_locator(row.get("review_locator")):
            errors.append(_issue(
                "capture_manifest_review_locator_missing",
                f"{path}.review_locator",
                "semantic captures require a review-index or DOM locator",
            ))
        if media_type.startswith("image/") and media_type != "image/svg+xml" and source_role == "semantic":
            dimensions = row.get("dimensions")
            if not (
                isinstance(dimensions, dict)
                and isinstance(dimensions.get("width"), int) and dimensions["width"] > 0
                and isinstance(dimensions.get("height"), int) and dimensions["height"] > 0
            ):
                errors.append(_issue(
                    "capture_manifest_image_dimensions_missing",
                    f"{path}.dimensions",
                    "semantic raster captures require positive integer width and height",
                ))
        if media_type == "text/plain" and supported_ids:
            for claim_id in supported_ids:
                claim = claim_by_id.get(claim_id) or {}
                source_ids = {str(item) for item in (claim.get("source_document_ids") or [])}
                has_image_source = any(
                    str(document_by_id.get(source_id, {}).get("source_type") or "").endswith("image")
                    or str(document_by_id.get(source_id, {}).get("media_type") or "").startswith("image/")
                    for source_id in source_ids
                )
                if document_id not in source_ids and has_image_source:
                    errors.append(_issue(
                        "capture_manifest_text_image_coverage",
                        f"{path}.supported_claim_ids",
                        "text/plain capture cannot masquerade as coverage for an image-only claim",
                    ))
                    break
        if str(row.get("disposition") or "") == "private_capture_not_distributed":
            raw_quote_ids = row.get("quote_anchor_ids")
            declared_quote_ids = (
                [str(item).strip() for item in raw_quote_ids]
                if _is_explicit_string_list(raw_quote_ids) and raw_quote_ids
                else []
            )
            coverage_is_nonpromoting = bool(
                coverage is not None
                and not coverage.get("accepted_claim_ids")
                and not coverage.get("canonical_eids")
            )
            source_is_private_partial = bool(
                document.get("access_state") == "partial"
                and document.get("capture_bytes_storage_scope") == "private"
                and not document.get("local_path")
            )
            quotes_match_document = bool(
                declared_quote_ids
                and all(
                    quote_id in quote_by_id
                    and str(quote_by_id[quote_id].get("document_id") or "") == document_id
                    for quote_id in declared_quote_ids
                )
            )
            raw_quote_locators = row.get("quote_locators")
            locators_match_attestation = bool(
                isinstance(raw_quote_locators, dict)
                and set(raw_quote_locators) == set(declared_quote_ids)
                and all(
                    isinstance(raw_quote_locators.get(quote_id), dict)
                    and raw_quote_locators[quote_id]
                    == quote_by_id.get(quote_id, {}).get("locator")
                    for quote_id in declared_quote_ids
                )
            )
            if declared_quote_ids and not locators_match_attestation:
                errors.append(_issue(
                    "private_capture_attestation_locator_mismatch",
                    f"{path}.quote_locators",
                    "private quote attestation requires an exact locator map for every quote ID",
                ))
            hash_is_bound = bool(
                re.fullmatch(r"[0-9a-f]{64}", digest)
                and digest == str(document.get("content_sha256") or "")
            )
            attestation_allowed = bool(
                str((purpose or {}).get("kind") or "") == "kol_method_handoff"
                and coverage_is_nonpromoting
                and source_is_private_partial
                and source_role == "semantic"
                and _nonempty_locator(row.get("review_locator"))
                and quotes_match_document
                and locators_match_attestation
                and hash_is_bound
            )
            if attestation_allowed:
                attested_quote_ids.update(declared_quote_ids)
            elif str((purpose or {}).get("kind") or "") == "kol_method_handoff":
                errors.append(_issue(
                    "private_capture_attestation_invalid",
                    path,
                    "private quote attestation requires partial/private undistributed capture, empty accepted/canonical IDs, matching hash/quotes, and review locator",
                ))
    return attested_quote_ids


def _suggested_signal(errors: list[Issue], warnings: list[Issue]) -> JsonDict | None:
    if errors:
        return {
            "module": "research_readiness", "max_action_level": "L0", "position_multiplier": 0.0,
            "hard_veto": False, "tighten_only": True,
            "reason": "provenance_blocked:" + ",".join(sorted({row["code"] for row in errors})),
        }
    if warnings:
        return {
            "module": "research_readiness", "max_action_level": "L1", "position_multiplier": 1.0,
            "hard_veto": False, "tighten_only": True,
            "reason": "provenance_partial:" + ",".join(sorted({row["code"] for row in warnings})),
        }
    return None


def _kol_handoff_signal(cards: list[JsonDict]) -> JsonDict | None:
    if not cards:
        return None
    card_ids = _sorted_ids(cards, "method_card_id")
    reason = "kol_method_card_research_only"
    if card_ids:
        reason += ":" + ",".join(card_ids)
    return {
        "module": "x_frontline",
        "sub_framework": "kol_method_card",
        "max_action_level": "L0",
        "position_multiplier": 0.0,
        "hard_veto": False,
        "tighten_only": True,
        "allowed_effects": ["tighten", "refresh_source", "request_manual_review"],
        "no_order_execution": True,
        "reason": reason,
    }


def _suggested_signals(
    errors: list[Issue],
    warnings: list[Issue],
    cards: list[JsonDict],
) -> tuple[JsonDict | None, list[JsonDict]]:
    generic_signal = _suggested_signal(errors, warnings)
    kol_signal = _kol_handoff_signal(cards)
    signals = [signal for signal in (generic_signal, kol_signal) if signal is not None]
    return kol_signal or generic_signal, signals


def _memory_link(
    digest: str,
    documents: list[JsonDict],
    quotes: list[JsonDict],
    frameworks: list[JsonDict],
    claims: list[JsonDict],
    checks: list[JsonDict],
    cards: list[JsonDict],
    purpose: JsonDict | None,
    coverage: JsonDict | None,
    readiness: str,
    errors: list[Issue],
) -> JsonDict:
    declared_accepted = _sorted_ids(
        [{"claim_id": value} for value in (coverage or {}).get("accepted_claim_ids", [])],
        "claim_id",
    )
    rejected = _sorted_ids(
        [{"claim_id": value} for value in (coverage or {}).get("rejected_claim_ids", [])],
        "claim_id",
    )
    canonical_eids = sorted(str(value) for value in (coverage or {}).get("canonical_eids", []) if value)
    accepted = [] if readiness == "blocked" else declared_accepted
    materiality_eligible = bool(
        readiness == "verified"
        and accepted
        and canonical_eids
        and not cards
        and str((purpose or {}).get("kind") or "") != "kol_method_handoff"
    )
    rejection_payload = {
        "error_codes": sorted({row["code"] for row in errors}),
        "rejected_claim_ids": rejected,
    }
    blocked = readiness == "blocked"
    result = {
        "provenance_bundle_sha256": digest,
        "source_document_ids": [] if blocked else _sorted_ids(documents, "document_id"),
        "quote_anchor_ids": [] if blocked else _sorted_ids(quotes, "quote_id"),
        "framework_claim_ids": [] if blocked else _sorted_ids(frameworks, "framework_claim_id"),
        "analysis_claim_ids": [] if blocked else _sorted_ids(claims, "claim_id"),
        "behavior_cross_check_ids": [] if blocked else _sorted_ids(checks, "cross_check_id"),
        "kol_method_card_ids": [] if blocked else _sorted_ids(cards, "method_card_id"),
        "accepted_claim_ids": accepted,
        "rejected_claim_ids": rejected,
        "canonical_eids": canonical_eids if readiness != "blocked" else [],
        "materiality_eligible": materiality_eligible,
        "admission_status": (
            "blocked_gap_only" if readiness == "blocked"
            else ("partial_watch_only" if readiness == "partial" else "verified")
        ),
    }
    if blocked:
        result["rejection_sha256"] = hashlib.sha256(
            json.dumps(rejection_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return result


def _section_rows(bundle: JsonDict, key: str, errors: list[Issue], *, required: bool = False) -> list[JsonDict]:
    if key not in bundle:
        if required:
            errors.append(_issue("required_section_missing", key, f"required section is absent: {key}"))
        return []
    value = bundle[key]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        errors.append(_issue("invalid_section_type", key, f"{key} must be a JSON array of objects"))
        return []
    return value


def validate_bundle(
    bundle: JsonDict,
    base_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> JsonDict:
    """Return a deterministic validation result without network or write side effects."""
    root = base_dir or Path.cwd()
    errors: list[Issue] = []
    warnings: list[Issue] = []
    documents = _section_rows(bundle, "source_documents", errors, required=True)
    quotes = _section_rows(bundle, "quote_anchors", errors, required=True)
    frameworks = _section_rows(bundle, "framework_claims", errors, required=True)
    claims = _section_rows(bundle, "analysis_claims", errors, required=True)
    checks = _section_rows(bundle, "behavior_cross_checks", errors, required=True)
    cards = _section_rows(bundle, "kol_method_cards", errors)
    manifest = _section_rows(bundle, "source_capture_manifest", errors)
    sections = [
        (documents, "document_id", "source_documents"), (quotes, "quote_id", "quote_anchors"),
        (frameworks, "framework_claim_id", "framework_claims"), (claims, "claim_id", "analysis_claims"),
        (checks, "cross_check_id", "behavior_cross_checks"),
        (cards, "method_card_id", "kol_method_cards"),
    ]
    current = _trusted_now(now)
    _validate_top_level(bundle, sections, errors, now=current)
    bundle_as_of = _parse_source_time(bundle.get("as_of"), allow_date_only=False)
    source_text_by_id = _validate_sources(
        documents,
        quotes,
        claims,
        bundle_as_of,
        bool("bundle_purpose" in bundle or "claim_coverage" in bundle or not cards),
        root,
        errors,
        warnings,
    )
    coverage = _validate_bundle_purpose_and_coverage(
        bundle,
        documents,
        quotes,
        frameworks,
        claims,
        checks,
        cards,
        errors,
        warnings,
    )
    purpose = bundle.get("bundle_purpose") if isinstance(bundle.get("bundle_purpose"), dict) else None
    attested_quote_ids = _validate_source_capture_manifest(
        manifest,
        documents,
        quotes,
        frameworks,
        claims,
        purpose,
        coverage,
        errors,
    )
    _validate_quotes(
        quotes,
        documents,
        source_text_by_id,
        attested_quote_ids,
        errors,
        warnings,
    )
    quote_ids = {row.get("quote_id") for row in quotes}
    _validate_frameworks(frameworks, quote_ids, errors)
    _validate_analysis_claims(claims, documents, quotes, frameworks, errors)
    _validate_behaviors(checks, claims, errors)
    validate_kol_cards(cards, documents, quotes, frameworks, errors, warnings)
    digest = canonical_sha256(bundle)
    readiness = "blocked" if errors else ("partial" if warnings else "verified")
    legacy_signal, signals = _suggested_signals(errors, warnings, cards)
    return {
        "ok": not errors, "schema_version": SCHEMA_VERSION, "provenance_readiness": readiness,
        "errors": errors, "warnings": warnings, "bundle_sha256": digest,
        "bundle_role": "claim_coverage" if coverage is not None else "capture_only",
        "memory_link": _memory_link(
            digest,
            documents,
            quotes,
            frameworks,
            claims,
            checks,
            cards,
            bundle.get("bundle_purpose") if isinstance(bundle.get("bundle_purpose"), dict) else None,
            coverage,
            readiness,
            errors,
        ),
        "suggested_module_signal": legacy_signal,
        "suggested_module_signals": signals,
        "no_order_execution": True,
    }


def _invalid_bundle_result(exc: Exception, code: str = "invalid_bundle_json") -> JsonDict:
    signal = {
        "module": "research_readiness", "max_action_level": "L0", "position_multiplier": 0.0,
        "hard_veto": False, "tighten_only": True, "reason": f"provenance_blocked:{code}",
    }
    return {
        "ok": False, "schema_version": SCHEMA_VERSION, "provenance_readiness": "blocked",
        "errors": [_issue(code, "bundle", str(exc))], "warnings": [],
        "suggested_module_signal": signal,
        "suggested_module_signals": [signal],
        "no_order_execution": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a research_provenance.v1 bundle.")
    parser.add_argument("--bundle", required=True, type=Path, help="Path to the provenance JSON bundle")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)
    bundle_path = args.bundle.resolve()
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        result = _invalid_bundle_result(exc)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2
    if not isinstance(bundle, dict):
        result = _invalid_bundle_result(ValueError("bundle root must be a JSON object"), "invalid_bundle_shape")
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2
    result = validate_bundle(bundle, base_dir=bundle_path.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
