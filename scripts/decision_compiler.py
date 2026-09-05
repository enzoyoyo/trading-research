#!/usr/bin/env python3
"""Decision Compiler for trading-research.

Compiles registered module constraints into separate entry and holding actions.
The compiler never executes orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from functools import reduce
from operator import mul
from typing import Any

ACTION_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
SELL_ACTIONS = {"L4", "L5"}
ALL_ACTIONS = set(ACTION_ORDER) | SELL_ACTIONS
ENTRY_ORDER = {"BLOCK": -1, "WATCH": 0, "TEST": 1, "BUILD": 2, "ADD": 3}
LEVEL_TO_ENTRY = {"L0": "WATCH", "L1": "TEST", "L2": "BUILD", "L3": "ADD"}
ENTRY_TO_LEVEL = {"WATCH": "L0", "TEST": "L1", "BUILD": "L2", "ADD": "L3", "BLOCK": "L0"}
HOLDING_ORDER = {"HOLD": 0, "REDUCE": 1, "EXIT": 2}
SUPPORTED_REQUEST_SCHEMAS = {"decision_request.v2"}
# Keep parser behavior deterministic across Python runtimes. Python 3.11's
# int_max_str_digits rejects very long JSON integers before field validation,
# while older runtimes parse them into hostile unbounded ints. Decision payloads
# have no legitimate need for a 1025-digit integer; return a JSON-safe sentinel
# rather than the raw token so downstream errors cannot echo attacker input.
MAX_JSON_INTEGER_DIGITS = 1024
INVALID_JSON_INTEGER = "__invalid_json_integer__"
REGISTERED_MODULES = {
    "account",
    "a_share_raw_source",
    "brokerage_portfolio_margin",
    "calculation_quality",
    "conflict_ledger",
    "data_quality",
    "dispersion_crowding",
    "endogenous_structure",
    "event_proximity",
    "execution_window",
    "filing",
    "forced_liquidation",
    "fundamentals",
    "gamma",
    "grok_web",
    "ingestion_permission",
    "liquidity",
    "liquidity_squeeze",
    "macro",
    "market_data",
    "modeled_scenario",
    "participant_flow",
    "portfolio_risk_budget",
    "prediction_market_prior",
    "quant_robustness",
    "research_readiness",
    "risk_regime",
    "x_frontline",
}

EPISTEMIC_VETO_MODULES = {
    "calculation_quality",
    "conflict_ledger",
    "data_quality",
    "ingestion_permission",
    "research_readiness",
}

# --- Cap & Tighten-Only Registry (runtime enforcement) ----------------------------
# Single source of truth for the *values* is references/decision-compiler.md,
# "Cap & Tighten-Only Registry" table (lines 62-78) -- do not duplicate numbers here
# without a matching doc row, and do not add a doc row without wiring it below.
#
# Keys are (module, sub_framework); sub_framework=None is the module-wide fallback
# used when no more specific (module, sub_framework) entry matches. Several doc rows
# describe concepts (`kol_method_cards`, `analog_prior`, `overnight_ensemble_ranker`,
# the v2.33 four-state factor classification) that the doc itself says are *not* new
# registered modules -- they compile into an existing REGISTERED_MODULES entry via
# `sub_framework`. The sub_framework spellings below follow the producers that
# already exist in this repo (see scripts/provenance_guard.py:565 for
# "kol_method_card", singular); rows with no producer yet use the doc's own naming.
MODULE_POSITION_MULTIPLIER_CAP: dict[tuple[str, str | None], tuple[float, str]] = {
    ("x_frontline", None): (0.15, "decision-compiler.md:69 x_frontline/KOL 封顶 0.15"),
    ("x_frontline", "political_disclosure"): (0.10, "decision-compiler.md:68 political_disclosure vote 封顶 0.10"),
    ("x_frontline", "kol_method_card"): (0.0, "decision-compiler.md:70 kol_method_cards handoff position_multiplier=0.0"),
    ("research_readiness", "kol_method_card"): (0.0, "decision-compiler.md:70 kol_method_cards handoff position_multiplier=0.0"),
    ("modeled_scenario", None): (0.0, "decision-compiler.md:72 modeled_scenario/analog_prior position_multiplier=0.0"),
    ("modeled_scenario", "analog_prior"): (0.0, "decision-compiler.md:72 modeled_scenario/analog_prior position_multiplier=0.0"),
    ("execution_window", "overnight_ensemble_ranker"): (0.0, "decision-compiler.md:73 overnight_ensemble_ranker 自身 position_multiplier=0.0"),
    ("quant_robustness", "train_only"): (0.0, "decision-compiler.md:75 train_only/noise/reversed_strict position_multiplier=0.0"),
    ("quant_robustness", "noise"): (0.0, "decision-compiler.md:75 train_only/noise/reversed_strict position_multiplier=0.0"),
    ("quant_robustness", "reversed_strict"): (0.0, "decision-compiler.md:75 train_only/noise/reversed_strict position_multiplier=0.0"),
    ("endogenous_structure", "counter_consensus_thesis"): (0.3, "decision-compiler.md:78 counter_consensus_thesis position_multiplier<=0.3"),
}

# research_readiness ceiling table (Mira Quality Gate -> Compiler mapping,
# decision-compiler.md lines 83-93). Only applied to module=="research_readiness"
# signals; readiness_level/knowability_status are read as optional extra fields on
# the signal (not part of the v2 base schema) -- signals that omit them still get
# the unconditional "never independently claim REDUCE/EXIT" ceiling below (rule 9).
READINESS_LEVEL_ACTION_CEILING = {
    "draft": "L0",
    "not_actionable": "L0",
    "needs_refresh": "L0",
    "working_view": "L1",
    "watch_only": "L1",
    "actionable_with_caveats": "L3",  # L2 unless caveat_resolved=true, see _readiness_ceiling
}
KNOWABILITY_ACTION_CEILING = {"irreducible_uncertainty": "L0"}

# Baseline modules a strict decision_request.v2 payload must vouch for when it can
# grant new entry permission. Enforced INSIDE the compiler so a caller cannot shrink
# its own required_modules contract to dodge scrutiny (audit finding:
# required_modules was previously payload self-declared with no floor -- "自己给自己
# 发准考证"). Scoped by decision_context.intent per the "按请求类型/scope 推导" repair
# instruction: non-open intents carry none of the capital-commitment risk this
# floor protects against, so they are exempt only because the result layer forces
# their entry_permission to BLOCK and multiplier to zero. "open" and any
# unrecognized/missing intent fail closed onto the floor. Payloads may declare
# additional required modules beyond this floor; they may not declare fewer when
# the floor applies.
BASELINE_REQUIRED_MODULES: frozenset[str] = frozenset({
    "risk_regime",
    "portfolio_risk_budget",
    "data_quality",
})
INTENT_EXEMPT_FROM_BASELINE_REQUIRED_MODULES: frozenset[str] = frozenset({"research", "hold", "reduce", "exit"})


def _module_multiplier_cap(module: str, sub_framework: str | None) -> tuple[float, str] | None:
    if sub_framework:
        specific = MODULE_POSITION_MULTIPLIER_CAP.get((module, sub_framework))
        if specific is not None:
            return specific
    return MODULE_POSITION_MULTIPLIER_CAP.get((module, None))


def _readiness_ceiling(signal: dict[str, Any]) -> tuple[str, str] | None:
    if str(signal.get("module") or "").strip() != "research_readiness":
        return None
    ceiling = "L3"  # rule 9: research_readiness may inform up to L3 but never REDUCE/EXIT on its own
    doc_ref = "decision-compiler.md rule 9 research_readiness 只降级不加分，不得独立驱动 REDUCE/EXIT"
    readiness_level = str(signal.get("readiness_level") or "").strip()
    if readiness_level in READINESS_LEVEL_ACTION_CEILING:
        candidate = READINESS_LEVEL_ACTION_CEILING[readiness_level]
        if readiness_level == "actionable_with_caveats" and not signal.get("caveat_resolved"):
            candidate = "L2"
        if ACTION_ORDER[candidate] < ACTION_ORDER[ceiling]:
            ceiling = candidate
            doc_ref = f"decision-compiler.md:83-93 readiness_level={readiness_level}"
    knowability = str(signal.get("knowability_status") or "").strip()
    if knowability in KNOWABILITY_ACTION_CEILING:
        candidate = KNOWABILITY_ACTION_CEILING[knowability]
        if ACTION_ORDER[candidate] < ACTION_ORDER[ceiling]:
            ceiling = candidate
            doc_ref = f"decision-compiler.md:90 knowability_status={knowability}"
    return ceiling, doc_ref


HK_DEEP_VALUE_DOC_REF = "decision-compiler.md:76 hk_deep_value_no_catalyst entry_permission 封顶 WATCH"
HK_MOMENTUM_REVIEW_DOC_REF = (
    "decision-compiler.md:77 hk_momentum_drawdown_review 强制止盈/止损复核，复核前不得维持或提高原动作等级"
)
COUNTER_CONSENSUS_DOC_REF = (
    "decision-compiler.md:78 counter_consensus_thesis 缺 falsifier/time_stop 时 entry_permission 封顶 WATCH"
)


def _current_entry(row: dict[str, Any]) -> str:
    explicit = str(row.get("entry_permission") or "").upper()
    if explicit in ENTRY_ORDER:
        return explicit
    return LEVEL_TO_ENTRY.get(str(row.get("max_action_level") or "L0"), "WATCH")


def _current_holding(row: dict[str, Any]) -> str:
    explicit = str(row.get("holding_directive") or "").upper()
    return explicit if explicit in HOLDING_ORDER else "HOLD"


def _hk_overlay_entry_doc_ref(row: dict[str, Any]) -> str | None:
    """v2.45 HK playbook tighten-only flags (Cap & Tighten-Only Registry rows for
    hk_deep_value_no_catalyst / hk_momentum_drawdown_review, decision-compiler.md
    lines 76-77). These are signal-level boolean flags rather than a dedicated
    module -- the doc attaches them to whichever module (fundamentals/
    endogenous_structure/etc.) carries the underlying HK playbook finding -- so
    they are checked on any signal regardless of `module`.
    """
    if row.get("hk_deep_value_no_catalyst"):
        return HK_DEEP_VALUE_DOC_REF
    if row.get("hk_momentum_drawdown_review") and not row.get("review_completed"):
        return HK_MOMENTUM_REVIEW_DOC_REF
    return None


def apply_caps(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Clamp signals to the Cap & Tighten-Only Registry; never raises a value.

    Returns (capped_signals, cap_applied) where cap_applied is a non-silent audit
    trail of every field a signal was clamped on (empty list when nothing fired).
    """
    capped: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    for signal in signals:
        row = dict(signal)
        module = str(row.get("module") or "").strip()
        sub_framework = str(row.get("sub_framework") or "").strip() or None
        label = constraint_label(row)

        multiplier_cap = _module_multiplier_cap(module, sub_framework)
        if multiplier_cap is not None:
            ceiling, doc_ref = multiplier_cap
            requested = clamp_multiplier(row.get("position_multiplier", 1.0))
            if requested > ceiling:
                applied.append({
                    "module": module, "sub_framework": sub_framework, "constraint": label,
                    "field": "position_multiplier", "requested": requested, "capped_to": ceiling,
                    "doc_ref": doc_ref,
                })
                row["position_multiplier"] = ceiling

        readiness = _readiness_ceiling(row)
        if readiness is not None:
            ceiling_level, doc_ref = readiness
            ceiling_entry = LEVEL_TO_ENTRY[ceiling_level]
            level = str(row.get("max_action_level") or "L0")
            if level in SELL_ACTIONS or (level in ACTION_ORDER and ACTION_ORDER[level] > ACTION_ORDER[ceiling_level]):
                applied.append({
                    "module": module, "sub_framework": sub_framework, "constraint": label,
                    "field": "max_action_level", "requested": level, "capped_to": ceiling_level,
                    "doc_ref": doc_ref,
                })
                row["max_action_level"] = ceiling_level
            explicit_entry = str(row.get("entry_permission") or "").upper()
            if explicit_entry in ENTRY_ORDER and ENTRY_ORDER[explicit_entry] > ENTRY_ORDER[ceiling_entry]:
                applied.append({
                    "module": module, "sub_framework": sub_framework, "constraint": label,
                    "field": "entry_permission", "requested": explicit_entry, "capped_to": ceiling_entry,
                    "doc_ref": doc_ref,
                })
                row["entry_permission"] = ceiling_entry
            explicit_holding = str(row.get("holding_directive") or "").upper()
            if explicit_holding in {"REDUCE", "EXIT"}:
                applied.append({
                    "module": module, "sub_framework": sub_framework, "constraint": label,
                    "field": "holding_directive", "requested": explicit_holding, "capped_to": "HOLD",
                    "doc_ref": doc_ref,
                })
                row["holding_directive"] = "HOLD"

        hk_doc_ref = _hk_overlay_entry_doc_ref(row)
        if hk_doc_ref is not None:
            current_entry = _current_entry(row)
            if ENTRY_ORDER[current_entry] > ENTRY_ORDER["WATCH"]:
                applied.append({
                    "module": module, "sub_framework": sub_framework, "constraint": label,
                    "field": "entry_permission", "requested": current_entry, "capped_to": "WATCH",
                    "doc_ref": hk_doc_ref,
                })
                row["entry_permission"] = "WATCH"
        if row.get("hk_momentum_drawdown_review") and not row.get("review_completed"):
            current_holding = _current_holding(row)
            if HOLDING_ORDER[current_holding] < HOLDING_ORDER["REDUCE"]:
                applied.append({
                    "module": module, "sub_framework": sub_framework, "constraint": label,
                    "field": "holding_directive", "requested": current_holding, "capped_to": "REDUCE",
                    "doc_ref": HK_MOMENTUM_REVIEW_DOC_REF,
                })
                row["holding_directive"] = "REDUCE"

        if module == "endogenous_structure" and sub_framework == "counter_consensus_thesis":
            missing_fields = [
                field for field in ("falsifier", "time_stop")
                if not isinstance(row.get(field), str) or not row[field].strip()
            ]
            current_entry = _current_entry(row)
            if missing_fields and ENTRY_ORDER[current_entry] > ENTRY_ORDER["WATCH"]:
                applied.append({
                    "module": module,
                    "sub_framework": sub_framework,
                    "constraint": label,
                    "field": "entry_permission",
                    "requested": current_entry,
                    "capped_to": "WATCH",
                    "doc_ref": COUNTER_CONSENSUS_DOC_REF,
                    "missing_fields": missing_fields,
                })
                row["entry_permission"] = "WATCH"

        capped.append(row)

    return capped, applied


def constraint_label(signal: dict[str, Any]) -> str | None:
    module = str(signal.get("module") or "").strip()
    if not module:
        return None
    sub = str(signal.get("sub_framework") or "").strip()
    return f"{module}:{sub}" if sub else module


def signal_key(signal: dict[str, Any]) -> str:
    return constraint_label(signal) or "<missing>"


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_multiplier(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0.0:
        return None
    if parsed == 0.0 and math.copysign(1.0, parsed) < 0.0:
        return None
    return parsed


def clamp_multiplier(value: Any) -> float:
    parsed = _finite_multiplier(value)
    if parsed is None:
        return 0.0
    return max(0.0, min(1.0, parsed))


def aggregate_position_multipliers(
    signals: list[dict[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    """Take the tightest multiplier inside a module, then multiply modules.

    Multiple overlays can compile into the same registered module and are often
    correlated views of the same underlying facts. Multiplying them separately
    double-counts that risk. The non-selected rows remain visible in the audit
    trail so aggregation is deterministic and reviewable.
    """
    grouped: dict[str, list[tuple[int, dict[str, Any], float]]] = {}
    for index, signal in enumerate(signals):
        module = str(signal.get("module") or "").strip()
        grouped.setdefault(module, []).append(
            (index, signal, clamp_multiplier(signal.get("position_multiplier", 1.0)))
        )

    module_multipliers: list[float] = []
    audit: list[dict[str, Any]] = []
    for module, rows in grouped.items():
        selected_index, selected_signal, module_min = min(
            rows,
            key=lambda item: (item[2], constraint_label(item[1]) or "", item[0]),
        )
        module_multipliers.append(module_min)
        if len(rows) == 1:
            continue
        selected_constraint = constraint_label(selected_signal)
        for index, signal, requested in rows:
            if index == selected_index:
                continue
            audit.append({
                "module": module,
                "sub_framework": str(signal.get("sub_framework") or "").strip() or None,
                "constraint": constraint_label(signal),
                "field": "position_multiplier",
                "requested": requested,
                "capped_to": module_min,
                "doc_ref": "decision-compiler.md rule 4 same-module min aggregation",
                "superseded_by_min": True,
                "selected_constraint": selected_constraint,
            })

    final_multiplier = reduce(mul, module_multipliers, 1.0)
    return max(0.0, min(1.0, round(final_multiplier, 4))), audit


def level_to_axes(signal: dict[str, Any]) -> tuple[str, str, str]:
    level = str(signal.get("max_action_level") or "L0")
    explicit_entry = str(signal.get("entry_permission") or "").upper()
    explicit_holding = str(signal.get("holding_directive") or "").upper()

    if level == "L4":
        derived_entry, derived_holding = "BLOCK", "REDUCE"
    elif level == "L5":
        derived_entry, derived_holding = "BLOCK", "EXIT"
    else:
        derived_entry = LEVEL_TO_ENTRY.get(level, "WATCH")
        derived_holding = "HOLD"

    entry = explicit_entry if explicit_entry in ENTRY_ORDER else derived_entry
    holding = explicit_holding if explicit_holding in HOLDING_ORDER else derived_holding
    return level, entry, holding



def _evidence_refs_errors(signal: dict[str, Any], key: str) -> list[str]:
    """Strict-v2 evidence refs must be non-empty text ids.

    Parser sentinels, non-strings, blanks, and mixed invalid items must not mint
    open authority via a presence-only truthiness check.
    """
    evidence_refs = signal.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        return [f"missing_evidence_refs:{key}"]
    errors: list[str] = []
    seen_valid = False
    for item in evidence_refs:
        if not isinstance(item, str):
            errors.append(f"invalid_evidence_refs:{key}")
            continue
        if item == INVALID_JSON_INTEGER:
            errors.append(f"invalid_evidence_refs:{key}")
            continue
        if not item.strip():
            errors.append(f"invalid_evidence_refs:{key}")
            continue
        seen_valid = True
    if errors:
        # one error code is enough for the signal key
        return [f"invalid_evidence_refs:{key}"]
    if not seen_valid:
        return [f"missing_evidence_refs:{key}"]
    return []


def validate_payload(
    payload: dict[str, Any],
    signals: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    schema = payload.get("schema_version")
    strict = schema is not None
    raw_context = payload.get("decision_context")
    context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}

    if strict and schema not in SUPPORTED_REQUEST_SCHEMAS:
        errors.append(f"unsupported_schema:{schema}")

    seen: set[str] = set()
    modules: set[str] = set()
    evaluated_at = now or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    else:
        evaluated_at = evaluated_at.astimezone(timezone.utc)
    context_as_of = parse_dt(context.get("as_of"))
    as_of = context_as_of or evaluated_at

    if strict:
        if context.get("query_tier") not in {"T0", "T1", "T2"}:
            errors.append("invalid_query_tier")
        if context.get("intent") not in {"research", "open", "hold", "reduce", "exit"}:
            errors.append("invalid_intent")
        if not isinstance(context.get("has_position"), bool):
            errors.append("invalid_has_position")
        if context_as_of is None:
            errors.append("invalid_decision_context_as_of")
        elif context_as_of > evaluated_at:
            errors.append("future_decision_context_as_of")

    for signal in signals:
        module = str(signal.get("module") or "").strip()
        if not module:
            errors.append("missing_module")
            continue
        modules.add(module)
        if module not in REGISTERED_MODULES:
            errors.append(f"unknown_module:{module}")

        level = str(signal.get("max_action_level") or "")
        if level not in ALL_ACTIONS:
            errors.append(f"invalid_action_level:{signal_key(signal)}")

        key = signal_key(signal)
        if _finite_multiplier(signal.get("position_multiplier", 1.0)) is None:
            errors.append(f"invalid_position_multiplier:{key}")
        if strict and key in seen:
            errors.append(f"duplicate_module_signal:{key}")
        seen.add(key)

        # Zero-cap modules (Cap & Tighten-Only Registry: modeled_scenario,
        # kol_method_card, overnight_ensemble_ranker, quant_robustness bad-states)
        # structurally cannot justify a new-entry claim on their own. A signal that
        # pairs a hard-zero position cap with TEST/BUILD/ADD is not a miscalibrated
        # value to clamp -- it is forging permission the module cannot hold -- so it
        # is rejected outright, in both strict and legacy mode (same treatment as
        # unknown_module above).
        sub_framework = str(signal.get("sub_framework") or "").strip() or None
        module_cap = _module_multiplier_cap(module, sub_framework)
        if module_cap is not None and module_cap[0] == 0.0:
            _, entry, _ = level_to_axes(signal)
            if entry in {"TEST", "BUILD", "ADD"}:
                errors.append(f"forged_zero_cap_entry:{key}")

        # Every strict signal participates in action synthesis. L0 can still lower
        # entry to WATCH, multiply exposure to zero, carry a hard veto, or request
        # REDUCE/EXIT, so it needs the same evidence and runtime freshness checks.
        if not strict:
            continue
        errors.extend(_evidence_refs_errors(signal, key))
        observed_at = parse_dt(signal.get("observed_at"))
        stale_after = parse_dt(signal.get("stale_after"))
        if observed_at is None:
            errors.append(f"invalid_observed_at:{key}")
        elif observed_at > as_of or observed_at > evaluated_at:
            errors.append(f"future_observed_at:{key}")
        if stale_after is None:
            errors.append(f"invalid_stale_after:{key}")
        elif stale_after <= as_of or stale_after <= evaluated_at:
            errors.append(f"stale_signal:{key}")

    if strict:
        required = context.get("required_modules")
        declared: list[str] = [str(module or "").strip() for module in required] if isinstance(required, list) else []
        if not isinstance(required, list) or not required:
            errors.append("missing_required_modules_contract")
        else:
            for name in declared:
                if name not in REGISTERED_MODULES:
                    errors.append(f"unknown_required_module:{name}")
                elif name not in modules:
                    errors.append(f"missing_required_module:{name}")
        # required_modules must not be a self-issued admission ticket: a payload
        # that can grant new entry permission cannot shrink its own accountability
        # by omitting a baseline module the compiler mandates internally. Declaring
        # extra modules is fine; omitting a baseline one is a contract violation,
        # not a value to silently backfill. Intents that cannot grant entry
        # permission (research/hold/reduce/exit) are exempt; "open" and any
        # unrecognized/missing intent fail closed onto the floor.
        intent = str(context.get("intent") or "").strip().lower()
        if intent not in INTENT_EXEMPT_FROM_BASELINE_REQUIRED_MODULES:
            for name in sorted(BASELINE_REQUIRED_MODULES - set(declared)):
                errors.append(f"required_modules_below_baseline:{name}")

    return list(dict.fromkeys(errors))


def failed_result(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    strict = payload.get("schema_version") is not None
    return {
        "ok": False,
        "schema_version": "decision_compilation.v2",
        "contract_status": "strict_failed" if strict else "legacy_failed",
        "compiled_action": "L0",
        "entry_permission": "BLOCK",
        "holding_directive": "HOLD",
        "final_position_multiplier": 0.0,
        "hard_veto": False,
        "epistemic_veto": False,
        "hard_veto_modules": [],
        "unresolved_conflicts": bool(payload.get("unresolved_conflicts")),
        "dominant_constraints": [],
        "repair_signals": [],
        "validation_errors": errors,
        "cap_applied": [],
        "manual_review_required": True,
        "no_order_execution": True,
    }


def compile_payload(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    raw_signals = payload.get("module_signals")
    signals = [row for row in raw_signals if isinstance(row, dict)] if isinstance(raw_signals, list) else []
    errors = validate_payload(payload, signals, now=now)
    if not signals:
        errors.append("no_evaluable_modules")
    if errors:
        return failed_result(payload, list(dict.fromkeys(errors)))

    raw_context = payload.get("decision_context")
    context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
    strict = payload.get("schema_version") in SUPPORTED_REQUEST_SCHEMAS
    intent = str(context.get("intent") or "").strip().lower()
    non_open_intent = strict and intent != "open"
    capped_signals, cap_applied = apply_caps(signals)
    has_position = bool(context.get("has_position"))
    hard_vetoes = [signal for signal in capped_signals if signal.get("hard_veto")]
    epistemic_vetoes = [signal for signal in hard_vetoes if signal.get("module") in EPISTEMIC_VETO_MODULES]
    market_risk_vetoes = [signal for signal in hard_vetoes if signal.get("module") not in EPISTEMIC_VETO_MODULES]
    unresolved = payload.get("unresolved_conflicts") or [signal for signal in capped_signals if signal.get("unresolved_conflict")]
    axes = [(signal, *level_to_axes(signal)) for signal in capped_signals]

    entry_permissions = [entry for _, _, entry, _ in axes]
    holding_directives = [holding for _, _, _, holding in axes]
    holding_directive = max(holding_directives, key=lambda value: HOLDING_ORDER[value], default="HOLD")
    if market_risk_vetoes and has_position:
        holding_directive = "EXIT"

    if non_open_intent or hard_vetoes or unresolved or holding_directive in {"REDUCE", "EXIT"}:
        entry_permission = "BLOCK"
    else:
        entry_permission = min(entry_permissions, key=lambda value: ENTRY_ORDER[value], default="WATCH")

    final_multiplier, aggregation_audit = aggregate_position_multipliers(capped_signals)
    cap_applied.extend(aggregation_audit)
    if entry_permission == "BLOCK" or hard_vetoes or unresolved:
        final_multiplier = 0.0
    elif final_multiplier == 0.0:
        entry_permission = "WATCH"

    if holding_directive == "EXIT":
        compiled_action = "L5"
    elif holding_directive == "REDUCE":
        compiled_action = "L4"
    else:
        compiled_action = ENTRY_TO_LEVEL[entry_permission]

    repair_signals = [str(signal["repair_signal"]) for signal in capped_signals if signal.get("repair_signal")]
    dominant_constraints = []
    for signal, level, entry, holding in axes:
        label = constraint_label(signal)
        if not label:
            continue
        if (
            signal.get("hard_veto")
            or holding == holding_directive and holding != "HOLD"
            or entry == entry_permission
            or clamp_multiplier(signal.get("position_multiplier", 1.0)) == 0.0
        ):
            dominant_constraints.append(label)

    return {
        "ok": True,
        "schema_version": "decision_compilation.v2",
        "contract_status": "strict_pass" if strict else "legacy_unverified",
        "compiled_action": compiled_action,
        "entry_permission": entry_permission,
        "holding_directive": holding_directive,
        "final_position_multiplier": final_multiplier,
        "hard_veto": bool(hard_vetoes),
        "epistemic_veto": bool(epistemic_vetoes),
        "hard_veto_modules": [signal.get("module") for signal in hard_vetoes],
        "unresolved_conflicts": bool(unresolved),
        "dominant_constraints": list(dict.fromkeys(dominant_constraints)),
        "repair_signals": repair_signals,
        "validation_errors": [],
        "cap_applied": cap_applied,
        "manual_review_required": (not strict) or bool(epistemic_vetoes),
        "required_modules": context.get("required_modules", []),
        "signal_count": len(capped_signals),
        "rule": "contract_validation > non-open intent blocks entry > hard_veto/conflict > holding(EXIT>REDUCE>HOLD) + entry(min permission); multipliers use same-module min then cross-module product; caps applied per Cap & Tighten-Only Registry",
        "no_order_execution": True,
    }


def compile_decision(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Backward-compatible public entry point."""
    return compile_payload(payload, now=now)


def _parse_json_integer(raw: str) -> int | float | str:
    """Preserve lexical -0 and bound hostile integers before runtime conversion."""
    digits = raw[1:] if raw.startswith("-") else raw
    if raw == "-0":
        return -0.0
    if len(digits) > MAX_JSON_INTEGER_DIGITS:
        return INVALID_JSON_INTEGER
    return int(raw)


def load_payload(path: str | None) -> dict[str, Any]:
    if path:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = sys.stdin.read()
    data = json.loads(text, parse_int=_parse_json_integer)
    return data if isinstance(data, dict) else {}


def self_test() -> dict[str, Any]:
    payload = {
        "module_signals": [
            {"module": "fundamentals", "sub_framework": "three_clocks", "max_action_level": "L1", "position_multiplier": 0.5},
            {"module": "macro", "max_action_level": "L3", "position_multiplier": 1.0},
        ]
    }
    out = compile_payload(payload)
    assert out["compiled_action"] == "L1", out
    assert out["contract_status"] == "legacy_unverified", out
    assert "fundamentals:three_clocks" in out["dominant_constraints"], out
    return {"ok": True, "self_test": "passed", "dominant_constraints": out["dominant_constraints"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="JSON payload path; stdin if omitted")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    try:
        result = compile_payload(load_payload(args.path))
    except (OSError, OverflowError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "no_order_execution": True}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
