#!/usr/bin/env python3
"""Translate factor-engine statistics into strict four-state research verdicts."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
ROOT = SCRIPTS.parent
THRESHOLD = 3.5
MIN_OOS_PERIODS = 3
ENGINE_DIR = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-runs"
BACKTEST_DIR = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-backtests"
VERDICT_DIR = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-verdicts"
VERDICT_DIR_ENV = "FACTOR_VERDICT_DIR"
FIXTURE = ROOT / "templates" / "factor-experiment-example.json"


def envelope(schema: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": schema, **fields, "no_order_execution": True}


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def save_verdict(verdict: dict[str, Any], directory: Path | None = None) -> Path:
    """Persist one judge output plus an atomic latest pointer for reconciliation."""
    root = directory or Path(os.environ.get(VERDICT_DIR_ENV) or VERDICT_DIR).expanduser()
    digest = hashlib.sha256(json.dumps(verdict, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    stamp = str(verdict.get("generated_at_utc") or utc_now()).replace("-", "").replace(":", "")
    artifact = root / f"{stamp}-{digest}.json"
    verdict["verdict_path"] = str(artifact)
    _atomic_write_json(artifact, verdict)
    _atomic_write_json(root / "latest.json", verdict)
    return artifact


def load_run(value: str, *, kind: str) -> dict[str, Any]:
    candidate = Path(value).expanduser()
    if not candidate.exists():
        root = Path(os.environ.get("FACTOR_RUN_DIR" if kind == "engine" else "FACTOR_BACKTEST_DIR",
                                   str(ENGINE_DIR if kind == "engine" else BACKTEST_DIR))).expanduser()
        candidate = root / f"{value}.json"
    return _load_json(candidate)


def opposite_direction(mean_ic: float, direction: str) -> bool:
    if direction == "+":
        return mean_ic < 0
    if direction == "-":
        return mean_ic > 0
    return False


def classify(stats: dict[str, Any], direction_hypothesis: str) -> str | None:
    required = (stats.get("alpha_t"), stats.get("random_ic_mean"))
    train = stats.get("train")
    test = stats.get("test")
    if not all(finite(value) for value in required) or not isinstance(train, dict) or not isinstance(test, dict):
        return None
    train_alpha, test_alpha = train.get("alpha_t"), test.get("alpha_t")
    train_ic, test_ic = train.get("mean_ic"), test.get("mean_ic")
    if not all(finite(value) for value in (train_alpha, test_alpha, train_ic, test_ic)):
        return None
    sign = -1.0 if direction_hypothesis == "-" else 1.0
    train_o = sign * float(train_alpha)
    test_o = sign * float(test_alpha)
    alpha_o = sign * float(stats["alpha_t"])
    if train_o >= THRESHOLD and test_o >= THRESHOLD and float(train_ic) * float(test_ic) > 0:
        return "confirmed_alive"
    if train_o >= THRESHOLD and test_o < THRESHOLD:
        return "train_only"
    overall_ic = stats.get("mean_ic")
    if (alpha_o <= -THRESHOLD and test_o <= -THRESHOLD
            and finite(overall_ic) and opposite_direction(float(overall_ic), direction_hypothesis)
            and opposite_direction(float(test_ic), direction_hypothesis)):
        return "reversed_strict"
    return "noise"


def _backtest_gate(backtest: dict[str, Any] | None) -> tuple[bool, list[str]]:
    gaps: list[str] = []
    if not isinstance(backtest, dict):
        return False, ["missing_backtest"]
    if backtest.get("status") != "ok":
        gaps.append("backtest_not_ok")
    if backtest.get("costs_included") != "yes":
        gaps.append("missing_costs")
    train = backtest.get("train")
    test = backtest.get("test")
    if not isinstance(train, dict) or not isinstance(test, dict):
        gaps.append("missing_walk_forward")
    elif not finite(train.get("periods")) or not finite(test.get("periods")) or int(test["periods"]) < MIN_OOS_PERIODS:
        gaps.append("missing_walk_forward")
    elif not finite(test.get("sharpe")):
        gaps.append("missing_walk_forward")
    elif float(test["sharpe"]) <= 0.0:
        gaps.append("oos_net_sharpe_nonpositive")
    return not gaps, gaps


def _reason(state: str | None, backtest_ok: bool, gate_gaps: list[str]) -> str:
    if state is None:
        return "missing_random_control_fields"
    if state != "confirmed_alive":
        return state
    if not backtest_ok:
        return gate_gaps[0]
    return "robustness_passed"


def build_verdict(
    engine_run: dict[str, Any], backtest_run: dict[str, Any] | None = None, *,
    factor: str | None = None, horizon: str | None = None,
) -> dict[str, Any]:
    factors = engine_run.get("factors")
    if engine_run.get("status") != "ok" or not isinstance(factors, dict) or not factors:
        return envelope("factor_verdict.v1", ok=False, status="not_judgeable", state=None,
                        generated_at_utc=utc_now(),
                        decision_use="hypothesis_only", readiness_level="research_hypothesis",
                        data_gaps=["engine_run_not_ok"], suggested_module_signal=None)
    factor_name = factor or (str(backtest_run.get("factor")) if isinstance(backtest_run, dict) and backtest_run.get("factor") else next(iter(factors)))
    factor_row = factors.get(factor_name)
    if not isinstance(factor_row, dict):
        raise ValueError(f"factor not found in engine run: {factor_name}")
    horizons = factor_row.get("horizons")
    if not isinstance(horizons, dict) or not horizons:
        raise ValueError(f"factor has no horizons: {factor_name}")
    horizon_id = str(horizon) if horizon is not None else next(iter(horizons))
    stats = horizons.get(horizon_id)
    if not isinstance(stats, dict):
        raise ValueError(f"horizon not found for {factor_name}: {horizon_id}")
    direction = str(factor_row.get("direction_hypothesis") or "")
    direction_gap = False
    if not direction:
        try:
            import factor_engine  # local stdlib-only module
            direction = str(factor_engine.FACTOR_METHODS.get(factor_name, {}).get("direction_hypothesis") or "")
        except ImportError:
            direction = ""
    if not direction:
        direction_gap = True
    state = classify(stats, direction)
    required_present = state is not None and finite(engine_run.get("n_factors_scanned")) and not direction_gap
    backtest_ok, gate_gaps = _backtest_gate(backtest_run)
    decision_use = "ranking_support" if state == "confirmed_alive" and backtest_ok and required_present else "hypothesis_only"
    readiness = "working_view" if decision_use == "ranking_support" else "research_hypothesis"
    reason = _reason(state, backtest_ok, gate_gaps)
    run_id = str(engine_run.get("run_id") or "factor_run_unidentified")
    market = str(engine_run.get("market") or "unknown")
    universe = engine_run.get("universe") if isinstance(engine_run.get("universe"), dict) else {}
    feature_set = [factor_name]
    robustness = list(backtest_run.get("robustness_checks") or []) if isinstance(backtest_run, dict) else []
    research_experiment = {
        "hypothesis": f"{factor_name} retains cross-sectional alpha in {market} market at horizon {horizon_id}",
        "data_scope": f"market={market};universe={universe.get('basis', 'unknown')};run_id={run_id};horizon={horizon_id}",
        "feature_set": feature_set,
        "train_validation_test": "time_split" if isinstance(stats.get("train"), dict) and isinstance(stats.get("test"), dict) else "unavailable",
        "costs_included": backtest_run.get("costs_included", "no") if isinstance(backtest_run, dict) else "no",
        "robustness_checks": robustness,
        "failure_modes": list(backtest_run.get("failure_modes") or []) if isinstance(backtest_run, dict) else gate_gaps,
        "decision_use": decision_use,
        "module_signal": {
            "module": "quant_robustness", "max_action_level": "L0", "position_multiplier": 0.0,
            "hard_veto": False, "reason": reason,
        },
    }
    validation = {
        "random_ic_mean": stats.get("random_ic_mean"), "alpha_t": stats.get("alpha_t"),
        "n_factors_scanned": engine_run.get("n_factors_scanned"), "state": state,
        "threshold": THRESHOLD, "train": stats.get("train"), "test": stats.get("test"),
    }
    statement = (f"factor={factor_name};market={market};universe={universe.get('basis', 'unknown')};"
                 f"horizon={horizon_id};run_id={run_id};state={state or 'not_judgeable'}")
    reconciliation_key = f"{market}|{factor_name}|{horizon_id}"
    hypothesis_payload = {
        "statement": statement, "status": state or "open", "source_module": "quant_robustness",
        "tags": [factor_name, market], "evidence_ids": [run_id],
        "falsifiers": [f"next rolling OOS {factor_name}/{horizon_id} alpha_t < {THRESHOLD}"],
        "reconciliation_key": reconciliation_key, "record_type": "factor_verdict",
        "note": f"factor verdict {state or 'not_judgeable'}; survivorship ceiling enforced",
    }
    suggested = None
    suppression_reason = None
    if state == "confirmed_alive":
        suggested = {
            "module": "quant_robustness", "sub_framework": "factor_validation_strict_gate",
            "max_action_level": "L0", "position_multiplier": 0.0, "hard_veto": False,
            "reason": reason, "evidence_refs": [run_id], "tighten_only": True,
            "cannot_raise_upstream": True, "no_order_execution": True,
        }
    else:
        suppression_reason = f"state={state or 'not_judgeable'} cannot enter compiler input"
    gaps = [] if state is not None and finite(engine_run.get("n_factors_scanned")) else ["missing_random_ic_mean_alpha_t_or_n_factors_scanned"]
    if direction_gap:
        gaps.append("missing_direction_hypothesis")
    gaps.extend(gate_gaps)
    return envelope(
        "factor_verdict.v1", ok=state is not None, status="judged" if state is not None else "not_judgeable",
        generated_at_utc=utc_now(), run_id=run_id, market=market,
        factor=factor_name, horizon_id=horizon_id, reconciliation_key=reconciliation_key,
        state=state, threshold=THRESHOLD,
        decision_use=decision_use, readiness_level=readiness,
        survivorship_ceiling={"decision_use_max": "ranking_support", "readiness_max": "working_view"},
        research_experiment=research_experiment, factor_validation=validation,
        hypothesis_payload=hypothesis_payload, suggested_module_signal=suggested,
        suggested_module_signal_suppressed_reason=suppression_reason, data_gaps=list(dict.fromkeys(gaps)),
    )


def register_hypothesis(verdict: dict[str, Any], update_id: str | None = None) -> dict[str, Any]:
    payload = verdict.get("hypothesis_payload")
    if not isinstance(payload, dict):
        raise ValueError("verdict has no hypothesis payload")
    env = dict(os.environ)
    if update_id:
        command = [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "update", "--id", update_id,
                   "--status", str(payload["status"]), "--note", str(payload["note"]),
                   "--reconciliation-key", str(payload["reconciliation_key"]),
                   "--record-type", str(payload["record_type"])]
        for tag in payload.get("tags") or []:
            command.extend(["--tag", str(tag)])
        for falsifier in payload.get("falsifiers") or []:
            command.extend(["--falsifier", str(falsifier)])
    else:
        fd, name = tempfile.mkstemp(prefix="factor-verdict-", suffix=".json")
        os.close(fd)
        path = Path(name)
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            command = [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "create", "--payload", str(path)]
            completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10, check=False)
        finally:
            path.unlink(missing_ok=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        return json.loads(completed.stdout)
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    result = json.loads(completed.stdout)
    evidence_ids = payload.get("evidence_ids") or []
    if evidence_ids:
        link = [sys.executable, str(SCRIPTS / "hypothesis_registry.py"), "link-evidence", "--id", update_id]
        for evidence_id in evidence_ids:
            link.extend(["--evidence-id", str(evidence_id)])
        linked = subprocess.run(link, env=env, capture_output=True, text=True, timeout=10, check=False)
        if linked.returncode != 0:
            raise RuntimeError(linked.stderr.strip() or linked.stdout.strip())
        result["evidence_link"] = json.loads(linked.stdout)
    return result


def validate_fixture(path: Path = FIXTURE) -> None:
    fixture = _load_json(path)
    cases = fixture.get("cases")
    if fixture.get("schema_version") != "factor_experiment_fixture.v1" or not isinstance(cases, list) or len(cases) < 2:
        raise AssertionError("invalid factor experiment fixture")
    observed_states = set()
    for case in cases:
        verdict = build_verdict(case["engine_run"], case.get("backtest_run"),
                                factor=case.get("factor"), horizon=str(case.get("horizon", "5")))
        expected = case["expected_contract"]
        assert verdict["state"] == expected["factor_validation"]["state"]
        assert verdict["research_experiment"] == expected["research_experiment"]
        assert verdict["factor_validation"] == expected["factor_validation"]
        observed_states.add(verdict["state"])
    assert {"confirmed_alive", "train_only"} <= observed_states


def self_test() -> None:
    validate_fixture()
    with tempfile.TemporaryDirectory() as tmp:
        registry = Path(tmp) / "hypotheses.json"
        verdict_dir = Path(tmp) / "factor-verdicts"
        previous = os.environ.get("TRADING_RESEARCH_HYPOTHESES_PATH")
        previous_verdict_dir = os.environ.get(VERDICT_DIR_ENV)
        os.environ["TRADING_RESEARCH_HYPOTHESES_PATH"] = str(registry)
        os.environ[VERDICT_DIR_ENV] = str(verdict_dir)
        try:
            fixture = _load_json(FIXTURE)
            case = fixture["cases"][0]
            verdict = build_verdict(case["engine_run"], case["backtest_run"], factor=case["factor"], horizon=str(case["horizon"]))
            result = register_hypothesis(verdict)
            assert result["status"] == "created" and registry.exists()
            verdict["registration"] = result
            artifact = save_verdict(verdict)
            assert artifact.exists() and (verdict_dir / "latest.json").exists(), artifact
        finally:
            if previous is None:
                os.environ.pop("TRADING_RESEARCH_HYPOTHESES_PATH", None)
            else:
                os.environ["TRADING_RESEARCH_HYPOTHESES_PATH"] = previous
            if previous_verdict_dir is None:
                os.environ.pop(VERDICT_DIR_ENV, None)
            else:
                os.environ[VERDICT_DIR_ENV] = previous_verdict_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    judge = sub.add_parser("judge")
    judge.add_argument("--engine-run", required=True)
    judge.add_argument("--backtest-run")
    judge.add_argument("--factor")
    judge.add_argument("--horizon")
    judge.add_argument("--register", action="store_true")
    judge.add_argument("--update-id")
    judge.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed"}))
        return 0
    args = build_parser().parse_args(args_list)
    try:
        engine_run = load_run(args.engine_run, kind="engine")
        backtest = load_run(args.backtest_run, kind="backtest") if args.backtest_run else None
        verdict = build_verdict(engine_run, backtest, factor=args.factor, horizon=args.horizon)
        if args.register or args.update_id:
            verdict["registration"] = register_hypothesis(verdict, args.update_id)
        save_verdict(verdict)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(json.dumps(envelope("factor_verdict_error.v1", ok=False, status="error",
                                  data_gaps=[{"reason_code": "error", "gap": str(exc)}]), ensure_ascii=False))
        return 2
    print(json.dumps(verdict, ensure_ascii=False, indent=2 if args.json else None, allow_nan=False))
    return 0 if verdict.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
