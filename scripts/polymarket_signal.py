#!/usr/bin/env python3
"""Read-only Polymarket prediction-market signal snapshot.

Public endpoints only. No wallet, no auth, no order placement.
Outputs a `PredictionMarketSignal`-compatible JSON object for trading-research.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
USER_AGENT = "Hermes trading-research polymarket_signal.py/1.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def http_json(url: str, timeout: int) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 public read-only API
        return json.loads(resp.read().decode("utf-8"))


def safe_json(url: str, timeout: int) -> tuple[Any | None, str | None]:
    try:
        return http_json(url, timeout), None
    except Exception as exc:  # network/API failures become data gaps, not fabricated data
        return None, f"{type(exc).__name__}: {exc}"


def parse_json_field(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp(value: float, low: float = 0.0, high: float = 0.75) -> float:
    return max(low, min(high, value))


def build_market_url(event_slug: str | None, market_slug: str | None) -> str | None:
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if market_slug:
        return f"https://polymarket.com/market/{market_slug}"
    return None


def is_market_alive(market: dict[str, Any]) -> bool:
    """True unless the market is verifiably already resolved/dead.

    `closed` is the field to trust: verified 2026-07-26 against live
    gamma-api data (a "Fed rate cut" search), every already-resolved market
    (Jan/Mar/Apr/Jun 2026 FOMC meetings, all in the past relative to
    2026-07-26) had closed=True while still reporting active=True -- `active`
    does not flip on close. `endDate` is not trustworthy either: sibling
    markets for FOMC meetings still months away (Sep/Oct/Dec 2026, closed=
    False) carried the same stale endDate as already-closed ones. So this
    only excludes closed==True or the rarer explicit active==False; it does
    not filter on endDate.
    """
    if market.get("closed") is True:
        return False
    if market.get("active") is False:
        return False
    return True


def market_volume(market: dict[str, Any]) -> float:
    return as_float(market.get("volume")) or as_float(market.get("volume24hr")) or 0.0


def search_markets(query: str, timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    url = f"{GAMMA}/public-search?" + urllib.parse.urlencode({"q": query})
    data, err = safe_json(url, timeout)
    if err:
        return [], [{"source": url, "error": err}]
    markets: list[dict[str, Any]] = []
    for event in (data or {}).get("events", []):
        for market in event.get("markets", []) or []:
            item = dict(market)
            item["_event_title"] = event.get("title")
            item["_event_slug"] = event.get("slug")
            item["_event_volume"] = event.get("volume")
            markets.append(item)
    for market in (data or {}).get("markets", []) or []:
        item = dict(market)
        item.setdefault("_event_title", market.get("question"))
        item.setdefault("_event_slug", market.get("eventSlug") or market.get("slug"))
        markets.append(item)
    live = [m for m in markets if is_market_alive(m)]
    live.sort(key=market_volume, reverse=True)
    if markets and not live:
        # Every match was already resolved/closed -- this is a real data gap,
        # not a healthy "no signal" result (audit finding
        # polymarket-expired-markets-health-illusion). Surfacing it here
        # means main()'s existing `ok = bool(signals)` naturally goes False
        # once `markets` (now `live`) is empty, instead of quietly reporting
        # dead-market probabilities as ok:true.
        return [], [{
            "source": url,
            "error": f"no_active_market_matched: {len(markets)} market(s) matched '{query}' but all are closed/resolved",
        }]
    return live, []


def fetch_clob(token_id: str | None, condition_id: str | None, timeout: int) -> tuple[dict[str, Any], list[dict[str, str]]]:
    out: dict[str, Any] = {}
    gaps: list[dict[str, str]] = []
    if token_id:
        for key, path in {
            "midpoint": "/midpoint",
            "spread": "/spread",
            "book": "/book",
        }.items():
            url = f"{CLOB}{path}?" + urllib.parse.urlencode({"token_id": token_id})
            data, err = safe_json(url, timeout)
            if err:
                gaps.append({"source": url, "error": err})
            else:
                out[key] = data
    if condition_id:
        url = f"{CLOB}/prices-history?" + urllib.parse.urlencode(
            {"market": condition_id, "interval": "1d", "fidelity": "60"}
        )
        data, err = safe_json(url, timeout)
        if err:
            gaps.append({"source": url, "error": err})
        else:
            out["history_1d"] = data
        oi_url = f"{DATA}/oi?" + urllib.parse.urlencode({"market": condition_id})
        data, err = safe_json(oi_url, timeout)
        if err:
            gaps.append({"source": oi_url, "error": err})
        else:
            out["open_interest_raw"] = data
    return out, gaps


def book_depth(book: dict[str, Any] | None) -> tuple[float | None, float | None, float | None]:
    if not isinstance(book, dict):
        return None, None, None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid_depth = sum(as_float(x.get("size")) or 0.0 for x in bids[:5] if isinstance(x, dict))
    ask_depth = sum(as_float(x.get("size")) or 0.0 for x in asks[:5] if isinstance(x, dict))
    if bid_depth <= 0 and ask_depth <= 0:
        return None, None, None
    denom = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / denom if denom else None
    return round(bid_depth, 4), round(ask_depth, 4), round(imbalance, 6) if imbalance is not None else None


def history_delta(history: dict[str, Any] | None) -> float | None:
    points = (history or {}).get("history") if isinstance(history, dict) else None
    if not points or len(points) < 2:
        return None
    first = as_float(points[0].get("p"))
    last = as_float(points[-1].get("p"))
    if first is None or last is None:
        return None
    return round(last - first, 6)


def open_interest_value(raw: Any) -> float | None:
    if isinstance(raw, dict):
        for key in ("open_interest", "openInterest", "oi", "value"):
            val = as_float(raw.get(key))
            if val is not None:
                return val
    if isinstance(raw, list) and raw:
        return open_interest_value(raw[0])
    return as_float(raw)


def reliability(signal: dict[str, Any]) -> float:
    score = 0.75
    spread = signal.get("spread")
    if signal.get("market_url") is None:
        score -= 0.20
    if spread is None:
        score -= 0.15
    elif spread > 0.10:
        score -= 0.30
    elif spread > 0.05:
        score -= 0.20
    elif spread > 0.02:
        score -= 0.05
    if signal.get("depth_bid") is None or signal.get("depth_ask") is None:
        score -= 0.15
    elif min(signal["depth_bid"], signal["depth_ask"]) < 100:
        score -= 0.10
    if signal.get("resolution_rule_status") != "clear":
        score -= 0.20
    if not signal.get("cross_check_eids"):
        score -= 0.10
    return round(clamp(score), 4)


def module_signal(signal: dict[str, Any], max_spread: float) -> dict[str, Any]:
    blockers: list[str] = []
    for key in ("market_url", "observed_at", "implied_probability"):
        if signal.get(key) in (None, ""):
            blockers.append(f"missing_{key}")
    if signal.get("spread") is None:
        blockers.append("missing_spread")
    elif signal["spread"] > max_spread:
        blockers.append("wide_spread")
    if signal.get("depth_bid") is None or signal.get("depth_ask") is None:
        blockers.append("missing_depth")
    if signal.get("resolution_rule_status") != "clear":
        blockers.append("resolution_not_verified")
    if not signal.get("cross_check_eids"):
        blockers.append("missing_independent_cross_check")
    if blockers:
        return {
            "module": "prediction_market_prior",
            "max_action_level": "L0",
            "position_multiplier": 0.0,
            "hard_veto": False,
            "reason": ";".join(blockers),
        }
    return {
        "module": "prediction_market_prior",
        "max_action_level": "L1",
        "position_multiplier": 0.0,
        "hard_veto": False,
        "reason": "read_only_market_pricing_prior; cannot raise position cap",
    }


def build_signal(
    market: dict[str, Any],
    args: argparse.Namespace,
    observed_at: str,
) -> dict[str, Any]:
    outcomes = parse_json_field(market.get("outcomes"))
    prices = parse_json_field(market.get("outcomePrices"))
    token_ids = parse_json_field(market.get("clobTokenIds"))
    yes_idx = 0
    if outcomes:
        lowered = [str(x).lower() for x in outcomes]
        if "yes" in lowered:
            yes_idx = lowered.index("yes")
    yes_token = str(token_ids[yes_idx]) if yes_idx < len(token_ids) else None
    implied = as_float(prices[yes_idx]) if yes_idx < len(prices) else None
    condition_id = market.get("conditionId") or market.get("condition_id")
    clob, gaps = fetch_clob(yes_token, condition_id, args.timeout)
    mid = as_float((clob.get("midpoint") or {}).get("mid"))
    spread = as_float((clob.get("spread") or {}).get("spread"))
    depth_bid, depth_ask, imbalance = book_depth(clob.get("book"))
    if mid is not None:
        implied = mid
    signal: dict[str, Any] = {
        "eid": args.eid,
        "source": "Polymarket public market data",
        "market_url": build_market_url(market.get("_event_slug"), market.get("slug")),
        "market_question": market.get("question") or market.get("title") or market.get("_event_title"),
        "event_slug": market.get("_event_slug") or "unavailable",
        "condition_id": condition_id or "unavailable",
        "yes_token_id": yes_token or "unavailable",
        "observed_at": observed_at,
        "implied_probability": implied,
        "probability_basis": "midpoint" if mid is not None else "outcomePrices" if implied is not None else "unavailable",
        "probability_momentum_1h": None,
        "probability_momentum_24h": history_delta(clob.get("history_1d")),
        "probability_momentum_7d": None,
        "spread": spread,
        "depth_bid": depth_bid,
        "depth_ask": depth_ask,
        "depth_imbalance": imbalance,
        "volume_24h": as_float(market.get("volume24hr") or market.get("volume24h")),
        "open_interest": open_interest_value(clob.get("open_interest_raw")),
        "trade_flow_velocity": None,
        "liquidity_adjusted_reliability": None,
        "resolution_rule_status": args.resolution_rule_status,
        "resolution_risk": args.resolution_risk,
        "cross_venue_gap": None,
        "cross_check_eids": args.cross_check_eid,
        "claim_type": "market_pricing",
        "verification_status": "verified" if not gaps else "disclosed",
        "readiness_impact": "monitoring_only",
        "data_gaps": gaps,
    }
    signal["liquidity_adjusted_reliability"] = reliability(signal)
    signal["module_signal"] = module_signal(signal, args.max_spread)
    if signal["module_signal"]["max_action_level"] == "L1":
        signal["readiness_impact"] = "supports_working_view"
    return signal


def self_test() -> int:
    sample = {
        "market_url": "https://polymarket.com/event/sample",
        "observed_at": now_iso(),
        "implied_probability": 0.63,
        "spread": 0.02,
        "depth_bid": 1000,
        "depth_ask": 900,
        "resolution_rule_status": "clear",
        "cross_check_eids": ["E1"],
    }
    sample["liquidity_adjusted_reliability"] = reliability(sample)
    sample["module_signal"] = module_signal(sample, 0.05)
    ok = sample["module_signal"]["max_action_level"] == "L1" and sample["module_signal"]["position_multiplier"] == 0.0
    print(json.dumps({"ok": ok, "sample": sample}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch read-only Polymarket auxiliary signal JSON.")
    parser.add_argument("--query", help="Search query, e.g. 'Fed rate cut' or 'Trump election'.")
    parser.add_argument("--limit", type=int, default=3, help="Max markets to return.")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--eid", default="E55")
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--resolution-rule-status", choices=["clear", "ambiguous", "disputed", "unavailable"], default="unavailable")
    parser.add_argument("--resolution-risk", choices=["low", "medium", "high", "unknown"], default="unknown")
    parser.add_argument("--cross-check-eid", action="append", default=[])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.query:
        parser.error("--query is required unless --self-test is used")
    observed_at = now_iso()
    markets, gaps = search_markets(args.query, args.timeout)
    signals = [build_signal(m, args, observed_at) for m in markets[: max(args.limit, 0)]]
    result = {
        "ok": bool(signals),
        "query": args.query,
        "observed_at": observed_at,
        "signals": signals,
        "data_gaps": gaps,
        "contract": "PredictionMarketSignal",
        "safety": {
            "read_only": True,
            "uses_wallet": False,
            "places_orders": False,
            "position_multiplier_can_increase": False,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if signals else 2


if __name__ == "__main__":
    raise SystemExit(main())
