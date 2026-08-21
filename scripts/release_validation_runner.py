#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BUNDLED_LIFECYCLE_MODULE = SCRIPTS / "paper_trade_lifecycle.py"
RELEASE_VALIDATION_GUARD = SCRIPTS / "release_validation_guard" / "sitecustomize.py"
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def _trusted_account_home() -> Path:
    try:
        raw_home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError) as exc:
        fail(f"trusted account home is unavailable: {exc}")
    if not raw_home or any(value in raw_home for value in ('"', "\r", "\n")):
        fail("trusted account home is invalid for the release sandbox profile")
    account_home = Path(raw_home)
    if not account_home.is_absolute() or account_home == Path("/"):
        fail(f"trusted account home is not a safe absolute path: {account_home}")
    return account_home


def _protected_live_paths(account_home: Path) -> tuple[Path, ...]:
    return (
        account_home / ".hermes" / "longbridge-paper-trading",
        account_home / ".hermes" / "longbridge-paper-home",
        account_home / ".longbridge",
        account_home / ".config" / "hermes",
    )


OWNER_HOME = _trusted_account_home()
PROTECTED_LIVE_PATHS = _protected_live_paths(OWNER_HOME)
RELEASE_SANDBOX_PROFILE = " ".join(
    ["(version 1)", "(allow default)", "(deny network*)"]
    + [
        f'(deny file-read* file-write* (subpath "{path}"))'
        for path in PROTECTED_LIVE_PATHS
    ]
)


def assert_live_path_sandbox_coverage() -> bool:
    trusted_home = _trusted_account_home()
    expected_paths = _protected_live_paths(trusted_home)
    if OWNER_HOME != trusted_home or PROTECTED_LIVE_PATHS != expected_paths:
        fail("release sandbox live paths are not bound to the trusted account home")
    missing = [
        path
        for path in expected_paths
        if f'(deny file-read* file-write* (subpath "{path}"))'
        not in RELEASE_SANDBOX_PROFILE
    ]
    if missing:
        fail(
            "release sandbox profile is missing protected live paths: "
            + ",".join(str(path) for path in missing)
        )
    return True


def _candidate_local_release_file(path: Path) -> Path:
    if path.is_symlink():
        fail(f"release validation file must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        fail(f"release validation file unavailable: {path}: {exc}")
    if not resolved.is_file() or not resolved.is_relative_to(ROOT.resolve()):
        fail(f"release validation file escaped the candidate tree: {path}")
    return resolved


def release_validation_env(home: Path, temp_dir: Path, db: Path) -> dict[str, str]:
    """Build a minimal, secret-free environment for one release child command."""
    lifecycle = _candidate_local_release_file(BUNDLED_LIFECYCLE_MODULE)
    guard = _candidate_local_release_file(RELEASE_VALIDATION_GUARD)
    return {
        "HOME": str(home),
        "TMPDIR": str(temp_dir),
        "TMP": str(temp_dir),
        "TEMP": str(temp_dir),
        "TRADING_MEMORY_DB": str(db),
        "PAPER_TRADE_LIFECYCLE_MODULE": str(lifecycle),
        "TRADING_RESEARCH_RELEASE_VALIDATION": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(guard.parent),
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "en_US.UTF-8",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def run_all_checks() -> int:
    live_path_profile_proven = assert_live_path_sandbox_coverage()
    os_network_sandbox = SANDBOX_EXEC.is_file()
    live_paper_auth_state_sandboxed = live_path_profile_proven and os_network_sandbox
    commands = [
        [sys.executable, "scripts/prediction_ledger.py", "--self-test"],
        [sys.executable, "scripts/record_due_results.py", "--self-test"],
        [sys.executable, "scripts/paper_outcome_calibration_feed.py", "--self-test"],
        [sys.executable, "scripts/calibration_scorecard.py", "--self-test"],
        [sys.executable, "scripts/decision_compiler.py", "--self-test"],
        [sys.executable, "scripts/hypothesis_registry.py", "--self-test"],
        [sys.executable, "scripts/daily_journal.py", "--self-test"],
        [sys.executable, "scripts/data_retention.py", "--self-test"],
        [sys.executable, "scripts/output_quality_regression.py"],
        [sys.executable, "scripts/validate_skill.py"],
        [sys.executable, "scripts/validate_scenarios.py"],
        [sys.executable, "scripts/validate_report.py", "templates/report-contract-pass.md", "--provenance-bundle", "templates/research-provenance-bundle-pass.json"],
        [sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"],
        [sys.executable, "scripts/self_optimization_check.py", "--json", "--skip-network"],
    ]
    results: list[dict[str, Any]] = []
    homes: set[str] = set()
    databases: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="trading-research-validation-") as temp_root:
        suite_root = Path(temp_root)
        for index, command in enumerate(commands, 1):
            child_root = suite_root / f"child-{index:02d}"
            home = child_root / "home"
            temp_dir = child_root / "tmp"
            db = child_root / "trading_memory.sqlite"
            home.mkdir(parents=True)
            temp_dir.mkdir()
            if db.exists() or str(home) in homes or str(db) in databases:
                fail("release child isolation path was reused")
            homes.add(str(home))
            databases.add(str(db))
            env = release_validation_env(home, temp_dir, db)
            child_command = (
                [str(SANDBOX_EXEC), "-p", RELEASE_SANDBOX_PROFILE, *command]
                if os_network_sandbox
                else command
            )
            proc = subprocess.run(
                child_command,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            row: dict[str, Any] = {
                "command": " ".join(command),
                "exit_code": proc.returncode,
                "fresh_home": True,
                "fresh_trading_memory_db": True,
                "candidate_local_lifecycle": True,
                "network_disabled": True,
                "os_network_sandbox": os_network_sandbox,
                "live_path_sandbox_profile_proven": live_path_profile_proven,
            }
            if proc.returncode:
                row["stdout_tail"] = proc.stdout[-1600:]
                row["stderr_tail"] = proc.stderr[-1600:]
            results.append(row)
    ok = all(row["exit_code"] == 0 for row in results)
    print(json.dumps({
        "ok": ok,
        "all_checks": results,
        "isolated_trading_memory_db": True,
        "fresh_home_per_command": len(homes) == len(commands),
        "fresh_trading_memory_db_per_command": len(databases) == len(commands),
        "candidate_local_lifecycle_binding": str(BUNDLED_LIFECYCLE_MODULE),
        "network_guard": str(RELEASE_VALIDATION_GUARD),
        "os_network_sandbox": (
            "sandbox-exec:deny network*" if SANDBOX_EXEC.is_file() else "unavailable"
        ),
        "protected_live_paths": [str(path) for path in PROTECTED_LIVE_PATHS],
        "live_path_sandbox_profile_proven": live_path_profile_proven,
        "live_paper_auth_state_sandboxed": live_paper_auth_state_sandboxed,
        "external_production_python_loaded": False,
        "live_auth_environment_inherited": False,
        "child_command_count": len(commands),
    }, ensure_ascii=False))
    return 0 if ok else 1
