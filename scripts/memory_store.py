"""trading-research decision memory store.

Connection, path resolution, and JSON/event/state persistence primitives for
the decision-memory substrate. Split out of trading_memory_core.py (Task 6,
skill_optimization_plan_20260705.md). No broker access, no real trade
execution, no external secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_schema import DEFAULT_DB, init_db, jdump, jload


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_decay_weight(value: str | None, half_life_days: float = 30.0) -> float:
    """Return a recency weight in (0, 1] using exponential half-life decay.

    Trading memory should not forget historical decisions, but older validations
    should have less force when adjusting the next analysis. This mirrors
    YantrikDB's decay concept without deleting or hiding old events.
    """
    dt = parse_time(value)
    if not dt:
        return 1.0
    now = datetime.now(dt.tzinfo or timezone.utc)
    age_days = max((now - dt).total_seconds() / 86400, 0.0)
    return max(0.05, 0.5 ** (age_days / half_life_days))


def make_id(prefix: str, symbol: str | None = None) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    if symbol:
        clean = "".join(c for c in symbol.upper() if c.isalnum())[:12]
        return f"{prefix}-{ts}-{clean}-{suffix}"
    return f"{prefix}-{ts}-{suffix}"


def db_path(args: argparse.Namespace) -> Path:
    raw = args.db or os.environ.get("TRADING_MEMORY_DB") or str(DEFAULT_DB)
    return Path(raw).expanduser()


def connect(path: Path) -> sqlite3.Connection:
    # Preserve SQLite's real in-memory mode. Treating ':memory:' as a Path
    # would otherwise create a literal runtime file inside the skill tree.
    if str(path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def read_payload(path: str | None) -> dict[str, Any]:
    if not path:
        raise SystemExit("--payload is required")
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("payload must be a JSON object")
    return data


def event(conn: sqlite3.Connection, event_type: str, object_type: str, object_id: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO memory_events VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), now_iso(), event_type, object_type, object_id, jdump(payload)),
    )


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO memory_state VALUES (?, ?, ?)",
        (key, jdump(value), now_iso()),
    )


def get_state(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value_json FROM memory_state WHERE key=?", (key,)).fetchone()
    return jload(row[0], default) if row else default

