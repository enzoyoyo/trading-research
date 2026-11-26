#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from release_validation_runner import (
    BUNDLED_LIFECYCLE_MODULE,
    RELEASE_VALIDATION_GUARD,
    release_validation_env,
    run_all_checks,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Fixed entries that a directory glob cannot express: the skill root file and
# the templates/ fixtures (a mix of .md/.json/.jsonl/.txt files,
# most of which are eval corpora rather than capability-map documentation and
# so are deliberately excluded from the references/scripts glob below).
STATIC_REQUIRED = [
    "ACCEPT.yml",
    "SKILL.md",
    "templates/universal-equity-report.md",
    "templates/routing-evals.jsonl",
    "templates/market-router-evals.jsonl",
    "templates/method-router-evals.jsonl",
    "templates/scenario-regression-evals.jsonl",
    "templates/short-cycle-structure-evals.jsonl",
    "templates/report-contract-pass.md",
    "templates/research-provenance-bundle-pass.json",
    "templates/kol-method-cards-public-ledger.json",
    "templates/provenance-source-example.txt",
    "templates/report-contract-fail-no-sources.md",
    "templates/report-contract-fail-too-long.md",
    # Not top-level under scripts/, so the glob below (scripts/*.py) can't see it.
    "scripts/release_validation_guard/sitecustomize.py",
]


def _discover_required() -> list[str]:
    """references/*.md and scripts/*.py are required and reachability-checked
    by directory scan rather than a hand-maintained list, so a new/renamed/
    deleted file is caught automatically instead of silently falling out of
    coverage (see finding required-list-lags-capability-map)."""
    refs = sorted(f"references/{p.name}" for p in (ROOT / "references").glob("*.md"))
    scripts = sorted(f"scripts/{p.name}" for p in SCRIPTS.glob("*.py"))
    return refs + scripts


REQUIRED = STATIC_REQUIRED + _discover_required()
FORBIDDEN_RUNTIME = [
    "GROK2API_BASE_URL",
    "GROK2API_API_KEY",
    "127.0.0.1:8000/v1",
    ".config/grok2api",
    "/chat/completions",
    "Chat upstream returned",
]
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s`]+"),
]

SKILL = "trading-research"
HOME = Path.home()
CANONICAL = HOME / ".claude" / "skills" / SKILL
DEFAULT_ROOTS = [
    HOME / ".claude" / "skills",
    HOME / ".hermes" / "skills",
    HOME / ".codex" / "skills",
    HOME / ".Codex" / "skills",
]
EXCLUDE_SUBSTRINGS = ["node_modules", "vendor_imports", ".hermes/hermes-agent"]
SUPPORTED_LEARNING_PACKET_SCHEMAS = {"paper_learning_packet.v1", "paper_learning_packet.v2"}
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
OVERLAY_MODULE_CONTRACTS = {
    "attention-rumor-triage.md": ("endogenous_structure", "adjudication_fallback"),
    "a-share-derivatives-ipo.md": ("endogenous_structure", "file_explicit"),
    "capex-cashflow-duration-rotation.md": ("endogenous_structure", "adjudication_fallback"),
    "cycle-position-three-clocks.md": ("endogenous_structure", "adjudication_fallback"),
    "earnings-call-interpretation.md": ("fundamentals", "file_explicit"),
    "etf-selection-rotation.md": ("endogenous_structure", "adjudication_fallback"),
    "hk-offshore-market-playbook.md": ("endogenous_structure", "adjudication_fallback"),
    "prosperity-davis-double-framework.md": ("fundamentals", "file_explicit"),
    "rates-fx-crypto-overlay.md": ("endogenous_structure", "adjudication_fallback"),
    "second-order-supply-shock-mapping.md": ("endogenous_structure", "adjudication_fallback"),
    "semis-index-divergence-overlay.md": ("endogenous_structure", "file_explicit"),
    "short-cycle-market-structure-overlay.md": ("execution_window", "file_explicit"),
    "us-close-to-open-execution-overlay.md": ("execution_window", "file_explicit"),
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def _literal_assignment(source: str, name: str) -> Any:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise ValueError(f"assignment not found: {name}")


def _marked_section(text: str, start: str, end: str) -> str:
    before, separator, remainder = text.partition(start)
    if not separator:
        return ""
    section, separator, _after = remainder.partition(end)
    return section if separator else ""


def method_module_mapping_errors(
    decision_text: str, method_router_source: str, compiler_source: str
) -> list[str]:
    errors: list[str] = []
    section = _marked_section(
        decision_text, "<!-- method-module-map:start -->", "<!-- method-module-map:end -->"
    )
    if not section:
        return ["method-to-module mapping section missing"]
    expected_methods = set(_literal_assignment(method_router_source, "METHOD_LABELS"))
    registered_modules = set(_literal_assignment(compiler_source, "REGISTERED_MODULES"))
    mappings: dict[str, list[str]] = {}
    for line in section.splitlines():
        match = re.match(r"^\|\s*`([a-z0-9_]+)`\s*\|\s*([^|]+)\|", line)
        if not match:
            continue
        method = match.group(1)
        if method in mappings:
            errors.append(f"duplicate method mapping: {method}")
            continue
        mappings[method] = re.findall(r"`([a-z0-9_]+)`", match.group(2))
    missing = sorted(expected_methods - set(mappings))
    extra = sorted(set(mappings) - expected_methods)
    if missing:
        errors.append(f"method mapping missing: {','.join(missing)}")
    if extra:
        errors.append(f"method mapping unknown: {','.join(extra)}")
    for method, modules in mappings.items():
        if not modules:
            errors.append(f"method mapping has no module: {method}")
        unknown = sorted(set(modules) - registered_modules)
        if unknown:
            errors.append(f"method mapping uses unregistered module for {method}: {','.join(unknown)}")
    return errors


def overlay_module_contract_errors(decision_text: str, references_root: Path) -> list[str]:
    errors: list[str] = []
    section = _marked_section(
        decision_text, "<!-- overlay-module-map:start -->", "<!-- overlay-module-map:end -->"
    )
    if not section:
        return ["overlay module mapping section missing"]
    mappings: dict[str, tuple[str, str]] = {}
    for line in section.splitlines():
        match = re.match(
            r"^\|\s*`([^`]+\.md)`\s*\|\s*`([a-z_]+)`\s*\|\s*`([a-z_]+)`\s*\|", line
        )
        if not match:
            continue
        filename, module, basis = match.groups()
        if filename in mappings:
            errors.append(f"duplicate overlay mapping: {filename}")
            continue
        mappings[filename] = (module, basis)
    missing = sorted(set(OVERLAY_MODULE_CONTRACTS) - set(mappings))
    extra = sorted(set(mappings) - set(OVERLAY_MODULE_CONTRACTS))
    if missing:
        errors.append(f"overlay mapping missing: {','.join(missing)}")
    if extra:
        errors.append(f"overlay mapping unknown: {','.join(extra)}")
    for filename, expected in OVERLAY_MODULE_CONTRACTS.items():
        if mappings.get(filename) != expected:
            errors.append(f"overlay mapping mismatch for {filename}: expected {expected}, got {mappings.get(filename)}")
        path = references_root / filename
        if not path.exists():
            errors.append(f"overlay reference missing: {filename}")
            continue
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:8])
        declared = re.findall(r"编译进\s+`([a-z_]+)`", head)
        if declared != [expected[0]]:
            errors.append(
                f"overlay {filename} must declare exactly one primary module in first 8 lines: "
                f"expected {expected[0]}, got {declared}"
            )
    return errors


def openstock_clean_room_errors(text: str) -> list[str]:
    heading = re.search(r"(?m)^#{2,3}[^\n]*OpenStock[^\n]*$", text)
    if not heading:
        return ["OpenStock section missing"]
    next_heading = re.search(r"(?m)^#{2,3}\s", text[heading.end():])
    end = heading.end() + next_heading.start() if next_heading else len(text)
    section = text[heading.start():end]
    required = (
        "Open-Dev-Society/OpenStock",
        "4597c9a668118844b588f95eddb9342eed31c41d",
        "AGPL-3.0",
        "methodology_only_no_code_copied=true",
    )
    errors: list[str] = []
    for phrase in required:
        if phrase not in section:
            errors.append(f"OpenStock section missing {phrase}")
    return errors


def multica_collaboration_contract_errors(text: str) -> list[str]:
    heading = re.search(r"(?m)^## Multica 可选单向协作[^\n]*$", text)
    if not heading:
        return ["Multica collaboration section missing"]
    next_heading = re.search(r"(?m)^##\s", text[heading.end():])
    end = heading.end() + next_heading.start() if next_heading else len(text)
    section = text[heading.start():end]
    required = {
        "standalone_default": "true",
        "fable_5": "one_max_compact_context_framework_pass;tools=none;files=none;code=none;retry=none",
        "grok": "web_x_source_ledger_only",
        "gemini": "long_context_index_only_when_needed",
        "sol_ultra": "sole_corrector_writer_validator",
        "downstream_return_to_fable": "false",
    }
    assignment_pattern = re.compile(
        r"^\s*[-*+]\s+(?P<tick>`?)(?P<key>[a-z][a-z0-9_]*)\s*=\s*"
        r"(?P<value>[^`\s]+)(?P=tick)\s*$"
    )
    assignments: dict[str, list[str]] = {}
    for line in section.splitlines():
        match = assignment_pattern.match(line)
        if match:
            assignments.setdefault(match.group("key"), []).append(match.group("value"))

    errors: list[str] = []
    for key, safe_value in required.items():
        values = assignments.get(key, [])
        if safe_value not in values:
            errors.append(f"Multica collaboration section missing {key}={safe_value}")
        for value in values:
            if value != safe_value:
                errors.append(f"Multica collaboration section forbids {key}={value}")
    return errors


def daily_journal_window_contract_errors(reference_text: str, runtime_text: str) -> list[str]:
    """Keep the documented daily-journal window aligned with the CLI default."""
    errors: list[str] = []
    expected_command = "`python3 scripts/daily_journal.py --compose --days 10 --publish`"
    if expected_command not in reference_text:
        errors.append("daily journal reference must prescribe --days 10")

    try:
        tree = ast.parse(runtime_text)
    except SyntaxError:
        return errors + ["daily journal runtime parser could not be parsed"]

    defaults: list[Any] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or first.value != "--days":
            continue
        for keyword in node.keywords:
            if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
                defaults.append(keyword.value.value)

    if defaults != [10]:
        errors.append("daily journal runtime --days default must be 10")
    return errors


def run_json(cmd: list[str], *, env: dict[str, str] | None = None) -> Any:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    if proc.returncode:
        fail(f"command failed: {' '.join(cmd)} :: {proc.stderr[-400:]}")
    try:
        return json.loads(proc.stdout)
    except Exception as exc:
        fail(f"command did not return JSON: {' '.join(cmd)} :: {exc}")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        fail("SKILL.md must start with YAML frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        fail("SKILL.md frontmatter closing delimiter missing")
    data: dict[str, str] = {}
    for line in text[4:end].strip().splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"')
    return data


def frontmatter_checks() -> None:
    data = parse_frontmatter((ROOT / "SKILL.md").read_text(encoding="utf-8"))
    name = data.get("name", "")
    description = data.get("description", "")
    if not name:
        fail("frontmatter name is required")
    if not NAME_RE.match(name):
        fail("frontmatter name must be lowercase letters/numbers/hyphens and cannot start/end with hyphen")
    if len(name) > 64:
        fail("frontmatter name exceeds 64 characters")
    # A public clone may use an arbitrary checkout directory name; SKILL.md is
    # the canonical package identity, not the local filesystem path.
    if not description:
        fail("frontmatter description is required")
    if len(description) > 1024:
        fail(f"frontmatter description exceeds 1024 characters: {len(description)}")


def reachability_checks() -> None:
    """Every reference/non-test script in REQUIRED must be named in SKILL.md so the model can find it (no orphans)."""
    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    excluded_scripts = {"scripts/release_validation_guard/sitecustomize.py"}
    for rel in REQUIRED:
        if rel.startswith("references/"):
            pass
        elif rel.startswith("scripts/") and rel not in excluded_scripts and not Path(rel).name.startswith("test_"):
            pass
        else:
            continue
        name = Path(rel).name
        if name not in skill_text:
            fail(f"SKILL.md capability map is missing an orphaned file: {rel} (add it to the 能力地图)")


def validate_learning_packet_case(case: dict[str, Any]) -> None:
    packet = case.get("input", {}).get("learning_packet", {})
    schema = packet.get("schema_version")
    mismatch = schema not in SUPPORTED_LEARNING_PACKET_SCHEMAS
    expected = bool(case.get("expect", {}).get("packet_schema_mismatch"))
    if mismatch != expected:
        fail(f"{case['name']} expected packet_schema_mismatch={expected}, got {mismatch} for schema={schema!r}")


def scenario_contract_checks() -> int:
    evals = ROOT / "templates" / "scenario-regression-evals.jsonl"
    compiler = SCRIPTS / "decision_compiler.py"
    count = 0
    for line in evals.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if "learning_packet" in case.get("input", {}):
            validate_learning_packet_case(case)
            count += 1
            continue
        proc = subprocess.run([sys.executable, str(compiler)], input=json.dumps(case["input"], ensure_ascii=False), text=True, capture_output=True, timeout=30)
        if proc.returncode:
            fail(f"{case['name']} compiler failed: {proc.stderr[-300:]}")
        got = json.loads(proc.stdout)
        for key, value in case.get("expect", {}).items():
            if key == "max_multiplier_lte":
                if got.get("final_position_multiplier", 1) > value:
                    fail(f"{case['name']} expected multiplier <= {value}, got {got.get('final_position_multiplier')}")
                continue
            if got.get(key) != value:
                fail(f"{case['name']} expected {key}={value}, got {got.get(key)}")
        count += 1
    return count


def discover_roots() -> list[Path]:
    roots: list[Path] = []
    for base in [HOME, HOME / ".config"]:
        if not base.exists():
            continue
        patterns = [".*/skills"] if base == HOME else ["*/skills"]
        for pattern in patterns:
            for root in base.glob(pattern):
                s = str(root)
                if any(x in s for x in EXCLUDE_SUBSTRINGS):
                    continue
                if root.is_dir():
                    roots.append(root)
    seen: set[tuple[int, int]] = set()
    out: list[Path] = []
    for root in sorted(set(roots), key=str):
        try:
            st = root.stat()
            key = (st.st_dev, st.st_ino)
        except OSError:
            key = (0, hash(str(root)))
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def ensure_link(root: Path, fix: bool) -> dict[str, object]:
    dest = root / SKILL
    if root.resolve() == CANONICAL.parent.resolve():
        return {"root": str(root), "status": "canonical", "dest": str(dest), "ok": (CANONICAL / "SKILL.md").exists()}
    if not root.exists():
        return {"root": str(root), "status": "missing_root", "ok": False}
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() and dest.resolve() == CANONICAL.resolve():
            return {"root": str(root), "status": "linked", "dest": str(dest), "target": os.readlink(dest), "ok": True}
        return {"root": str(root), "status": "conflict", "dest": str(dest), "is_symlink": dest.is_symlink(), "resolved": str(dest.resolve()) if dest.exists() else None, "ok": False}
    if fix:
        dest.symlink_to(CANONICAL)
        return {"root": str(root), "status": "created", "dest": str(dest), "target": str(CANONICAL), "ok": True}
    return {"root": str(root), "status": "missing", "dest": str(dest), "ok": False}


def symlink_report(all_discovered: bool = False, fix: bool = False) -> dict[str, Any]:
    roots = discover_roots() if all_discovered else DEFAULT_ROOTS
    results = [ensure_link(root, fix) for root in roots]
    return {"ok": all(r.get("ok") for r in results), "canonical": str(CANONICAL), "count": len(results), "results": results}


def offline_checks() -> None:
    frontmatter_checks()
    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            fail(f"missing {rel}")

    all_md = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in ROOT.rglob("*.md"))
    required_phrases = [
        "xai-oauth", "hermes chat -Q --provider xai-oauth", "CBOE", "options_gamma.py",
        "risk_regime_snapshot.py", "## 单一 Skill 边界", "本 skill 无子 skill 目录",
        "Evidence Ledger", "Hypothesis Ledger", "Conflict Ledger", "Position Cap",
        "证据账本", "冲突账本", "杀杠杆", "VIX", "Skew", "risk_regime",
        "宏观四象限", "内生市场结构", "WindClaw", "windclaw_bridge.py",
        "Decision Memory", "trading_memory.py", "decision_id", "memory_multiplier",
        "YantrikDB", "append-only", "claims", "decayed_win_rate", "think",
        "X Frontline Intelligence", "x_frontline_clue", "Decision Compiler", "Mira Evidence Posture",
        "Mira Quality Gates", "readiness_level", "stale_after", "must_refresh_if",
        "claim_type", "evidence_category", "derived_calculation", "calculation_ref",
        "ingestion_route", "license_scope", "knowability_status", "irreducible_uncertainty",
        "主回复默认简洁决策版", "完整账本进文件/附录", "supply_chain_xray", "early_stage_quality",
        "Grok Web Research Layer", "grok_web_clue", "agent_reported_clue",
        "Open-Source Quant Research Patterns", "Quant Robustness Gate", "portfolio_risk_budget",
        "walk-forward", "no-lookahead", "lookahead_bias", "Backtest Claim Cap",
        "Adaptive Self-Optimization", "materiality gate", "no_necessary_upgrade", "self_optimization_check.py",
        "Polymarket Prediction-Market Signal Layer", "PredictionMarketSignal", "prediction_market_prior",
        "Query Tier Router", "Tier 0", "paper_learning_packet.v1", "paper_learning_packet.v2", "packet_schema_mismatch",
        "polymarket_signal.py", "read_only_market_pricing_prior",
        "resolution_rule_status", "market_pricing", "cannot raise position cap",
        "交易规则 freshness gate", "上海证券交易所交易规则（2026年修订）",
        "calibration_scorecard.py", "Brier", "calibration_materiality",
        "eval_candidate_generator.py", "learning_digest.py", "Weekly Learning Digest",
        "record_due_results.py", "price_at_decision", "completeness=partial",
        "A-Stock Data Source Layer", "a_stock_data_bridge.py", "AShareRawSignal",
        "Tencent Finance", "Eastmoney", "CNINFO", "cninfo_orgid_fallback", "cannot_raise_position_cap",
        "Prosperity Davis Double Framework", "景气度·戴维斯双击", "周期长度判断",
        "戴维斯双击双段", "回撤归因三分法", "信息有效性过滤器", "双门槛",
        "Overnight Ensemble Ranker", "conviction_floor", "political_disclosure",
        "STOCK Act", "disclosure_lag_days", "earnings_blackout",
        "pre_open_read", "vol_confirmation", "hedge_mispricing",
        "winners_holding", "known_flow_events", "09:40 后必须",
        "MiroFish Swarm Simulation Patterns", "ModeledScenarioSignal", "modeled_scenario",
        "simulation_trace", "evidence_collection_plan", "AGPL-3.0", "methodology_only_no_code_copied",
        "synthetic modeled scenario", "position_multiplier=0.0",
        "Cycle Position Three Clocks", "三时钟", "三峰分离", "七路标", "signpost_count",
        "second_derivative", "earnings_reaction_quality", "analog_prior", "analogy_breaks_bearish",
        "jevons_check", "thesis_half_life", "thesis_trade_gate",
        "Second-Order Supply Shock Mapping", "narrative_fact_consistency", "theme_beta_proxy",
        "take-or-pay", "asset_generation_bifurcation", "leader_gap_integrity", "认知与执行分离",
        "variance_risk_premium", "Garman-Klass", "implied_1d_move", "range_expansion_warning",
        "reaction_function_read", "data_surprise", "known_flow_calendar",
        "KOL 轮巡抓取纪律", "kol_model_signal", "subscriber_only_gap", "promotion_conflict",
        "回撤自检",
        "random_ic_mean", "alpha_t", "confirmed_alive", "train_only", "reversed_strict",
        "Harvey-Liu-Zhu", "n_factors_scanned",
        "missed_signals_pnl", "处置效应",
        "SearchDiscoveryCandidate", "discovery_only", "independent_provider_count",
        "Attention & Rumor Triage", "credibility_state", "Dividend Quality Framework",
        "allow-external-search", "no-search-fallback", "freshness_state", "published_at_unknown",
        "Intelligence Coverage Matrix", "intelligence_coverage.v1", "coverage_id", "ResearchWatchTrigger",
        "research_watch_trigger.v1", "research_watch_trigger_compilation.v1", "research_watch_trigger.py", "request_manual_review", "source_coverage", "fair_collection_queue",
        "methodology_only_no_code_copied", "Open-Dev-Society/OpenStock", "Credential Quarantine Gate", "upstream_credential_compromised=true",
        "research_provenance.v1", "SourceDocument", "QuoteAnchor", "provenance_guard.py", "lyra81604/zhengxi-views",
        "kol_method_cards", "public_claim", "falsifiable_thesis", "post_trade_calibration",
        "direct_assertion", "self_reported_performance", "not_found_publicly",
        "standalone_default=true", "downstream_return_to_fable=false",
        "收敛契机", "动量崩溃",
        "OKX 研究与执行监督适配层", "Unified Tokenized Stocks", "entry_score.v1",
        "okx_public_snapshot.py", "okx_execution_supervisor.py", "okx_monitor_dashboard.py", "pause_required", "last-good",
        "position_reconciliation_status", "order_state_status", "credential_scope", "execution_permission",
        "private_ws_connected", "account_hard_redline", "compiler_effect=none",
        "Multi-Horizon Prediction Contract", "horizon_id", "no_trade_if",
        "Signal Fusion & Risk-Budget Agreement", "条件交叉", "risk_budget_scale",
        "gamma_model_conflict", "名义成交额 ≠ 净 gamma",
        "options_positioning_snapshot.py", "earnings_move_history.py",
        "earnings_implied_distribution.py", "KMC-BALDER-EARNINGS-RADAR-20260821",
    ]
    for phrase in required_phrases:
        if phrase not in all_md:
            fail(f"missing documentation phrase: {phrase}")

    reachability_checks()

    source_map = (ROOT / "references" / "source-map.md").read_text(encoding="utf-8")
    scoped_openstock_errors = openstock_clean_room_errors(source_map)
    if scoped_openstock_errors:
        fail("; ".join(scoped_openstock_errors))

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    collaboration_errors = multica_collaboration_contract_errors(skill_text)
    if collaboration_errors:
        fail("; ".join(collaboration_errors))

    journal_window_errors = daily_journal_window_contract_errors(
        (ROOT / "references" / "adaptive-self-optimization.md").read_text(encoding="utf-8"),
        (SCRIPTS / "daily_journal.py").read_text(encoding="utf-8"),
    )
    if journal_window_errors:
        fail("; ".join(journal_window_errors))

    for pat in SECRET_PATTERNS:
        if pat.search(all_md):
            fail(f"secret-like pattern found in markdown: {pat.pattern}")

    code = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in SCRIPTS.glob("*.py")
        if p.name not in ("validate_skill.py", "validate_report.py")
    )
    for pat in FORBIDDEN_RUNTIME:
        if pat in code:
            fail(f"forbidden grok2api/local-gateway integration pattern in runtime scripts: {pat}")

    okx_hardening = {
        "entry_score.py": (
            "entry_score.v1", "no_order_execution", "SENSITIVE_FIELD_NAMES", "entry_score_100",
            "cannot_authorize_action", "compiler_effect", "product_identity_channel_mix", "OverflowError",
        ),
        "okx_public_snapshot.py": (
            "okx_public_market_snapshot.v1", "no_order_execution", "credentials_used",
            "/api/v5/public/instruments", "MAX_PUBLIC_AGE_SECONDS", "future market timestamp",
            "must have positive size", "strictly descending", "strictly ascending", "OverflowError",
            "_derived_number", "_round_derived", "invalid derived numeric field",
        ),
        "okx_execution_supervisor.py": (
            "okx_execution_supervision.v1", "no_order_execution", "SENSITIVE_FIELD_NAMES",
            "pause_effective", "last_good_value", "account_hard_redline",
            "position_reconciliation_status", "order_state_status", "private_ws_connected",
            "live_credentials_not_rotated", "tighten_only", "order_detail_mismatches",
            "open_order_detail_mismatch", "quantity < 0", "_is_negative_zero", "math.copysign",
            "_parse_json_integer", "parse_int=_parse_json_integer", "duplicate_position_instrument", "OverflowError",
        ),
        "okx_monitor_dashboard.py": (
            "okx_execution_supervision.v1", "no_order_execution", "SENSITIVE_KEYS",
            "REFRESH_SECONDS = 30", "loopback_only_if_served", "Content-Security-Policy",
            "position_reconciliation_status", "order_state_status", "snapshot.entry_score", "OverflowError",
        ),
        "decision_compiler.py": (
            "_finite_multiplier", "OverflowError", "invalid_position_multiplier",
            "no_order_execution", "return 0.0", "MAX_JSON_INTEGER_DIGITS",
            "_parse_json_integer", "parse_int=_parse_json_integer", "math.copysign",
            "invalid_evidence_refs", "_evidence_refs_errors", "INVALID_JSON_INTEGER",
        ),
    }
    for filename, phrases in okx_hardening.items():
        text = (SCRIPTS / filename).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase not in text:
                fail(f"{filename} missing OKX safety phrase: {phrase}")

    prediction_code = (SCRIPTS / "prediction_ledger.py").read_text(encoding="utf-8")
    for phrase in (
        "probability_must_be_strictly_between_0_and_1",
        "resolution_threshold_pct_must_be_positive",
        "due_at_must_be_in_the_future",
        "verified_longbridge_symbol_required",
        "identity_mismatch",
        "stale_settlement_excluded",
        "status='unresolved'",
        "no_order_execution",
    ):
        if phrase not in prediction_code:
            fail(f"prediction ledger missing fail-closed phrase: {phrase}")

    for py in SCRIPTS.rglob("*.py"):
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))

    live_intel = (SCRIPTS / "live_intel_run.py").read_text(encoding="utf-8")
    for phrase in ("东方财富", "巨潮资讯", "WindClaw", "windclaw_cross_check", "dragon_tiger_list", "macro_dashboard_state", "issuance_lockup_overhang"):
        if phrase not in live_intel:
            fail(f"live intelligence plan missing phrase: {phrase}")

    multi_source = (SCRIPTS / "multi_source_search.py").read_text(encoding="utf-8")
    for phrase in ("DISCOVERY_ONLY", "freshness_hours", "published_at_unknown", "analyst@example.com", "no_order_execution"):
        if phrase not in multi_source:
            fail(f"multi-source search missing hardening phrase: {phrase}")

    coverage_code = (SCRIPTS / "intelligence_coverage.py").read_text(encoding="utf-8")
    for phrase in ("coverage_id", "evidence_refs", "COVERAGE_SIGNAL_TTL_MINUTES", "fallback_level", "for severity in"):
        if phrase not in coverage_code:
            fail(f"intelligence coverage missing strict-chain phrase: {phrase}")

    watch_code = (SCRIPTS / "research_watch_trigger.py").read_text(encoding="utf-8")
    for phrase in ("ALLOWED_ACTIONS", "ALLOWED_FIELDS", "request_manual_review", "no_external_side_effects", "requires_decision_recompile"):
        if phrase not in watch_code:
            fail(f"research watch trigger missing safety phrase: {phrase}")

    provenance_code = (SCRIPTS / "provenance_guard.py").read_text(encoding="utf-8")
    for phrase in ("research_provenance.v1", "source_hash_mismatch", "quote_not_exact", "behavior_causality_forbidden", "MAX_PUBLIC_EXCERPT_CHARS", "no_order_execution"):
        if phrase not in provenance_code:
            fail(f"provenance guard missing safety phrase: {phrase}")
    provenance_fixture = run_json([
        sys.executable, str(SCRIPTS / "provenance_guard.py"),
        "--bundle", str(ROOT / "templates" / "research-provenance-bundle-pass.json"),
    ])
    if not provenance_fixture.get("ok") or provenance_fixture.get("provenance_readiness") != "verified":
        fail(f"provenance guard fixture failed: {provenance_fixture}")
    kol_code = (SCRIPTS / "kol_method_card.py").read_text(encoding="utf-8")
    for phrase in (
        "KOL_LIFECYCLE", "self_reported_performance", "kol_decision_boundary_invalid",
        "raise_action_level", "revive_l0", "request_manual_review",
    ):
        if phrase not in kol_code:
            fail(f"KOL method-card validator missing safety phrase: {phrase}")
    kol_fixture = run_json([
        sys.executable, str(SCRIPTS / "provenance_guard.py"),
        "--bundle", str(ROOT / "templates" / "kol-method-cards-public-ledger.json"),
    ])
    expected_cards = {
        "KMC-CITRINI-20260717", "KMC-BALDER-20260717",
        "KMC-BALDER-20260731-FINAL", "KMC-BALDER-EARNINGS-RADAR-20260821",
        "KMC-FRANK-20260717",
    }
    kol_memory_link = kol_fixture.get("memory_link", {})
    kol_signal = kol_fixture.get("suggested_module_signal", {})
    if (
        not kol_fixture.get("ok")
        or kol_fixture.get("provenance_readiness") != "partial"
        or set(kol_memory_link.get("kol_method_card_ids", [])) != expected_cards
        or kol_memory_link.get("accepted_claim_ids") != []
        or kol_memory_link.get("canonical_eids") != []
        or kol_memory_link.get("materiality_eligible") is not False
        or kol_signal.get("module") != "x_frontline"
        or kol_signal.get("sub_framework") != "kol_method_card"
        or kol_signal.get("max_action_level") != "L0"
        or kol_signal.get("position_multiplier") != 0.0
        or kol_signal.get("tighten_only") is not True
        or kol_signal.get("no_order_execution") is not True
    ):
        fail(f"KOL public-ledger fixture failed: {kol_fixture}")

    method_router = (SCRIPTS / "method_router.py").read_text(encoding="utf-8")
    for phrase in ("macro_policy_news_account", "endogenous_microstructure", "US_flow_rotation", "supply_chain_xray", "early_stage_quality", "top_weights"):
        if phrase not in method_router:
            fail(f"method_router missing {phrase}")

    decision_text = (ROOT / "references" / "decision-compiler.md").read_text(encoding="utf-8")
    compiler_source = (SCRIPTS / "decision_compiler.py").read_text(encoding="utf-8")
    mapping_errors = method_module_mapping_errors(decision_text, method_router, compiler_source)
    mapping_errors.extend(overlay_module_contract_errors(decision_text, ROOT / "references"))
    if mapping_errors:
        fail("; ".join(mapping_errors))

    hypothesis_registry = (SCRIPTS / "hypothesis_registry.py").read_text(encoding="utf-8")
    for phrase in ("reconcile", "read_only", "no_recent_evidence", "latest_verdict_has_no_corresponding_factor"):
        if phrase not in hypothesis_registry:
            fail(f"hypothesis_registry missing reconciliation contract phrase: {phrase}")

    memory_self_test = run_json([sys.executable, str(SCRIPTS / "trading_memory.py"), "self-test", "--json"])
    if not memory_self_test.get("ok") or memory_self_test.get("memory_multiplier", 1) >= 1:
        fail(f"trading memory self-test failed: {memory_self_test}")

    memory_think = run_json([sys.executable, str(SCRIPTS / "trading_memory.py"), "--db", ":memory:", "think", "--json"])
    if not memory_think.get("ok") or "triggers" not in memory_think:
        fail(f"trading memory think failed: {memory_think}")

    # Self-evolution loops A/B/C + the result-recorder: each ships a deterministic self-test.
    for script in ("calibration_scorecard.py", "eval_candidate_generator.py", "learning_digest.py", "prediction_ledger.py", "record_due_results.py", "hypothesis_registry.py", "multi_source_search.py", "intelligence_coverage.py", "factor_panel.py", "factor_engine.py", "factor_backtest.py", "factor_verdict.py", "premarket_screen.py", "options_positioning_snapshot.py", "earnings_move_history.py", "earnings_implied_distribution.py"):
        result = run_json([sys.executable, str(SCRIPTS / script), "--self-test"])
        if not result.get("ok") or result.get("self_test") != "passed":
            fail(f"{script} self-test failed: {result}")


def routing_eval_checks() -> int:
    evals = []
    for i, line in enumerate((ROOT / "templates/routing-evals.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        obj = json.loads(line)
        for key in ("query", "expect_load", "skill", "reason"):
            if key not in obj:
                fail(f"routing eval line {i} missing {key}")
        evals.append(obj)

    checks = [
        any("Gamma" in e["query"] or "Put Wall" in e["query"] for e in evals),
        any("VIX" in e["query"] or "杀杠杆" in e["query"] or "GEX 出逃" in e["query"] for e in evals),
        any("不要用 grok2api" in e["query"] for e in evals),
        any("Longbridge" in e["query"] or "IBKR" in e["query"] or "宏观" in e["query"] for e in evals),
        any("A股" in e["query"] or "A 股" in e["query"] or "300846" in e["query"] for e in evals),
        any("pair trade" in e["query"] or "拥挤交易" in e["query"] or "解禁" in e["query"] for e in evals),
        any("一线" in e["query"] or "X 上" in e["query"] or "交叉验证" in e["query"] for e in evals),
        any("证据覆盖" in e["query"] or "抓取预算" in e["query"] for e in evals),
        any("复核触发器" in e["query"] or "ResearchWatchTrigger" in e["query"] for e in evals),
    ]
    labels = [
        "missing Gamma/Put Wall routing eval",
        "missing deleveraging/VIX routing eval",
        "missing Hermes-Grok-not-grok2api routing eval",
        "missing top-down/broker-aware routing eval",
        "missing A-share routing eval",
        "missing endogenous-flow/crowding routing eval",
        "missing X frontline / cross-check routing eval",
        "missing intelligence coverage / fair queue routing eval",
        "missing research watch trigger routing eval",
    ]
    for ok, label in zip(checks, labels):
        if not ok:
            fail(label)
    return len(evals)


def market_router_checks() -> int:
    count = 0
    for line in (ROOT / "templates/market-router-evals.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected = json.loads(line)
        result = run_json([sys.executable, str(SCRIPTS / "market_router.py"), expected["query"]])
        got = result[0]
        for key in ("market", "symbol", "longbridge_symbol"):
            if got.get(key) != expected.get(key):
                fail(f"market-router {expected['query']} expected {key}={expected.get(key)} got {got.get(key)}")
        count += 1
    return count


def method_router_checks() -> int:
    count = 0
    with tempfile.TemporaryDirectory(prefix="method-router-state-") as state_root:
        eval_env = os.environ.copy()
        eval_env["TRADING_RESEARCH_STATE_DIR"] = state_root
        for line in (ROOT / "templates/method-router-evals.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            args = case["args"]
            cmd = [
                sys.executable,
                str(SCRIPTS / "method_router.py"),
                "--market", args.get("market", "US"),
                "--horizon", args.get("horizon", "swing"),
                "--theme", args.get("theme", ""),
            ]
            if args.get("options_heavy"):
                cmd.append("--options-heavy")
            if args.get("macro_policy"):
                cmd.append("--macro-policy")
            got = run_json(cmd, env=eval_env)
            if case.get("expect_scenario") and got.get("scenario") != case["expect_scenario"]:
                fail(f"method-router expected scenario {case['expect_scenario']} got {got.get('scenario')}")
            if case.get("must_macro_policy_context") and not got.get("macro_policy_context"):
                fail("method-router macro_policy_context false")
            weights = got.get("weights", {})
            if sum(weights.values()) != case.get("weights_sum", 100):
                fail(f"method weights sum != 100: {weights}")
            for k, min_v in case.get("must_weight_gte", {}).items():
                if weights.get(k, 0) < min_v:
                    fail(f"method weight {k} expected >= {min_v}, got {weights.get(k)}")
            count += 1
    return count


def short_cycle_checks() -> int:
    count = 0
    fixture = ROOT / "templates/short-cycle-structure-evals.jsonl"
    script = SCRIPTS / "short_cycle_structure.py"

    def dotted(obj: Any, key: str) -> Any:
        current = obj
        for part in key.split("."):
            current = current[int(part)] if isinstance(current, list) else current[part]
        return current

    for line_no, line in enumerate(fixture.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(case["input"]),
            text=True,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode:
            fail(f"short-cycle fixture line {line_no} process failed: {proc.stderr or proc.stdout}")
        got = json.loads(proc.stdout)
        for key, expected in case["expect"].items():
            actual = dotted(got, key)
            if actual != expected:
                fail(f"short-cycle {case['name']} {key}: expected {expected!r}, got {actual!r}")
        count += 1
    return count


def report_contract_checks() -> None:
    subprocess.run([
        sys.executable, str(SCRIPTS / "validate_report.py"), str(ROOT / "templates/report-contract-pass.md"),
        "--provenance-bundle", str(ROOT / "templates/research-provenance-bundle-pass.json"),
    ], check=True, timeout=30)
    bad = subprocess.run([sys.executable, str(SCRIPTS / "validate_report.py"), str(ROOT / "templates/report-contract-fail-no-sources.md")], capture_output=True, text=True, timeout=30)
    if bad.returncode == 0:
        fail("bad report fixture unexpectedly passed")
    too_long = subprocess.run([sys.executable, str(SCRIPTS / "validate_report.py"), str(ROOT / "templates/report-contract-fail-too-long.md")], capture_output=True, text=True, timeout=30)
    if too_long.returncode == 0:
        fail("too-long report fixture unexpectedly passed")


def symlink_checks(all_discovered: bool = False) -> None:
    report = symlink_report(all_discovered=all_discovered, fix=False)
    if not report.get("ok"):
        fail(f"symlink validation failed: {json.dumps(report, ensure_ascii=False)[-1600:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="run the complete offline validation and self-test suite")
    ap.add_argument("--symlinks", action="store_true")
    ap.add_argument("--all-discovered", action="store_true")
    ap.add_argument("--fix-symlinks", action="store_true")
    ap.add_argument("--scenario-only", action="store_true")
    args = ap.parse_args()
    if args.all:
        return run_all_checks()
    if args.scenario_only:
        print(json.dumps({"ok": True, "scenario_evals": scenario_contract_checks()}, ensure_ascii=False))
        return 0
    if args.fix_symlinks:
        report = symlink_report(all_discovered=args.all_discovered, fix=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1
    offline_checks()
    routing_count = routing_eval_checks()
    market_count = market_router_checks()
    method_count = method_router_checks()
    short_cycle_count = short_cycle_checks()
    report_contract_checks()
    scenario_count = scenario_contract_checks()
    if args.symlinks or args.all_discovered:
        symlink_checks(all_discovered=args.all_discovered)
    print(json.dumps({
        "ok": True,
        "root": str(ROOT),
        "routing_evals": routing_count,
        "market_router_evals": market_count,
        "method_router_evals": method_count,
        "short_cycle_evals": short_cycle_count,
        "scenario_evals": scenario_count,
        "python_files": len(list(SCRIPTS.rglob("*.py"))),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
