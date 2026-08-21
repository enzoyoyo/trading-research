#!/usr/bin/env python3
"""Compatibility wrapper for scenario-regression validation.

This keeps cron/runbook commands stable while delegating the single source of
truth to validate_skill.py --scenario-only.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATE_SKILL = ROOT / "scripts" / "validate_skill.py"


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_SKILL), "--scenario-only"],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
