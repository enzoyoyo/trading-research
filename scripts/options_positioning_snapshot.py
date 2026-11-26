#!/usr/bin/env python3
"""Reproducible CBOE delayed option-positioning snapshot; research only."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_OCC_RE = re.compile(r"^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
DEFAULT_CACHE_ROOT = (
    Path.home() / ".cache" / "hermes" / "trading-research"
    / "earnings-radar" / "positioning"
)


def cache_root() -> Path:
    return Path(os.environ.get("EARNINGS_RADAR_POSITIONING_DIR", str(DEFAULT_CACHE_ROOT))).expanduser()


def _clear_proxy_env() -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)


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


def _clean_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise ValueError(f"invalid US symbol: {symbol!r}")
    return normalized


def fetch_cboe_payload(
    symbol: str,
    *,
    opener: Any = urllib.request.urlopen,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Fetch once plus one retry; failures remain a structured DataGap."""
    clean = _clean_symbol(symbol)
    _clear_proxy_env()
    last_error = "unknown"
    for _attempt in range(2):
        try:
            request = urllib.request.Request(
                _CBOE_URL.format(symbol=clean),
                headers={"User-Agent": "Mozilla/5.0 trading-research/earnings-radar"},
            )
            response = opener(request, timeout=20)
            try:
                payload = json.load(response)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
            if not isinstance(payload, dict):
                raise ValueError("CBOE response must be a JSON object")
            return payload, {}
        except Exception as exc:  # network and malformed payload both fail closed
            last_error = f"{type(exc).__name__}: {exc}"[:300]
    return None, {
        "gap": "cboe_fetch_failed",
        "reason_code": "error",
        "attempts": 2,
        "timeout_seconds": 20,
        "detail": last_error,
        "impact": "positioning metrics unavailable",
    }


def calculate_snapshot(
    symbol: str,
    payload: dict[str, Any],
    *,
    observed_on: str | None = None,
) -> dict[str, Any]:
    """Calculate unsigned positioning metrics from one frozen CBOE payload."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_options = data.get("options") if isinstance(data.get("options"), list) else []
    spot = _number(data.get("current_price") or data.get("close"))
    today = date.fromisoformat(observed_on) if observed_on else datetime.now(timezone.utc).date()
    rows: list[dict[str, Any]] = []
    for raw in raw_options:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_occ(raw.get("option"))
        if parsed is None or parsed[0] < today.isoformat():
            continue
        expiry, side, strike = parsed
        rows.append({
            "expiry": expiry,
            "side": side,
            "strike": strike,
            "volume": max(_number(raw.get("volume")) or 0.0, 0.0),
            "open_interest": max(_number(raw.get("open_interest")) or 0.0, 0.0),
            "iv": _number(raw.get("iv")),
            "delta": _number(raw.get("delta")),
        })

    call_volume = sum(row["volume"] for row in rows if row["side"] == "C")
    put_volume = sum(row["volume"] for row in rows if row["side"] == "P")
    ratio = put_volume / call_volume if call_volume > 0 else None
    nearest_expiry = min((row["expiry"] for row in rows), default=None)
    near_rows = [row for row in rows if row["expiry"] == nearest_expiry]

    atm_strike: float | None = None
    atm_iv: float | None = None
    if spot is not None and spot > 0 and near_rows:
        strikes_with_both = {
            row["strike"] for row in near_rows
            if {item["side"] for item in near_rows if item["strike"] == row["strike"]} == {"C", "P"}
        }
        if strikes_with_both:
            atm_strike = min(strikes_with_both, key=lambda strike: (abs(strike - spot), strike))
            ivs = [
                row["iv"] for row in near_rows
                if row["strike"] == atm_strike and row["iv"] is not None and row["iv"] > 0
            ]
            if len(ivs) == 2:
                atm_iv = sum(ivs) / 2.0

    def nearest_delta(side: str, target: float) -> float | None:
        candidates = [
            row for row in near_rows
            if row["side"] == side and row["delta"] is not None and row["iv"] is not None and row["iv"] > 0
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda row: (abs(row["delta"] - target), row["strike"]))["iv"]

    put_25_iv = nearest_delta("P", -0.25)
    call_25_iv = nearest_delta("C", 0.25)
    skew = round((put_25_iv - call_25_iv) * 100.0, 8) if put_25_iv is not None and call_25_iv is not None else None
    gaps = [{
        "gap": "signed_flow_unavailable_no_open_close_fields",
        "reason_code": "unsupported",
        "permanent": True,
        "impact": "signed flow has zero directional weight",
    }]
    if spot is None or spot <= 0:
        gaps.append({"gap": "spot_missing_or_invalid", "reason_code": "missing"})
    if not rows:
        gaps.append({"gap": "option_chain_missing_or_invalid", "reason_code": "missing"})
    if ratio is None:
        gaps.append({"gap": "call_volume_zero", "reason_code": "insufficient"})
    if atm_iv is None:
        gaps.append({"gap": "atm_iv_inputs_missing", "reason_code": "missing"})
    if skew is None:
        gaps.append({"gap": "iv_skew_inputs_missing", "reason_code": "missing"})
    complete = bool(rows and spot is not None and spot > 0 and ratio is not None and atm_iv is not None and skew is not None)
    return {
        "schema_version": "options_positioning_snapshot.v1",
        "symbol": symbol.strip().upper(),
        "status": "ok" if complete else "insufficient_data",
        "as_of": str(payload.get("timestamp") or data.get("timestamp") or datetime.now(timezone.utc).isoformat()),
        "source": "cboe_delayed",
        "spot": spot,
        "put_call_volume_ratio": ratio,
        "atm_iv": atm_iv,
        "atm_iv_formula": "mean(nearest_expiry_ATM_call_iv, nearest_expiry_ATM_put_iv)",
        "atm_expiry": nearest_expiry,
        "atm_strike": atm_strike,
        "iv_skew_pp": skew,
        "iv_skew_formula": "nearest_expiry_25delta_put_iv_minus_25delta_call_iv_times_100",
        "oi_total": int(sum(row["open_interest"] for row in rows)),
        "volume_total": int(sum(row["volume"] for row in rows)),
        "signed_flow_direction_weight": 0.0,
        "data_gaps": gaps,
        "cannot_raise_upstream": True,
        "no_order_execution": True,
    }


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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def persist_snapshot(
    snapshot: dict[str, Any],
    *,
    root: Path | None = None,
    snapshot_date: date | None = None,
) -> dict[str, Any]:
    clean = _clean_symbol(str(snapshot.get("symbol") or ""))
    target_date = snapshot_date or datetime.now(timezone.utc).date()
    target_root = root or cache_root()
    symbol_dir = target_root / clean
    output = dict(snapshot)
    output["data_gaps"] = [dict(row) for row in snapshot.get("data_gaps", []) if isinstance(row, dict)]
    output["oi_delta"] = None
    output["oi_delta_previous_snapshot_date"] = None
    output["oi_delta_definition"] = "current_oi_total_minus_most_recent_prior_valid_daily_snapshot"

    previous: tuple[str, float] | None = None
    if symbol_dir.exists():
        for path in sorted(symbol_dir.glob("*.json"), reverse=True):
            if path.stem >= target_date.isoformat():
                continue
            payload = _read_json(path)
            prior_oi = _number(payload.get("oi_total")) if payload else None
            if prior_oi is not None and prior_oi >= 0 and payload.get("status") != "insufficient_data":
                previous = (path.stem, prior_oi)
                break
    current_oi = _number(output.get("oi_total"))
    if previous is not None and current_oi is not None and current_oi >= 0:
        delta = current_oi - previous[1]
        output["oi_delta"] = int(delta) if delta.is_integer() else delta
        output["oi_delta_previous_snapshot_date"] = previous[0]
    else:
        output["data_gaps"].append({
            "gap": "oi_history_insufficient",
            "reason_code": "missing",
            "impact": "oi_delta unavailable until a prior valid daily snapshot exists",
        })
    path = symbol_dir / f"{target_date.isoformat()}.json"
    output["cache_path"] = str(path)
    _atomic_json(path, output)
    return output


def snapshot_symbol(
    symbol: str,
    *,
    root: Path | None = None,
    snapshot_date: date | None = None,
) -> dict[str, Any]:
    clean = _clean_symbol(symbol)
    payload, fetch_gap = fetch_cboe_payload(clean)
    if payload is None:
        observed = snapshot_date or datetime.now(timezone.utc).date()
        result = {
            "schema_version": "options_positioning_snapshot.v1",
            "symbol": clean,
            "status": "insufficient_data",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "source": "cboe_delayed",
            "spot": None,
            "put_call_volume_ratio": None,
            "atm_iv": None,
            "atm_expiry": None,
            "atm_strike": None,
            "iv_skew_pp": None,
            "oi_total": None,
            "volume_total": None,
            "signed_flow_direction_weight": 0.0,
            "data_gaps": [fetch_gap, {
                "gap": "signed_flow_unavailable_no_open_close_fields",
                "reason_code": "unsupported",
                "permanent": True,
                "impact": "signed flow has zero directional weight",
            }],
            "cannot_raise_upstream": True,
            "no_order_execution": True,
        }
        return persist_snapshot(result, root=root, snapshot_date=observed)
    observed = snapshot_date or datetime.now(timezone.utc).date()
    result = calculate_snapshot(clean, payload, observed_on=observed.isoformat())
    return persist_snapshot(result, root=root, snapshot_date=observed)


def self_test() -> None:
    payload = {
        "timestamp": "2026-08-21T20:00:00Z",
        "data": {"current_price": 100, "options": [
            {"option": "MSFT260821C00100000", "volume": 10, "open_interest": 20, "iv": 0.20, "delta": 0.25},
            {"option": "MSFT260821P00100000", "volume": 5, "open_interest": 30, "iv": 0.25, "delta": -0.25},
        ]},
    }
    calculated = calculate_snapshot("MSFT", payload, observed_on="2026-08-21")
    assert calculated["put_call_volume_ratio"] == 0.5
    assert calculated["atm_iv"] == 0.225
    assert calculated["iv_skew_pp"] == 5.0
    assert calculated["signed_flow_direction_weight"] == 0.0
    with tempfile.TemporaryDirectory() as tmp:
        saved = persist_snapshot(calculated, root=Path(tmp), snapshot_date=date(2026, 8, 21))
        assert Path(saved["cache_path"]).is_file()
        assert saved["oi_delta"] is None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot", help="write one CBOE delayed positioning snapshot")
    snapshot.add_argument("--symbol", required=True, help="uppercase US ticker, for example MSFT")
    snapshot.add_argument("--json", action="store_true", help="pretty-print JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed", "no_order_execution": True}))
        return 0
    args = build_parser().parse_args(args_list)
    try:
        result = snapshot_symbol(args.symbol)
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
