#!/usr/bin/env python3
"""System A data-lightening / retention tool for trading-research.

System A (`~/.hermes/longbridge-paper-trading`) accumulates roughly 150MB
across ~7000 files over a month of paper-trading runs, most of it timestamped
snapshot/proposal/decision-packet artifacts that are cheap to re-derive but
expensive to keep forever. This tool archives (tar.gz + sha256 manifest) or
deletes only what a hardcoded, table-driven policy names, and only inside a
hardcoded allowlist of directories -- any path outside that allowlist raises
instead of being touched. High-value ledgers (`journal/*.jsonl`, `config/`,
`scripts/`, `docs/`, `.git`, all `reports/*.md` narratives) are never in the
allowlist and are therefore structurally unreachable by this script.

Default mode is always a dry-run: it only reports what *would* be archived or
deleted. Nothing is ever written or removed unless `--apply` is passed
explicitly. No broker access, no order execution, no external secrets. Pure
stdlib.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SYSTEM_A_ROOT = Path.home() / ".hermes" / "longbridge-paper-trading"
DEFAULT_SKILL_ROOT = Path.home() / ".claude" / "skills" / "trading-research"
DEFAULT_EVAL_CANDIDATES_ROOT = (
    Path.home() / ".hermes" / "work" / "trading-research-autoevolve" / "eval-candidates"
)
SCHEMA_VERSION = "data_retention.v1"
TIMESTAMPED_RE = re.compile(r"_20\d{2}")
SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class RetentionRule:
    name: str
    relative_dir: str  # dir under its root this rule scans
    keep_days: int | None  # None => permanent, never archived
    extensions: tuple[str, ...] | None = None
    require_timestamp_gate: bool = True  # only *_20YY-named files are archival-eligible
    arcname_prefix: str | None = None  # defaults to relative_dir


def default_rules() -> list[RetentionRule]:
    return [
        RetentionRule("reports_md", "reports", None, extensions=(".md",)),
        RetentionRule("reports_json", "reports", 30, extensions=(".json",)),
        RetentionRule("decision_packets", "decision_packets", 30),
        RetentionRule("state", "state", 14),
        RetentionRule("proposals", "proposals", 90),
        RetentionRule("learning_packets", "learning_packets", 60),
        RetentionRule("runs", "runs", 14),
        RetentionRule("run_logs", "run_logs", 14),
    ]


# --------------------------------------------------------------------------
# Allowlist / guard
# --------------------------------------------------------------------------

def allowed_roots(system_a_root: Path, skill_root: Path, eval_candidates_root: Path) -> list[Path]:
    roots = [
        (system_a_root / rule.relative_dir).resolve()
        for rule in default_rules()
    ]
    roots.append((system_a_root / "archive").resolve())
    roots.append(skill_root.resolve())  # pycache cleanup only touches __pycache__ dirs within
    roots.append(eval_candidates_root.resolve())
    roots.append((eval_candidates_root.parent / "archive").resolve())
    return roots


def guard_path(path: Path, roots: list[Path]) -> Path:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PermissionError(f"refusing to touch path outside allowlist: {resolved}")


# --------------------------------------------------------------------------
# Candidate discovery
# --------------------------------------------------------------------------

def iter_candidate_files(root_dir: Path, rule: RetentionRule, now: float, keep_days_override: int | None) -> list[Path]:
    keep_days = rule.keep_days if keep_days_override is None else keep_days_override
    if keep_days is None or not root_dir.exists():
        return []
    cutoff = now - keep_days * SECONDS_PER_DAY
    out = []
    for p in sorted(root_dir.iterdir()):
        if not p.is_file():
            continue
        if rule.extensions and p.suffix not in rule.extensions:
            continue
        if rule.require_timestamp_gate and not TIMESTAMPED_RE.search(p.name):
            continue  # e.g. universe_candidates.jsonl, risk_regime.json, current_run.env: never touched
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            out.append(p)
    return out


def month_key(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m")


def group_by_month(files: list[Path]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in files:
        groups[month_key(p.stat().st_mtime)].append(p)
    return groups


def archive_path_for(archive_root: Path, label: str, month: str) -> Path:
    base = archive_root / label / f"{month}.tar.gz"
    if not base.exists():
        return base
    return archive_root / label / f"{month}_{uuid.uuid4().hex[:8]}.tar.gz"


# --------------------------------------------------------------------------
# Manifest (atomic append via rewrite)
# --------------------------------------------------------------------------

def append_manifest(manifest_path: Path, entry: dict[str, Any]) -> None:
    existing = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else ""
    content = existing + (json.dumps(entry, ensure_ascii=False) + "\n")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(manifest_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, manifest_path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------
# Plan (dry-run) / apply for one directory rule
# --------------------------------------------------------------------------

def plan_dir_rule(root_dir: Path, rule: RetentionRule, archive_root: Path, now: float, keep_days_override: int | None) -> dict[str, Any]:
    candidates = iter_candidate_files(root_dir, rule, now, keep_days_override)
    groups = group_by_month(candidates)
    planned = []
    for month, files in sorted(groups.items()):
        planned.append({
            "month": month,
            "file_count": len(files),
            "byte_count": sum(f.stat().st_size for f in files),
            "planned_tar_path": str(archive_path_for(archive_root, rule.name, month)),
        })
    return {
        "rule": rule.name,
        "dir": str(root_dir),
        "pending_file_count": len(candidates),
        "pending_byte_count": sum(f.stat().st_size for f in candidates),
        "planned_archives": planned,
    }


def apply_dir_rule(
    root_dir: Path, rule: RetentionRule, archive_root: Path, roots: list[Path],
    now: float, keep_days_override: int | None, manifest_path: Path,
) -> dict[str, Any]:
    candidates = iter_candidate_files(root_dir, rule, now, keep_days_override)
    groups = group_by_month(candidates)
    archived = []
    prefix = rule.arcname_prefix if rule.arcname_prefix is not None else rule.relative_dir
    for month, files in sorted(groups.items()):
        tar_path = guard_path(archive_path_for(archive_root, rule.name, month), roots)
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(tar_path.parent), suffix=".tar.gz.tmp")
        os.close(fd)
        try:
            with tarfile.open(tmp_name, "w:gz") as tar:
                for f in files:
                    guard_path(f, roots)
                    arcname = f"{prefix}/{f.name}" if prefix else f.name
                    tar.add(f, arcname=arcname)
            with tarfile.open(tmp_name, "r:gz") as tar:
                if len(tar.getnames()) != len(files):
                    raise RuntimeError(f"tar verification failed for {tar_path}: member count mismatch")
            os.replace(tmp_name, tar_path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        byte_count = sum(f.stat().st_size for f in files)
        sha256 = hashlib.sha256(tar_path.read_bytes()).hexdigest()
        for f in files:
            guard_path(f, roots)
            f.unlink()
        entry = {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dir": rule.name,
            "month": month,
            "file_count": len(files),
            "byte_count": byte_count,
            "tar_path": str(tar_path),
            "sha256": sha256,
        }
        append_manifest(manifest_path, entry)
        archived.append(entry)
    return {"rule": rule.name, "dir": str(root_dir), "archived": archived}


# --------------------------------------------------------------------------
# Skill-side extras: __pycache__ cleanup + eval-candidates archival
# --------------------------------------------------------------------------

def find_pycache_dirs(skill_root: Path) -> list[Path]:
    if not skill_root.exists():
        return []
    return sorted(skill_root.rglob("__pycache__"))


def plan_pycache(skill_root: Path) -> dict[str, Any]:
    dirs = find_pycache_dirs(skill_root)
    byte_count = sum(f.stat().st_size for d in dirs for f in d.rglob("*") if f.is_file())
    return {
        "rule": "skill_pycache", "dir": str(skill_root),
        "pending_dir_count": len(dirs), "pending_byte_count": byte_count,
        "paths": [str(d) for d in dirs],
    }


def apply_pycache(skill_root: Path, roots: list[Path]) -> dict[str, Any]:
    dirs = find_pycache_dirs(skill_root)
    removed = []
    for d in dirs:
        guard_path(d, roots)
        shutil.rmtree(d)
        removed.append(str(d))
    return {"rule": "skill_pycache", "removed": removed}


def eval_candidates_rule() -> RetentionRule:
    return RetentionRule("eval_candidates", ".", 60, extensions=None, require_timestamp_gate=False, arcname_prefix="")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run(
    mode: str,
    system_a_root: Path,
    skill_root: Path,
    eval_candidates_root: Path,
    now: float | None = None,
    overrides: dict[str, int] | None = None,
) -> dict[str, Any]:
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    overrides = overrides or {}
    roots = allowed_roots(system_a_root, skill_root, eval_candidates_root)
    archive_root = system_a_root / "archive"
    manifest_path = archive_root / "manifest.jsonl"
    eval_archive_root = eval_candidates_root.parent / "archive"
    eval_manifest_path = eval_archive_root / "manifest.jsonl"

    jobs: list[dict[str, Any]] = []
    total_files = 0
    total_bytes = 0
    for rule in default_rules():
        root_dir = system_a_root / rule.relative_dir
        keep_override = overrides.get(rule.name)
        if mode == "dry_run":
            result = plan_dir_rule(root_dir, rule, archive_root, now, keep_override)
            total_files += result["pending_file_count"]
            total_bytes += result["pending_byte_count"]
        else:
            result = apply_dir_rule(root_dir, rule, archive_root, roots, now, keep_override, manifest_path)
        jobs.append(result)

    ec_rule = eval_candidates_rule()
    keep_override = overrides.get(ec_rule.name)
    if mode == "dry_run":
        ec_result = plan_dir_rule(eval_candidates_root, ec_rule, eval_archive_root, now, keep_override)
        total_files += ec_result["pending_file_count"]
        total_bytes += ec_result["pending_byte_count"]
    else:
        ec_result = apply_dir_rule(eval_candidates_root, ec_rule, eval_archive_root, roots, now, keep_override, eval_manifest_path)
    jobs.append(ec_result)

    if mode == "dry_run":
        pycache = plan_pycache(skill_root)
    else:
        pycache = apply_pycache(skill_root, roots)

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "system_a_root": str(system_a_root),
        "jobs": jobs,
        "pycache": pycache,
        "totals": {
            "pending_file_count": total_files,
            "pending_byte_count": total_bytes,
            "pending_pycache_dir_count": pycache.get("pending_dir_count", len(pycache.get("removed", []))),
        },
    }


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _touch(path: Path, age_days: float, now: float, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    mtime = now - age_days * SECONDS_PER_DAY
    os.utime(path, (mtime, mtime))


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "system_a"
        skill_root = Path(td) / "skill"
        eval_root = Path(td) / "autoevolve" / "eval-candidates"
        now = datetime(2026, 7, 6, tzinfo=timezone.utc).timestamp()

        # reports/: old .json (archive-eligible) + old .md (must stay, permanent) + fresh .json (must stay)
        _touch(root / "reports" / "daily_decision_20260601T120000Z.json", 40, now)
        _touch(root / "reports" / "daily_decision_20260601T120000Z.md", 400, now)
        _touch(root / "reports" / "daily_decision_20260705T120000Z.json", 1, now)

        # decision_packets/: old (archive) + fresh (stay)
        _touch(root / "decision_packets" / "paper_decision_packet_20260601T120000Z.json", 40, now)
        _touch(root / "decision_packets" / "paper_decision_packet_20260705T120000Z.json", 1, now)

        # state/: old timestamped (archive) + fresh timestamped (stay) + non-timestamped (must NEVER touch even if ancient)
        _touch(root / "state" / "capability_inventory_20260501T120000Z.json", 60, now)
        _touch(root / "state" / "capability_inventory_20260705T120000Z.json", 1, now)
        _touch(root / "state" / "universe_candidates.jsonl", 9999, now)

        # proposals/: old (< 90d stays; > 90d archives)
        _touch(root / "proposals" / "generated_20260101T120000Z.json", 100, now)
        _touch(root / "proposals" / "generated_20260701T120000Z.json", 5, now)

        # learning_packets/: > 60d archives
        _touch(root / "learning_packets" / "paper_learning_packet_20260401T120000Z.json", 90, now)

        # runs/ + run_logs/: > 14d archives; a non-timestamped current-run pointer must survive
        _touch(root / "runs" / "paper_executor_20260601T120000Z_abc123.json", 30, now)
        _touch(root / "run_logs" / "capability_inventory_20260601T120000Z.log", 30, now)
        _touch(root / "run_logs" / "current_run.env", 9999, now)

        # denylisted dir: must never even be scanned/touched
        _touch(root / "journal" / "paper_orders.jsonl", 9999, now)

        # skill-side: pycache (delete) + a real script (must survive)
        _touch(skill_root / "scripts" / "__pycache__" / "foo.cpython-313.pyc", 1, now)
        _touch(skill_root / "scripts" / "real_script.py", 1, now)

        # eval-candidates: old (archive) + fresh (stay)
        _touch(eval_root / "2026-05-01-findings.md", 90, now)
        _touch(eval_root / "2026-07-01-findings.md", 5, now)

        roots = allowed_roots(root, skill_root, eval_root)

        # Guard test: a path inside the denylisted journal/ dir must be refused.
        try:
            guard_path(root / "journal" / "paper_orders.jsonl", roots)
            raise AssertionError("guard_path should have refused a journal/ path")
        except PermissionError:
            pass

        dry = run("dry_run", root, skill_root, eval_root, now=now)
        by_rule = {j["rule"]: j for j in dry["jobs"]}
        assert by_rule["reports_md"]["pending_file_count"] == 0, "reports/*.md must never be archive-eligible"
        assert by_rule["reports_json"]["pending_file_count"] == 1, by_rule["reports_json"]
        assert by_rule["decision_packets"]["pending_file_count"] == 1
        assert by_rule["state"]["pending_file_count"] == 1, "only the old timestamped state file is eligible"
        assert by_rule["proposals"]["pending_file_count"] == 1
        assert by_rule["learning_packets"]["pending_file_count"] == 1
        assert by_rule["runs"]["pending_file_count"] == 1
        assert by_rule["run_logs"]["pending_file_count"] == 1, "current_run.env (non-timestamped) must be excluded"
        assert by_rule["eval_candidates"]["pending_file_count"] == 1
        assert dry["pycache"]["pending_dir_count"] == 1

        applied = run("apply", root, skill_root, eval_root, now=now)
        assert not (root / "state" / "capability_inventory_20260501T120000Z.json").exists()
        assert (root / "state" / "capability_inventory_20260705T120000Z.json").exists()
        assert (root / "state" / "universe_candidates.jsonl").exists(), "non-timestamped state file must survive"
        assert (root / "reports" / "daily_decision_20260601T120000Z.md").exists(), "reports/*.md must survive"
        assert not (root / "reports" / "daily_decision_20260601T120000Z.json").exists()
        assert (root / "journal" / "paper_orders.jsonl").exists(), "journal/ must never be touched"
        assert not (skill_root / "scripts" / "__pycache__").exists()
        assert (skill_root / "scripts" / "real_script.py").exists()
        assert not (eval_root / "2026-05-01-findings.md").exists()
        assert (eval_root / "2026-07-01-findings.md").exists()

        manifest_path = root / "archive" / "manifest.jsonl"
        manifest_lines = [json.loads(l) for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert manifest_lines, "manifest should have entries after apply"
        for entry in manifest_lines:
            assert len(entry["sha256"]) == 64
            assert Path(entry["tar_path"]).exists()
            with tarfile.open(entry["tar_path"], "r:gz") as tar:
                assert len(tar.getnames()) == entry["file_count"]

        # Idempotency: re-applying with nothing left eligible must add no new manifest lines.
        rerun = run("apply", root, skill_root, eval_root, now=now)
        manifest_lines_2 = [l for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(manifest_lines_2) == len(manifest_lines), "re-apply must be idempotent (no new archives)"
        assert all(not j.get("archived") for j in rerun["jobs"] if j["rule"] != "reports_md"), rerun["jobs"]

        return {"ok": True, "self_test": "passed", "manifest_entries": len(manifest_lines)}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

OVERRIDE_ARGS = {
    "reports_json": "--keep-days-reports-json",
    "decision_packets": "--keep-days-decision-packets",
    "state": "--keep-days-state",
    "proposals": "--keep-days-proposals",
    "learning_packets": "--keep-days-learning-packets",
    "runs": "--keep-days-runs",
    "run_logs": "--keep-days-run-logs",
    "eval_candidates": "--keep-days-eval-candidates",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="System A data retention / archival tool (dry-run by default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--system-a-root", default=str(DEFAULT_SYSTEM_A_ROOT))
    ap.add_argument("--skill-root", default=str(DEFAULT_SKILL_ROOT))
    ap.add_argument("--eval-candidates-root", default=str(DEFAULT_EVAL_CANDIDATES_ROOT))
    for name, flag in OVERRIDE_ARGS.items():
        ap.add_argument(flag, type=int, default=None, dest=f"override_{name}")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    if args.apply and args.dry_run:
        ap.error("--apply and --dry-run are mutually exclusive")
    mode = "apply" if args.apply else "dry_run"

    overrides = {name: getattr(args, f"override_{name}") for name in OVERRIDE_ARGS}
    overrides = {k: v for k, v in overrides.items() if v is not None}

    result = run(
        mode,
        Path(args.system_a_root).expanduser(),
        Path(args.skill_root).expanduser(),
        Path(args.eval_candidates_root).expanduser(),
        overrides=overrides,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
