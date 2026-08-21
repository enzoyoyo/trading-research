#!/usr/bin/env python3
"""Session-aligned historical earnings moves from SEC 8-K and LongBridge candles."""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

try:
    from us_company_evidence import collect_company_evidence
except ImportError:
    collect_company_evidence = None  # type: ignore[assignment]

DEFAULT_CACHE_ROOT = (
    Path.home() / ".cache" / "hermes" / "trading-research"
    / "earnings-radar" / "moves"
)
MIN_VALID_SAMPLES = 4
ET = ZoneInfo("America/New_York")


def cache_root() -> Path:
    return Path(os.environ.get("EARNINGS_RADAR_MOVE_DIR", str(DEFAULT_CACHE_ROOT))).expanduser()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def select_sec_8k_events(rows: list[dict[str, Any]], *, n: int) -> list[dict[str, Any]]:
    if n < 1:
        raise ValueError("n must be positive")
    selected = [
        dict(row) for row in rows
        if str(row.get("form_type") or "").upper() == "8-K"
        and str(row.get("source_family") or "").lower() == "sec_edgar"
        and str(row.get("filed_at") or "").strip()
    ]
    selected.sort(key=lambda row: str(row.get("filed_at") or ""), reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in selected:
        identity = str(row.get("accession_number") or row.get("filed_at"))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(row)
    return unique[:n]


def _classify_session(filed_at: Any) -> tuple[str, str | None]:
    text = str(filed_at or "").strip()
    if len(text) <= 10:
        return "unknown", text[:10] or None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return "unknown", text[:10] or None
    if parsed.tzinfo is None:
        return "unknown", parsed.date().isoformat()
    local = parsed.astimezone(ET)
    if local.timetz().replace(tzinfo=None) < time(9, 30):
        return "BMO", local.date().isoformat()
    if local.timetz().replace(tzinfo=None) >= time(16, 0):
        return "AMC", local.date().isoformat()
    return "intraday", local.date().isoformat()


def _normalize_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for row in candles:
        if not isinstance(row, dict):
            continue
        trading_date = str(row.get("time") or row.get("date") or "")[:10]
        try:
            datetime.strptime(trading_date, "%Y-%m-%d")
        except ValueError:
            continue
        open_price = _number(row.get("open"))
        close_price = _number(row.get("close"))
        if open_price is None or close_price is None or open_price <= 0 or close_price <= 0:
            continue
        normalized[trading_date] = {
            "date": trading_date,
            "open": open_price,
            "close": close_price,
        }
    return [normalized[key] for key in sorted(normalized)]


def _abs_return(end: float, start: float) -> float | None:
    if start <= 0 or end <= 0:
        return None
    return abs(end / start - 1.0)


def build_history(
    symbol: str,
    regulatory_rows: list[dict[str, Any]],
    candles: list[dict[str, Any]],
    *,
    n: int = 8,
    min_samples: int = MIN_VALID_SAMPLES,
    extra_gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if n < 1 or min_samples < 1:
        raise ValueError("n and min_samples must be positive")
    events = select_sec_8k_events(regulatory_rows, n=n)
    prices = _normalize_candles(candles)
    index_by_date = {row["date"]: index for index, row in enumerate(prices)}
    output_events: list[dict[str, Any]] = []
    gaps = [dict(row) for row in (extra_gaps or [])]
    gaps.append({
        "gap": "sec_8k_filed_at_is_release_proxy",
        "reason_code": "method_limit",
        "impact": "verify issuer IR earnings timestamp before predictive use",
    })
    if not events:
        gaps.append({
            "gap": "sec_8k_earnings_events_unavailable",
            "reason_code": "missing",
            "impact": "event dates are not fabricated",
        })
    if not prices:
        gaps.append({
            "gap": "longbridge_daily_candles_unavailable",
            "reason_code": "missing",
            "impact": "realized earnings moves unavailable",
        })

    for raw in events:
        filed_at = str(raw.get("filed_at") or "")
        session, event_date = _classify_session(filed_at)
        row: dict[str, Any] = {
            "event_date": event_date,
            "filed_at": filed_at,
            "session": session,
            "event_date_source": "sec_edgar_8k_filed_at_proxy",
            "accession_number": raw.get("accession_number"),
            "overnight_abs_move": None,
            "close_to_close_abs_move": None,
            "abs_move": None,
            "primary_window": None,
            "pre_close_date": None,
            "reaction_date": None,
            "data_gaps": [],
        }
        current_index = index_by_date.get(event_date or "")
        if session == "AMC" and current_index is not None and current_index + 1 < len(prices):
            current = prices[current_index]
            reaction = prices[current_index + 1]
            row["overnight_abs_move"] = _abs_return(reaction["open"], current["close"])
            row["close_to_close_abs_move"] = _abs_return(reaction["close"], current["close"])
            row["abs_move"] = row["close_to_close_abs_move"]
            row["primary_window"] = "close_T_to_close_T_plus_1"
            row["pre_close_date"] = current["date"]
            row["reaction_date"] = reaction["date"]
        elif session == "BMO" and current_index is not None and current_index > 0:
            previous = prices[current_index - 1]
            reaction = prices[current_index]
            row["overnight_abs_move"] = _abs_return(reaction["open"], previous["close"])
            row["close_to_close_abs_move"] = _abs_return(reaction["close"], previous["close"])
            row["abs_move"] = row["close_to_close_abs_move"]
            row["primary_window"] = "close_T_minus_1_to_close_T"
            row["pre_close_date"] = previous["date"]
            row["reaction_date"] = reaction["date"]
        else:
            reason = "release_session_unknown" if session == "unknown" else (
                "intraday_release_not_aligned" if session == "intraday" else "aligned_candles_missing"
            )
            row["data_gaps"].append({"gap": reason, "reason_code": "missing"})
        output_events.append(row)

    valid_moves = [row["abs_move"] for row in output_events if row["abs_move"] is not None]
    enough = len(valid_moves) >= min_samples
    median = statistics.median(valid_moves) if enough else None
    if not enough:
        gaps.append({
            "gap": "historical_sample_insufficient",
            "reason_code": "insufficient",
            "required": min_samples,
            "available": len(valid_moves),
            "impact": "hist_median is null",
        })
    for row in output_events:
        row["hist_median"] = median
        row["sample_count"] = len(valid_moves)

    return {
        "schema_version": "earnings_move_history.v1",
        "symbol": str(symbol).strip().upper().removesuffix(".US"),
        "status": "partial" if enough else "insufficient_data",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "event_source": "sec_edgar_submissions_8k",
        "price_source": "longbridge_daily_candle",
        "requested_n": n,
        "sample_count": len(valid_moves),
        "hist_median": median,
        "primary_abs_move_definition": "session_aligned_close_to_close",
        "session_alignment": {
            "AMC": "close_T_to_open_T_plus_1_and_close_T_plus_1; primary=close_to_close",
            "BMO": "close_T_minus_1_to_open_T_and_close_T; primary=close_to_close",
            "intraday_or_unknown": "null_with_data_gap",
        },
        "events": output_events,
        "data_gaps": gaps,
        "cannot_raise_upstream": True,
        "no_order_execution": True,
    }


def longbridge_candle_command(symbol: str, *, count: int) -> list[str]:
    clean = str(symbol).strip().upper().removesuffix(".US")
    if not clean or count < 2 or count > 2000:
        raise ValueError("invalid symbol or candle count")
    return [
        sys.executable, str(SCRIPTS / "longbridge_query.py"),
        "candle", f"{clean}.US", "--period", "day", "--count", str(count), "--json",
    ]


def fetch_longbridge_candles(
    symbol: str,
    *,
    count: int,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    command = longbridge_candle_command(symbol, count=count)
    try:
        proc = runner(command, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], [{"gap": "longbridge_candle_failed", "reason_code": "error", "detail": type(exc).__name__}]
    if proc.returncode != 0:
        return [], [{"gap": "longbridge_candle_failed", "reason_code": "error", "detail": (proc.stderr or "")[-300:]}]
    try:
        payload = json.loads(proc.stdout or "null")
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, list):
        return [], [{"gap": "longbridge_candle_schema_invalid", "reason_code": "schema_mismatch"}]
    return [row for row in payload if isinstance(row, dict)], []


def fetch_sec_8k_events(symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if collect_company_evidence is None:
        return [], [{"gap": "sec_event_adapter_unavailable", "reason_code": "adapter_unavailable"}]
    try:
        evidence = collect_company_evidence(f"{str(symbol).strip().upper().removesuffix('.US')}.US")
    except Exception as exc:
        return [], [{"gap": "sec_event_fetch_failed", "reason_code": "error", "detail": type(exc).__name__}]
    rows = evidence.get("regulatory_evidence") if isinstance(evidence, dict) else None
    if not isinstance(rows, list):
        return [], [{"gap": "sec_event_schema_invalid", "reason_code": "schema_mismatch"}]
    gaps = [
        dict(row) for row in evidence.get("gaps", [])
        if isinstance(row, dict) and "SEC" in str(row.get("gap") or "")
    ]
    return [row for row in rows if isinstance(row, dict)], gaps


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def persist_history(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    output = dict(payload)
    path = (root or cache_root()) / f"{output['symbol']}.json"
    output["cache_path"] = str(path)
    _atomic_json(path, output)
    return output


def run_history(symbol: str, *, n: int = 8, root: Path | None = None) -> dict[str, Any]:
    if n < 1 or n > 40:
        raise ValueError("n must be between 1 and 40")
    events, event_gaps = fetch_sec_8k_events(symbol)
    candles, candle_gaps = fetch_longbridge_candles(symbol, count=min(2000, max(400, n * 100)))
    result = build_history(symbol, events, candles, n=n, extra_gaps=[*event_gaps, *candle_gaps])
    return persist_history(result, root=root)


def self_test() -> None:
    events = [
        {"form_type": "8-K", "filed_at": "2026-04-20T16:05:00-04:00", "source_family": "sec_edgar"},
        {"form_type": "8-K", "filed_at": "2026-05-05T08:00:00-04:00", "source_family": "sec_edgar"},
    ]
    candles = [
        {"time": "2026-04-20", "open": 99, "close": 100},
        {"time": "2026-04-21", "open": 110, "close": 108},
        {"time": "2026-05-04", "open": 201, "close": 200},
        {"time": "2026-05-05", "open": 190, "close": 180},
    ]
    result = build_history("MSFT", events, candles, min_samples=2)
    assert result["sample_count"] == 2
    assert abs(result["hist_median"] - 0.09) < 1e-12
    assert result["no_order_execution"] is True
    with tempfile.TemporaryDirectory() as tmp:
        saved = persist_history(result, root=Path(tmp))
        assert Path(saved["cache_path"]).is_file()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="uppercase US ticker, for example MSFT")
    parser.add_argument("--n", type=int, default=8, help="number of SEC 8-K event candidates")
    parser.add_argument("--json", action="store_true", help="pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed", "no_order_execution": True}))
        return 0
    args = build_parser().parse_args(args_list)
    try:
        result = run_history(args.symbol, n=args.n)
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "ok": False,
            "status": "error",
            "data_gaps": [{"gap": str(exc), "reason_code": "error"}],
            "no_order_execution": True,
        }, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
