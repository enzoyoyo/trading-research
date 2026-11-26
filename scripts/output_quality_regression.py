#!/usr/bin/env python3
"""Output-quality golden-set regression for trading-research.

Replays frozen research fixtures through decision_compiler.py and checks the
resulting POSTURE (action band, hard_veto, multiplier ceiling, dominating
guard) rather than exact wording — catching semantic/quality drift that the
structural eval suites (routing / market-router / method-router / scenario)
cannot see.

The deterministic posture lint here runs every day in the self-optimization
gate as a 5th eval suite. The complementary LLM-semantic slice (concise-reply
contract, no overclaim) is scored via the eval-harness skill when a patch
touches reply templates — it is not run in this deterministic preflight.

Usage:
  output_quality_regression.py                      # lint all golden cases
  output_quality_regression.py --baseline PATH      # also snapshot postures
  output_quality_regression.py --compare PATH        # flag posture regressions
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import tempfile
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPILER = ROOT / "scripts" / "decision_compiler.py"
GOLDEN = ROOT / "templates" / "output-quality-golden-set.jsonl"

ACTION_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}


def action_rank(level: str | None) -> int:
    """Higher = more aggressive. Sells/unknown treated as most conservative."""
    return ACTION_RANK.get(level or "", -1)


def load_cases(path: pathlib.Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def compute_posture(case: dict[str, Any]) -> dict[str, Any]:
    if "report_text" in case:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(case["report_text"])
            tmp = pathlib.Path(fh.name)
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_report.py"), str(tmp)],
                text=True,
                capture_output=True,
                timeout=30,
            )
        finally:
            tmp.unlink(missing_ok=True)
        return {"report_validation_passed": proc.returncode == 0, "stdout_tail": (proc.stdout or "")[-400:], "stderr_tail": (proc.stderr or "")[-400:]}
    proc = subprocess.run(
        [sys.executable, str(COMPILER)],
        input=json.dumps(case["input"], ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if proc.returncode:
        raise RuntimeError(f"compiler failed for {case['name']}: {proc.stderr[-300:]}")
    return json.loads(proc.stdout)


def dominators(result: dict[str, Any]) -> set[str]:
    return set(result.get("dominant_constraints") or []) | set(result.get("hard_veto_modules") or [])


def lint_case(case: dict[str, Any], result: dict[str, Any]) -> list[str]:
    exp = case.get("expected_posture") or {}
    out: list[str] = []
    if exp.get("report_must_fail") and result.get("report_validation_passed"):
        out.append("report unexpectedly passed validation")
    if exp.get("report_must_pass") and not result.get("report_validation_passed"):
        out.append("report unexpectedly failed validation")
    if "report_text" in case:
        return out
    compiled = result.get("compiled_action")
    if "action_at_most" in exp and action_rank(compiled) > action_rank(exp["action_at_most"]):
        out.append(f"action {compiled} exceeds ceiling {exp['action_at_most']}")
    if "hard_veto" in exp and bool(result.get("hard_veto")) != bool(exp["hard_veto"]):
        out.append(f"hard_veto {result.get('hard_veto')} != expected {exp['hard_veto']}")
    if "max_multiplier" in exp and float(result.get("final_position_multiplier", 1)) > float(exp["max_multiplier"]) + 1e-9:
        out.append(f"multiplier {result.get('final_position_multiplier')} > {exp['max_multiplier']}")
    have = dominators(result)
    for guard in exp.get("must_dominate") or []:
        if guard not in have:
            out.append(f"missing dominating guard '{guard}'")
    return out


def compare_regressions(case: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if "report_text" in case:
        if (case.get("expected_posture") or {}).get("report_must_fail") and after.get("report_validation_passed"):
            out.append("report validation guard loosened")
        return out
    if action_rank(after.get("compiled_action")) > action_rank(before.get("compiled_action")):
        out.append(f"action loosened {before.get('compiled_action')} -> {after.get('compiled_action')}")
    if bool(before.get("hard_veto")) and not bool(after.get("hard_veto")):
        out.append("hard_veto dropped")
    if float(after.get("final_position_multiplier", 0)) > float(before.get("final_position_multiplier", 0)) + 1e-9:
        out.append(f"multiplier raised {before.get('final_position_multiplier')} -> {after.get('final_position_multiplier')}")
    before_dom, after_dom = dominators(before), dominators(after)
    for guard in (case.get("expected_posture") or {}).get("must_dominate") or []:
        if guard in before_dom and guard not in after_dom:
            out.append(f"dropped guard '{guard}'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", help="write current postures to this path as baseline")
    ap.add_argument("--compare", help="compare current postures against this baseline path")
    args = ap.parse_args()

    cases = load_cases(GOLDEN)
    by_name = {c["name"]: c for c in cases}
    postures = {c["name"]: compute_posture(c) for c in cases}

    lint_failures = {n: v for n in postures if (v := lint_case(by_name[n], postures[n]))}
    out: dict[str, Any] = {
        "ok": not lint_failures,
        "output_quality_evals": len(cases),
        "lint_failures": lint_failures,
    }

    if args.baseline:
        pathlib.Path(args.baseline).write_text(
            json.dumps({"postures": postures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        out["baseline_written"] = args.baseline

    if args.compare:
        try:
            baseline = json.loads(pathlib.Path(args.compare).read_text(encoding="utf-8")).get("postures", {})
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"baseline_read_failed: {exc}"}, ensure_ascii=False))
            return 1
        regressions = {
            n: regs
            for n, after in postures.items()
            if baseline.get(n) and (regs := compare_regressions(by_name[n], baseline[n], after))
        }
        out["regressions"] = regressions
        out["ok"] = out["ok"] and not regressions

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
