#!/usr/bin/env python3
"""ATM-straddle implied earnings move from a frozen CBOE delayed chain."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from options_positioning_snapshot import fetch_cboe_payload

_OCC_RE = re.compile(r"^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$")
DEFAULT_MOVE_ROOT = (
    Path.home() / ".cache" / "hermes" / "trading-research"
    / "earnings-radar" / "moves"
)
# Legacy median adaptation; these cutoffs are not validated for this statistic.
RICH_THRESHOLD = 1.25
CHEAP_THRESHOLD = 0.95


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_occ(value: Any) -> tuple[str, str, float] | None:
    match = _OCC_RE.match(str(value or ""))
    if not match:
        return None
    _, ymd, side, strike_raw = match.groups()
    try:
        expiry = datetime.strptime(ymd, "%y%m%d").date().isoformat()
    except ValueError:
        return None
    return expiry, side, int(strike_raw) / 1000.0


def _mid(bid_value: Any, ask_value: Any) -> float | None:
    bid = _number(bid_value)
    ask = _number(ask_value)
    if bid is None or ask is None or bid < 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return mid if mid > 0 else None


def _clock(value: Any, now: datetime) -> tuple[str | None, str]:
    if value is None or value == "":
        return None, "missing"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None, "invalid_clock"
        if parsed > now:
            return None, "future_clock"
        return parsed.isoformat(), "reported_not_verified"
    except (ValueError, TypeError, OverflowError):
        return None, "invalid_clock"


def _history_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    events = [row for row in payload.get("events", []) if isinstance(row, dict)
              and _number(row.get("abs_move")) is not None]
    return {
        "sample_count": payload.get("sample_count"),
        "window_dates": payload.get("window_dates") or ([{
            "event_date": row.get("event_date"), "reaction_date": row.get("reaction_date")
        } for row in events] or None),
        "history_as_of": payload.get("history_as_of"),
        # build_history v1 as_of is computation time, never a market-data clock.
        "computed_at": payload.get("computed_at") or payload.get("as_of"),
    }


def resolve_hist_median(
    symbol: str,
    provided: float | None,
    *,
    move_root: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[float | None, str, list[dict[str, Any]]]:
    if metadata is not None:
        metadata.clear()
    value = _number(provided)
    if provided is not None:
        if value is not None and value > 0:
            return value, "caller_provided", []
        return None, "caller_provided_invalid", [{
            "gap": "hist_median_invalid",
            "reason_code": "invalid",
        }]
    path = (move_root or DEFAULT_MOVE_ROOT) / f"{str(symbol).strip().upper().removesuffix('.US')}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    cached = _number(payload.get("hist_median")) if isinstance(payload, dict) else None
    samples = payload.get("sample_count") if isinstance(payload, dict) else None
    if cached is not None and cached > 0 and isinstance(samples, int) and not isinstance(samples, bool) and samples >= 4:
        if metadata is not None:
            metadata.update(_history_metadata(payload))
        return cached, "earnings_move_history_cache", []
    return None, "unavailable", [{
        "gap": "historical_earnings_median_unavailable",
        "reason_code": "missing",
        "impact": "implied_vs_typical_ratio and label are null",
    }]


def calculate_distribution(
    symbol: str,
    payload: dict[str, Any],
    *,
    expiry: str | None = None,
    hist_median: float | None = None,
    hist_median_source: str = "caller_provided",
    observed_on: str | None = None,
    hist_metadata: dict[str, Any] | None = None,
    latest_known_earnings_date: str | None = None,
    extra_gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    computed_at = datetime.now(timezone.utc)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    options = data.get("options") if isinstance(data.get("options"), list) else []
    spot = _number(data.get("current_price") or data.get("close"))
    today = date.fromisoformat(observed_on) if observed_on else datetime.now(timezone.utc).date()
    rows: list[dict[str, Any]] = []
    for raw in options:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_occ(raw.get("option"))
        if parsed is None or parsed[0] < today.isoformat():
            continue
        parsed_expiry, side, strike = parsed
        rows.append({
            "expiry": parsed_expiry,
            "side": side,
            "strike": strike,
            "mid": _mid(raw.get("bid"), raw.get("ask")),
        })

    gaps = [dict(row) for row in (extra_gaps or [])]
    quote_source = "payload.timestamp" if payload.get("timestamp") is not None else "data.timestamp"
    quote_as_of, quote_status = _clock(payload.get("timestamp") if payload.get("timestamp") is not None
                                       else data.get("timestamp"), computed_at)
    if quote_status != "reported_not_verified":
        gaps.append({"gap": "quote_clock_missing" if quote_status == "missing" else "quote_clock_invalid",
                     "reason_code": quote_status})
    meta = hist_metadata or {}
    history_as_of, history_status = _clock(meta.get("history_as_of"), computed_at)
    history_computed_at, computation_status = _clock(meta.get("computed_at"), computed_at)
    history_invalid = any(state in ("invalid_clock", "future_clock")
                          for state in (history_status, computation_status))
    for field, state in (("history_as_of", history_status), ("computed_at", computation_status)):
        if state != "reported_not_verified":
            gaps.append({"gap": "historical_" + field + "_" + ("missing" if state == "missing" else "invalid"),
                         "reason_code": state})
    windows = meta.get("window_dates")
    if windows is not None:
        try:
            if not isinstance(windows, list):
                raise ValueError("invalid window")
            for row in windows:
                if not isinstance(row, dict):
                    raise ValueError("invalid window row")
                for field in ("event_date", "reaction_date"):
                    if row.get(field) is not None and date.fromisoformat(row[field]) > computed_at.date():
                        raise ValueError("future window")
        except (ValueError, TypeError):
            history_invalid = True
            gaps.append({"gap": "historical_window_dates_invalid", "reason_code": "invalid_clock"})
    latest_status = "unknown_latest_earnings_date"
    if latest_known_earnings_date is not None:
        try:
            latest = date.fromisoformat(latest_known_earnings_date)
            if latest > computed_at.date():
                raise ValueError("future event")
            dates = [date.fromisoformat(row["event_date"]) for row in (windows or [])
                     if isinstance(row, dict) and row.get("event_date")]
            if any(day > computed_at.date() for day in dates):
                raise ValueError("future history")
            latest_status = "reported_not_verified" if dates and max(dates) >= latest else "unknown_history_window"
            if dates and max(dates) < latest:
                latest_status = "hist_median_excludes_latest_print"
                history_invalid = True
                gaps.append({"gap": latest_status, "reason_code": "missing"})
        except (ValueError, TypeError):
            latest_status = "invalid_event_date"
            history_invalid = True
            gaps.append({"gap": latest_status, "reason_code": "invalid_clock"})
    available_expiries = sorted({row["expiry"] for row in rows})
    if expiry is not None:
        try:
            date.fromisoformat(expiry)
        except ValueError:
            raise ValueError("expiry must use YYYY-MM-DD")
        selected_expiry = expiry if expiry in available_expiries else None
        expiry_selection = "explicit"
        if selected_expiry is None:
            gaps.append({"gap": "requested_expiry_unavailable", "reason_code": "missing"})
    else:
        selected_expiry = available_expiries[0] if available_expiries else None
        expiry_selection = "nearest_available_not_earnings_verified"
        gaps.append({
            "gap": "earnings_release_date_unavailable_expiry_not_verified",
            "reason_code": "method_limit",
            "impact": "nearest expiry is not asserted to be first expiry after earnings",
        })

    selected_rows = [row for row in rows if row["expiry"] == selected_expiry]
    atm_strike: float | None = None
    call_mid: float | None = None
    put_mid: float | None = None
    if spot is None or spot <= 0:
        gaps.append({"gap": "spot_missing_or_invalid", "reason_code": "missing"})
    elif selected_rows:
        paired_strikes = {
            row["strike"] for row in selected_rows
            if {item["side"] for item in selected_rows if item["strike"] == row["strike"]} == {"C", "P"}
        }
        if paired_strikes:
            atm_strike = min(paired_strikes, key=lambda strike: (abs(strike - spot), strike))
            call_mid = next((row["mid"] for row in selected_rows if row["strike"] == atm_strike and row["side"] == "C"), None)
            put_mid = next((row["mid"] for row in selected_rows if row["strike"] == atm_strike and row["side"] == "P"), None)
    if selected_expiry is None:
        gaps.append({"gap": "option_expiry_unavailable", "reason_code": "missing"})
    if atm_strike is None or call_mid is None or put_mid is None:
        gaps.append({
            "gap": "atm_call_put_mid_unavailable",
            "reason_code": "missing",
            "impact": "implied move and all dependent comparisons are null",
        })

    implied_move = None
    if spot is not None and spot > 0 and call_mid is not None and put_mid is not None:
        implied_move = (call_mid + put_mid) / spot
    typical = None if history_invalid else _number(hist_median)
    ratio = implied_move / typical if implied_move is not None and typical is not None and typical > 0 else None
    label = None
    if ratio is not None:
        label = "RICH" if ratio >= RICH_THRESHOLD else ("CHEAP" if ratio <= CHEAP_THRESHOLD else "FAIR")
    elif not any(row.get("gap") == "historical_earnings_median_unavailable" for row in gaps):
        gaps.append({
            "gap": "historical_earnings_median_unavailable",
            "reason_code": "missing",
            "impact": "implied_vs_typical_ratio and label are null",
        })

    if ratio is not None:
        gaps.extend([
            {"gap": "median_ratio_thresholds_not_validated", "reason_code": "method_limit",
             "impact": "RICH/CHEAP/FAIR are independent heuristic labels, not the documented mean-last-nine method"},
            {"gap": "historical_median_sample_window_and_cutoff_not_verified", "reason_code": "method_limit",
             "impact": "reported metadata is unaudited; missing fields remain null"},
        ])
    critical_missing = implied_move is None
    status = "insufficient_data" if critical_missing else ("partial" if gaps else "ok")
    return {
        "schema_version": "earnings_implied_distribution.v1",
        "symbol": str(symbol).strip().upper().removesuffix(".US"),
        "status": status,
        "as_of": quote_as_of,
        "computed_at": computed_at.isoformat(),
        "source_freshness": {
            "status": "unknown_no_age_policy",
            "clocks": {
                "quote": {"as_of": quote_as_of, "source": quote_source if quote_as_of else None,
                          "status": quote_status},
                "history": {"as_of": history_as_of, "source": hist_median_source + ".history_as_of",
                            "status": history_status},
                "history_computation": {"as_of": history_computed_at,
                                        "source": hist_median_source + ".computed_at_or_legacy_as_of",
                                        "status": computation_status},
            },
            "latest_print_check": latest_status,
        },
        "source": "cboe_delayed",
        "spot": spot,
        "expiry": selected_expiry,
        "expiry_selection": expiry_selection,
        "atm_strike": atm_strike,
        "atm_call_mid": call_mid,
        "atm_put_mid": put_mid,
        "implied_move_premium_pct": implied_move,
        "implied_move_unit": "decimal_fraction_of_spot",
        "implied_move_formula": "(ATM_call_mid + ATM_put_mid) / spot",
        "hist_median": typical,
        "hist_median_source": hist_median_source,
        "implied_vs_typical_ratio": ratio,
        "relative_value_label": label,
        "relative_value_thresholds": {"RICH_gte": RICH_THRESHOLD, "CHEAP_lte": CHEAP_THRESHOLD},
        "method_source": "independent_unvalidated_median_adaptation" if ratio is not None else None,
        "comparison_contract": {
            "denominator_statistic": "median_absolute_session_aligned_reaction",
            "documented_author_statistic": "mean_absolute_reaction_last_9_prints",
            "author_formula_reproduced": False,
            "threshold_validation": "unvalidated_for_median",
            "label_authority": "descriptive_heuristic_only",
            "sample_count": meta.get("sample_count"), "sample_window": windows,
            "window_dates": windows, "history_as_of": history_as_of,
            "historical_data_as_of": history_as_of,
            "sample_metadata_status": "reported_not_verified" if meta else "not_verified_by_scalar_median_interface",
        },
        "risk_neutral": True,
        "risk_neutral_density_computed": False,
        "is_direction_prediction": False,
        "position_multiplier": 0.0,
        "cannot_raise_upstream": True,
        "data_gaps": gaps,
        "no_order_execution": True,
    }


def run_distribution(
    symbol: str,
    *,
    expiry: str | None = None,
    hist_median: float | None = None,
    hist_metadata: dict[str, Any] | None = None,
    latest_known_earnings_date: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    typical, typical_source, typical_gaps = resolve_hist_median(symbol, hist_median, metadata=metadata)
    if hist_metadata is not None:
        metadata = dict(hist_metadata)
    payload, fetch_gap = fetch_cboe_payload(symbol)
    if payload is None:
        payload = {}
        if fetch_gap:
            typical_gaps.append(fetch_gap)
    return calculate_distribution(
        symbol,
        payload,
        expiry=expiry,
        hist_median=typical,
        hist_median_source=typical_source,
        extra_gaps=typical_gaps,
        hist_metadata=metadata,
        latest_known_earnings_date=latest_known_earnings_date,
    )


def self_test() -> None:
    payload = {"data": {"current_price": 100, "options": [
        {"option": "MSFT260821C00100000", "bid": 4, "ask": 6},
        {"option": "MSFT260821P00100000", "bid": 3, "ask": 5},
    ]}}
    result = calculate_distribution(
        "MSFT", payload, expiry="2026-08-21", hist_median=0.06, observed_on="2026-08-21"
    )
    assert result["implied_move_premium_pct"] == 0.09
    assert result["relative_value_label"] == "RICH"
    assert result["position_multiplier"] == 0.0
    assert result["no_order_execution"] is True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="uppercase US ticker, for example MSFT")
    parser.add_argument("--expiry", help="explicit option expiry YYYY-MM-DD")
    parser.add_argument("--hist-median", type=float, help="caller-provided session-aligned historical median")
    parser.add_argument("--latest-known-earnings-date", help="explicit known completed earnings date YYYY-MM-DD")
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
        result = run_distribution(args.symbol, expiry=args.expiry, hist_median=args.hist_median,
                                  latest_known_earnings_date=args.latest_known_earnings_date)
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
