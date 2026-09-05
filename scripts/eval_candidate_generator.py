#!/usr/bin/env python3
"""Failure-pattern -> eval-candidate generator for trading-research.

Self-evolution feedback loop A. System A (paper trading) keeps losing in the
same way; those losses land in the decision memory as recurring failure tags.
This script turns a *gated* recurring failure pattern into a Decision-Compiler
guard case: "when the risk module that should have stopped this fires, the
compiled posture MUST stay conservative." Each candidate is self-verified
against the live ``decision_compiler.py`` before it is staged.

Hard safety rules (mirror references/adaptive-self-optimization.md):
  * Only patterns that pass the SAME anti-overfit gate as the rest of the skill
    (min_count + min_share) become candidates. One unlucky loss never edits anything.
  * Candidates are STAGED to ~/.hermes/work, never written into templates/. The
    skill must not grade itself on tests it silently wrote into its own golden
    set — promotion into the golden set stays a human decision.
  * Failure tags with no clean compiler mapping are emitted as manual_review,
    not silently dropped.
  * Read-only against memory; no broker access; no trade execution.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_memory_core import connect, db_path, review_stats, verified_rows

# Reuse the exact posture verifier the daily output-quality suite uses, so a
# staged guard is judged by the same yardstick as the golden set it may join.
from output_quality_regression import compute_posture, lint_case

DEFAULT_MIN_COUNT = 3
DEFAULT_MIN_SHARE = 0.20
DEFAULT_STAGE_DIR = Path("~/.hermes/work/trading-research-autoevolve/eval-candidates").expanduser()

# A tempting bullish counter-signal every guard case must dominate: a strong
# fundamentals read that, left unchecked, would justify a full L3 position.
_TEMPTATION = {"module": "fundamentals", "max_action_level": "L3", "position_multiplier": 1.0, "hard_veto": False}


def _cap_case(module: str, level: str, multiplier: float, repair: str | None = None) -> dict[str, Any]:
    guard = {"module": module, "max_action_level": level, "position_multiplier": multiplier, "hard_veto": False}
    if repair:
        guard["repair_signal"] = repair
    return {
        "input": {"module_signals": [guard, dict(_TEMPTATION)]},
        "expected_posture": {
            "action_at_most": level,
            "hard_veto": False,
            "max_multiplier": multiplier,
            "must_dominate": [module],
        },
    }


def _veto_case(module: str, repair: str | None = None) -> dict[str, Any]:
    guard = {"module": module, "max_action_level": "L0", "position_multiplier": 0.0, "hard_veto": True}
    if repair:
        guard["repair_signal"] = repair
    return {
        "input": {"module_signals": [guard, dict(_TEMPTATION)]},
        "expected_posture": {
            "action_at_most": "L0",
            "hard_veto": True,
            "max_multiplier": 0,
            "must_dominate": [module],
        },
    }


# failure_tag -> the conservative posture the compiler must enforce next time.
# Only tags with an unambiguous module-signal mapping live here; the rest go to
# manual_review on purpose.
GUARD_BUILDERS = {
    "low_coverage_loss": lambda: _cap_case("data_quality", "L1", 0.5, "raise data-coverage gate before sizing"),
    "data_gap_mispriced": lambda: _cap_case("data_quality", "L1", 0.5, "fill data gap before sizing"),
    "quick_loss": lambda: _cap_case("entry_timing", "L1", 0.5, "add entry buffer / second confirmation"),
    "entry_too_early": lambda: _cap_case("entry_timing", "L1", 0.5, "wait for confirmed trigger"),
    "chase_reversal": lambda: _cap_case("entry_timing", "L1", 0.5, "await pullback before entry"),
    "execution_risk_high": lambda: _cap_case("liquidity", "L1", 0.5, "size down on thin liquidity"),
    "flow_against": lambda: _cap_case("capital_flow", "L1", 0.6, "respect adverse capital flow"),
    "news_against": lambda: _cap_case("news", "L1", 0.6, "respect adverse news"),
    "smart_money_against": lambda: _cap_case("smart_money", "L1", 0.6, "respect smart-money positioning"),
    "model_signal_against": lambda: _cap_case("model_signal", "L1", 0.6, "respect adverse model signal"),
    "fake_breakout": lambda: _veto_case("gamma", "spot reclaim / confirm breakout"),
    "direction_invalidated": lambda: _veto_case("direction_invalidation", "thesis invalidated; flatten"),
    "regime_misread": lambda: _veto_case("risk_regime", "forced-liquidation regime: no new risk"),
}


def gated_failure_tags(stats: dict[str, Any], min_count: int, min_share: float) -> list[dict[str, Any]]:
    n = max(int(stats.get("sample_count", 0)), 1)
    out: list[dict[str, Any]] = []
    for tag, count in stats.get("failure_tags", {}).items():
        share = count / n
        if count >= min_count and share >= min_share:
            out.append({"failure_tag": tag, "count": count, "share": round(share, 3)})
    return out


def verify_candidate(case: dict[str, Any]) -> dict[str, Any]:
    """Run the candidate through the live compiler and classify it.

    verified -> the compiler already enforces the guard (safe regression lock-in).
    exposes_compiler_gap -> the loss implies a guard the compiler does NOT enforce
    (a real methodology finding; never an auto-added test).
    """
    posture = compute_posture(case)
    failures = lint_case(case, posture)
    return {
        "status": "verified_guards_current_behavior" if not failures else "exposes_compiler_gap",
        "compiled_posture": {
            "compiled_action": posture.get("compiled_action"),
            "hard_veto": posture.get("hard_veto"),
            "final_position_multiplier": posture.get("final_position_multiplier"),
            "dominant_constraints": posture.get("dominant_constraints"),
        },
        "lint_failures": failures,
    }


def build_candidates(stats: dict[str, Any], min_count: int, min_share: float) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    for hit in gated_failure_tags(stats, min_count, min_share):
        tag = hit["failure_tag"]
        builder = GUARD_BUILDERS.get(tag)
        if builder is None:
            manual_review.append({**hit, "reason": "no_compiler_guard_mapping"})
            continue
        case = builder()
        candidate = {
            "name": f"guard_{tag}_auto",
            "source": "failure_pattern",
            "failure_tag": tag,
            "observed": {"count": hit["count"], "share": hit["share"]},
            **case,
            "verification": verify_candidate(case),
        }
        candidates.append(candidate)
    return {"candidates": candidates, "manual_review": manual_review}


def learning_packet_candidates() -> list[dict[str, Any]]:
    """Best-effort: surface System A's own upgrade suggestions for manual mapping.

    Structured v1 failure evidence carries stable IDs and review dispositions.
    Neither a structured diagnosis nor legacy prose defines compiler posture:
    both stay manual_review until a concrete method mapping is validated."""
    try:
        from self_optimization_check import latest_learning_packet

        packet = latest_learning_packet()
    except Exception as exc:  # pragma: no cover - defensive for cron environments
        return [{"reason": "learning_packet_read_failed", "error": type(exc).__name__, "message": str(exc)}]
    out: list[dict[str, Any]] = []
    structured = packet.get("paper_learning_consumption") or {}
    if structured.get("structured_evidence_present") is True:
        for cand in structured.get("candidates") or []:
            out.append({"source": "learning_packet", "candidate": cand,
                        "reason": cand.get("reason", "method_mapping_and_validation_required"),
                        "review_state": cand.get("review_state", "awaiting_evidence"),
                        "no_order_execution": True})
        if structured.get("status") != "present":
            out.append({"source": "learning_packet", "reason": "structured_learning_evidence_unavailable",
                        "data_gaps": structured.get("data_gaps") or [], "review_state": "awaiting_evidence"})
        return out
    for cand in packet.get("skill_upgrade_candidates") or []:
        out.append(
            {
                "source": "learning_packet",
                "candidate": cand,
                "reason": "learning_packet_candidate_needs_manual_mapping",
            }
        )
    return out


def generate(conn, window: int, min_count: int, min_share: float, include_packet: bool = True) -> dict[str, Any]:
    items = verified_rows(conn, window)
    stats = review_stats(items)
    built = build_candidates(stats, min_count, min_share)
    manual_review = list(built["manual_review"])
    if include_packet:
        manual_review.extend(learning_packet_candidates())
    verified = [c for c in built["candidates"] if c["verification"]["status"] == "verified_guards_current_behavior"]
    gaps = [c for c in built["candidates"] if c["verification"]["status"] == "exposes_compiler_gap"]
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": stats.get("sample_count", 0),
        "min_count": min_count,
        "min_share": min_share,
        "candidates": built["candidates"],
        "verified_guard_count": len(verified),
        "compiler_gap_count": len(gaps),
        "manual_review": manual_review,
        "promotion_policy": "STAGED ONLY. A verified guard may be proposed for templates/output-quality-golden-set.jsonl by a human; a compiler_gap is a methodology finding, never an auto-added test. Nothing here is merged automatically.",
        "no_order_execution": True,
    }


def stage(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"eval-candidates-{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def self_test() -> dict[str, Any]:
    import argparse as _argparse

    from trading_memory_core import cmd_record_decision, cmd_record_result, now_iso

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "memory.sqlite"
        conn = connect(path)
        # 6 losses, each tagged with one mappable tag (fake_breakout) and one
        # unmapped tag (symbol_drag): clears the gate for both.
        for i in range(6):
            decision = {
                "symbol": f"EVG{i}",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "factors": {"estimated_win_rate": 0.7},
                "review_clock": now_iso(),
            }
            dpath = Path(td) / f"d{i}.json"
            dpath.write_text(json.dumps(decision), encoding="utf-8")
            out = cmd_record_decision(conn, _argparse.Namespace(payload=str(dpath), db=str(path)))
            result = {
                "return_pct": -5.0,
                "outcome": "failure",
                "failure_tags": ["fake_breakout", "symbol_drag"],
            }
            rpath = Path(td) / f"r{i}.json"
            rpath.write_text(json.dumps(result), encoding="utf-8")
            cmd_record_result(
                conn,
                _argparse.Namespace(payload=str(rpath), decision_id=out["decision_id"], db=str(path)),
            )
        report = generate(conn, window=36, min_count=3, min_share=0.2, include_packet=False)
        conn.close()

        names = [c["name"] for c in report["candidates"]]
        assert "guard_fake_breakout_auto" in names, report
        assert report["verified_guard_count"] >= 1, report
        fb = next(c for c in report["candidates"] if c["name"] == "guard_fake_breakout_auto")
        assert fb["verification"]["status"] == "verified_guards_current_behavior", fb
        assert any(m.get("failure_tag") == "symbol_drag" for m in report["manual_review"]), report

        # The gap branch: an impossible expectation must be flagged, not staged as verified.
        bogus = {
            "input": {"module_signals": [{"module": "gamma", "max_action_level": "L0", "position_multiplier": 0.0, "hard_veto": True}]},
            "expected_posture": {"action_at_most": "L3", "hard_veto": False, "max_multiplier": 1.0, "must_dominate": ["fundamentals"]},
        }
        assert verify_candidate(bogus)["status"] == "exposes_compiler_gap"

        # Staging must land under the given runtime dir, never the skill tree.
        staged = stage(report, Path(td) / "stage")
        assert staged.exists() and staged.parent.name == "stage", staged
        return {"ok": True, "self_test": "passed", "verified_guard_count": report["verified_guard_count"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate compiler-verified eval candidates from gated failure patterns")
    ap.add_argument("--db", help="SQLite DB path; default TRADING_MEMORY_DB or core default")
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    ap.add_argument("--min-share", type=float, default=DEFAULT_MIN_SHARE)
    ap.add_argument("--out-dir", default=str(DEFAULT_STAGE_DIR), help="staging dir under ~/.hermes/work; never templates/")
    ap.add_argument("--no-stage", action="store_true", help="print report only, do not write a staged file")
    ap.add_argument("--no-packet", action="store_true", help="skip reading the System A learning packet")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    conn = connect(db_path(args))
    try:
        report = generate(conn, args.window, args.min_count, args.min_share, include_packet=not args.no_packet)
    finally:
        conn.close()
    if not args.no_stage:
        report["staged_to"] = str(stage(report, Path(args.out_dir).expanduser()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
