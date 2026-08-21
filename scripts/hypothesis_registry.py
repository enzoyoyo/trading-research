#!/usr/bin/env python3
"""Hypothesis lifecycle registry for trading-research.

Persistent create/update/search ledger for research hypotheses so a factor or
market claim that lands on train_only/noise/reversed_strict (see
references/factor-validation-strict-gate.md) keeps being tracked instead of
disappearing after being reported dead. Runtime data is stored under
~/.cache/hermes/trading-research/memory, next to trading_memory, following
the DEFAULT_DB convention in memory_schema.py. The skill directory only
contains this script; no broker access, no order execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".cache" / "hermes" / "trading-research" / "memory" / "hypotheses.json"
ENV_PATH = "TRADING_RESEARCH_HYPOTHESES_PATH"
STATUSES = {"open", "confirmed_alive", "train_only", "reversed_strict", "noise", "retired"}
DEFAULT_STALE_DAYS = 30


def registry_path() -> Path:
    return Path(os.environ.get(ENV_PATH) or DEFAULT_PATH)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = obj.get("hypotheses") if isinstance(obj, dict) else obj
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def save_registry(path: Path, rows: list[dict[str, Any]]) -> None:
    """Atomic write: build the full file in a tempfile, then os.replace so a
    crash mid-write never leaves a truncated/corrupt registry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"hypotheses": rows}, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load_payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("payload must be a JSON object")
    return obj


def new_id(statement: str) -> str:
    digest = hashlib.sha256(f"{statement}|{utc_now()}".encode("utf-8")).hexdigest()[:12]
    return f"hyp_{digest}"


def find(rows: list[dict[str, Any]], hypothesis_id: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("hypothesis_id") == hypothesis_id:
            return row
    return None


def create_hypothesis(
    rows: list[dict[str, Any]],
    *,
    statement: str | None,
    status: str = "open",
    tags: list[str] | None = None,
    source_module: str | None = None,
    note: str | None = None,
    evidence_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not statement:
        raise SystemExit("create requires --statement (or payload.statement)")
    if status not in STATUSES:
        raise SystemExit(f"unsupported status: {status}")
    now = utc_now()
    row = {
        "hypothesis_id": new_id(statement),
        "statement": statement,
        "status": status,
        "source_module": source_module,
        "tags": sorted(set(tags or [])),
        "evidence_ids": sorted(set(evidence_ids or [])),
        "notes": [{"at": now, "text": note or "created"}],
        "created_at_utc": now,
        "updated_at_utc": now,
    }
    return rows + [row], row


def update_hypothesis(
    rows: list[dict[str, Any]],
    hypothesis_id: str,
    *,
    status: str | None = None,
    tags: list[str] | None = None,
    note: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target = find(rows, hypothesis_id)
    if target is None:
        raise SystemExit(f"hypothesis_id not found: {hypothesis_id}")
    if status is not None and status not in STATUSES:
        raise SystemExit(f"unsupported status: {status}")
    now = utc_now()
    updated = {
        **target,
        "status": status or target["status"],
        "tags": sorted(set(target.get("tags") or []) | set(tags or [])),
        "notes": (target.get("notes") or []) + ([{"at": now, "text": note}] if note else []),
        "updated_at_utc": now,
    }
    new_rows = [updated if row is target else row for row in rows]
    return new_rows, updated


def link_evidence(
    rows: list[dict[str, Any]], hypothesis_id: str, evidence_ids: list[str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target = find(rows, hypothesis_id)
    if target is None:
        raise SystemExit(f"hypothesis_id not found: {hypothesis_id}")
    updated = {
        **target,
        "evidence_ids": sorted(set(target.get("evidence_ids") or []) | set(evidence_ids)),
        "updated_at_utc": utc_now(),
    }
    new_rows = [updated if row is target else row for row in rows]
    return new_rows, updated


def list_hypotheses(rows: list[dict[str, Any]], *, status: str | None = None, tag: str | None = None) -> list[dict[str, Any]]:
    out = rows
    if status:
        out = [row for row in out if row.get("status") == status]
    if tag:
        out = [row for row in out if tag in (row.get("tags") or [])]
    return out


def search_hypotheses(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.lower()

    def haystack(row: dict[str, Any]) -> str:
        parts = [
            row.get("statement") or "",
            row.get("source_module") or "",
            " ".join(row.get("tags") or []),
            " ".join(note.get("text", "") for note in row.get("notes") or []),
        ]
        return " ".join(parts).lower()

    return [row for row in rows if needle in haystack(row)]


def stale_hypotheses(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    """A hypothesis is stale if it is not retired and has not been touched
    (status/note/evidence update) within `days`; an unparsable timestamp is
    treated as stale so a corrupt row surfaces instead of hiding silently."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") == "retired":
            continue
        try:
            updated = parse_utc(str(row["updated_at_utc"]))
        except Exception:
            out.append(row)
            continue
        if updated < cutoff:
            out.append(row)
    return out


def print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    payload = load_payload(args.payload)
    path = registry_path()
    rows = load_registry(path)
    new_rows, row = create_hypothesis(
        rows,
        statement=args.statement or payload.get("statement"),
        status=args.status or payload.get("status") or "open",
        tags=args.tag or payload.get("tags"),
        source_module=args.source_module or payload.get("source_module"),
        note=args.note or payload.get("note"),
        evidence_ids=payload.get("evidence_ids"),
    )
    save_registry(path, new_rows)
    return print_json({"ok": True, "status": "created", "hypothesis": row, "registry_path": str(path)})


def cmd_update(args: argparse.Namespace) -> int:
    path = registry_path()
    rows = load_registry(path)
    new_rows, row = update_hypothesis(rows, args.id, status=args.status, tags=args.tag, note=args.note)
    save_registry(path, new_rows)
    return print_json({"ok": True, "status": "updated", "hypothesis": row, "registry_path": str(path)})


def cmd_link_evidence(args: argparse.Namespace) -> int:
    path = registry_path()
    rows = load_registry(path)
    new_rows, row = link_evidence(rows, args.id, args.evidence_id)
    save_registry(path, new_rows)
    return print_json({"ok": True, "status": "linked", "hypothesis": row, "registry_path": str(path)})


def cmd_list(args: argparse.Namespace) -> int:
    path = registry_path()
    filtered = list_hypotheses(load_registry(path), status=args.status, tag=args.tag)
    return print_json({"ok": True, "count": len(filtered), "hypotheses": filtered, "registry_path": str(path)})


def cmd_search(args: argparse.Namespace) -> int:
    path = registry_path()
    hits = search_hypotheses(load_registry(path), args.query)
    return print_json({"ok": True, "count": len(hits), "hypotheses": hits, "registry_path": str(path)})


def cmd_stale(args: argparse.Namespace) -> int:
    path = registry_path()
    stale = stale_hypotheses(load_registry(path), args.days)
    return print_json({"ok": True, "count": len(stale), "stale_after_days": args.days, "hypotheses": stale, "registry_path": str(path)})


def self_test() -> dict[str, Any]:
    """Exercise create/update/link-evidence/list/search/stale against a
    throwaway registry file under tempfile; never reads or writes the real
    ~/.cache/hermes/trading-research/memory/hypotheses.json."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "hypotheses.json"

        rows, created = create_hypothesis(
            [],
            statement="因子X在A股制造业子板块 IC>0.02，但缺同宇宙随机对照",
            status="open",
            tags=["factor_x", "quant_robustness"],
            source_module="quant_robustness",
            note="registered pending random-control test",
        )
        save_registry(path, rows)
        assert len(rows) == 1 and created["status"] == "open", created

        rows = load_registry(path)
        rows, updated = update_hypothesis(
            rows, created["hypothesis_id"], status="train_only", note="random control failed; train-only overfit signature"
        )
        save_registry(path, rows)
        assert updated["status"] == "train_only", updated
        assert len(updated["notes"]) == 2, updated

        rows = load_registry(path)
        rows, linked = link_evidence(rows, created["hypothesis_id"], ["decision:D123", "report:factor_x_run1"])
        save_registry(path, rows)
        assert linked["evidence_ids"] == ["decision:D123", "report:factor_x_run1"], linked

        assert len(list_hypotheses(rows, status="train_only")) == 1, rows
        assert len(search_hypotheses(rows, "随机对照")) == 1, rows

        # Backdate updated_at_utc to prove staleness detection without a real wait.
        old_rows = [
            {**row, "updated_at_utc": (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")}
            for row in rows
        ]
        save_registry(path, old_rows)
        assert len(stale_hypotheses(load_registry(path), DEFAULT_STALE_DAYS)) == 1, old_rows

        # A retired hypothesis must never surface as stale, however old it is.
        retired_rows = [{**row, "status": "retired"} for row in old_rows]
        save_registry(path, retired_rows)
        assert not stale_hypotheses(load_registry(path), DEFAULT_STALE_DAYS), retired_rows

        return {"ok": True, "self_test": "passed", "hypothesis_id": created["hypothesis_id"], "final_status": updated["status"]}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Hypothesis lifecycle registry for trading-research")
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="command")

    p_create = sub.add_parser("create", help="register a new research hypothesis")
    p_create.add_argument("--statement")
    p_create.add_argument("--status", choices=sorted(STATUSES))
    p_create.add_argument("--tag", action="append")
    p_create.add_argument("--source-module")
    p_create.add_argument("--note")
    p_create.add_argument("--payload", help="JSON payload path; CLI flags override payload fields")
    p_create.set_defaults(func=cmd_create)

    p_update = sub.add_parser("update", help="change status/tags/notes on an existing hypothesis")
    p_update.add_argument("--id", required=True)
    p_update.add_argument("--status", choices=sorted(STATUSES))
    p_update.add_argument("--tag", action="append")
    p_update.add_argument("--note")
    p_update.set_defaults(func=cmd_update)

    p_link = sub.add_parser("link-evidence", help="attach evidence ids (decision/report/event) to a hypothesis")
    p_link.add_argument("--id", required=True)
    p_link.add_argument("--evidence-id", action="append", required=True)
    p_link.set_defaults(func=cmd_link_evidence)

    p_list = sub.add_parser("list", help="list hypotheses, optionally filtered")
    p_list.add_argument("--status", choices=sorted(STATUSES))
    p_list.add_argument("--tag")
    p_list.set_defaults(func=cmd_list)

    p_search = sub.add_parser("search", help="substring search over statement/tags/notes")
    p_search.add_argument("--query", required=True)
    p_search.set_defaults(func=cmd_search)

    p_stale = sub.add_parser("stale", help="list non-retired hypotheses untouched for N days")
    p_stale.add_argument("--days", type=int, default=DEFAULT_STALE_DAYS)
    p_stale.set_defaults(func=cmd_stale)

    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        return print_json(self_test())
    if not args.command:
        build_parser().error("a command is required unless --self-test is passed")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
