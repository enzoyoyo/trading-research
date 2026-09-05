#!/usr/bin/env python3
"""Read-only HiThink Financial-API bridge; no credentials on disk or order APIs.

Contract: references/financial-api-data-source.md. Synthetic tests are separate.
Only FINANCIAL_API_KEY is read; the official HTTPS origin and GET paths are fixed.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_URL = "https://fuyao.aicubes.cn"
UPSTREAM = "https://github.com/HiThink-Tech/Financial-API"
CONTRACT_COMMIT = "765513c2616030803ad80915ed65b205f425a942"
MAX_BYTES = 5 * 1024 * 1024
SYMBOL_RE = re.compile(r"\d{6}\.(SH|SZ|BJ)\Z")
PATHS = {
    "search": "/api/meta/tickers/search",
    "quote": "/api/a-share/prices/snapshot",
    "history": "/api/a-share/prices/historical",
    "valuation": "/api/a-share/valuations/snapshot",
    "income": "/api/a-share/financials/income-statements",
    "balance": "/api/a-share/financials/balance-sheets",
    "cashflow": "/api/a-share/financials/cash-flow-statements",
    "calendar": "/api/a-share/calendar/trading-days",
}
DEFAULT_MAX_AGE = {"quote": 300, "valuation": 86400, "history": 345600,
                   "search": 86400, "calendar": 86400,
                   "income": 17280000, "balance": 17280000, "cashflow": 17280000}
ERROR_STATES = {2001: "auth_missing", 2003: "auth_invalid", 3001: "missing",
                3002: "not_ready", 3004: "unsupported", 4001: "rate_limited"}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def as_datetime(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 946684800000 or value > 4102444800000:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def full_symbol(value: str) -> str:
    value = str(value).strip().upper()
    if not SYMBOL_RE.fullmatch(value):
        raise ValueError("explicit_A_share_thscode_required_use_search_for_names_or_bare_codes")
    return value


def integer(value: Any, low: int, high: int, field: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"invalid_{field}")
    return value


def request_spec(command: str, options: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if command not in PATHS:
        raise ValueError("unsupported_read_only_command")
    allowed = {
        "search": {"query", "limit"}, "quote": {"symbols"}, "valuation": {"symbols"},
        "history": {"symbol", "start", "end", "adjust", "offset", "interval"},
        "income": {"symbol", "period", "limit"}, "balance": {"symbol", "period", "limit"},
        "cashflow": {"symbol", "period", "limit"}, "calendar": set(),
    }[command]
    if set(options) - allowed:
        raise ValueError("unsupported_parameter")
    if command == "search":
        query = str(options.get("query", "")).strip()
        if not query or len(query) > 80 or not re.fullmatch(r"[\w .\-\u3400-\u9fff]+", query):
            raise ValueError("public_symbol_search_query_required")
        if query.lower().startswith(("sk-", "bearer ")):
            raise ValueError("credential_like_search_query_rejected")
        params = {"q": query, "asset_type": "a-share", "limit": integer(options.get("limit", 5), 1, 50, "limit")}
    elif command in {"quote", "valuation"}:
        symbols = options.get("symbols")
        if not isinstance(symbols, (list, tuple)) or not 1 <= len(symbols) <= 100:
            raise ValueError("one_to_100_explicit_symbols_required")
        params = {"thscodes": ",".join(dict.fromkeys(full_symbol(x) for x in symbols))}
    elif command == "history":
        start = integer(options.get("start"), 946684800000, 4102444800000, "start_ms")
        end = integer(options.get("end"), 946684800000, 4102444800000, "end_ms")
        if start >= end or end - start > 3652 * 86400000:
            raise ValueError("history_window_must_be_positive_and_at_most_10_years")
        if options.get("interval", "1d") != "1d":
            raise ValueError("only_daily_1d_history_supported")
        adjust = options.get("adjust", "none")
        if adjust not in {"none", "forward", "backward"}:
            raise ValueError("invalid_adjust")
        params = {"thscode": full_symbol(options.get("symbol", "")), "interval": "1d",
                  "start": start, "end": end, "adjust": adjust,
                  "offset": integer(options.get("offset", 0), 0, 1000000, "offset")}
    elif command in {"income", "balance", "cashflow"}:
        period = options.get("period", "quarterly")
        if period not in {"quarterly", "annual"}:
            raise ValueError("invalid_report_period")
        params = {"thscode": full_symbol(options.get("symbol", "")), "period": period,
                  "limit": integer(options.get("limit", 4), 1, 20, "limit")}
    else:
        params = {}
    return PATHS[command], params


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the API header to a redirect target, including same-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _gap(name: str, severity: str = "high") -> dict[str, str]:
    return {"gap": name, "severity": severity,
            "impact": "Preserve the gap; do not substitute zero, current data, or an action signal."}


def _redact(value: Any, key: str) -> Any:
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, list):
        return [_redact(x, key) for x in value]
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    return value


def _finite_number(value: Any) -> float | int | None:
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _reject_constant(value: str) -> None:
    raise ValueError("non_finite_json")


def _check_finite(value: Any) -> None:
    # JSON exponent overflow (1e999) bypasses parse_constant in Python's decoder.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non_finite_json_number")
    if isinstance(value, dict):
        for child in value.values():
            _check_finite(child)
    elif isinstance(value, list):
        for child in value:
            _check_finite(child)


def fetch(command: str, *, options: dict[str, Any] | None = None,
          timeout: int = 20, max_age_seconds: int | None = None,
          opener: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """One bounded GET. ok means payload available, never permission to trade.

    No automatic retry: orchestration owns the shared two-failure source budget.
    opener/now are dependency injection for offline tests, not CLI configuration.
    """
    now = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "schema_version": "financial_api_snapshot.v1", "command": command, "market": "A",
        "provider": "financial_api", "provider_family": "ths_financial_api",
        "source_family": "tonghuashun_aggregated_data", "source": "HiThink Financial-API",
        "observed_at": iso(now), "as_of": None, "ok": False, "status": "error",
        "items": [], "data": None, "data_gaps": [], "request_id": None,
        "no_order_execution": True, "suggested_module_signals": [],
        "may_write_formal_conclusion": False,
        "source_meta": {"upstream": UPSTREAM, "contract_commit": CONTRACT_COMMIT,
                        "endpoint_origin": BASE_URL, "license": "MIT"},
    }
    try:
        path, params = request_spec(command, options or {})
        timeout = integer(timeout, 1, 60, "timeout")
        max_age_seconds = integer(DEFAULT_MAX_AGE[command] if max_age_seconds is None else max_age_seconds,
                                  1, 31536000, "max_age_seconds")
    except (TypeError, ValueError):
        result.update(status="invalid_request", data_gaps=[_gap("financial_api_invalid_request")])
        return result
    url = BASE_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
    result["source_url"] = url
    result["request_parameters"] = params
    key = os.environ.get("FINANCIAL_API_KEY", "").strip()
    if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
        result.update(status="auth_missing", data_gaps=[_gap("financial_api_auth_missing")])
        return result
    req = urllib.request.Request(url, method="GET", headers={"X-api-key": key,
        "Accept": "application/json", "User-Agent": "trading-research-financial-api/1"})
    client = opener if opener is not None else urllib.request.build_opener(NoRedirect())
    try:
        with client.open(req, timeout=timeout) as response:
            http_status = response.status
            raw = response.read(MAX_BYTES + 1)
        result["http_status"] = http_status
        if http_status != 200:
            raise ValueError("unexpected_http_status")
        if len(raw) > MAX_BYTES:
            result.update(status="oversized", data_gaps=[_gap("financial_api_response_too_large")])
            return result
        result["response_sha256"] = hashlib.sha256(raw).hexdigest()
        result["response_bytes"] = len(raw)
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        _check_finite(payload)
        if not isinstance(payload, dict) or type(payload.get("code")) is not int or "data" not in payload:
            raise ValueError("invalid_response_envelope")
        result["request_id"] = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
        code = payload["code"]
        result["business_code"] = code
        if code != 0:
            state = ERROR_STATES.get(code, "invalid_request" if 1000 <= code < 2000 else "error")
            result.update(status=state, data_gaps=[_gap("financial_api_" + state)])
            return _redact(result, key)
        data = payload["data"]
        if not isinstance(data, dict) or not isinstance(data.get("item"), list):
            raise ValueError("invalid_data_contract")
        if any(not isinstance(row, dict) for row in data["item"]):
            raise ValueError("invalid_item_contract")
        result["data"] = data
        result["items"] = data["item"]
        _annotate(result, command, params, max_age_seconds, now)
        return _redact(result, key)
    except urllib.error.HTTPError as exc:
        code = exc.code
        state = "auth_missing" if code == 401 else "auth_invalid" if code == 403 else (
            "rate_limited" if code == 429 else "redirect_blocked" if 300 <= code < 400 else "error")
        result.update(status=state, http_status=code, data_gaps=[_gap("financial_api_" + state)])
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError, http.client.HTTPException):
        result.update(status="network_error", data_gaps=[_gap("financial_api_network_error")])
    except (ValueError, TypeError, UnicodeError, OverflowError):
        result.update(status="schema_error", items=[], data=None,
                      data_gaps=[_gap("financial_api_response_contract_invalid")])
    return _redact(result, key)


def _annotate(result: dict[str, Any], command: str, params: dict[str, Any],
              max_age_seconds: int, now: datetime) -> None:
    data = result["data"]
    items = data["item"]
    gaps = result["data_gaps"]
    source_time = as_datetime(data.get("timestamp"))
    as_of = iso(source_time) if source_time else None
    age = (now.astimezone(timezone.utc) - source_time).total_seconds() if source_time else None
    state = "missing" if age is None else "future" if age < -60 else "stale" if age > max_age_seconds else "fresh"
    # A dataset timestamp never proves every row/field was updated at that time.
    result["as_of"] = as_of
    result["freshness"] = {"status": state, "age_seconds": age,
        "max_age_seconds": max_age_seconds, "timestamp_basis": "provider_dataset_timestamp",
        "per_item_timestamp_verified": False, "observed_at_is_market_time": False}
    if state != "fresh":
        gaps.append(_gap("financial_api_source_timestamp_" + state))
    if not items:
        gaps.append(_gap("financial_api_empty_result"))
    if command in {"quote", "valuation"}:
        requested = params["thscodes"].split(",")
        returned = [row.get("thscode") for row in items]
        result["coverage"] = {"requested_symbols": requested,
            "missing_symbols": [x for x in requested if x not in returned],
            "unexpected_symbols": [x for x in returned if x not in requested]}
        if result["coverage"]["missing_symbols"]:
            gaps.append(_gap("financial_api_symbols_missing"))
        if result["coverage"]["unexpected_symbols"] or len(set(returned)) != len(returned):
            raise ValueError("unexpected_or_duplicate_symbol")
    if command == "quote":
        fields = {"price": "last_price", "prev_close": "prev_price", "open": "open_price",
                  "high": "high_price", "low": "low_price", "change_amt": "price_change",
                  "change_pct": "price_change_ratio_pct", "volume": "volume", "turnover": "turnover"}
        result["items"] = []
        for row in items:
            symbol = full_symbol(row.get("thscode", ""))
            normalized = {target: _finite_number(row.get(source)) for target, source in fields.items()}
            normalized.update(canonical_symbol=symbol, code=symbol[:6], exchange=symbol[7:],
                              market="A", as_of=None, currency="CNY")
            result["items"].append(normalized)
            if normalized["price"] is None or normalized["price"] <= 0:
                gaps.append(_gap("financial_api_price_missing_or_invalid"))
        gaps.append(_gap("financial_api_quote_row_timestamp_unavailable"))
        result["field_map"] = fields
        result["units"] = {"volume": "shares", "turnover": "CNY", "change_pct": "percentage_points"}
    if command == "valuation":
        gaps.append(_gap("financial_api_valuation_field_timestamps_unavailable", "medium"))
    if command in {"income", "balance", "cashflow"}:
        for row in items:
            if row.get("thscode") != params["thscode"]:
                raise ValueError("financial_statement_symbol_mismatch")
            report_time = as_datetime(row.get("report_date_ms"))
            if report_time is None or report_time > now.astimezone(timezone.utc):
                gaps.append(_gap("financial_api_disclosure_date_missing_or_future"))
        gaps.append(_gap("financial_api_point_in_time_revision_history_unverified", "medium"))
    if command == "history":
        result["coverage"] = {"status": "page_only", "complete_series_verified": False,
                              "offset": params["offset"], "adjust": params["adjust"]}
        dates = [row.get("date_ms") for row in items]
        if any(as_datetime(x) is None or not params["start"] <= x <= params["end"] for x in dates):
            raise ValueError("bar_dates_outside_requested_window")
        if len(set(dates)) != len(dates):
            raise ValueError("duplicate_bar_dates")
        gaps.append(_gap("financial_api_history_full_coverage_unverified", "medium"))
    result["ok"] = bool(items)
    result["status"] = "partial" if gaps and items else "ok" if items else "missing"


def date_ms(value: str, *, end: bool = False) -> int:
    # Calendar bounds are Shanghai dates, not machine-local dates or UTC dates.
    from zoneinfo import ZoneInfo
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return int(parsed.timestamp() * 1000) + (86399999 if end else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Save JSON; print only a summary")
    parser.add_argument("--timeout", type=int, default=20)
    subs = parser.add_subparsers(dest="command", required=True)
    search = subs.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    for command in ("quote", "valuation"):
        sub = subs.add_parser(command)
        sub.add_argument("symbols", nargs="+")
    history = subs.add_parser("history")
    history.add_argument("symbol")
    history.add_argument("--start", required=True, help="Shanghai date YYYY-MM-DD")
    history.add_argument("--end", required=True, help="Shanghai date YYYY-MM-DD")
    history.add_argument("--adjust", choices=("none", "forward", "backward"), default="none")
    history.add_argument("--offset", type=int, default=0)
    for command in ("income", "balance", "cashflow"):
        sub = subs.add_parser(command)
        sub.add_argument("symbol")
        sub.add_argument("--period", choices=("annual", "quarterly"), default="quarterly")
        sub.add_argument("--limit", type=int, default=4)
    subs.add_parser("calendar")
    args = vars(parser.parse_args(argv))
    out = args.pop("out")
    timeout = args.pop("timeout")
    command = args.pop("command")
    if command == "history":
        try:
            args["start"] = date_ms(args["start"])
            args["end"] = date_ms(args["end"], end=True)
        except ValueError:
            parser.error("start/end must be real dates in YYYY-MM-DD format")
    payload = fetch(command, options=args, timeout=timeout)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"ok": payload["ok"], "status": payload["status"], "path": str(out.resolve()),
                          "rows": len(payload["items"]), "as_of": payload["as_of"],
                          "gaps": [x["gap"] for x in payload["data_gaps"]]}, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
