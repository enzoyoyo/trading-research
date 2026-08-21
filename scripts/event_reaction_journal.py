#!/usr/bin/env python3
"""Append-only event → reaction journal for trading-research.

Runtime data is stored under ~/.cache/hermes/trading-research/events by default.
The skill directory only contains this script.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LONGBRIDGE_QUERY = ROOT / "scripts" / "longbridge_query.py"
STATE_ROOT = Path(os.environ.get("TRADING_RESEARCH_STATE_DIR") or (Path.home() / ".cache" / "hermes" / "trading-research"))
DEFAULT_LOG = STATE_ROOT / "events" / "event_reactions.jsonl"
RELATIONSHIP_DIR = Path(os.environ.get("RELATIONSHIP_GRAPH_DIR") or (STATE_ROOT / "relationships"))
HORIZON_DAYS = {"T0": 0, "T1": 1, "T3": 3}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path() -> Path:
    return Path(os.environ.get("EVENT_REACTION_LOG") or DEFAULT_LOG)


def parse_day(value: str) -> date:
    return datetime.fromisoformat(value[:10]).date()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"record_kind": "parse_error", "raw": line[:500]})
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_payload(path: str) -> dict[str, Any]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("payload must be a JSON object")
    return obj


def is_event(row: dict[str, Any]) -> bool:
    return row.get("record_kind") in {"event", "event_instance", None} and bool(row.get("event_id")) and bool(row.get("event_date")) and not row.get("symbol")


def event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for row in rows:
        if not is_event(row):
            continue
        event_id = str(row.get("event_id"))
        if event_id in seen:
            continue
        seen.add(event_id)
        events.append(row)
    return events


def reaction_key(row: dict[str, Any]) -> tuple[str, str, str] | None:
    if row.get("record_kind") != "reaction":
        return None
    event_id, symbol, horizon = row.get("event_id"), row.get("symbol"), row.get("horizon")
    if event_id and symbol and horizon:
        return str(event_id), str(symbol).upper(), str(horizon).upper()
    return None


def existing_reaction_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {key for row in rows if (key := reaction_key(row)) is not None}


def event_symbols(event: dict[str, Any]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    for symbol in event.get("primary_symbols") or []:
        output.append((str(symbol).upper(), "primary"))
    for symbol in event.get("related_symbols") or []:
        item = str(symbol).upper()
        if item not in {s for s, _ in output}:
            output.append((item, "related"))
    return output


def benchmark_for(symbol: str) -> str:
    suffix = symbol.rsplit(".", 1)[-1].upper() if "." in symbol else "US"
    if suffix == "HK":
        return "HSI.HK"
    if suffix in {"SH", "SZ", "CN"}:
        return "000300.SH"
    return "SPY.US"


def fetch_candles(symbol: str, count: int = 260) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [sys.executable, str(LONGBRIDGE_QUERY), "candle", symbol, "--period", "day", "--count", str(count), "--json"],
        text=True,
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:500] or f"longbridge_query failed for {symbol}")
    data = json.loads(proc.stdout or "[]")
    if not isinstance(data, list):
        raise RuntimeError(f"longbridge_query returned non-list for {symbol}")
    return [row for row in data if isinstance(row, dict)]


def close_series(symbol: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in fetch_candles(symbol):
        if row.get("close") is None or row.get("time") is None:
            continue
        out[parse_day(str(row["time"]))] = float(row["close"])
    if len(out) < 2:
        raise RuntimeError(f"{symbol}: insufficient candle closes")
    return dict(sorted(out.items()))


def previous_close(series: dict[date, float], event_day: date) -> tuple[date, float] | None:
    candidates = [(d, v) for d, v in series.items() if d < event_day]
    return candidates[-1] if candidates else None


def close_on_or_after(series: dict[date, float], target_day: date) -> tuple[date, float] | None:
    for d, v in series.items():
        if d >= target_day:
            return d, v
    return None


def pct_return(series: dict[date, float], event_day: date, horizon: str) -> tuple[float | None, str | None, list[str]]:
    gaps: list[str] = []
    base = previous_close(series, event_day)
    ref = close_on_or_after(series, event_day + timedelta(days=HORIZON_DAYS[horizon]))
    if base is None:
        gaps.append("previous_close_unavailable")
    if ref is None:
        gaps.append(f"{horizon.lower()}_close_unavailable")
    if base is None or ref is None:
        return None, None, gaps
    _, base_close = base
    ref_date, ref_close = ref
    if base_close == 0:
        return None, ref_date.isoformat(), gaps + ["previous_close_zero"]
    return round((ref_close / base_close - 1.0) * 100.0, 4), ref_date.isoformat(), gaps


def load_latest_graph() -> dict[str, Any] | None:
    path = RELATIONSHIP_DIR / "latest.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def beta_for(symbol: str, benchmark: str, graph: dict[str, Any] | None) -> tuple[float, str, list[str]]:
    if not graph or graph.get("benchmark") != benchmark:
        return 1.0, "benchmark_fallback", ["beta_unavailable_used_raw_excess"]
    for row in graph.get("market_beta") or []:
        if isinstance(row, dict) and row.get("symbol") == symbol:
            beta = row.get("beta_vs_benchmark_120d") or row.get("beta_vs_benchmark_60d")
            if beta is not None:
                return float(beta), "relationship_graph", []
    return 1.0, "benchmark_fallback", ["beta_unavailable_used_raw_excess"]


def build_reaction(event: dict[str, Any], symbol: str, role: str, horizon: str, graph: dict[str, Any] | None) -> dict[str, Any]:
    gaps: list[str] = []
    event_day = parse_day(str(event["event_date"]))
    benchmark = benchmark_for(symbol)
    raw, ref_date, raw_gaps = pct_return(close_series(symbol), event_day, horizon)
    bench_ret, _, bench_gaps = pct_return(close_series(benchmark), event_day, horizon)
    gaps.extend([f"{symbol}:{gap}" for gap in raw_gaps])
    gaps.extend([f"{benchmark}:{gap}" for gap in bench_gaps])
    beta, beta_source, beta_gaps = beta_for(symbol, benchmark, graph)
    gaps.extend(beta_gaps)
    abnormal = None
    if raw is not None and bench_ret is not None:
        abnormal = round(raw - beta * bench_ret, 4)
    else:
        gaps.append("abnormal_return_unavailable")
    gaps.extend(["iv_before_unavailable", "iv_after_unavailable"])
    return {
        "record_kind": "reaction",
        "event_id": event["event_id"],
        "symbol": symbol,
        "role": role,
        "horizon": horizon,
        "ref_date": ref_date,
        "raw_return_pct": raw,
        "benchmark_symbol": benchmark,
        "benchmark_return_pct": bench_ret,
        "beta_source": beta_source,
        "beta_vs_benchmark": round(beta, 6),
        "abnormal_return_pct": abnormal,
        "iv_before": None,
        "iv_after": None,
        "iv_change": None,
        "realized_vol_5d_after": None,
        "data_gaps": sorted(set(gaps)),
        "computed_at_utc": utc_now(),
    }


def cmd_register(args: argparse.Namespace) -> int:
    path = log_path()
    rows = read_jsonl(path)
    payload = load_payload(args.payload)
    if not payload.get("event_id") or not payload.get("event_date"):
        raise ValueError("payload requires event_id and event_date")
    event_id = str(payload["event_id"])
    if any(str(row.get("event_id")) == event_id for row in event_rows(rows)):
        return print_json({"status": "exists", "event_id": event_id, "log_path": str(path)})
    row = {"record_kind": "event", **payload}
    row.setdefault("registered_at_utc", utc_now())
    append_jsonl(path, row)
    return print_json({"status": "ok", "event_id": event_id, "log_path": str(path)})


def cmd_backfill(args: argparse.Namespace) -> int:
    path = log_path()
    rows = read_jsonl(path)
    events = {str(row.get("event_id")): row for row in event_rows(rows)}
    if args.event_id not in events:
        raise ValueError(f"event_id not found: {args.event_id}")
    horizon = args.horizon.upper()
    if horizon not in HORIZON_DAYS:
        raise ValueError(f"unsupported horizon: {args.horizon}")
    existing = existing_reaction_keys(rows)
    graph = load_latest_graph()
    appended: list[dict[str, Any]] = []
    for symbol, role in event_symbols(events[args.event_id]):
        if args.symbol and symbol != args.symbol.upper():
            continue
        key = (args.event_id, symbol, horizon)
        if key in existing:
            appended.append({"event_id": args.event_id, "symbol": symbol, "horizon": horizon, "status": "exists"})
            continue
        try:
            reaction = build_reaction(events[args.event_id], symbol, role, horizon, graph)
        except Exception as exc:  # data source failure becomes an auditable gap row
            reaction = {
                "record_kind": "reaction",
                "event_id": args.event_id,
                "symbol": symbol,
                "role": role,
                "horizon": horizon,
                "ref_date": None,
                "raw_return_pct": None,
                "benchmark_symbol": benchmark_for(symbol),
                "benchmark_return_pct": None,
                "beta_source": "unavailable",
                "abnormal_return_pct": None,
                "iv_before": None,
                "iv_after": None,
                "iv_change": None,
                "realized_vol_5d_after": None,
                "data_gaps": [f"backfill_error:{type(exc).__name__}:{str(exc)[:200]}"],
                "computed_at_utc": utc_now(),
            }
        append_jsonl(path, reaction)
        appended.append({"event_id": args.event_id, "symbol": symbol, "horizon": horizon, "status": "appended"})
    return print_json({"status": "ok", "rows": appended, "log_path": str(path)})


def cmd_pending(args: argparse.Namespace) -> int:
    rows = read_jsonl(log_path())
    existing = existing_reaction_keys(rows)
    today = date.today()
    pending: list[dict[str, Any]] = []
    for event in event_rows(rows):
        event_day = parse_day(str(event["event_date"]))
        for horizon, offset in HORIZON_DAYS.items():
            if event_day + timedelta(days=offset) > today:
                continue
            for symbol, role in event_symbols(event):
                key = (str(event["event_id"]), symbol, horizon)
                if key not in existing:
                    pending.append({"event_id": event["event_id"], "symbol": symbol, "role": role, "horizon": horizon, "due_date": (event_day + timedelta(days=offset)).isoformat()})
    return print_json({"status": "ok", "pending": pending, "count": len(pending), "log_path": str(log_path())})


def cmd_list(args: argparse.Namespace) -> int:
    rows = read_jsonl(log_path())
    if args.since:
        since = parse_day(args.since)
        rows = [r for r in rows if parse_day(str(r.get("event_date") or r.get("ref_date") or "1900-01-01")) >= since]
    return print_json({"status": "ok", "count": len(rows), "rows": rows, "log_path": str(log_path())})


def print_json(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Append-only event reaction journal")
    sub = parser.add_subparsers(dest="command", required=True)
    p_register = sub.add_parser("register")
    p_register.add_argument("--payload", required=True)
    p_register.add_argument("--json", action="store_true")
    p_register.set_defaults(func=cmd_register)
    p_backfill = sub.add_parser("backfill")
    p_backfill.add_argument("--event-id", required=True)
    p_backfill.add_argument("--horizon", required=True, choices=sorted(HORIZON_DAYS))
    p_backfill.add_argument("--symbol")
    p_backfill.add_argument("--json", action="store_true")
    p_backfill.set_defaults(func=cmd_backfill)
    p_pending = sub.add_parser("pending-backfill")
    p_pending.add_argument("--json", action="store_true")
    p_pending.set_defaults(func=cmd_pending)
    p_list = sub.add_parser("list")
    p_list.add_argument("--since")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
