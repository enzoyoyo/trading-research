#!/usr/bin/env python3
"""Read-only OKX public-market adapter for Unified Tokenized Stocks.

Only public API V5 endpoints are used. The adapter never reads credentials and
never calls account, order, transfer, bot, or trading endpoints.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

SITE_BASE_URLS = {
    "global": "https://www.okx.com",
    "eea": "https://eea.okx.com",
    "us": "https://app.okx.com",
    "tr": "https://tr.okx.com",
}
INST_ID_RE = re.compile(r"^X[A-Z0-9]+-USDT$")
MAX_PUBLIC_AGE_SECONDS = 120.0
MAX_FUTURE_SKEW_SECONDS = 5.0


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid numeric field: {label}")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric field: {label}") from exc
    if not math.isfinite(number):
        raise ValueError(f"invalid numeric field: {label}")
    return number


def _derived_number(value: float, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"invalid derived numeric field: {label}")
    return value


def _round_derived(value: float, label: str, digits: int) -> float:
    value = _derived_number(value, label)
    rounded = _derived_number(round(value, digits), label)
    return value if value > 0 and rounded == 0 else rounded


def _timestamp_ms(value: Any, label: str) -> datetime:
    milliseconds = _number(value, label)
    try:
        return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"invalid timestamp field: {label}") from exc


def _data(response: dict[str, Any], label: str) -> list[Any]:
    if not isinstance(response, dict):
        raise ValueError(f"{label} response must be an object")
    code = str(response.get("code") or "")
    if code != "0":
        message = str(response.get("msg") or "")
        raise ValueError(f"OKX {label} error {code}: {message}")
    rows = response.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"OKX {label} data must be an array")
    return rows


def _single(response: dict[str, Any], label: str) -> dict[str, Any]:
    rows = _data(response, label)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError(f"OKX {label} must contain exactly one row")
    return rows[0]


def _book_side(rows: Any, label: str) -> tuple[list[dict[str, float]], float]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"OKX order book {label} is missing")
    normalized: list[dict[str, float]] = []
    total = 0.0
    previous_price: float | None = None
    for index, row in enumerate(rows[:5]):
        if not isinstance(row, list) or len(row) < 2:
            raise ValueError(f"OKX order book {label}[{index}] is invalid")
        price = _number(row[0], f"{label}[{index}].price")
        size = _number(row[1], f"{label}[{index}].size")
        if price <= 0:
            raise ValueError(f"OKX order book {label}[{index}] is out of range")
        if size <= 0:
            raise ValueError(f"OKX order book {label}[{index}] must have positive size")
        if previous_price is not None:
            if label == "bids" and price >= previous_price:
                raise ValueError("OKX order book bids must be strictly descending")
            if label == "asks" and price <= previous_price:
                raise ValueError("OKX order book asks must be strictly ascending")
        previous_price = price
        notional = _derived_number(price * size, f"{label}[{index}].notional")
        total = _derived_number(total + notional, f"{label}.total_notional")
        normalized.append({"price": price, "size": size, "notional": _round_derived(notional, f"{label}[{index}].notional", 8)})
    if total <= 0:
        raise ValueError(f"OKX order book {label} must have positive notional")
    return normalized, _round_derived(total, f"{label}.total_notional", 8)


def _completed_closes(response: dict[str, Any], label: str) -> tuple[list[tuple[datetime, float]], int]:
    rows = _data(response, label)
    completed: list[tuple[datetime, float]] = []
    incomplete = 0
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 9:
            raise ValueError(f"OKX {label}[{index}] candle is invalid")
        confirmed = str(row[8]) == "1"
        if not confirmed:
            incomplete += 1
            continue
        close = _number(row[4], f"{label}[{index}].close")
        if close <= 0:
            raise ValueError(f"OKX {label}[{index}].close must be positive")
        completed.append((_timestamp_ms(row[0], f"{label}[{index}].ts"), close))
    return completed, incomplete


def _return_pct(current: float, historical: float | None, label: str) -> float | None:
    if historical is None or historical <= 0:
        return None
    try:
        value = (current / historical - 1.0) * 100.0
    except OverflowError as exc:
        raise ValueError(f"invalid derived numeric field: {label}") from exc
    return round(_derived_number(value, label), 4)


def _validate_market_timestamp(value: datetime, *, fetched_at: datetime, label: str) -> float:
    future_seconds = (value - fetched_at).total_seconds()
    if future_seconds > MAX_FUTURE_SKEW_SECONDS:
        raise ValueError(f"future market timestamp: {label}")
    age_seconds = max(0.0, (fetched_at - value).total_seconds())
    if age_seconds > MAX_PUBLIC_AGE_SECONDS:
        raise ValueError(f"stale market timestamp: {label}")
    return round(age_seconds, 3)


def build_public_snapshot(
    *,
    inst_id: str,
    instrument_response: dict[str, Any],
    ticker_response: dict[str, Any],
    book_response: dict[str, Any],
    candles_1d_response: dict[str, Any],
    candles_4h_response: dict[str, Any],
    fetched_at: datetime,
    site: str = "global",
) -> dict[str, Any]:
    inst_id = str(inst_id or "").strip().upper()
    if not INST_ID_RE.fullmatch(inst_id):
        raise ValueError("inst_id must match X<TICKER>-USDT")
    if site not in SITE_BASE_URLS:
        raise ValueError(f"unsupported site: {site}")
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    fetched_at = fetched_at.astimezone(timezone.utc)

    instrument = _single(instrument_response, "instrument")
    if str(instrument.get("instId") or "") != inst_id:
        raise ValueError("instrument instId does not match requested inst_id")
    if str(instrument.get("instType") or "") != "SPOT":
        raise ValueError("instrument must be SPOT")
    if str(instrument.get("instCategory") or "") != "3":
        raise ValueError("instrument instCategory must be 3 for tokenized stocks")
    if str(instrument.get("state") or "") != "live":
        raise ValueError("instrument is not live")
    tick_size = _number(instrument.get("tickSz"), "instrument.tickSz")
    lot_size = _number(instrument.get("lotSz"), "instrument.lotSz")
    min_size = _number(instrument.get("minSz"), "instrument.minSz")
    if min(tick_size, lot_size, min_size) <= 0:
        raise ValueError("instrument tickSz, lotSz, and minSz must be positive")

    ticker = _single(ticker_response, "ticker")
    if str(ticker.get("instId") or "") != inst_id:
        raise ValueError("ticker instId does not match requested inst_id")
    last = _number(ticker.get("last"), "ticker.last")
    ticker_bid = _number(ticker.get("bidPx"), "ticker.bidPx")
    ticker_ask = _number(ticker.get("askPx"), "ticker.askPx")
    if min(last, ticker_bid, ticker_ask) <= 0 or ticker_ask < ticker_bid:
        raise ValueError("ticker prices are invalid")
    market_ts = _timestamp_ms(ticker.get("ts"), "ticker.ts")
    market_age_seconds = _validate_market_timestamp(market_ts, fetched_at=fetched_at, label="ticker.ts")

    book = _single(book_response, "order book")
    # /market/books currently does not echo instId. The production call binds
    # the exact instId in a fixed URL above; if OKX or a test fixture does echo
    # an identifier, any conflict with that request binding fails closed.
    book_inst_id = book.get("instId")
    if book_inst_id is not None and str(book_inst_id) != inst_id:
        raise ValueError("order book instId does not match requested inst_id")
    bids, bid_notional = _book_side(book.get("bids"), "bids")
    asks, ask_notional = _book_side(book.get("asks"), "asks")
    if asks[0]["price"] < bids[0]["price"]:
        raise ValueError("order book is crossed")
    bid = bids[0]["price"]
    ask = asks[0]["price"]
    midpoint = _derived_number(bid + (ask - bid) / 2.0, "midpoint")
    spread_bps = round(_derived_number((ask - bid) / midpoint * 10_000.0, "spread_bps"), 4)
    book_ts = _timestamp_ms(book.get("ts"), "order_book.ts")
    book_age_seconds = _validate_market_timestamp(book_ts, fetched_at=fetched_at, label="order_book.ts")

    daily, incomplete_daily = _completed_closes(candles_1d_response, "candles_1d")
    four_hour, incomplete_4h = _completed_closes(candles_4h_response, "candles_4h")
    close_1d = daily[0][1] if daily else None
    close_5d = daily[4][1] if len(daily) >= 5 else None
    list_time = _timestamp_ms(instrument.get("listTime"), "instrument.listTime")
    if list_time > fetched_at + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise ValueError("future listing timestamp")
    for label, rows in (("candles_1d", daily), ("candles_4h", four_hour)):
        if any(timestamp > fetched_at + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS) for timestamp, _ in rows):
            raise ValueError(f"future market timestamp: {label}")
    listing_age_days = max(0, int((fetched_at - list_time).total_seconds() // 86_400))

    return {
        "schema_version": "okx_public_market_snapshot.v1",
        "venue": "okx",
        "channel": "cex_spot",
        "mode": "public",
        "site": site,
        "fetched_at": fetched_at.isoformat(),
        "instrument": {
            "inst_id": inst_id,
            "inst_type": "SPOT",
            "inst_category": "3",
            "base_ccy": str(instrument.get("baseCcy") or ""),
            "quote_ccy": str(instrument.get("quoteCcy") or ""),
            "state": "live",
            "tick_size": tick_size,
            "lot_size": lot_size,
            "min_size": min_size,
            "list_time": list_time.isoformat(),
            "listing_age_days": listing_age_days,
        },
        "product_identity": {
            "venue": "okx",
            "channel": "cex_spot",
            "instrument_id": inst_id,
            "instrument_type": "SPOT",
            "instrument_category": "3",
            "mapping_verified": True,
            "state": "live",
            "region_eligible": None,
            "evidence_refs": [f"OKX-PUBLIC-INSTRUMENT:{inst_id}"],
            "observed_at": fetched_at.isoformat(),
            "stale_after": (fetched_at + timedelta(minutes=5)).isoformat(),
            "mapping_scope": "exact_exchange_instrument_only",
        },
        "market": {
            "source_ts": market_ts.isoformat(),
            "source_age_seconds": market_age_seconds,
            "last": last,
            "bid": bid,
            "ask": ask,
            "midpoint": _round_derived(midpoint, "midpoint", 8),
            "spread_bps": spread_bps,
            "open_24h": _number(ticker.get("open24h"), "ticker.open24h"),
            "high_24h": _number(ticker.get("high24h"), "ticker.high24h"),
            "low_24h": _number(ticker.get("low24h"), "ticker.low24h"),
            "volume_24h_base": _number(ticker.get("vol24h"), "ticker.vol24h"),
            "volume_24h_quote": _number(ticker.get("volCcy24h"), "ticker.volCcy24h"),
            "order_book_ts": book_ts.isoformat(),
            "order_book_age_seconds": book_age_seconds,
            "freshness_status": "fresh",
            "top5_bid_notional": bid_notional,
            "top5_ask_notional": ask_notional,
            "top5_bids": bids,
            "top5_asks": asks,
        },
        "technical_observables": {
            "completed_1d_candles": len(daily),
            "incomplete_1d_candles": incomplete_daily,
            "completed_4h_candles": len(four_hour),
            "incomplete_4h_candles": incomplete_4h,
            "return_1d_pct": _return_pct(last, close_1d, "return_1d_pct"),
            "return_5d_pct": _return_pct(last, close_5d, "return_5d_pct"),
            "history_limited": len(daily) < 20,
            "note": "observables are evidence inputs, not an entry score",
        },
        "credentials_used": False,
        "public_endpoints_only": True,
        "no_order_execution": True,
    }


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "trading-research-okx-public/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"public OKX request failed: {type(exc).__name__}") from exc
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError("public OKX response is not a JSON object")
    return parsed


def fetch_public_snapshot(inst_id: str, *, site: str = "global", timeout: float = 10.0) -> dict[str, Any]:
    inst_id = str(inst_id or "").strip().upper()
    if not INST_ID_RE.fullmatch(inst_id):
        raise ValueError("inst_id must match X<TICKER>-USDT")
    if site not in SITE_BASE_URLS:
        raise ValueError(f"unsupported site: {site}")
    base = SITE_BASE_URLS[site]

    def url(path: str, **params: str) -> str:
        return f"{base}{path}?{urllib.parse.urlencode(params)}"

    instrument_response = _get_json(url("/api/v5/public/instruments", instType="SPOT", instId=inst_id), timeout)
    ticker_response = _get_json(url("/api/v5/market/ticker", instId=inst_id), timeout)
    book_response = _get_json(url("/api/v5/market/books", instId=inst_id, sz="5"), timeout)
    candles_1d_response = _get_json(
        url("/api/v5/market/history-candles", instId=inst_id, bar="1Dutc", limit="100"),
        timeout,
    )
    candles_4h_response = _get_json(
        url("/api/v5/market/history-candles", instId=inst_id, bar="4H", limit="100"),
        timeout,
    )
    fetched_at = datetime.now(timezone.utc)
    return build_public_snapshot(
        inst_id=inst_id,
        site=site,
        fetched_at=fetched_at,
        instrument_response=instrument_response,
        ticker_response=ticker_response,
        book_response=book_response,
        candles_1d_response=candles_1d_response,
        candles_4h_response=candles_4h_response,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch an OKX Unified Tokenized Stock public snapshot")
    parser.add_argument("inst_id", help="Exact OKX instrument ID, e.g. XMU-USDT")
    parser.add_argument("--site", choices=sorted(SITE_BASE_URLS), default="global")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    try:
        result = fetch_public_snapshot(args.inst_id, site=args.site, timeout=args.timeout)
    except (OverflowError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "credentials_used": False, "no_order_execution": True}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
