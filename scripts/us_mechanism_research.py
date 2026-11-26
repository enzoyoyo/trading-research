#!/usr/bin/env python3
"""Compute a versioned US mechanism research bundle, without an order interface.

This joins actual calculators, not votes: event residuals, historical paths and
option expressions retain distinct clocks, evidence and limitations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from conditional_path_study import study, retrospective_panel
from event_dislocation import analyze as event_analyze
from options_expression_lab import analyze as option_analyze

SCHEMA = "us_mechanism_research.v1"
REQUEST_SCHEMA = "us_mechanism_request.v1"
HORIZONS = {"intraday", "overnight_cto", "swing_days", "position_months", "theme_years"}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def calculator_fingerprint() -> str:
    root = Path(__file__).parent
    return fingerprint({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in ("us_mechanism_research.py", "conditional_path_study.py", "event_dislocation.py", "options_expression_lab.py")})


def stamp(value: Any) -> datetime:
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return t.astimezone(timezone.utc)


def symbol(value: Any) -> str:
    return str(value).upper().removesuffix(".US")


def _component_symbols(kind: str, data: dict) -> set[str]:
    if kind == "event_dislocation":
        return {symbol(data.get("event", {}).get("issuer")), symbol(data.get("candidate", {}).get("symbol")), symbol(data.get("benchmark"))}
    if kind == "conditional_paths":
        return {symbol(data.get("symbol"))}
    values = {symbol(leg.get("underlying")) for c in data.get("candidates", []) for leg in c.get("legs", [])}
    values.update(symbol(h.get("underlying") or h.get("symbol")) for h in data.get("holdings", []))
    return values


def assemble(request: dict) -> dict:
    result = {"schema_version": SCHEMA, "no_order_execution": True,
              "materiality_eligible": False, "action_authority": False,
              "state": "blocked", "components": {}, "data_gaps": [],
              "probability_profit": None, "expected_net_return": None,
              "method_origin": "independent transparent implementation, not Balder private rules"}
    try:
        if request.get("schema_version") != REQUEST_SCHEMA:
            raise ValueError("request_schema_mismatch")
        for key in ("research_id", "as_of", "scope_symbols", "horizon_id", "hypothesis_ids", "invalidation", "components"):
            if key not in request:
                raise ValueError("missing_request_field:" + key)
        if not isinstance(request["research_id"], str) or not request["research_id"].strip():
            raise ValueError("research_id_required")
        cutoff = stamp(request["as_of"])
        if request["horizon_id"] not in HORIZONS:
            raise ValueError("explicit_supported_horizon_required")
        scope = request["scope_symbols"]
        if not isinstance(scope, list) or not scope or any(not isinstance(s, str) or not s.strip() for s in scope):
            raise ValueError("scope_symbols_required")
        if not isinstance(request["hypothesis_ids"], list) or any(not isinstance(s, str) for s in request["hypothesis_ids"]):
            raise ValueError("hypothesis_ids_must_be_list")
        if not isinstance(request["invalidation"], list) or not request["invalidation"] or any(not isinstance(x, str) or not x.strip() for x in request["invalidation"]):
            raise ValueError("explicit_invalidation_observations_required")
        components = request["components"]
        if not isinstance(components, dict) or not components:
            raise ValueError("calculator_inputs_required")
        unknown = set(components) - {"event_dislocation", "conditional_paths", "options_expression"}
        if unknown:
            raise ValueError("unknown_component:" + ",".join(sorted(unknown)))
        digest = fingerprint(request)
        calculator_sha = calculator_fingerprint()
        contract_sha = fingerprint({"request": digest, "calculators": calculator_sha})
        result.update({"research_id": request["research_id"], "as_of": request["as_of"],
                       "request_sha256": digest, "calculator_sha256": calculator_sha, "contract_sha256": contract_sha, "scope_symbols": scope,
                       "horizon_id": request["horizon_id"], "hypothesis_ids": request["hypothesis_ids"],
                       "invalidation": request["invalidation"],
                       "research_link": {"schema_version": "paper_mechanism_link.v1", "research_id": request["research_id"],
                                         "request_sha256": digest, "contract_sha256": contract_sha, "frozen_at": request["as_of"],
                                         "horizon_id": request["horizon_id"], "scope_symbols": scope}})
    except (ValueError, TypeError, AttributeError) as exc:
        result["data_gaps"].append(str(exc))
        return result

    accepted_scope = {symbol(s) for s in scope}
    for name, raw in components.items():
        try:
            if not isinstance(raw, dict):
                raise ValueError("component_input_must_be_object")
            data = raw
            if name == "conditional_paths" and "panel" in raw:
                data = retrospective_panel(raw["panel"], raw["rule"])
            asof = stamp(data.get("as_of"))
            if asof > cutoff:
                raise ValueError("component_after_bundle_cutoff")
            if not _component_symbols(name, data) <= accepted_scope:
                raise ValueError("component_symbol_outside_declared_scope")
            if name == "event_dislocation":
                output = event_analyze(data)
            elif name == "conditional_paths":
                horizon = data.get("rule", {}).get("horizon")
                if request["horizon_id"] == "intraday":
                    raise ValueError("daily_paths_cannot_answer_intraday_request")
                if request["horizon_id"] == "overnight_cto":
                    raise ValueError("daily_close_high_low_paths_cannot_answer_close_to_open_request")
                output = study(data, horizon)
            else:
                output = option_analyze(data)
            result["components"][name] = {"input_sha256": fingerprint(data),
                "input_as_of": data.get("as_of"), "age_at_bundle_hours": (cutoff-asof).total_seconds()/3600,
                "output": output}
        except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            result["components"][name] = {"state": "blocked", "data_gaps": [str(exc)]}

    computed = []
    for name, component in result["components"].items():
        output = component.get("output", {})
        gaps = list(component.get("data_gaps", [])) + list(output.get("data_gaps", []))
        gaps += list(output.get("hedge_data_gaps", [])) + list(output.get("relationship_data_gaps", []))
        for candidate in output.get("candidates", []):
            gaps.extend(str(candidate.get("id", "candidate")) + ":" + str(g) for g in candidate.get("data_gaps", []))
        result["data_gaps"].extend(name + ":" + str(g) for g in gaps)
        if output and output.get("readiness") != "blocked" and output.get("state") != "blocked" and output.get("status") != "blocked":
            computed.append(name)
        if not output or output.get("readiness") == "blocked" or output.get("status") == "blocked":
            result["data_gaps"].append(name + ":blocked_or_incomplete")
    result["data_gaps"] = sorted(set(result["data_gaps"]))
    result["state"] = "research_computed" if computed else "blocked"
    result["computed_components"] = computed
    # Inputs include issuers and benchmarks; only actual research subjects can
    # receive descriptive attribution. A broad input scope is not a target list.
    attribution = {}
    for name in computed:
        output = result["components"][name]["output"]
        targets = set()
        if name == "event_dislocation":
            targets.add(symbol(output.get("target")))
        elif name == "conditional_paths":
            targets.add(symbol(output.get("symbol")))
        else:
            for candidate in output.get("candidates", []):
                if candidate.get("readiness") != "blocked":
                    targets.update(symbol(leg.get("underlying")) for leg in candidate.get("contracts", []))
            if output.get("baseline_scenario_details"):
                raw = components[name]
                targets.update(symbol(h.get("underlying") or h.get("symbol")) for h in raw.get("holdings", []))
        attribution[name] = sorted(targets & accepted_scope)
    attribution_symbols = sorted({s for values in attribution.values() for s in values})
    result["attribution_by_component"] = attribution
    result["attribution_symbols"] = attribution_symbols
    result["research_link"]["attribution_symbols"] = attribution_symbols
    result["attribution_scope_semantics"] = "computed_subjects_only_not_all_input_assets"
    result["stage_coverage"] = {
        "event_relative_dislocation": "computed" if "event_dislocation" in computed else "not_computed",
        "conditional_historical_paths": "computed" if "conditional_paths" in computed else "not_computed",
        "instrument_and_joint_risk": "computed_with_separate_hedge_completeness" if "options_expression" in computed else "not_computed",
        "execution_and_attribution": "requires_actual_entry_link_and_fills",
        "replay": "price_paths_only; options require historical contract quotes and execution model",
        "strategy_promotion": "none; existing hypothesis and Compiler gates remain authoritative"}
    result["handoff"] = {
        "next": "review evidence, invalidation, costs and branch-specific plan; submit original proposal to canonical Compiler",
        "proposal_metadata_field": "mechanism_research_link",
        "link_is_execution_permission": False,
        "forecasts": "keep terminal, touch, reversion and net-PnL contracts separate; never average clocks or convert z to win rate",
        "paper_feedback": "entry-frozen link plus actual lifecycle; no backfill of historical trades into new hypotheses",
        "attack_defence": "risk budget controls permitted exposure; research continues during defence, re-entry requires refreshed evidence"}
    return result


def feedback(bundles: list[dict], outcomes: list[dict]) -> dict:
    """Use the existing lifecycle/net-cost implementation. Linking is not causation."""
    from paper_trade_lifecycle import aggregate_lifecycles
    from paper_outcome_calibration_feed import net_lifecycle_outcome
    by_hash = {b.get("contract_sha256"): b for b in bundles if b.get("schema_version") == SCHEMA and b.get("contract_sha256")}
    report = {"schema_version": "paper_mechanism_feedback.v1", "linked_entries": 0, "completed_linked": 0,
              "net_settled_linked": 0, "legacy_unlinked": 0, "invalid_links": 0, "rows": [],
              "no_action_authority": True, "materiality_eligible": False, "no_order_execution": True,
              "interpretation": "descriptive linked outcomes, not causal strategy validation or probability calibration"}
    root = Path(__file__).parent
    report["feedback_implementation_sha256"] = fingerprint({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("us_mechanism_research.py", "paper_trade_lifecycle.py", "paper_outcome_calibration_feed.py")})
    for trade in aggregate_lifecycles(outcomes)["lifecycles"]:
        entry = trade.get("entry_payload") or {}
        link = entry.get("mechanism_research_link")
        if not link:
            report["legacy_unlinked"] += 1
            continue
        gap = None
        try:
            if not isinstance(link, dict) or link.get("schema_version") != "paper_mechanism_link.v1":
                raise ValueError("invalid_link_contract")
            bundle = by_hash.get(link.get("contract_sha256"))
            if not bundle:
                raise ValueError("matching_frozen_research_artifact_missing")
            if bundle.get("research_link") != link:
                raise ValueError("entry_link_differs_from_frozen_artifact")
            request_hash, calculator_hash = bundle.get("request_sha256"), bundle.get("calculator_sha256")
            if any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h) for h in (request_hash, calculator_hash)):
                raise ValueError("research_artifact_digest_invalid")
            if (fingerprint({"request": request_hash, "calculators": calculator_hash}) != link.get("contract_sha256")
                    or request_hash != link.get("request_sha256")
                    or bundle.get("research_id") != link.get("research_id")
                    or bundle.get("as_of") != link.get("frozen_at")
                    or bundle.get("scope_symbols") != link.get("scope_symbols")
                    or bundle.get("horizon_id") != link.get("horizon_id")):
                raise ValueError("research_artifact_digest_mismatch")
            if bundle.get("state") != "research_computed":
                raise ValueError("blocked_bundle_cannot_be_outcome_evidence")
            if not stamp(link["frozen_at"]) <= stamp(bundle["receipt_recorded_at"]) <= stamp(trade["opened_at"]):
                raise ValueError("artifact_not_recorded_before_entry")
            if symbol(trade.get("symbol")) not in {symbol(s) for s in bundle["scope_symbols"]}:
                raise ValueError("entry_symbol_outside_research_scope")
            targets = bundle.get("attribution_symbols")
            if (not isinstance(targets, list) or not targets or targets != link.get("attribution_symbols")
                    or any(not isinstance(s, str) or not s.strip() for s in targets)):
                raise ValueError("research_attribution_scope_missing")
            if (not {symbol(s) for s in targets} <= {symbol(s) for s in bundle["scope_symbols"]}
                    or symbol(trade.get("symbol")) not in {symbol(s) for s in targets}):
                raise ValueError("entry_symbol_is_input_only_not_research_subject")
            if entry.get("horizon_id") != link["horizon_id"]:
                raise ValueError("entry_horizon_not_explicitly_bound_to_research")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            gap = str(exc)
        row = {"trade_lifecycle_id": trade["trade_lifecycle_id"], "symbol": trade["symbol"],
               "link_status": "invalid" if gap else "entry_frozen_link_verified", "gap": gap,
               "status": trade["status"], "opened_at": trade.get("opened_at"), "closed_at": trade.get("completed_at"),
               "horizon_binding": "named_bucket_only_not_numeric_holding_window_or_causal_validation",
               "net_pnl": None, "net_pnl_currency": None}
        if gap:
            report["invalid_links"] += 1
        else:
            report["linked_entries"] += 1
            row.update({"research_id": link["research_id"], "contract_sha256": link["contract_sha256"],
                        "horizon_id": link["horizon_id"], "strategy": trade.get("strategy")})
            if trade["status"] == "closed":
                report["completed_linked"] += 1
                label, receipts = net_lifecycle_outcome(trade, outcomes)
                if label is not None:
                    report["net_settled_linked"] += 1
                    row.update(net_pnl=sum(r["net_pnl"] for r in receipts), net_pnl_currency=receipts[0]["net_pnl_currency"])
                else:
                    row["gap"] = "full_lifecycle_net_cost_receipts_pending"
        report["rows"].append(row)
    report["sample_set_sha256"] = fingerprint(report["rows"])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input")
    group.add_argument("--feedback-input", help="JSON containing bundles and outcomes; read-only")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.feedback_input:
        data = json.loads(Path(args.feedback_input).read_text())
        result = feedback(data["bundles"], data["outcomes"])
    else:
        result = assemble(json.loads(Path(args.input).read_text()))
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text, end="")
    return 2 if result.get("state") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
