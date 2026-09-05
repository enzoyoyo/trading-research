#!/usr/bin/env python3
"""Deterministic preflight for trading-research daily self-optimization.

This script is intentionally conservative: it gathers source health and runs
validators, but it does not edit files or execute trades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import urllib.request
import importlib.util
from datetime import date, datetime, timedelta, timezone
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HOME = pathlib.Path.home()
SUPPORTED_LEARNING_PACKET_SCHEMAS = {"paper_learning_packet.v2"}
JOURNAL_STALE_DAYS = 3
CALIBRATION_MIN_SAMPLES = 12  # mirrors calibration_scorecard.DEFAULT_MIN_SAMPLES

# ---- deep-check config (v2.46 A3) ------------------------------------------
# See deep_audit_and_repair_plan_20260726.md findings ledger-health-blind-to-
# missed-runs and paper-calibration-loop-stalled-reported-as-pending. Both are
# the same shape of bug: a health/liveness check that only inspects rows/state
# that already exist goes blind the moment the upstream cron stops producing
# rows at all, or the upstream data it references silently vanishes.
DEFAULT_TRADING_MEMORY_DB = HOME / ".cache" / "hermes" / "trading-research" / "memory" / "trading_memory.sqlite"
DEFAULT_PAPER_POSITION_SNAPSHOTS = HOME / ".hermes" / "longbridge-paper-trading" / "journal" / "paper_position_snapshots.jsonl"
PENDING_CALIBRATION_TABLE = "calibration_pending_paper_predictions"
# Same orphan-triage convention as System A's scripts/paper_watchdog.py: a
# pending prediction the fill-reconciler (A1) has already tagged orphaned /
# closed_unreconciled has been handled and dealt with -- do not re-alert on it.
_ORPHANED_FLAG_KEYS = ("orphaned", "is_orphaned")
_ORPHANED_STATUS_KEYS = ("status", "reconciliation_status", "lifecycle_status")
_ORPHANED_STATUS_VALUES = {"orphaned", "closed_unreconciled"}
SELF_OPT_CRON_JOB_ID = os.environ.get("TRADING_RESEARCH_SELF_OPT_CRON_JOB_ID", "").strip()
DEFAULT_CRON_EXECUTIONS_DB = HOME / ".hermes" / "cron" / "executions.db"

REFERENCE_REPOS = [
    "OpenBB-finance/OpenBB",
    "freqtrade/freqtrade",
    "microsoft/qlib",
    "microsoft/RD-Agent",
    "quantconnect/Lean",
    "polakowo/vectorbt",
    "AI4Finance-Foundation/FinRL",
    "AI4Finance-Foundation/FinRL-Trading",
    "AI4Finance-Foundation/FinGPT",
    "mementum/backtrader",
    "robertmartin8/PyPortfolioOpt",
    "quantopian/pyfolio",
]

REQUIRED_REFERENCES = [
    "references/grok-web-research-layer.md",
    "references/open-source-quant-research-patterns.md",
    "references/polymarket-signal-layer.md",
    "references/adaptive-self-optimization.md",
    "references/x-frontline-intelligence.md",
    "references/decision-compiler.md",
    "references/mira-quality-gates.md",
]


def safe_run(cmd: list[str], timeout: int = 120) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "cmd": cmd,
            "stdout_tail": (proc.stdout or "")[-1200:],
            "stderr_tail": (proc.stderr or "")[-1200:],
        }
    except Exception as exc:  # pragma: no cover - defensive for cron environments
        return {"ok": False, "cmd": cmd, "error": type(exc).__name__, "message": str(exc)}


def github_repo(repo: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "hermes-trading-research-self-check"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.load(resp)
    return {
        "repo": repo,
        "stars": data.get("stargazers_count"),
        "forks": data.get("forks_count"),
        "pushed_at": data.get("pushed_at"),
        "updated_at": data.get("updated_at"),
        "archived": data.get("archived"),
        "description": data.get("description"),
        "license": (data.get("license") or {}).get("spdx_id"),
    }


def github_snapshot(skip_network: bool) -> dict[str, Any]:
    if skip_network:
        return {"skipped": True, "reason": "skip_network"}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for repo in REFERENCE_REPOS:
        try:
            rows.append(github_repo(repo))
        except Exception as exc:
            errors.append({"repo": repo, "error": type(exc).__name__, "message": str(exc)})
    return {"ok": not errors, "repos": rows, "errors": errors}


def grok_auth_presence() -> dict[str, Any]:
    auth = HOME / ".hermes" / "auth.json"
    if not auth.exists():
        return {"present": False, "reason": "auth.json missing"}
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"present": False, "reason": f"auth.json parse failed: {type(exc).__name__}"}
    providers = data.get("providers", {}) if isinstance(data, dict) else {}
    pool = data.get("credential_pool", {}) if isinstance(data, dict) else {}
    return {
        "present": bool(providers.get("xai-oauth") or pool.get("xai-oauth")),
        "provider_keys": [k for k in providers.keys() if "xai" in k.lower() or "grok" in k.lower()],
        "pool_keys": [k for k in pool.keys() if "xai" in k.lower() or "grok" in k.lower()],
    }


def grok_health(enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {"skipped": True, "reason": "use --grok-health to spend a tiny xAI call"}
    return safe_run([sys.executable, str(SCRIPTS / "live_intel_run.py"), "--health", "--json", "--timeout", "90"], timeout=120)


def load_validate_skill_module():
    spec = importlib.util.spec_from_file_location("trading_research_validate_skill", SCRIPTS / "validate_skill.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load validate_skill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_eval(name: str, fn) -> dict[str, Any]:
    try:
        count = fn()
        return {"ok": True, "count": count}
    except SystemExit as exc:
        return {"ok": False, "error": "SystemExit", "code": getattr(exc, "code", None), "name": name}
    except Exception as exc:  # pragma: no cover - defensive for cron environments
        return {"ok": False, "error": type(exc).__name__, "message": str(exc), "name": name}


def eval_suite_snapshot(run: bool) -> dict[str, Any]:
    if not run:
        return {"skipped": True}
    module = load_validate_skill_module()
    scenario = safe_run([sys.executable, str(SCRIPTS / "validate_skill.py"), "--scenario-only"], timeout=60)
    scenario_count = None
    if scenario.get("ok"):
        try:
            scenario_count = json.loads(scenario.get("stdout_tail") or "{}").get("scenario_evals")
        except Exception:
            scenario_count = None
    oq = safe_run([sys.executable, str(SCRIPTS / "output_quality_regression.py")], timeout=60)
    oq_count = None
    if oq.get("ok"):
        try:
            oq_count = json.loads(oq.get("stdout_tail") or "{}").get("output_quality_evals")
        except Exception:
            oq_count = None
    suites = {
        "routing": call_eval("routing", module.routing_eval_checks),
        "market_router": call_eval("market_router", module.market_router_checks),
        "method_router": call_eval("method_router", module.method_router_checks),
        "scenario_regression": {"ok": bool(scenario.get("ok")), "count": scenario_count, "stdout_tail": scenario.get("stdout_tail"), "stderr_tail": scenario.get("stderr_tail")},
        "output_quality": {"ok": bool(oq.get("ok")), "count": oq_count, "stdout_tail": oq.get("stdout_tail"), "stderr_tail": oq.get("stderr_tail")},
    }
    passed = sum(1 for row in suites.values() if row.get("ok"))
    return {"ok": passed == len(suites), "passed": passed, "total": len(suites), "suites": suites}


def compare_eval_baseline(current: dict[str, Any], baseline_path: str | None) -> dict[str, Any]:
    if not baseline_path:
        return {"skipped": True}
    try:
        baseline_doc = json.loads(pathlib.Path(baseline_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "baseline_read_failed", "message": str(exc)}
    baseline = baseline_doc.get("eval_suite") if isinstance(baseline_doc, dict) else None
    if not isinstance(baseline, dict):
        return {"ok": False, "error": "baseline_missing_eval_suite"}
    regressions = []
    for name, before in (baseline.get("suites") or {}).items():
        after = (current.get("suites") or {}).get(name, {})
        if before.get("ok") and not after.get("ok"):
            regressions.append(name)
    before_passed = int(baseline.get("passed", 0) or 0)
    after_passed = int(current.get("passed", 0) or 0)
    if after_passed < before_passed:
        regressions.append("overall_pass_count")
    return {"ok": not regressions, "baseline_passed": before_passed, "current_passed": after_passed, "regressions": sorted(set(regressions))}


def local_files() -> dict[str, Any]:
    missing = [rel for rel in REQUIRED_REFERENCES if not (ROOT / rel).exists()]
    skill = ROOT / "SKILL.md"
    version = None
    if skill.exists():
        for line in skill.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("version:"):
                version = line.split(":", 1)[1].strip()
                break
    return {"ok": not missing, "missing": missing, "version": version}


def validators(run: bool, skip_network: bool) -> dict[str, Any]:
    if not run:
        return {"skipped": True}
    checks = {
        "scenario_regression": safe_run([sys.executable, str(SCRIPTS / "validate_skill.py"), "--scenario-only"], timeout=60),
        "python_compile": safe_run([sys.executable, "-m", "py_compile", *[str(p) for p in SCRIPTS.glob("*.py")]], timeout=120),
    }
    if skip_network:
        # validate_skill is mostly offline but can run helper scripts; keep it separate for diagnostics.
        checks["validate_skill"] = safe_run([sys.executable, str(SCRIPTS / "validate_skill.py")], timeout=180)
    else:
        checks["validate_skill"] = safe_run([sys.executable, str(SCRIPTS / "validate_skill.py")], timeout=180)
    return {"ok": all(v.get("ok") for v in checks.values()), "checks": checks}


def run_json(cmd: list[str], timeout: int = 60) -> dict[str, Any]:
    """Run a command and parse its FULL stdout as JSON (safe_run truncates)."""
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # pragma: no cover - defensive for cron environments
        return {"_ok": False, "_error": type(exc).__name__, "_message": str(exc)}
    if proc.returncode != 0:
        return {"_ok": False, "_returncode": proc.returncode, "_stderr": (proc.stderr or "")[-600:]}
    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        return {"_ok": False, "_error": f"json_parse_{type(exc).__name__}"}
    return {**data, "_ok": True}


def mechanism_learning_snapshot(value: Any) -> dict[str, Any]:
    """Carry descriptive research separately; it never grants learning eligibility."""
    boundary = {"action_authority": False, "materiality_eligible": False,
                "changes_existing_learning_gates": False}
    if not isinstance(value, dict) or value.get("schema_version") != "paper_mechanism_learning.v1":
        return {"state": "unknown", "feedback": None,
                "data_gaps": ["mechanism_learning_missing_or_invalid_schema"], **boundary}
    return {**value, **boundary}


def latest_learning_packet() -> dict[str, Any]:
    packet_dir = pathlib.Path(
        os.environ.get(
            "PAPER_LEARNING_PACKET_DIR",
            str(HOME / ".hermes" / "longbridge-paper-trading" / "learning_packets"),
        )
    )
    if not packet_dir.exists():
        return {"status": "missing", "reason": "packet_dir_absent", "dir": str(packet_dir)}
    packets = list(packet_dir.glob("paper_learning_packet_*.json"))
    if not packets:
        return {"status": "missing", "reason": "no_packets", "dir": str(packet_dir)}
    latest = max(packets, key=lambda p: p.stat().st_mtime)
    try:
        packet_text = latest.read_text(encoding="utf-8")
        doc = json.loads(packet_text)
    except Exception as exc:
        return {"status": "error", "reason": f"parse_failed_{type(exc).__name__}", "file": latest.name}
    schema_version = doc.get("schema_version") if isinstance(doc, dict) else None
    if schema_version not in SUPPORTED_LEARNING_PACKET_SCHEMAS:
        return {
            "status": "error",
            "reason": "packet_schema_mismatch",
            "file": latest.name,
            "schema_version": schema_version,
            "supported_schema_versions": sorted(SUPPORTED_LEARNING_PACKET_SCHEMAS),
        }
    from paper_learning_consumer import consume, read_history
    from self_optimization_ledger import ledger_path
    paper_learning = consume(doc, read_history(ledger_path()), packet_file=latest.name)
    paper_learning["packet_content_sha256"] = hashlib.sha256(packet_text.encode("utf-8")).hexdigest()
    return {
        "status": "present",
        "file": latest.name,
        "paper_learning_consumption": paper_learning,
        "schema_version": schema_version,
        "summary": doc.get("summary"),
        "materiality_gate": doc.get("materiality_gate"),
        "skill_upgrade_candidates": doc.get("skill_upgrade_candidates") or [],
        "mechanism_research": mechanism_learning_snapshot(doc.get("mechanism_research")),
    }


def trading_memory_review() -> dict[str, Any]:
    doc = run_json([sys.executable, str(SCRIPTS / "trading_memory.py"), "review", "--json"], timeout=60)
    if not doc.get("_ok"):
        return {"status": "error", "error": doc.get("_error") or doc.get("_stderr") or doc.get("_message")}
    stats = doc.get("stats") or {}
    sample_count = int(doc.get("sample_count") or stats.get("sample_count") or 0)
    return {
        "status": "ok" if sample_count > 0 else "empty",
        "adjustment_applied": bool(doc.get("adjustment_applied")),
        "sample_count": sample_count,
        "min_samples": doc.get("min_samples"),
        "reason": doc.get("reason"),
        "degrading_factors": stats.get("degrading_factors") or {},
        "biggest_drag_symbols": stats.get("biggest_drag_symbols") or [],
        "failure_tags": stats.get("failure_tags") or {},
    }


def calibration_snapshot() -> dict[str, Any]:
    """Advisory read-only confidence-honesty check (loop B).

    Process-isolated like the other suites; never blocks validators.
    """
    doc = run_json([sys.executable, str(SCRIPTS / "calibration_scorecard.py"), "--source", "skill", "--window", "200"], timeout=60)
    if not doc.get("_ok"):
        return {"status": "error", "error": doc.get("_error") or doc.get("_stderr") or doc.get("_message")}
    return {
        "status": doc.get("calibration_status", "insufficient"),
        "calibration_gap": doc.get("calibration_gap"),
        "confidence_posture": doc.get("confidence_posture"),
        "calibration_materiality": doc.get("calibration_materiality", "none"),
        "samples_with_prediction": doc.get("samples_with_prediction"),
    }


def performance_snapshot() -> dict[str, Any]:
    """Advisory read-only view of the skill's OWN track record.

    Derives performance_materiality strictly from EXISTING anti-overfit gates:
    the learning packet's separate research and per-strategy sizing gates, the
    decision-memory's gates (min_samples, pattern thresholds), and the
    calibration scorecard (loop B). Never blocks validators and never moves
    live sizing.
    """
    packet = latest_learning_packet()
    memory = trading_memory_review()
    calibration = calibration_snapshot()

    gate = packet.get("materiality_gate") or {}
    packet_sizing_eligible = bool(gate.get("enough_for_policy_sizing_change"))
    paper_learning = packet.get("paper_learning_consumption") or {}
    # Structured research eligibility is separate from the 30-sample sizing gate.
    # A reviewed/repeated real sample set does not become fresh evidence on a timer.
    structured_learning = paper_learning.get("structured_evidence_present") is True
    packet_research_eligible = (paper_learning.get("research_upgrade_eligible") is True
                               and paper_learning.get("new_review_candidate_count", 0) > 0)
    if structured_learning:
        packet_sizing_eligible = (packet_sizing_eligible and paper_learning.get("status") == "present"
                                  and paper_learning.get("new_independent_sample_count", 0) > 0)
    packet_candidates = [] if structured_learning else packet.get("skill_upgrade_candidates") or []
    memory_adjustment = bool(memory.get("adjustment_applied"))
    mem_samples = int(memory.get("sample_count") or 0)
    mem_min = int(memory.get("min_samples") or 12)
    memory_gate_passed = mem_samples >= mem_min and bool(memory.get("degrading_factors"))
    # Calibration alone can flag a methodology look (medium) but never a sizing
    # change (high): an honest-confidence drift is not a profitability signal.
    calibration_material = calibration.get("calibration_materiality") in ("high", "medium")

    drivers: list[str] = []
    if packet_sizing_eligible:
        drivers.append("learning_packet:enough_for_policy_sizing_change")
    if memory_adjustment:
        drivers.append("trading_memory:adjustment_applied")
    if packet_research_eligible:
        drivers.append("learning_packet:enough_for_trading_research_skill_upgrade_with_new_real_evidence")
    if packet_candidates:
        drivers.append(f"learning_packet:skill_upgrade_candidates({len(packet_candidates)})")
    if memory_gate_passed:
        drivers.append("trading_memory:gate_passed_degrading_factor")
    if calibration_material:
        drivers.append(f"calibration:{calibration.get('confidence_posture')}_gap_material")

    if packet_sizing_eligible or memory_adjustment:
        materiality = "high"
    elif packet_research_eligible or packet_candidates or memory_gate_passed or calibration_material:
        materiality = "medium"
    else:
        materiality = "none"

    return {
        "learning_packet_status": packet.get("status", "missing"),
        "learning_packet": packet,
        "paper_learning_consumption": paper_learning,
        "research_upgrade_eligible": paper_learning.get("research_upgrade_eligible", False),
        "trading_memory_status": memory.get("status", "error"),
        "trading_memory_review": memory,
        "calibration_status": calibration.get("status", "error"),
        "calibration": calibration,
        "performance_materiality": materiality,
        "drivers": drivers,
        "advisory": True,
        "note": "Advisory: never blocks validators or trades. A performance-driven patch must pass an anti-overfit gate AND be encoded as a scenario regression case.",
    }


def weekly_digest_check(force: bool = False) -> dict[str, Any]:
    """Run the weekly plain-Chinese digest when due.

    This is intentionally embedded in the existing daily self-check path so the
    cron schedule itself does not need to change. Due rule: Monday local run or
    latest digest older than 7 days.
    """
    digest_dir = HOME / ".hermes" / "work" / "trading-research-autoevolve" / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    latest = max(digest_dir.glob("weekly-*.md"), key=lambda p: p.stat().st_mtime, default=None)
    now = datetime.now(timezone.utc).astimezone()
    age_days = None
    if latest is not None:
        age_days = (now.timestamp() - latest.stat().st_mtime) / 86400
    due = force or now.weekday() == 0 or latest is None or (age_days is not None and age_days >= 7)
    if not due:
        return {"status": "skipped", "reason": "not_due", "latest": str(latest) if latest else None, "latest_age_days": round(age_days, 2) if age_days is not None else None}
    out = digest_dir / f"weekly-{now.date().isoformat()}.md"
    if out.exists() and force:
        out = digest_dir / f"weekly-{now.strftime('%Y-%m-%d-%H%M%S')}.md"
    result = safe_run([sys.executable, str(SCRIPTS / "learning_digest.py"), "--out", str(out)], timeout=120)
    return {"status": "written" if result.get("ok") else "error", "due_reason": "force" if force else ("monday_or_stale"), "out": str(out), "result": result}


def journal_liveness() -> dict[str, Any]:
    """Advisory: is System A's daily-journal cron actually publishing pages."""
    journal_dir = HOME / ".hermes" / "trading-journal" / "daily"
    if not journal_dir.exists():
        return {"status": "missing", "dir": str(journal_dir), "finding": "daily journal directory absent: daily_journal.py --publish has never run"}
    pages = list(journal_dir.glob("*.md"))
    if not pages:
        return {"status": "missing", "dir": str(journal_dir), "finding": "daily journal directory exists but has no pages"}
    latest = max(pages, key=lambda p: p.stat().st_mtime)
    age_days = (datetime.now(timezone.utc).timestamp() - latest.stat().st_mtime) / 86400
    stale = age_days > JOURNAL_STALE_DAYS
    result: dict[str, Any] = {"status": "stale" if stale else "ok", "latest_file": latest.name, "age_days": round(age_days, 1)}
    if stale:
        result["finding"] = (
            f"daily journal stale: newest page {latest.name} is {round(age_days, 1)}d old "
            f"(>{JOURNAL_STALE_DAYS}d threshold); System A's daily cron is not calling daily_journal.py --publish"
        )
    return result


def hypothesis_registry_liveness() -> dict[str, Any]:
    """Advisory: ledger liveness plus read-only latest-factor reconciliation."""
    doc = run_json([sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "list"], timeout=30)
    if not doc.get("_ok"):
        return {"status": "error", "error": doc.get("_error") or doc.get("_stderr") or doc.get("_message")}
    count = int(doc.get("count") or 0)
    result: dict[str, Any] = {"status": "empty" if count == 0 else "ok", "count": count}
    reconciliation = run_json(
        [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "reconcile"], timeout=30
    )
    result["reconciliation"] = reconciliation
    if not reconciliation.get("_ok") or not reconciliation.get("ok"):
        result["status"] = "error"
        result["finding"] = (
            "hypothesis registry factor reconciliation failed: "
            f"{reconciliation.get('_error') or reconciliation.get('_stderr') or reconciliation.get('_message') or reconciliation.get('status')}"
        )
        return result
    if reconciliation.get("ambiguous"):
        result["status"] = "ambiguous_factor_identity"
        result["finding"] = (
            "hypothesis registry factor reconciliation found ambiguous records; "
            "review duplicate factor reconciliation keys without auto-updating status"
        )
        return result
    drift_count = int(reconciliation.get("drift_count") or 0)
    if drift_count:
        result["status"] = "factor_status_drift"
        result["finding"] = (
            f"hypothesis registry has {drift_count} factor status drift item(s) versus the latest verdict; "
            "reconcile is read-only and did not auto-update status"
        )
        return result
    no_recent_count = int(reconciliation.get("no_recent_evidence_count") or 0)
    if no_recent_count:
        result["status"] = "no_recent_evidence"
        result["finding"] = (
            f"hypothesis registry has {no_recent_count} factor record(s) with no corresponding factor in the latest verdict; "
            "this is missing recent evidence, not status drift"
        )
        return result
    not_judgeable_count = int(reconciliation.get("not_judgeable_count") or 0)
    if not_judgeable_count:
        result["status"] = "latest_factor_not_judgeable"
        result["finding"] = (
            f"latest factor verdict could not judge {not_judgeable_count} matched registry record(s); "
            "registry status remains unchanged"
        )
        return result
    if count == 0:
        result["finding"] = "hypothesis registry is empty: factor/signal conclusions are not being registered per SKILL.md fixed-process step 3 write obligation"
    return result


def _pending_prediction_is_orphaned(row: dict[str, Any]) -> bool:
    for key in _ORPHANED_FLAG_KEYS:
        if row.get(key):
            return True
    for key in _ORPHANED_STATUS_KEYS:
        if str(row.get(key) or "").lower() in _ORPHANED_STATUS_VALUES:
            return True
    return False


def _latest_position_symbols(path: pathlib.Path = DEFAULT_PAPER_POSITION_SNAPSHOTS) -> set[str] | None:
    """Read-only: symbols in the last line of System A's position snapshot
    journal. Returns None (not an empty set) when the file/line is unreadable
    so callers can tell "no snapshot data" apart from "snapshot has zero
    positions"."""
    if not path.exists():
        return None
    last_line = None
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line:
            last_line = line
    if last_line is None:
        return None
    try:
        doc = json.loads(last_line)
    except json.JSONDecodeError:
        return None
    positions = doc.get("positions") if isinstance(doc, dict) else None
    if not isinstance(positions, list):
        return None
    return {str(row.get("symbol") or "").upper() for row in positions if isinstance(row, dict) and row.get("symbol")}


def _pending_calibration_rows(db_path: pathlib.Path = DEFAULT_TRADING_MEMORY_DB) -> list[dict[str, Any]]:
    """Read-only: rows currently persisted in calibration_pending_paper_predictions.

    Selects '*' rather than named columns so an upstream orphan-triage column
    (see _pending_prediction_is_orphaned) is picked up automatically, mirroring
    System A's scripts/paper_watchdog.py fetch_pending_calibration_predictions.
    """
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.OperationalError:
        return []
    try:
        cur = conn.execute(f"SELECT * FROM {PENDING_CALIBRATION_TABLE}")
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def calibration_liveness() -> dict[str, Any]:
    """Advisory: is the read-only paper calibration bucket sample-starved --
    and, more specifically, is a "starved/waiting" reading actually a broken
    data link (SKILL.md pitfall state 1: no position left to close) being
    misreported as the benign "waiting to close" (state 2).

    See finding paper-calibration-loop-stalled-reported-as-pending: this used
    to hardcode "root cause is System A trade-lifecycle throughput ... not a
    skill-side bug" for every pending prediction, which kept describing a dead
    fill_reconcile pipeline as a legitimate queue. A pending prediction whose
    symbol is no longer in System A's latest position snapshot has nothing
    left to wait for. Predictions already tagged orphaned/closed_unreconciled
    by A1's fill-reconciler triage are treated as handled, not re-alerted.
    """
    doc = run_json([sys.executable, str(SCRIPTS / "paper_outcome_calibration_feed.py"), "--dry-run", "--json"], timeout=60)
    if not doc.get("_ok"):
        return {"status": "error", "error": doc.get("_error") or doc.get("_stderr") or doc.get("_message")}
    paired = int(doc.get("paired_samples") or 0)
    pending = int(doc.get("pending_prediction_count") or 0)

    # Pass the module-level defaults explicitly (rather than relying on the
    # callees' own default parameter binding) so tests can patch
    # DEFAULT_TRADING_MEMORY_DB / DEFAULT_PAPER_POSITION_SNAPSHOTS at the
    # module level and have it take effect here.
    position_symbols = _latest_position_symbols(DEFAULT_PAPER_POSITION_SNAPSHOTS)
    broken: list[dict[str, Any]] = []
    orphaned_count = 0
    if position_symbols is not None:
        for row in _pending_calibration_rows(DEFAULT_TRADING_MEMORY_DB):
            if _pending_prediction_is_orphaned(row):
                orphaned_count += 1
                continue
            symbol = str(row.get("symbol") or "").upper()
            if symbol and symbol not in position_symbols:
                broken.append(row)

    starved = pending > 0 and paired < CALIBRATION_MIN_SAMPLES
    result: dict[str, Any] = {
        "status": "starved" if starved else "ok",
        "paired_samples": paired,
        "pending_prediction_count": pending,
        "min_samples_threshold": CALIBRATION_MIN_SAMPLES,
        "orphaned_pending_count": orphaned_count,
    }
    if broken:
        symbols = sorted({str(row.get("symbol")) for row in broken})
        result["status"] = "calibration_data_link_broken"
        result["broken_pending_count"] = len(broken)
        result["finding"] = (
            f"{len(broken)} pending calibration prediction(s) reference position(s) no longer in System "
            f"A's latest snapshot ({', '.join(symbols)}): SKILL.md pitfall state 2 (waiting to close) does "
            "not apply -- there is no position left to close, so this is state 1 (data link broken), not a "
            "throughput issue. Run paper_fill_reconciler.py and reconcile or mark these lifecycles "
            "orphaned/closed_unreconciled."
        )
    elif starved:
        result["finding"] = (
            f"paper calibration bucket sample-starved: {paired} paired sample(s) vs min_samples="
            f"{CALIBRATION_MIN_SAMPLES} threshold, {pending} prediction(s) still pending pairing; "
            "position-snapshot cross-check found no broken links, so this looks like genuine low "
            "trade-lifecycle throughput (see donchian_breakout diagnostic pause), not a skill-side bug"
        )
    return result


def git_drift_liveness() -> dict[str, Any]:
    """Advisory: does this skill's own repo have uncommitted working-tree drift."""
    status = safe_run(["git", "status", "--porcelain"], timeout=30)
    if not status.get("ok"):
        return {"status": "error", "error": status.get("stderr_tail") or status.get("message")}
    dirty = bool((status.get("stdout_tail") or "").strip())
    result: dict[str, Any] = {"status": "dirty" if dirty else "clean"}
    if dirty:
        result["finding"] = "trading-research skill git working tree has uncommitted drift; the self-optimization cron has write access but no commit discipline without this being caught"
    return result


def _load_self_optimization_ledger_module():
    spec = importlib.util.spec_from_file_location(
        "trading_research_self_optimization_ledger", SCRIPTS / "self_optimization_ledger.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load self_optimization_ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cron_execution_crosscheck(
    missing_dates: list[str],
    db_path: pathlib.Path = DEFAULT_CRON_EXECUTIONS_DB,
) -> dict[str, Any]:
    """Classify missing ledger dates from explicitly configured cron history."""
    if not SELF_OPT_CRON_JOB_ID:
        return {"status": "unavailable", "reason": "cron_job_id_not_configured"}
    if not db_path.exists():
        return {"status": "unavailable", "reason": "executions_db_missing", "db": str(db_path)}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.OperationalError as exc:
        return {"status": "unavailable", "reason": f"executions_db_open_failed:{type(exc).__name__}"}
    try:
        placeholders = ",".join("?" for _ in missing_dates)
        rows = conn.execute(
            f"SELECT status, substr(claimed_at, 1, 10) AS run_date FROM executions "
            f"WHERE job_id = ? AND substr(claimed_at, 1, 10) IN ({placeholders})",
            [SELF_OPT_CRON_JOB_ID, *missing_dates],
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return {"status": "unavailable", "reason": f"executions_query_failed:{type(exc).__name__}"}
    finally:
        conn.close()

    statuses_by_date: dict[str, set[str]] = {day: set() for day in missing_dates}
    for status, run_date in rows:
        if run_date in statuses_by_date:
            statuses_by_date[run_date].add(str(status))

    completed: list[str] = []
    failed: list[str] = []
    unfinished: list[str] = []
    absent: list[str] = []
    for day in sorted(missing_dates):
        statuses = statuses_by_date[day]
        if "completed" in statuses:
            completed.append(day)
        elif "failed" in statuses:
            failed.append(day)
        elif statuses:
            unfinished.append(day)
        else:
            absent.append(day)
    return {
        "status": "ok",
        "job_id": SELF_OPT_CRON_JOB_ID,
        "completed_without_ledger_append_dates": completed,
        "failed_execution_dates": failed,
        "unfinished_execution_dates": unfinished,
        "no_execution_record_dates": absent,
    }


def ledger_liveness(
    now: datetime | None = None,
    cron_db_path: pathlib.Path = DEFAULT_CRON_EXECUTIONS_DB,
) -> dict[str, Any]:
    """Advisory: does the self-optimization ledger have a missed-run gap.

    See finding ledger-health-blind-to-missed-runs: self_optimization_ledger.py
    query --health only inspects rows that exist (github/grok/blocked streaks,
    eval_pass_count drops), so a day where the daily cron fails before ever appending a row
    is invisible to it; it kept returning ok:true through the 07-24/07-25
    back-to-back cron timeouts. This cross-checks the ledger's *date sequence*
    against the cron's daily cadence instead of only the rows it produced.
    """
    now = now or datetime.now(timezone.utc)
    try:
        module = _load_self_optimization_ledger_module()
        rows = module.read_rows(module.ledger_path())
    except Exception as exc:  # pragma: no cover - defensive for cron environments
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    dates: list[date] = []
    for row in rows:
        raw = row.get("date")
        if not raw:
            continue
        try:
            dates.append(date.fromisoformat(str(raw)))
        except ValueError:
            continue
    if not dates:
        return {
            "status": "missing",
            "finding": "self-optimization ledger has no parseable rows: the append step (self_optimization_ledger.py append) has never run or has never succeeded",
        }

    latest = max(dates)
    today = now.date()
    # This check itself runs as the daily cron's first step, before that same
    # run's own ledger append -- so "no row for today yet" is expected and is
    # not a gap. Scan backward from yesterday and stop at the first day that
    # does have a row; older isolated gaps are historical backfill noise, not
    # live cron drift.
    missing = module.trailing_missing_dates(rows, today=today)

    gap_days = len(missing)
    gap_threshold = int(module.LEDGER_GAP_MIN_CONSECUTIVE_DAYS)
    result: dict[str, Any] = {
        "status": "gap" if gap_days >= gap_threshold else "ok",
        "latest_row_date": latest.isoformat(),
        "latest_row_age_days": (today - latest).days,
        "missing_trailing_dates": sorted(missing),
    }
    if gap_days >= gap_threshold:
        crosscheck = _cron_execution_crosscheck(sorted(missing), cron_db_path)
        result["cron_execution_crosscheck"] = crosscheck
        if crosscheck.get("status") == "ok":
            completed_count = len(crosscheck["completed_without_ledger_append_dates"])
            failed_count = len(crosscheck["failed_execution_dates"])
            unfinished_count = len(crosscheck["unfinished_execution_dates"])
            absent_count = len(crosscheck["no_execution_record_dates"])
            result["finding"] = (
                f"self-optimization ledger missing {gap_days} consecutive trailing day(s) "
                f"({', '.join(sorted(missing))}); read-only cron execution cross-check found "
                f"{completed_count} completed without ledger append, {failed_count} failed before append, "
                f"{unfinished_count} unfinished/unknown, and {absent_count} with no execution record. "
                "This separates closeout omission from transport failure; self_optimization_ledger.py query --health "
                "now reports the date-sequence gap, while this read-only cross-check adds causal attribution."
            )
        else:
            result["finding"] = (
                f"self-optimization ledger missing {gap_days} consecutive trailing day(s) "
                f"({', '.join(sorted(missing))}); cron execution cross-check is unavailable "
                f"({crosscheck.get('reason')}). Inspect {cron_db_path} read-only before attributing the gap; "
                "self_optimization_ledger.py query --health still reports the date-sequence gap without claiming a cause."
            )
    return result


def loop_liveness() -> dict[str, Any]:
    """Advisory closed-loop activity monitor (v2.44, v2.46 A3).

    Surfaces silent drift in the write-obligation loops this skill depends
    on: daily journal publishing, hypothesis registry writes, the paper
    calibration feed (including its cross-check against System A's live
    position snapshot), this repo's own git history, and the self-optimization
    ledger's own daily cadence. Never blocks validators or trades; findings
    are meant to be read, not auto-acted on.
    """
    journal = journal_liveness()
    hypotheses = hypothesis_registry_liveness()
    calibration = calibration_liveness()
    git_drift = git_drift_liveness()
    ledger = ledger_liveness()
    findings = [r["finding"] for r in (journal, hypotheses, calibration, git_drift, ledger) if r.get("finding")]
    return {
        "journal": journal,
        "hypothesis_registry": hypotheses,
        "calibration": calibration,
        "git_drift": git_drift,
        "ledger": ledger,
        "findings": findings,
        "advisory": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--run-validators", action="store_true")
    ap.add_argument("--skip-network", action="store_true")
    ap.add_argument("--grok-health", action="store_true")
    ap.add_argument("--eval-snapshot-out")
    ap.add_argument("--eval-baseline")
    ap.add_argument("--force-weekly-digest", action="store_true", help="force weekly learning digest generation for validation")
    args = ap.parse_args()

    eval_suite = eval_suite_snapshot(args.run_validators)
    eval_comparison = compare_eval_baseline(eval_suite, args.eval_baseline)
    out = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(ROOT),
        "local_files": local_files(),
        "grok_auth_presence": grok_auth_presence(),
        "grok_health": grok_health(args.grok_health),
        "github_reference_projects": github_snapshot(args.skip_network),
        "validators": validators(args.run_validators, args.skip_network),
        "eval_suite": eval_suite,
        "eval_comparison": eval_comparison,
        "performance_snapshot": performance_snapshot(),
        "weekly_digest": weekly_digest_check(args.force_weekly_digest),
        "loop_liveness": loop_liveness(),
        "materiality_rule": "Patch only when a verified change improves evidence quality, risk control, decision clarity, or fixes a real drift; otherwise report no_necessary_upgrade.",
        "validation_policy": "If any eval suite regresses versus baseline, reject/rollback candidate. If pass count is flat, keep only when materiality=high.",
        "no_order_execution": True,
    }
    out["ok"] = bool(out["local_files"].get("ok")) and bool(out["validators"].get("ok", True)) and bool(eval_suite.get("ok", True)) and bool(eval_comparison.get("ok", True))
    if args.eval_snapshot_out:
        pathlib.Path(args.eval_snapshot_out).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
