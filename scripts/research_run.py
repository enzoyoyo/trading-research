#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from strategy_orchestrator import build_strategy_mandate
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def skill_version() -> str:
    """Read the canonical version from SKILL.md so runtime output cannot drift."""
    for line in (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip()
            if version:
                return version
    raise RuntimeError("SKILL.md frontmatter is missing version")


def run_json(cmd: list[str]) -> object:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode:
        return {"ok": False, "cmd": cmd, "stderr_tail": proc.stderr[-500:]}
    try:
        return json.loads(proc.stdout)
    except Exception:
        return {"ok": True, "raw": proc.stdout[-2000:]}


def generate_scorecard_template(target: str, market_info: dict) -> dict:
    """Generate a bottleneck scorecard template pre-filled with target info."""
    import json as _json
    template = {
        "ticker": market_info.get("canonical_symbol", target),
        "company": market_info.get("company_name", target),
        "market": f"{market_info.get('market', 'US')}/{market_info.get('exchange', '')}".rstrip("/"),
        "factors": {
            "demand_inflection": None,
            "architecture_coupling": None,
            "chokepoint_severity": None,
            "supplier_concentration": None,
            "expansion_difficulty": None,
            "evidence_quality": None,
            "valuation_disconnect": None,
            "catalyst_timing": None,
        },
        "penalties": {
            "dilution_financing": None,
            "governance": None,
            "geopolitics": None,
            "liquidity": None,
            "hype_risk": None,
            "accounting_quality": None,
            "cyclicality": None,
            "alternative_design_risk": None,
        },
        "evidence": [],
        "what_could_weaken_view": ["", "", ""],
    }
    return template


def research_contract(query_type: str) -> dict:
    """Machine-visible Tier-2 chain; planning output never claims facts were fetched."""
    forced_state = {
        "insufficient": "data_gap",
        "conflict": "conflict",
    }.get(query_type, "pending_evidence")
    stages = [
        ("macro", "macro regime, policy, rates and liquidity"),
        ("sector", "industry cycle, breadth, supply and relative strength"),
        ("company", "identity, filings, fundamentals and valuation expectations"),
        ("sentiment", "attention, credibility, consensus and positioning"),
        ("capital_structure", "participant flow, liquidity, gamma and forced flows"),
        ("opportunity", "catalyst, horizon, triggers and invalidation"),
    ]
    return {
        "schema_version": "research_chain.v1",
        "query_type": query_type,
        "required_order": [name for name, _ in stages],
        "stages": [
            {
                "stage": name,
                "state": forced_state,
                "required_output": requirement,
                "evidence_ids": [],
                "data_gaps": ["live evidence not fetched by planning entry point"],
            }
            for name, requirement in stages
        ],
        "gate": {
            "missing_or_unverified_stage_effect": "degrade_only",
            "may_raise_action_or_position": False,
            "decision_compiler_required": True,
        },
    }


def decision_contract(query_type: str) -> dict:
    blocked = query_type in {"insufficient", "conflict"}
    return {
        "schema_version": "decision_chain.v1",
        "required_order": ["opportunity", "score", "probability", "risk", "plan"],
        "status": "blocked_pending_evidence" if blocked else "not_compiled",
        "max_action_level": "L0" if blocked else None,
        "entry_permission": "BLOCK" if blocked else None,
        "position_multiplier": 0.0 if blocked else None,
        "risk_posture": "neutral",
        "consensus_required": ["consensus_view", "price_discounts", "variant_view"],
        "premortem_min_failures": 3,
        "probability_contract": {
            "settleable_question_required": True,
            "horizon_id_required": True,
            "as_of_and_due_at_required": True,
            "cross_horizon_aggregate_forbidden": True,
        },
        "plan_required": ["entry_triggers", "invalidation", "no_trade_if", "review_clock"],
        "entry_score_authority": "display_only",
        "final_authority": "Decision Compiler",
        "no_order_execution": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--payoff", type=float, default=None)
    parser.add_argument("--scorecard", action="store_true", help="Include bottleneck scorecard template")
    parser.add_argument("--horizon", choices=["intraday", "overnight_cto", "swing_days", "position_months", "theme_years"])
    parser.add_argument("--instrument", choices=["equity", "etf", "option"], default="equity")
    parser.add_argument("--mechanism-input", help="Run explicit local us_mechanism_request.v1 calculator inputs; no order execution")
    parser.add_argument(
        "--query-type",
        choices=["equity", "macro", "sector", "opportunity", "insufficient", "conflict"],
        default="equity",
    )
    args = parser.parse_args()
    market = run_json([sys.executable, str(SCRIPTS / "market_router.py"), args.target])
    first = market[0] if isinstance(market, list) and market else {}
    mkt = first.get("market", "unknown") if isinstance(first, dict) else "unknown"
    plan = run_json([sys.executable, str(SCRIPTS / "live_intel_run.py"), args.target, "--plan-only"])
    weights = run_json([sys.executable, str(SCRIPTS / "method_router.py"), "--market", mkt if mkt in {"A", "HK", "US"} else "unknown", "--theme", args.target])
    # Credential presence signal only — read NAMES, never values; never echo secrets.
    # Canonical store lives OUTSIDE this skill dir/repo at ~/.config/longbridge/.env (chmod 600).
    env_names = []
    for env_path in (Path.home() / ".config/longbridge/.env",):
        if not env_path.exists():
            continue
        for line in env_path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name = stripped.split("=", 1)[0].replace("export ", "").strip()
            if "LONG" in name.upper():
                env_names.append(name)

    output = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": args.target,
        "payoff": args.payoff,
        "skill_version": skill_version(),
        "market_router": market,
        "live_intel_plan": plan,
        "method_weights": weights,
        "research_chain": research_contract(args.query_type),
        "decision_chain": decision_contract(args.query_type),
        "strategy_mandate": build_strategy_mandate(market=mkt, horizon_id=args.horizon, instrument=args.instrument),
        "top_down_context": {
            "longbridge_key_names_present": sorted(set(env_names)),
            "longbridge_tier": "unverified_run_capability_preflight",
            "ibkr_readonly_context": "unavailable unless user provides/export adapter exists",
            "no_order_execution": True,
        },
        "methods_available": {
            "counter_consensus": "references/counter-consensus-framework.md",
            "expected_returns": "references/expected-returns-framework.md",
            "bottleneck_scorecard": "references/bottleneck-scorecard.md",
            "liquidity_valuation_duality": "references/liquidity-valuation-duality.md",
            "evidence_ladder": "references/evidence-ladder.md",
            "a_share_short_term": "references/a-share-short-term-layer.md",
            "a_share_sentiment_cycle": "references/a-share-sentiment-cycle.md",
            "a_share_financial_api": "references/financial-api-data-source.md",
            "us_strategy_campaign": "references/us-strategy-campaign.md",
            "us_mechanism_research": "references/us-mechanism-research.md",
            "event_dislocation": "scripts/event_dislocation.py",
            "conditional_path_study": "scripts/conditional_path_study.py",
            "options_expression_lab": "scripts/options_expression_lab.py",
            "data_freshness_guard": "scripts/data_freshness_guard.py",
            "factor_research_engine": "references/factor-research-engine.md",
            "factor_deployment_playbook": "references/factor-deployment-playbook.md",
            "factor_panel": "scripts/factor_panel.py",
            "premarket_factor_screen": "scripts/premarket_screen.py",
            "earnings_event_options_prediction": "references/earnings-event-options-prediction-gate.md",
            "options_positioning_snapshot": "scripts/options_positioning_snapshot.py",
            "earnings_move_history": "scripts/earnings_move_history.py",
            "earnings_implied_distribution": "scripts/earnings_implied_distribution.py",
            "earnings_consensus_snapshot": "scripts/fundamental_snapshot.py",
            "serenity_method": "references/serenity-method.md",
            "multi_source_search": "references/multi-source-search-layer.md",
            "intelligence_coverage": "references/intelligence-coverage-and-watch-triggers.md",
            "research_watch_triggers": "scripts/research_watch_trigger.py",
            "attention_rumor_triage": "references/attention-rumor-triage.md",
            "dividend_quality": "references/dividend-quality-framework.md",
            "okx_product_and_execution_boundary": "references/okx-research-execution-supervision.md",
            "okx_public_market_snapshot": "scripts/okx_public_snapshot.py",
            "entry_score": "scripts/entry_score.py",
            "okx_execution_supervision": "scripts/okx_execution_supervisor.py",
            "okx_readonly_monitor_dashboard": "scripts/okx_monitor_dashboard.py",
        },
        "evidence_requirements": {
            "min_sources_for_deep_scan": 25,
            "evidence_ladder": "Strong (filings/contracts/patents) → Medium (credible media/trade pubs) → Weak (social/KOL)",
            "red_flags_checklist": "references/evidence-ladder.md 七大红旗",
            "search_candidates": "discovery-only until original URL is fetched and regraded",
        },
        "gaps": [
            "This archive is deterministic planning context; final answer must fetch/verify live facts with tools.",
            "IBKR account context is unavailable unless a read-only source is provided.",
        ],
    }
    if args.scorecard:
        output["scorecard_template"] = generate_scorecard_template(args.target, first)
    if args.mechanism_input:
        from us_mechanism_research import assemble
        output["mechanism_research"] = assemble(json.loads(Path(args.mechanism_input).read_text()))

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
