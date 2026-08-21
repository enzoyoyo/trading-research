#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

KNOWN: dict[str, dict[str, Any]] = {
    "腾讯": {"market": "HK", "symbol": "00700", "company_name": "腾讯控股", "confidence": "medium", "reason": "known_hk_name_hint"},
    "腾讯控股": {"market": "HK", "symbol": "00700", "company_name": "腾讯控股", "confidence": "medium", "reason": "known_hk_name_hint"},
    "阿里": {"market": "HK", "symbol": "09988", "company_name": "阿里巴巴-W", "confidence": "medium", "reason": "known_hk_name_hint"},
    "阿里巴巴": {"market": "HK", "symbol": "09988", "company_name": "阿里巴巴-W", "confidence": "medium", "reason": "known_hk_name_hint", "aliases": ["BABA", "BABA.US", "9988.HK"]},
    "阿里巴巴-W": {"market": "HK", "symbol": "09988", "company_name": "阿里巴巴-W", "confidence": "medium", "reason": "known_hk_name_hint", "aliases": ["BABA", "BABA.US", "9988.HK"]},
    "美团": {"market": "HK", "symbol": "03690", "company_name": "美团-W", "confidence": "medium", "reason": "known_hk_name_hint"},
    "小米": {"market": "HK", "symbol": "01810", "company_name": "小米集团-W", "confidence": "medium", "reason": "known_hk_name_hint"},
    "茅台": {"market": "A", "symbol": "600519", "company_name": "贵州茅台", "exchange": "SH", "confidence": "medium", "reason": "known_a_name_hint"},
    "贵州茅台": {"market": "A", "symbol": "600519", "company_name": "贵州茅台", "exchange": "SH", "confidence": "medium", "reason": "known_a_name_hint"},
    "新易盛": {"market": "A", "symbol": "300502", "company_name": "新易盛", "exchange": "SZ", "confidence": "medium", "reason": "known_a_name_hint_offline_fallback"},
    "特斯拉": {"market": "US", "symbol": "TSLA", "company_name": "Tesla", "confidence": "medium", "reason": "known_us_name_hint"},
    "英伟达": {"market": "US", "symbol": "NVDA", "company_name": "NVIDIA", "confidence": "medium", "reason": "known_us_name_hint"},
}
A_SUFFIX_MAP = {"SH": "SH", "SS": "SH", "SSE": "SH", "SZ": "SZ", "SZSE": "SZ", "BJ": "BJ", "BSE": "BJ"}
AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or sys.executable


def infer_a_exchange(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "SH"
    if s.startswith(("000", "001", "002", "003", "200", "300", "301")):
        return "SZ"
    if s.startswith(("4", "8", "920")):
        return "BJ"
    return "SH" if s.startswith("6") else "SZ"


def passport(base: dict[str, Any]) -> dict[str, Any]:
    market = base.get("market", "unknown")
    symbol = str(base.get("symbol", base.get("query", ""))).upper()
    exchange = base.get("exchange")
    out = {
        "query": base.get("query"),
        "market": market,
        "symbol": symbol,
        "confidence": base.get("confidence", "low"),
        "reason": base.get("reason", "unresolved"),
        "company_name": base.get("company_name"),
        "aliases": base.get("aliases", []),
        "ambiguity": base.get("ambiguity", []),
    }
    if market == "A":
        exchange = exchange or infer_a_exchange(symbol)
        out.update({
            "exchange": exchange,
            "canonical_symbol": f"{symbol}.{exchange}",
            "longbridge_symbol": f"{symbol}.{exchange}",
            "akshare_symbol": symbol,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "trading_rules": ["T+1", "limit_up_down", "northbound_sensitive"],
        })
    elif market == "HK":
        symbol = symbol.zfill(5)
        out.update({
            "symbol": symbol,
            "exchange": "HKEX",
            "canonical_symbol": f"{symbol}.HK",
            "longbridge_symbol": f"{symbol}.HK",
            "akshare_symbol": symbol,
            "currency": "HKD",
            "timezone": "Asia/Hong_Kong",
            "trading_rules": ["no_limit_up_down", "liquidity_gap_sensitive", "southbound_sensitive"],
        })
    elif market == "US":
        out.update({
            "exchange": exchange or "US",
            "canonical_symbol": symbol,
            "longbridge_symbol": f"{symbol}.US" if "." not in symbol else f"{symbol}.US",
            "akshare_symbol": symbol,
            "currency": "USD",
            "timezone": "America/New_York",
            "trading_rules": ["premarket_afterhours", "options_gamma_if_active", "sec_disclosure"],
        })
    else:
        out.update({
            "exchange": None,
            "canonical_symbol": symbol,
            "longbridge_symbol": None,
            "akshare_symbol": symbol,
            "currency": None,
            "timezone": None,
            "trading_rules": [],
        })
    return out


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def lookup_a_share_cn_name(query: str) -> dict[str, Any] | None:
    """Resolve arbitrary A-share Chinese names through AkShare's code/name table.

    This is a generic resolver, not a one-stock alias table. It only runs for unresolved CJK input.
    If AkShare is unavailable, classification remains unresolved instead of guessing.
    """
    if not has_cjk(query):
        return None
    code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
q = {query!r}
try:
    df = ak.stock_info_a_code_name()
    exact = df[df['name'].astype(str) == q]
    rows = exact.to_dict(orient='records')
    if not rows:
        partial = df[df['name'].astype(str).str.contains(q, regex=False, na=False)]
        rows = partial.head(8).to_dict(orient='records')
    print(json.dumps(rows, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[key] = ""
    env["no_proxy"] = "*"
    try:
        proc = subprocess.run([AKSHARE_PYTHON, "-c", code], capture_output=True, text=True, timeout=30, env=env)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        parsed = json.loads((proc.stdout or "").strip())
    except Exception:
        return None
    if isinstance(parsed, dict) and parsed.get("error"):
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    if len(parsed) == 1:
        row = parsed[0]
        symbol = str(row.get("code") or row.get("代码") or "").zfill(6)
        name = str(row.get("name") or row.get("名称") or query)
        if re.fullmatch(r"\d{6}", symbol):
            return {"query": query, "market": "A", "symbol": symbol, "company_name": name, "exchange": infer_a_exchange(symbol), "confidence": "high", "reason": "akshare_a_name_lookup"}
    ambiguity = []
    for row in parsed[:8]:
        code_val = row.get("code") or row.get("代码")
        name_val = row.get("name") or row.get("名称")
        if code_val and name_val:
            sym = str(code_val).zfill(6)
            ambiguity.append(f"{name_val} {sym}.{infer_a_exchange(sym)}")
    return {"query": query, "market": "unknown", "symbol": query, "confidence": "low", "reason": "a_name_ambiguous", "ambiguity": ambiguity or ["A股中文名匹配多结果，需要补代码"]}


def classify(query: str) -> dict[str, Any]:
    raw = str(query).strip()
    q = raw.strip().replace("$", "").replace("：", ":")
    compact = re.sub(r"\s+", "", q)
    if compact in KNOWN:
        return passport({"query": raw, **KNOWN[compact]})

    upper = compact.upper()

    # HK suffix: 700.HK / 00700.HK / 9988.HK
    m = re.fullmatch(r"0*(\d{1,5})\.(HK|HKG)", upper)
    if m:
        return passport({"query": raw, "market": "HK", "symbol": m.group(1).zfill(5), "confidence": "high", "reason": "hk_suffix_code"})

    # A-share suffix: 600519.SH / 300846.SZ / 430047.BJ
    m = re.fullmatch(r"(\d{6})\.(SH|SS|SSE|SZ|SZSE|BJ|BSE)", upper)
    if m:
        return passport({"query": raw, "market": "A", "symbol": m.group(1), "exchange": A_SUFFIX_MAP[m.group(2)], "confidence": "high", "reason": "a_share_suffix_code"})

    digits = "".join(ch for ch in compact if ch.isdigit())
    if re.fullmatch(r"\d{5}", digits):
        return passport({"query": raw, "market": "HK", "symbol": digits.zfill(5), "confidence": "high", "reason": "five_digit_hk_code"})
    if re.fullmatch(r"\d{6}", digits):
        return passport({"query": raw, "market": "A", "symbol": digits, "exchange": infer_a_exchange(digits), "confidence": "medium", "reason": "six_digit_numeric_default_a_share"})

    # Generic A-share Chinese name lookup. Do this before US ticker pattern fallback.
    lookup = lookup_a_share_cn_name(compact)
    if lookup:
        return passport(lookup)

    # US ticker: TSLA / BABA.US / BRK.B / GOOG
    if upper.endswith(".US"):
        upper = upper[:-3]
    if re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", upper):
        return passport({"query": raw, "market": "US", "symbol": upper, "confidence": "high", "reason": "us_ticker_pattern"})

    return passport({"query": raw, "market": "unknown", "symbol": compact or raw, "confidence": "low", "reason": "unresolved", "ambiguity": ["需要用搜索/交易所名录进一步解析公司名或同名证券"]})


if __name__ == "__main__":
    print(json.dumps([classify(a) for a in (sys.argv[1:] or ["TSLA"])], ensure_ascii=False, indent=2))
