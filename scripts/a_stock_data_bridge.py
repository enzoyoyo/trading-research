#!/usr/bin/env python3
"""A-share public-source bridge inspired by simonlin1212/a-stock-data.

Read-only helper for trading-research. No broker/order APIs. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
SOURCE_META = {
    "layer": "a_stock_data_bridge",
    "upstream": "https://github.com/simonlin1212/a-stock-data",
    "upstream_version": "v3.2.2",
    "upstream_commit": "9379ab90d0219312b5f4845cd8c97502f40b0806",
    "license": "Apache-2.0",
}
EM_MIN_INTERVAL = float(os.environ.get("EM_MIN_INTERVAL", "1.1"))
_EM_LAST_CALL = [0.0]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_opener(use_proxy: bool = False) -> urllib.request.OpenerDirector:
    if use_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except Exception:
        return None


def normalize_a_code(raw: str) -> dict[str, Any]:
    q = str(raw).strip().upper().replace("$", "")
    q = q.replace("SHSE.", "").replace("SZSE.", "").replace("SSE.", "").replace("SZSE", "SZ")
    exchange = None
    m = re.fullmatch(r"(\d{6})\.(SH|SS|SSE|SZ|SZSE|BJ|BSE)", q)
    if m:
        code = m.group(1)
        exchange = {"SS": "SH", "SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(m.group(2), m.group(2))
    else:
        digits = "".join(ch for ch in q if ch.isdigit())
        if not re.fullmatch(r"\d{6}", digits):
            raise ValueError(f"not an A-share 6-digit code: {raw}")
        code = digits
    if exchange is None:
        if code.startswith(("600", "601", "603", "605", "688", "689", "900")):
            exchange = "SH"
        elif code.startswith(("4", "8", "920")):
            exchange = "BJ"
        else:
            exchange = "SZ"
    return {
        "input": raw,
        "code": code,
        "exchange": exchange,
        "canonical_symbol": f"{code}.{exchange}",
        "market": "A",
    }


def tencent_prefix(code: str, exchange: str) -> str:
    if exchange == "SH":
        return f"sh{code}"
    if exchange == "BJ":
        return f"bj{code}"
    return f"sz{code}"


def eastmoney_market_id(code: str, exchange: str | None = None) -> int:
    if exchange == "SH" or code.startswith(("6", "9")):
        return 1
    return 0


def urlopen_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    timeout: int = 15,
    encoding: str = "utf-8",
) -> str:
    body = None
    method = "GET"
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        method = "POST"
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {"User-Agent": UA})
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode(encoding, errors="ignore")


def get_json(opener: urllib.request.OpenerDirector, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, *, eastmoney: bool = False) -> Any:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    if eastmoney:
        wait = EM_MIN_INTERVAL - (time.time() - _EM_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait + random.uniform(0.1, 0.4))
    try:
        text = urlopen_text(opener, url, headers=headers or {"User-Agent": UA}, timeout=15)
        return json.loads(text)
    finally:
        if eastmoney:
            _EM_LAST_CALL[0] = time.time()


def post_json(opener: urllib.request.OpenerDirector, url: str, data: dict[str, str], headers: dict[str, str]) -> Any:
    text = urlopen_text(opener, url, headers=headers, data=data, timeout=15)
    return json.loads(text)


def cmd_quote(opener: urllib.request.OpenerDirector, symbols: list[str]) -> dict[str, Any]:
    identities = [normalize_a_code(s) for s in symbols]
    query = ",".join(tencent_prefix(i["code"], i["exchange"]) for i in identities)
    text = urlopen_text(opener, f"https://qt.gtimg.cn/q={query}", headers={"User-Agent": UA}, timeout=15, encoding="gbk")
    id_by_code = {i["code"]: i for i in identities}
    items = []
    for line in text.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        values = line.split('"')[1].split("~")
        if len(values) < 53:
            continue
        code = key[2:]
        ident = id_by_code.get(code, normalize_a_code(code))
        items.append({
            **ident,
            "name": values[1],
            "price": safe_float(values[3]),
            "prev_close": safe_float(values[4]),
            "open": safe_float(values[5]),
            "change_amt": safe_float(values[31]),
            "change_pct": safe_float(values[32]),
            "high": safe_float(values[33]),
            "low": safe_float(values[34]),
            "amount_wan": safe_float(values[37]),
            "turnover_pct": safe_float(values[38]),
            "pe_ttm": safe_float(values[39]),
            "amplitude_pct": safe_float(values[43]),
            "mcap_yi": safe_float(values[44]),
            "float_mcap_yi": safe_float(values[45]),
            "pb": safe_float(values[46]),
            "limit_up": safe_float(values[47]),
            "limit_down": safe_float(values[48]),
            "vol_ratio": safe_float(values[49]),
            "pe_static": safe_float(values[52]),
            "field_notes": {"43": "amplitude_pct_not_pb", "46": "pb"},
        })
    return {
        "ok": bool(items),
        "command": "quote",
        "source": "Tencent Finance qt.gtimg.cn via a-stock-data field map",
        "observed_at": now_iso(),
        "source_meta": SOURCE_META,
        "items": items,
        "data_gaps": [] if items else [{"gap": "tencent_quote_empty", "severity": "high", "impact": "A股快照不可用"}],
    }


def cmd_concept(opener: urllib.request.OpenerDirector, symbol: str) -> dict[str, Any]:
    ident = normalize_a_code(symbol)
    params = {
        "fltt": "2", "invt": "2", "secid": f"{eastmoney_market_id(ident['code'], ident['exchange'])}.{ident['code']}",
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    payload = get_json(opener, "https://push2.eastmoney.com/api/qt/slist/get", params, headers, eastmoney=True)
    diff = (payload.get("data") or {}).get("diff") or []
    rows = diff.values() if isinstance(diff, dict) else diff
    boards = [{
        "name": row.get("f14", ""),
        "code": row.get("f12", ""),
        "change_pct": row.get("f3", ""),
        "lead_stock": row.get("f128", ""),
    } for row in rows if isinstance(row, dict)]
    return {
        "ok": bool(boards),
        "command": "concept",
        "source": "Eastmoney push2 slist via a-stock-data pattern",
        "observed_at": now_iso(),
        "source_meta": SOURCE_META,
        "identity": ident,
        "total": len(boards),
        "boards": boards,
        "concept_tags": [b["name"] for b in boards if b.get("name")],
        "notes": ["Eastmoney mixes industry/concept/region boards; do not treat tags as precise taxonomy."],
        "data_gaps": [] if boards else [{"gap": "eastmoney_concept_empty_or_rate_limited", "severity": "medium", "impact": "无法补充板块归属"}],
    }


def cmd_fund_flow(opener: urllib.request.OpenerDirector, symbol: str, limit: int) -> dict[str, Any]:
    ident = normalize_a_code(symbol)
    params = {
        "secid": f"{eastmoney_market_id(ident['code'], ident['exchange'])}.{ident['code']}",
        "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Origin": "https://quote.eastmoney.com"}
    payload = get_json(opener, "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get", params, headers, eastmoney=True)
    rows = []
    for line in (payload.get("data") or {}).get("klines", []) or []:
        parts = str(line).split(",")
        if len(parts) >= 6:
            rows.append({
                "time": parts[0],
                "main_net": safe_float(parts[1]),
                "small_net": safe_float(parts[2]),
                "mid_net": safe_float(parts[3]),
                "large_net": safe_float(parts[4]),
                "super_net": safe_float(parts[5]),
            })
    if limit > 0:
        rows = rows[-limit:]
    return {
        "ok": bool(rows),
        "command": "fund-flow",
        "source": "Eastmoney push2 fflow via a-stock-data pattern",
        "observed_at": now_iso(),
        "source_meta": SOURCE_META,
        "identity": ident,
        "items": rows,
        "unit": "CNY",
        "notes": ["Minute fund-flow is most meaningful during trading hours; empty response can be non-trading-time or Eastmoney rate/network gap."],
        "data_gaps": [] if rows else [{"gap": "eastmoney_fund_flow_empty_or_rate_limited", "severity": "medium", "impact": "无法确认盘中资金流"}],
    }


def cninfo_ts_to_date(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
    return str(value or "")[:10]


def cninfo_orgid(opener: urllib.request.OpenerDirector, code: str) -> tuple[str, bool, int | None]:
    try:
        payload = get_json(opener, "http://www.cninfo.com.cn/new/data/szse_stock.json", headers={"User-Agent": UA})
        mapping = {str(s.get("code")): str(s.get("orgId")) for s in payload.get("stockList", []) if s.get("code") and s.get("orgId")}
        org = mapping.get(code)
        if org:
            return org, False, len(mapping)
    except Exception:
        pass
    if code.startswith("6"):
        return f"gssh0{code}", True, None
    if code.startswith(("8", "4")):
        return f"gsbj0{code}", True, None
    return f"gssz0{code}", True, None


def cmd_announcements(opener: urllib.request.OpenerDirector, symbol: str, limit: int) -> dict[str, Any]:
    ident = normalize_a_code(symbol)
    org_id, fallback, map_size = cninfo_orgid(opener, ident["code"])
    payload = {
        "stock": f"{ident['code']},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(limit),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    data = post_json(opener, "https://www.cninfo.com.cn/new/hisAnnouncement/query", payload, headers)
    rows = []
    for item in data.get("announcements", []) or []:
        rows.append({
            "title": item.get("announcementTitle", ""),
            "type": item.get("announcementTypeName", ""),
            "date": cninfo_ts_to_date(item.get("announcementTime")),
            "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
        })
    return {
        "ok": bool(rows),
        "command": "announcements",
        "source": "CNINFO official announcements via a-stock-data dynamic orgId pattern",
        "observed_at": now_iso(),
        "source_meta": SOURCE_META,
        "identity": ident,
        "org_id": org_id,
        "cninfo_orgid_fallback": fallback,
        "cninfo_map_size": map_size,
        "items": rows,
        "data_gaps": [] if rows else [{"gap": "cninfo_announcements_empty", "severity": "medium", "impact": "无法确认最新公告；若 fallback=true 需人工复核 orgId"}],
    }


def cmd_health(opener: urllib.request.OpenerDirector) -> dict[str, Any]:
    checks = []
    for name, fn in [
        ("tencent_quote", lambda: cmd_quote(opener, ["600519.SH"])),
        ("eastmoney_concept", lambda: cmd_concept(opener, "600519.SH")),
        ("cninfo_orgid", lambda: {"ok": not cninfo_orgid(opener, "600519")[1]}),
    ]:
        try:
            result = fn()
            checks.append({"source": name, "status": "ok" if result.get("ok") else "fail", "checked_at": now_iso(), "safe_summary": "ok" if result.get("ok") else "empty"})
        except Exception as exc:
            checks.append({"source": name, "status": "fail", "checked_at": now_iso(), "safe_summary": type(exc).__name__, "error_class": type(exc).__name__})
    return {
        "ok": all(c["status"] == "ok" for c in checks),
        "command": "health",
        "source_meta": SOURCE_META,
        "observed_at": now_iso(),
        "checks": checks,
    }


def emit(obj: dict[str, Any], pretty: bool) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None, sort_keys=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A-share public data bridge for trading-research")
    parser.add_argument("--use-proxy", action="store_true", help="Use environment proxies instead of direct China-source connections")
    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health")
    p_health.add_argument("--json", action="store_true")

    p_quote = sub.add_parser("quote")
    p_quote.add_argument("symbols", nargs="+")
    p_quote.add_argument("--json", action="store_true")

    p_concept = sub.add_parser("concept")
    p_concept.add_argument("symbol")
    p_concept.add_argument("--json", action="store_true")

    p_ann = sub.add_parser("announcements")
    p_ann.add_argument("symbol")
    p_ann.add_argument("--limit", type=int, default=20)
    p_ann.add_argument("--json", action="store_true")

    p_flow = sub.add_parser("fund-flow")
    p_flow.add_argument("symbol")
    p_flow.add_argument("--limit", type=int, default=20)
    p_flow.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    opener = build_opener(use_proxy=args.use_proxy)
    pretty = bool(getattr(args, "json", False))
    try:
        if args.command == "health":
            result = cmd_health(opener)
        elif args.command == "quote":
            result = cmd_quote(opener, args.symbols)
        elif args.command == "concept":
            result = cmd_concept(opener, args.symbol)
        elif args.command == "announcements":
            result = cmd_announcements(opener, args.symbol, max(1, min(args.limit, 100)))
        elif args.command == "fund-flow":
            result = cmd_fund_flow(opener, args.symbol, max(0, min(args.limit, 500)))
        else:
            raise ValueError(f"unknown command: {args.command}")
        emit(result, pretty)
        return 0 if result.get("ok") else 2
    except Exception as exc:
        emit({
            "ok": False,
            "command": args.command,
            "source_meta": SOURCE_META,
            "observed_at": now_iso(),
            "error_class": type(exc).__name__,
            "error": str(exc),
            "data_gaps": [{"gap": "a_stock_data_bridge_error", "severity": "high", "impact": "A股直连公开源不可用"}],
        }, True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
