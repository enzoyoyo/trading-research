#!/usr/bin/env python3
"""Build pairwise beta/correlation relationship graph for trading-research.

Runtime snapshots are written under ~/.cache/hermes/trading-research/relationships.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LONGBRIDGE_QUERY = ROOT / "scripts" / "longbridge_query.py"
RISK_REGIME = ROOT / "scripts" / "risk_regime_snapshot.py"
STATE_ROOT = Path(os.environ.get("TRADING_RESEARCH_STATE_DIR") or (Path.home() / ".cache" / "hermes" / "trading-research"))
DEFAULT_OUTDIR = Path(os.environ.get("RELATIONSHIP_GRAPH_DIR") or (STATE_ROOT / "relationships"))
DEFAULT_UNIVERSE = [
    "SPY.US",
    "QQQ.US",
    "NVDA.US",
    "TSLA.US",
    "AAPL.US",
    "MU.US",
    "COHR.US",
    "XLE.US",
    "UVXY.US",
    "0700.HK",
    "9988.HK",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_day(value: str) -> str:
    return datetime.fromisoformat(value[:10]).date().isoformat()


def fetch_candles(symbol: str, count: int) -> list[dict[str, Any]]:
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
        raise RuntimeError(f"non-list candle payload for {symbol}")
    return [row for row in data if isinstance(row, dict)]


def close_by_day(symbol: str, count: int) -> dict[str, float]:
    rows = fetch_candles(symbol, count)
    out: dict[str, float] = {}
    for row in rows:
        if row.get("time") is None or row.get("close") is None:
            continue
        out[parse_day(str(row["time"]))] = float(row["close"])
    if len(out) < 30:
        raise RuntimeError(f"{symbol}: insufficient closes ({len(out)})")
    return dict(sorted(out.items()))


def log_returns(closes: dict[str, float]) -> dict[str, float]:
    items = list(closes.items())
    out: dict[str, float] = {}
    for idx in range(1, len(items)):
        day, close = items[idx]
        prev = items[idx - 1][1]
        if close > 0 and prev > 0:
            out[day] = math.log(close / prev)
    return out


def aligned(xs: dict[str, float], ys: dict[str, float]) -> tuple[list[float], list[float]]:
    days = sorted(set(xs) & set(ys))
    return [xs[d] for d in days], [ys[d] for d in days]


def tail_pair(xs: list[float], ys: list[float], size: int) -> tuple[list[float], list[float]]:
    if len(xs) <= size:
        return xs, ys
    return xs[-size:], ys[-size:]


def stats(y: list[float], x: list[float]) -> dict[str, float | int | None]:
    n = min(len(x), len(y))
    if n < 2:
        return {"beta": None, "corr": None, "r2": None, "n_obs": n}
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    beta = cov / vx if vx else None
    corr = cov / math.sqrt(vx * vy) if vx and vy else None
    if corr is not None:
        corr = max(-1.0, min(1.0, corr))
    return {
        "beta": round(beta, 6) if beta is not None else None,
        "corr": round(corr, 6) if corr is not None else None,
        "r2": round(corr * corr, 6) if corr is not None else None,
        "n_obs": n,
    }


def load_universe(args: argparse.Namespace) -> list[str]:
    if not args.universe_file:
        return [s.upper() for s in DEFAULT_UNIVERSE]
    text = Path(args.universe_file).read_text(encoding="utf-8")
    try:
        policy = json.loads(text)
    except json.JSONDecodeError:
        policy = None
    if isinstance(policy, dict) and ("universe_core" in policy or "universe_probation" in policy):
        return universe_from_policy(policy)
    rows = text.splitlines()
    return [row.strip().upper() for row in rows if row.strip() and not row.strip().startswith("#")]


def universe_from_policy(policy: dict[str, Any]) -> list[str]:
    """Read System A's two-layer universe (core + probation) straight out of
    its paper_policy.json, read-only — this skill never writes that file.
    Missing fields are ignored rather than treated as an error, per
    references/universe-governance.md."""
    symbols: list[str] = []
    for symbol in policy.get("universe_core") or []:
        if isinstance(symbol, str) and symbol.strip():
            symbols.append(symbol.strip().upper())
    probation = policy.get("universe_probation") or {}
    for entry in probation.get("symbols") or []:
        symbol = entry.get("symbol") if isinstance(entry, dict) else entry
        if isinstance(symbol, str) and symbol.strip():
            symbols.append(symbol.strip().upper())
    seen: set[str] = set()
    deduped = []
    for symbol in symbols:
        if symbol not in seen:
            seen.add(symbol)
            deduped.append(symbol)
    return deduped


def regime_label() -> tuple[str, list[str]]:
    proc = subprocess.run([sys.executable, str(RISK_REGIME), "--json"], text=True, capture_output=True, timeout=180)
    if proc.returncode != 0:
        return "unknown", [f"risk_regime_snapshot_failed:{proc.stderr[-300:]}"]
    try:
        obj = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return "unknown", ["risk_regime_snapshot_json_parse_failed"]
    if isinstance(obj, dict):
        return str(obj.get("regime_label") or obj.get("risk_regime") or obj.get("status") or "unknown"), []
    return "unknown", ["risk_regime_snapshot_non_object"]


def build_snapshot(universe: list[str], benchmark: str, fast_days: int, slow_days: int) -> dict[str, Any]:
    data_gaps: list[str] = []
    count = max(slow_days + 40, 180)
    returns: dict[str, dict[str, float]] = {}
    for symbol in universe:
        try:
            returns[symbol] = log_returns(close_by_day(symbol, count))
        except Exception as exc:
            data_gaps.append(f"{symbol}: {type(exc).__name__}: {str(exc)[:220]}")
    regime, regime_gaps = regime_label()
    data_gaps.extend(regime_gaps)
    market_beta = market_rows(universe, benchmark, returns, fast_days, slow_days, data_gaps)
    pairs = pair_rows(universe, returns, fast_days, slow_days, data_gaps)
    return {
        "generated_at_utc": utc_now(),
        "benchmark": benchmark,
        "regime_label": regime,
        "windows": {"fast_days": fast_days, "slow_days": slow_days},
        "universe": universe,
        "market_beta": market_beta,
        "pairs": pairs,
        "data_gaps": sorted(set(data_gaps)),
    }


def market_rows(universe: list[str], benchmark: str, returns: dict[str, dict[str, float]], fast_days: int, slow_days: int, gaps: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bench = returns.get(benchmark)
    if not bench:
        gaps.append(f"{benchmark}: benchmark returns unavailable")
        return rows
    for symbol in universe:
        series = returns.get(symbol)
        if not series:
            continue
        y, x = aligned(series, bench)
        if len(x) < max(30, slow_days - 10):
            gaps.append(f"{symbol}: insufficient aligned trading days ({len(x)}<{max(30, slow_days - 10)})")
        fast_y, fast_x = tail_pair(y, x, fast_days)
        slow_y, slow_x = tail_pair(y, x, slow_days)
        fast = stats(fast_y, fast_x)
        slow = stats(slow_y, slow_x)
        rows.append({
            "symbol": symbol,
            "beta_vs_benchmark_60d": fast["beta"],
            "beta_vs_benchmark_120d": slow["beta"],
            "corr_120d": slow["corr"],
            "n_obs": slow["n_obs"],
        })
    return rows


def pair_rows(universe: list[str], returns: dict[str, dict[str, float]], fast_days: int, slow_days: int, gaps: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for a in universe:
        for b in universe:
            if a == b or a not in returns or b not in returns:
                continue
            y, x = aligned(returns[a], returns[b])
            if len(x) < max(30, slow_days - 10):
                gaps.append(f"{a}~{b}: insufficient aligned trading days ({len(x)}<{max(30, slow_days - 10)})")
            fast_y, fast_x = tail_pair(y, x, fast_days)
            slow_y, slow_x = tail_pair(y, x, slow_days)
            fast = stats(fast_y, fast_x)
            slow = stats(slow_y, slow_x)
            rows.append({
                "a": a,
                "b": b,
                "beta_a_on_b_60d": fast["beta"],
                "beta_a_on_b_120d": slow["beta"],
                "corr_120d": slow["corr"],
                "r2": slow["r2"],
                "n_obs": slow["n_obs"],
                "relationship_regime_shift": beta_shift(fast["beta"], slow["beta"]),
                "aligned_trading_days": len(x),
            })
    return rows


def beta_shift(fast: Any, slow: Any) -> bool:
    if fast is None or slow is None:
        return False
    return abs(float(fast) - float(slow)) >= max(0.35, abs(float(slow)) * 0.35)


def write_snapshot(snapshot: dict[str, Any], outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = outdir / f"relationship_graph_{stamp}.json"
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(out_path, outdir / "latest.json")
    return out_path


def cmd_build(args: argparse.Namespace) -> int:
    universe = load_universe(args)
    if args.list_only:
        # Read-only mode: resolve --universe-file (including System A policy
        # JSON via universe_from_policy) without hitting the network. Used to
        # verify universe parsing independent of live quote availability.
        print(json.dumps({"status": "ok", "symbol_count": len(universe), "universe": universe}, ensure_ascii=False, indent=2))
        return 0
    benchmark = args.benchmark.upper()
    if benchmark not in universe:
        universe = [benchmark, *universe]
    snapshot = build_snapshot(universe, benchmark, args.fast_days, args.slow_days)
    path = write_snapshot(snapshot, Path(args.outdir))
    print(json.dumps({"status": "ok", "path": str(path), "latest_path": str(Path(args.outdir) / "latest.json"), **snapshot}, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = Path(args.outdir) / "latest.json"
    if not path.exists():
        raise SystemExit(json.dumps({"status": "missing", "path": str(path)}, ensure_ascii=False, indent=2))
    graph = json.loads(path.read_text(encoding="utf-8"))
    a, b = [part.strip().upper() for part in args.pair.split(",", 1)]
    pairs = [row for row in graph.get("pairs", []) if row.get("a") == a and row.get("b") == b]
    print(json.dumps({"status": "ok", "pair": pairs[0] if pairs else None, "path": str(path)}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build/show pairwise relationship graph")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build")
    p_build.add_argument("--universe-file")
    p_build.add_argument("--benchmark", default="SPY.US")
    p_build.add_argument("--slow-days", type=int, default=120)
    p_build.add_argument("--fast-days", type=int, default=60)
    p_build.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    p_build.add_argument("--json", action="store_true")
    p_build.add_argument("--list-only", action="store_true", help="Resolve --universe-file and print symbols only; no network calls.")
    p_build.set_defaults(func=cmd_build)
    p_show = sub.add_parser("show")
    p_show.add_argument("--pair", required=True, help="A.US,B.US")
    p_show.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
