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
SOURCE_MODES = {"auto", "akshare", "akshare_sina", "longbridge"}
SOURCE_ADJUST = {"akshare": "qfq", "akshare_sina": "sina_qfq", "longbridge": "forward"}
EM_HEALTH_COOLDOWN_HOURS = 6.0
EM_HEALTH_CANARY = {"A": "600519", "HK": "00700"}
# Eastmoney's WAF is leak-bucket-like: a clean IP can pass once before being blocked.
EM_CIRCUIT_MAX_CONSECUTIVE_FAILURES = 3
Runner = Callable[[str, int], dict[str, Any]]
LongBridgeRunner = Callable[[str, int], dict[str, Any]]


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


def _longbridge_symbol(symbol: str, market: str = "US") -> str:
    """Map panel symbols to LongBridge's market-qualified form."""
    market = market.upper()
    if market == "US":
        normalized = symbol.strip().upper()
        if normalized.endswith(".US"):
            return normalized
        prefix, separator, remainder = normalized.partition(".")
        if separator and prefix.isdigit() and remainder:
            normalized = remainder
        return f"{normalized}.US"
    if market == "HK":
        normalized = symbol.strip().upper()
        if normalized.endswith(".HK"):
            normalized = normalized[:-3]
        if not normalized.isdigit():
            raise ValueError("hk_symbol_invalid")
        digits = normalized.lstrip("0") or "0"
        return f"{digits}.HK"
    raise ValueError("longbridge_market_unsupported")


def run_longbridge(symbol: str, count: int, market: str = "US") -> dict[str, Any]:
    """Fetch forward-adjusted daily candles through the registered SDK/CLI adapter."""
    try:
        import longbridge_query as longbridge

        # QuoteContext initialization can print a quote-package banner. Keep the
        # factor_panel --json contract clean while retaining normal network env.
        with longbridge.suppress_stdout_fd():
            ctx = longbridge.get_ctx()
            rows = longbridge.cmd_candle(
                ctx, _longbridge_symbol(symbol, market), "day", count, adjust="forward"
            )
    except Exception as exc:
        detail = f"{type(exc).__name__}:{exc}"
        return {"status": "fail", "error": detail[-200:], "rows": []}
    return {"status": "pass", "error": None, "rows": rows}


def _child_code(market: str, symbol: str, start: str, end: str) -> str:
    # The child may import AkShare/pandas. This module itself remains stdlib-only.
    method = {"A": "stock_zh_a_hist", "US": "stock_us_hist", "HK": "stock_hk_hist"}[market]
    kwargs = f"symbol={symbol!r}, period='daily', start_date={start!r}, end_date={end!r}, adjust='qfq'"
    return f'''import json\nimport akshare as ak\ndf = ak.{method}({kwargs})\naliases = {{\n "date": ["日期", "date", "Date"], "open": ["开盘", "open", "Open"],\n "close": ["收盘", "close", "Close"], "high": ["最高", "high", "High"],\n "low": ["最低", "low", "Low"], "volume": ["成交量", "volume", "Volume"],\n "amount": ["成交额", "amount", "Amount"], "turnover_rate": ["换手率", "turnover_rate"],\n "pct_change": ["涨跌幅", "pct_change"]}}\nrows=[]\nfor _, row in df.iterrows():\n out={{}}\n for key, names in aliases.items():\n  value=None\n  for name in names:\n   if name in df.columns:\n    raw=row[name]\n    if raw is not None and str(raw) not in ("nan", "NaT", "<NA>"):\n     value=str(raw)[:10] if key == "date" else float(raw)\n    break\n  out[key]=value\n rows.append(out)\nprint(json.dumps(rows, ensure_ascii=False, allow_nan=False))'''


def _sina_a_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("0", "3")):
        return f"sz{symbol}"
    raise ValueError("sina_symbol_unsupported")


def _sina_child_code(market: str, symbol: str, start: str, end: str) -> str:
    """Build an AkShare Sina child snippet for A/HK daily qfq rows."""
    if market == "A":
        sina_symbol = _sina_a_symbol(symbol)
        method = "stock_zh_a_daily"
        kwargs = (
            f"symbol={sina_symbol!r}, start_date={start!r}, "
            f"end_date={end!r}, adjust='qfq'"
        )
        # Sina turnover is a decimal fraction; Eastmoney turnover is a percentage.
        # Preserve the Sina value unchanged and never compare it across these sources.
        turnover_expression = "number(row.get('turnover'))"
    elif market == "HK":
        method = "stock_hk_daily"
        kwargs = f"symbol={symbol!r}, adjust='qfq'"
        turnover_expression = "None"
    else:
        raise ValueError("akshare_sina only supports market A/HK")

    start_iso = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_iso = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    return f'''import json\nimport akshare as ak\ndf = ak.{method}({kwargs})\nstart_date = {start_iso!r}\nend_date = {end_iso!r}\ndef number(raw):\n if raw is None or str(raw) in ("nan", "NaT", "<NA>"):\n  return None\n return float(raw)\nrows=[]\nfor _, row in df.iterrows():\n day=str(row.get("date"))[:10]\n if day < start_date or day > end_date:\n  continue\n rows.append({{\n  "date": day, "open": number(row.get("open")),\n  "close": number(row.get("close")), "high": number(row.get("high")),\n  "low": number(row.get("low")), "volume": number(row.get("volume")),\n  "amount": number(row.get("amount")), "turnover_rate": {turnover_expression},\n  "pct_change": None}})\nrows.sort(key=lambda item: item["date"])\nfor index in range(1, len(rows)):\n previous=rows[index - 1]["close"]\n current=rows[index]["close"]\n if previous is not None and previous != 0 and current is not None:\n  rows[index]["pct_change"]=(current - previous) / previous * 100.0\nprint(json.dumps(rows, ensure_ascii=False, allow_nan=False))'''


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


def _em_health_gate(
    market: str, probe_call: Callable[[str], dict[str, Any]], root: Path, now: datetime,
) -> tuple[bool, str | None]:
    """Check one Eastmoney canary and persist a bounded retry cooldown."""
    market = market.upper()
    if market not in EM_HEALTH_CANARY:
        raise ValueError("em_health_market_unsupported")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    health_path = root / "em_health.json"
    health = _read_json(health_path, {})
    cooldown_until = health.get("cooldown_until") if isinstance(health, dict) else None
    if isinstance(cooldown_until, str):
        try:
            cooldown_at = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
            if cooldown_at.tzinfo is None:
                cooldown_at = cooldown_at.replace(tzinfo=timezone.utc)
            if cooldown_at > now:
                return False, "em_cooldown_active"
        except ValueError:
            pass

    end_date = now.date()
    code = _child_code(
        market, EM_HEALTH_CANARY[market],
        (end_date - timedelta(days=10)).strftime("%Y%m%d"), end_date.strftime("%Y%m%d"),
    )
    error: str | None = None
    try:
        result = probe_call(code)
        if result.get("status") != "pass":
            error = str(result.get("error") or result.get("stderr") or "akshare_error")
        else:
            try:
                _normalize_rows(json.loads(result.get("stdout", "")))
            except (json.JSONDecodeError, ValueError) as exc:
                error = f"invalid_output:{exc}"
    except Exception as exc:  # runner errors are a health failure, never a panel crash.
        error = f"{type(exc).__name__}:{exc}"
    if error is None:
        _atomic_json(health_path, {"status": "up", "checked_at": now.isoformat()})
        return True, None

    _write_em_cooldown(health_path, now, error)
    return False, "em_probe_failed"


def _write_em_cooldown(health_path: Path, now: datetime, error: str) -> None:
    """Persist the shared Eastmoney probe/circuit cooldown state."""
    _atomic_json(health_path, {
        "status": "down",
        "checked_at": now.isoformat(),
        "cooldown_until": (now + timedelta(hours=EM_HEALTH_COOLDOWN_HOURS)).isoformat(),
        "last_error": error[-200:],
    })


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lb_trade_date(value: Any, market: str) -> date | None:
    """Interpret LongBridge candle time as a trade date for the requested market."""
    if market != "HK" or not isinstance(value, str):
        return _parse_date(value)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _parse_date(value)
    if len(value) <= 10:
        return _parse_date(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) + timedelta(hours=8)).date()


def _normalize_longbridge_rows(
    raw: Any, start: date, end: date, market: str = "US",
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("LongBridge output must be a JSON array")
    base_rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed = _lb_trade_date(item.get("time") or item.get("timestamp") or item.get("date"), market)
        close = _optional_float(item.get("close"))
        if parsed is None or close is None or parsed < start or parsed > end:
            continue
        base_rows.append({
            "date": parsed.isoformat(),
            "open": _optional_float(item.get("open")),
            "close": close,
            "high": _optional_float(item.get("high")),
            "low": _optional_float(item.get("low")),
            "volume": _optional_float(item.get("volume")),
            "amount": _optional_float(item.get("turnover") or item.get("amount")),
            "turnover_rate": None,
            "pct_change": None,
        })
    base_rows.sort(key=lambda item: item["date"])
    deduped = list({row["date"]: row for row in base_rows}.values())
    for index in range(1, len(deduped)):
        previous = deduped[index - 1]["close"]
        current = deduped[index]["close"]
        if previous:
            deduped[index]["pct_change"] = (current - previous) / previous * 100.0
    if not deduped:
        raise ValueError("LongBridge returned no valid dated close rows")
    return deduped


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
    today: date | None = None, source: str = "auto",
    longbridge_runner: LongBridgeRunner | None = None,
) -> dict[str, Any]:
    market = market.upper()
    source = source.lower()
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if source not in SOURCE_MODES:
        raise ValueError(f"unsupported source: {source}")
    if source == "longbridge" and market not in {"US", "HK"}:
        raise ValueError("--source longbridge only supports markets US/HK")
    if source == "akshare_sina" and market not in {"A", "HK"}:
        raise ValueError("--source akshare_sina only supports market A/HK")
    if window_days < 2 or min_interval < 0:
        raise ValueError("window_days must be >=2 and min_interval must be non-negative")
    if market in {"A", "HK"} and min_interval < 1.5:
        raise ValueError("A/HK AkShare requests require min_interval >=1.5 seconds")
    if market in {"A", "HK"} and len(symbols) > 100:
        raise ValueError("A/HK panel runs support at most 100 symbols")
    target_root = root or cache_root()
    market_dir = target_root / market
    market_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end_date = today or now.date()
    start_date = end_date - timedelta(days=window_days)
    gaps: list[dict[str, Any]] = []
    fetched = skipped = calls = 0
    akshare_calls = akshare_sina_calls = longbridge_calls = 0
    last_call_at: dict[str, float | None] = {"akshare": None, "longbridge": None}
    symbol_records: dict[str, Any] = {}
    em_gate_applicable = market in {"A", "HK"} and source == "auto"
    em_gate_evaluated = False
    em_gate_active = True
    em_gate_reason: str | None = None
    em_consecutive_failures = 0

    def wait_for_slot(provider: str) -> None:
        previous_call = last_call_at[provider]
        if previous_call is not None and min_interval:
            elapsed = time.monotonic() - previous_call
            if elapsed < min_interval:
                sleep_fn(min_interval - elapsed)

    def throttled_akshare_call(code: str) -> dict[str, Any]:
        nonlocal calls, akshare_calls
        wait_for_slot("akshare")
        result = runner(code, 60)
        calls += 1
        akshare_calls += 1
        last_call_at["akshare"] = time.monotonic()
        return result

    def throttled_akshare_sina_call(code: str) -> dict[str, Any]:
        nonlocal calls, akshare_sina_calls
        wait_for_slot("akshare")
        result = runner(code, 60)
        calls += 1
        akshare_sina_calls += 1
        last_call_at["akshare"] = time.monotonic()
        return result

    def throttled_longbridge_call(symbol: str, count: int) -> dict[str, Any]:
        nonlocal calls, longbridge_calls
        wait_for_slot("longbridge")
        result = (
            run_longbridge(symbol, count, market)
            if longbridge_runner is None else longbridge_runner(symbol, count)
        )
        calls += 1
        longbridge_calls += 1
        last_call_at["longbridge"] = time.monotonic()
        return result

    for symbol in symbols:
        path = _panel_path(target_root, market, symbol)
        cached = _read_json(path, {})
        requested_source = source if source in SOURCE_ADJUST else None
        cached_source = cached.get("source")
        cache_has_valid_provenance = (
            cached_source in SOURCE_ADJUST
            and cached.get("adjust") == SOURCE_ADJUST[cached_source]
        )
        cache_matches_source = (
            cache_has_valid_provenance
            and (requested_source is None or cached_source == requested_source)
        )
        if not force and cache_matches_source and _is_fresh(path, _expected_trade_date(end_date)):
            skipped += 1
            symbol_records[symbol] = {
                "status": "skipped_fresh", "rows": len(cached.get("rows", [])),
                "path": str(path), "source": cached.get("source"),
                "panel_source": cached.get("panel_source") or cached.get("source"),
                "adjust": cached.get("adjust"), "adjust_basis": cached.get("adjust_basis"),
            }
            continue

        errors: dict[str, str] = {}
        attempted_sources: list[str] = []
        rows: list[dict[str, Any]] | None = None
        actual_source: str | None = None
        sina_symbol_unsupported = False

        if rows is None and market == "HK" and source in {"auto", "longbridge"}:
            attempted_sources.append("longbridge")
            result = throttled_longbridge_call(symbol, min(1000, max(2, window_days)))
            if result.get("status") == "pass":
                try:
                    rows = _normalize_longbridge_rows(result.get("rows"), start_date, end_date, market)
                    actual_source = "longbridge"
                except ValueError as exc:
                    errors["longbridge"] = f"invalid_output:{exc}"
            else:
                errors["longbridge"] = str(result.get("error") or "longbridge_error")[-200:]

        if rows is None and em_gate_applicable and not em_gate_evaluated:
            em_gate_active, em_gate_reason = _em_health_gate(
                market, throttled_akshare_call, target_root, now,
            )
            em_gate_evaluated = True

        if rows is None and source in {"auto", "akshare"} and (
            source == "akshare" or em_gate_active
        ):
            attempted_sources.append("akshare")
            code = _child_code(market, symbol, start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"))
            for _attempt in range(2):
                result = throttled_akshare_call(code)
                if result.get("status") == "pass":
                    try:
                        rows = _normalize_rows(json.loads(result.get("stdout", "")))
                        actual_source = "akshare"
                        if em_gate_applicable:
                            em_consecutive_failures = 0
                        break
                    except (json.JSONDecodeError, ValueError) as exc:
                        errors["akshare"] = f"invalid_output:{exc}"
                else:
                    errors["akshare"] = str(
                        result.get("error") or result.get("stderr") or "akshare_error"
                    )[-200:]
                if em_gate_applicable:
                    em_consecutive_failures += 1
                    if em_consecutive_failures >= EM_CIRCUIT_MAX_CONSECUTIVE_FAILURES:
                        _write_em_cooldown(
                            target_root / "em_health.json", now, errors["akshare"],
                        )
                        em_gate_active = False
                        em_gate_evaluated = True
                        em_gate_reason = "em_circuit_tripped"
                        break

        if rows is None and market in {"A", "HK"} and source in {"auto", "akshare_sina"}:
            attempted_sources.append("akshare_sina")
            try:
                code = _sina_child_code(
                    market, symbol, start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
                )
            except ValueError as exc:
                errors["akshare_sina"] = str(exc)
                sina_symbol_unsupported = str(exc) == "sina_symbol_unsupported"
            else:
                result = throttled_akshare_sina_call(code)
                if result.get("status") == "pass":
                    try:
                        rows = _normalize_rows(json.loads(result.get("stdout", "")))
                        actual_source = "akshare_sina"
                    except (json.JSONDecodeError, ValueError) as exc:
                        errors["akshare_sina"] = f"invalid_output:{exc}"
                else:
                    errors["akshare_sina"] = str(
                        result.get("error") or result.get("stderr") or "akshare_sina_error"
                    )[-200:]

        if rows is None and market == "US" and source in {"auto", "longbridge"}:
            attempted_sources.append("longbridge")
            result = throttled_longbridge_call(symbol, min(1000, max(2, window_days)))
            if result.get("status") == "pass":
                try:
                    rows = _normalize_longbridge_rows(result.get("rows"), start_date, end_date, market)
                    actual_source = "longbridge"
                except ValueError as exc:
                    errors["longbridge"] = f"invalid_output:{exc}"
            else:
                errors["longbridge"] = str(result.get("error") or "longbridge_error")[-200:]

        if rows is None:
            reason_code = "error"
            if sina_symbol_unsupported:
                gap_name = "sina_symbol_unsupported"
                reason_code = "unsupported"
            elif attempted_sources == ["longbridge", "akshare", "akshare_sina"]:
                gap_name = "longbridge_and_akshare_family_fetch_failed"
            elif attempted_sources == ["longbridge", "akshare_sina"]:
                gap_name = "longbridge_and_akshare_sina_fetch_failed"
            elif attempted_sources == ["akshare", "akshare_sina"]:
                gap_name = "akshare_and_akshare_sina_fetch_failed"
            elif attempted_sources == ["akshare_sina"]:
                gap_name = "akshare_sina_fetch_failed"
            elif attempted_sources == ["akshare", "longbridge"]:
                gap_name = "akshare_and_longbridge_fetch_failed"
            elif attempted_sources == ["longbridge"]:
                gap_name = "longbridge_fetch_failed"
            else:
                gap_name = "akshare_fetch_failed"
            detail = "; ".join(f"{name}={errors.get(name, 'unknown')}" for name in attempted_sources)
            gaps.append({
                "symbol": symbol, "reason_code": reason_code, "gap": gap_name,
                "detail": detail, "sources_attempted": attempted_sources,
            })
            symbol_records[symbol] = {
                "status": "failed", "gap": gap_name, "source": None,
                "adjust": None, "adjust_basis": None,
                "sources_attempted": attempted_sources,
            }
            continue

        assert actual_source in SOURCE_ADJUST
        adjust = SOURCE_ADJUST[actual_source]
        adjust_basis = f"{adjust}_snapshot_{end_date.strftime('%Y%m%d')}"
        rewrite_caveat = (
            "qfq_rewrites_history"
            if adjust in {"qfq", "sina_qfq"}
            else "forward_adjust_rewrites_history"
        )
        payload = envelope(
            "factor_panel_symbol.v1", market=market, symbol=symbol,
            fetched_at=now.isoformat(), source=actual_source, panel_source=actual_source, adjust=adjust,
            adjust_basis=adjust_basis, rows=rows,
            pit_caveats=[rewrite_caveat, "survivorship_current_constituents"],
        )
        _atomic_json(path, payload)
        fetched += 1
        symbol_records[symbol] = {
            "status": "fetched", "rows": len(rows), "path": str(path),
            "source": actual_source, "panel_source": actual_source,
            "adjust": adjust, "adjust_basis": adjust_basis,
            "sources_attempted": attempted_sources,
        }

    failed = sum(record.get("status") == "failed" for record in symbol_records.values())
    status = "failed" if fetched + skipped == 0 else ("partial" if failed else "ok")
    sources_used = sorted({
        str(record["source"]) for record in symbol_records.values() if record.get("source")
    })
    adjusts_used = sorted({
        str(record["adjust"]) for record in symbol_records.values() if record.get("adjust")
    })
    adjust_bases_used = sorted({
        str(record["adjust_basis"])
        for record in symbol_records.values() if record.get("adjust_basis")
    })
    if len(adjust_bases_used) > 1:
        gaps.append({"reason_code": "mixed_adjust_basis_risk", "bases": adjust_bases_used})
    manifest_source = sources_used[0] if len(sources_used) == 1 else None
    manifest_adjust = adjusts_used[0] if len(adjusts_used) == 1 else None
    manifest_adjust_basis = adjust_bases_used[0] if len(adjust_bases_used) == 1 else None
    aggregate_mode = "single" if len(sources_used) == 1 else ("mixed" if sources_used else "none")
    pit_caveats = []
    if {"qfq", "sina_qfq"}.intersection(adjusts_used):
        pit_caveats.append("qfq_rewrites_history")
    if "forward" in adjusts_used:
        pit_caveats.append("forward_adjust_rewrites_history")
    pit_caveats.append("survivorship_current_constituents")
    manifest_path = market_dir / "panel_manifest.json"
    previous = _read_json(manifest_path, {})
    em_gate = (
        {
            "evaluated": em_gate_evaluated,
            "active": em_gate_active,
            "reason": em_gate_reason if em_gate_evaluated else "not_evaluated",
        }
        if em_gate_applicable else None
    )
    manifest = envelope(
        "factor_panel_manifest.v1", market=market, updated_at=now.isoformat(),
        source=manifest_source, adjust=manifest_adjust, source_mode=source,
        source_aggregate={"mode": aggregate_mode, "values": sources_used},
        adjust_aggregate={"mode": aggregate_mode, "values": adjusts_used},
        sources_used=sources_used, adjust_basis=manifest_adjust_basis,
        adjust_bases_used=adjust_bases_used,
        requested_symbols=symbols, requested=len(symbols), fetched=fetched,
        skipped_fresh=skipped, failed=failed, calls_made=calls,
        akshare_calls_made=akshare_calls, akshare_sina_calls_made=akshare_sina_calls,
        longbridge_calls_made=longbridge_calls, em_gate=em_gate,
        cumulative_calls_made=int(previous.get("cumulative_calls_made", 0)) + calls,
        symbols=symbol_records, data_gaps=gaps,
    )
    _atomic_json(manifest_path, manifest)
    return envelope(
        "factor_panel_fetch.v1", ok=status != "failed", status=status, market=market,
        requested=len(symbols), fetched=fetched, skipped_fresh=skipped, failed=failed,
        calls_made=calls, min_interval_s=min_interval,
        source=manifest_source, adjust=manifest_adjust, source_mode=source,
        sources_used=sources_used, akshare_calls_made=akshare_calls,
        akshare_sina_calls_made=akshare_sina_calls, longbridge_calls_made=longbridge_calls,
        em_gate=em_gate,
        adjust_basis=manifest["adjust_basis"], adjust_bases_used=adjust_bases_used,
        pit_caveats=pit_caveats,
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
                      "fresh": _is_fresh(path, _expected_trade_date(today)),
                      "source": payload.get("source"),
                      "panel_source": payload.get("panel_source") or payload.get("source"),
                      "adjust": payload.get("adjust"),
                      "missing_fields": missing})
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
        first = fetch_panel("A", ["600519"], force=True, min_interval=1.5, root=root,
                            runner=fake, sleep_fn=lambda _seconds: None, today=date(2026, 8, 21))
        assert first["fetched"] == 1 and first["calls_made"] == 2
        second = fetch_panel("A", ["600519"], min_interval=1.5, root=root,
                             runner=fake, sleep_fn=lambda _seconds: None, today=date(2026, 8, 22))
        assert second["skipped_fresh"] == 1 and second["calls_made"] == 0 and len(calls) == 2
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
    fetch.add_argument("--source", choices=sorted(SOURCE_MODES), default="auto",
                       help="auto=AkShare Eastmoney primary + Sina A/HK or LongBridge US fallback")
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
            if args.market in {"A", "HK"} and args.max_symbols > 100:
                raise ValueError("A/HK --max-symbols cannot exceed 100")
            symbols = _load_symbols(args.symbols, args.symbols_file, args.max_symbols)
            result = fetch_panel(args.market, symbols, window_days=args.window_days,
                                 min_interval=args.min_interval, force=args.force,
                                 source=args.source)
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
