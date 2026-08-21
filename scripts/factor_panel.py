#!/usr/bin/env python3
"""Historical OHLCV factor-panel cache. Research only; never executes orders."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or sys.executable
DEFAULT_ROOT = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-panels"
REQUIRED_FIELDS = ("date", "open", "close", "high", "low", "volume", "amount", "turnover_rate", "pct_change")
MARKETS = {"A", "US", "HK"}
Runner = Callable[[str, int], dict[str, Any]]


def cache_root() -> Path:
    return Path(os.environ.get("FACTOR_PANEL_DIR", str(DEFAULT_ROOT))).expanduser()


def envelope(schema: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": schema, **fields, "no_order_execution": True}


def _no_proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[key] = ""
    env["no_proxy"] = "*"
    return env


def run_akshare(code: str, timeout: int = 60) -> dict[str, Any]:
    """Run AkShare in its dedicated interpreter; caller parses JSON stdout."""
    try:
        proc = subprocess.run(
            [AKSHARE_PYTHON, "-c", code], capture_output=True, text=True,
            timeout=timeout, env=_no_proxy_env(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error": "timeout", "stdout": "", "stderr": ""}
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
        "error": None if proc.returncode == 0 else "nonzero_exit",
    }


def _child_code(market: str, symbol: str, start: str, end: str) -> str:
    # The child may import AkShare/pandas. This module itself remains stdlib-only.
    method = {"A": "stock_zh_a_hist", "US": "stock_us_hist", "HK": "stock_hk_hist"}[market]
    kwargs = f"symbol={symbol!r}, period='daily', start_date={start!r}, end_date={end!r}, adjust='qfq'"
    return f'''import json\nimport akshare as ak\ndf = ak.{method}({kwargs})\naliases = {{\n "date": ["日期", "date", "Date"], "open": ["开盘", "open", "Open"],\n "close": ["收盘", "close", "Close"], "high": ["最高", "high", "High"],\n "low": ["最低", "low", "Low"], "volume": ["成交量", "volume", "Volume"],\n "amount": ["成交额", "amount", "Amount"], "turnover_rate": ["换手率", "turnover_rate"],\n "pct_change": ["涨跌幅", "pct_change"]}}\nrows=[]\nfor _, row in df.iterrows():\n out={{}}\n for key, names in aliases.items():\n  value=None\n  for name in names:\n   if name in df.columns:\n    raw=row[name]\n    if raw is not None and str(raw) not in ("nan", "NaT", "<NA>"):\n     value=str(raw)[:10] if key == "date" else float(raw)\n    break\n  out[key]=value\n rows.append(out)\nprint(json.dumps(rows, ensure_ascii=False, allow_nan=False))'''


def _expected_trade_date(today: date | None = None) -> date:
    current = today or datetime.now(timezone.utc).date()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _panel_path(root: Path, market: str, symbol: str) -> Path:
    safe = "".join(c for c in symbol if c.isalnum() or c in (".", "-", "_"))
    if not safe or safe != symbol:
        raise ValueError(f"invalid symbol: {symbol!r}")
    return root / market / f"{safe}.json"


def _is_fresh(path: Path, expected: date | None = None) -> bool:
    payload = _read_json(path, {})
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    dates = [_parse_date(row.get("date")) for row in rows if isinstance(row, dict)]
    valid = [item for item in dates if item is not None]
    return bool(valid and max(valid) >= (expected or _expected_trade_date()))


def _normalize_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("AkShare output must be a JSON array")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = {field: item.get(field) for field in REQUIRED_FIELDS}
        parsed = _parse_date(row["date"])
        if parsed is None:
            continue
        row["date"] = parsed.isoformat()
        rows.append(row)
    rows.sort(key=lambda item: item["date"])
    if not rows:
        raise ValueError("AkShare returned no valid dated rows")
    return rows


def _load_symbols(symbols: list[str], symbols_file: str | None, max_symbols: int) -> list[str]:
    values = list(symbols)
    if symbols_file:
        values.extend(
            line.strip().split()[0] for line in Path(symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    unique = list(dict.fromkeys(values))
    if not unique:
        raise ValueError("at least one symbol is required")
    if len(unique) > max_symbols:
        raise ValueError(f"symbol count exceeds max_symbols={max_symbols}")
    return unique


def fetch_panel(
    market: str, symbols: list[str], *, window_days: int = 800,
    min_interval: float = 1.5, force: bool = False, root: Path | None = None,
    runner: Runner = run_akshare, sleep_fn: Callable[[float], None] = time.sleep,
    today: date | None = None,
) -> dict[str, Any]:
    market = market.upper()
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if window_days < 2 or min_interval < 0:
        raise ValueError("window_days must be >=2 and min_interval must be non-negative")
    target_root = root or cache_root()
    market_dir = target_root / market
    market_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end_date = today or now.date()
    start_date = end_date - timedelta(days=window_days)
    gaps: list[dict[str, str]] = []
    fetched = skipped = calls = 0
    last_call_at: float | None = None
    symbol_records: dict[str, Any] = {}

    def throttled_call(code: str) -> dict[str, Any]:
        nonlocal calls, last_call_at
        if last_call_at is not None and min_interval:
            elapsed = time.monotonic() - last_call_at
            if elapsed < min_interval:
                sleep_fn(min_interval - elapsed)
        result = runner(code, 60)
        calls += 1
        last_call_at = time.monotonic()
        return result

    for symbol in symbols:
        path = _panel_path(target_root, market, symbol)
        if not force and _is_fresh(path, _expected_trade_date(end_date)):
            skipped += 1
            cached = _read_json(path, {})
            symbol_records[symbol] = {"status": "skipped_fresh", "rows": len(cached.get("rows", []))}
            continue
        code = _child_code(market, symbol, start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"))
        result: dict[str, Any] = {}
        error = "unknown"
        rows: list[dict[str, Any]] | None = None
        for attempt in range(2):
            result = throttled_call(code)
            if result.get("status") == "pass":
                try:
                    rows = _normalize_rows(json.loads(result.get("stdout", "")))
                    break
                except (json.JSONDecodeError, ValueError) as exc:
                    error = f"invalid_output:{exc}"
            else:
                error = str(result.get("error") or result.get("stderr") or "akshare_error")[-200:]
        if rows is None:
            gaps.append({"symbol": symbol, "reason_code": "error", "gap": "akshare_fetch_failed", "detail": error})
            symbol_records[symbol] = {"status": "failed", "gap": "akshare_fetch_failed"}
            continue
        adjust_basis = f"qfq_snapshot_{end_date.strftime('%Y%m%d')}"
        payload = envelope(
            "factor_panel_symbol.v1", market=market, symbol=symbol,
            fetched_at=now.isoformat(), adjust_basis=adjust_basis, rows=rows,
            pit_caveats=["qfq_rewrites_history", "survivorship_current_constituents"],
        )
        _atomic_json(path, payload)
        fetched += 1
        symbol_records[symbol] = {"status": "fetched", "rows": len(rows), "path": str(path)}

    failed = len(gaps)
    status = "failed" if fetched + skipped == 0 else ("partial" if failed else "ok")
    manifest_path = market_dir / "panel_manifest.json"
    previous = _read_json(manifest_path, {})
    manifest = envelope(
        "factor_panel_manifest.v1", market=market, updated_at=now.isoformat(),
        adjust_basis=f"qfq_snapshot_{end_date.strftime('%Y%m%d')}",
        requested_symbols=symbols, requested=len(symbols), fetched=fetched,
        skipped_fresh=skipped, failed=failed, calls_made=calls,
        cumulative_calls_made=int(previous.get("cumulative_calls_made", 0)) + calls,
        symbols=symbol_records, data_gaps=gaps,
    )
    _atomic_json(manifest_path, manifest)
    return envelope(
        "factor_panel_fetch.v1", ok=status != "failed", status=status, market=market,
        requested=len(symbols), fetched=fetched, skipped_fresh=skipped, failed=failed,
        calls_made=calls, min_interval_s=min_interval,
        adjust_basis=manifest["adjust_basis"],
        pit_caveats=["qfq_rewrites_history", "survivorship_current_constituents"],
        data_gaps=gaps, manifest_path=str(manifest_path),
    )


def panel_status(market: str, *, root: Path | None = None, today: date | None = None) -> dict[str, Any]:
    market = market.upper()
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    market_dir = (root or cache_root()) / market
    items: list[dict[str, Any]] = []
    for path in sorted(market_dir.glob("*.json")) if market_dir.exists() else []:
        if path.name == "panel_manifest.json":
            continue
        payload = _read_json(path, {})
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        missing = sorted({field for row in rows if isinstance(row, dict) for field in REQUIRED_FIELDS if field not in row})
        valid_latest = [str(row.get("date")) for row in rows if isinstance(row, dict) and row.get("date")]
        latest = max(valid_latest) if valid_latest else None
        items.append({"symbol": path.stem, "rows": len(rows), "latest_date": latest,
                      "fresh": _is_fresh(path, _expected_trade_date(today)), "missing_fields": missing})
    stale = sum(not item["fresh"] for item in items)
    return envelope("factor_panel_status.v1", ok=bool(items) and stale == 0,
                    status="ok" if items and stale == 0 else "insufficient_data",
                    market=market, symbols=len(items), stale=stale, items=items,
                    data_gaps=[] if items else [{"reason_code": "missing", "gap": "panel_cache_empty"}])


def prune_cache(keep_days: int, *, root: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    if keep_days < 0:
        raise ValueError("keep_days must be non-negative")
    target_root = root or cache_root()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    removed: list[str] = []
    for path in target_root.glob("*/*.json") if target_root.exists() else []:
        if path.name == "panel_manifest.json":
            continue
        payload = _read_json(path, {})
        fetched = payload.get("fetched_at") if isinstance(payload, dict) else None
        try:
            stamp = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if stamp < cutoff:
            path.unlink()
            removed.append(str(path))
    return envelope("factor_panel_prune.v1", ok=True, keep_days=keep_days,
                    removed_count=len(removed), removed=removed)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        calls: list[str] = []
        def fake(code: str, timeout: int) -> dict[str, Any]:
            calls.append(code)
            rows = [{field: ("2026-08-21" if field == "date" else 1.0) for field in REQUIRED_FIELDS}]
            return {"status": "pass", "stdout": json.dumps(rows), "stderr": ""}
        first = fetch_panel("A", ["600519"], force=True, min_interval=0, root=root, runner=fake, today=date(2026, 8, 21))
        assert first["fetched"] == 1 and first["calls_made"] == 1
        second = fetch_panel("A", ["600519"], min_interval=0, root=root, runner=fake, today=date(2026, 8, 22))
        assert second["skipped_fresh"] == 1 and second["calls_made"] == 0 and len(calls) == 1
        status = panel_status("A", root=root, today=date(2026, 8, 22))
        assert status["status"] == "ok" and status["items"][0]["rows"] == 1
        old = _read_json(root / "A" / "600519.json", {})
        old["fetched_at"] = "2020-01-01T00:00:00+00:00"
        _atomic_json(root / "A" / "600519.json", old)
        pruned = prune_cache(30, root=root, now=datetime(2026, 8, 21, tzinfo=timezone.utc))
        assert pruned["removed_count"] == 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--market", required=True, choices=sorted(MARKETS))
    fetch.add_argument("--symbols", nargs="*", default=[])
    fetch.add_argument("--symbols-file")
    fetch.add_argument("--window-days", type=int, default=800)
    fetch.add_argument("--min-interval", type=float, default=1.5)
    fetch.add_argument("--max-symbols", type=int, default=100)
    fetch.add_argument("--force", action="store_true")
    fetch.add_argument("--json", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--market", required=True, choices=sorted(MARKETS))
    status.add_argument("--json", action="store_true")
    prune = sub.add_parser("prune")
    prune.add_argument("--keep-days", type=int, default=30)
    prune.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed"}))
        return 0
    args = build_parser().parse_args(args_list)
    try:
        if args.command == "fetch":
            symbols = _load_symbols(args.symbols, args.symbols_file, args.max_symbols)
            result = fetch_panel(args.market, symbols, window_days=args.window_days,
                                 min_interval=args.min_interval, force=args.force)
        elif args.command == "status":
            result = panel_status(args.market)
        else:
            result = prune_cache(args.keep_days)
    except (OSError, ValueError) as exc:
        result = envelope("factor_panel_error.v1", ok=False, status="error",
                          data_gaps=[{"reason_code": "error", "gap": str(exc)}])
        print(json.dumps(result, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if getattr(args, "json", False) else None))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
