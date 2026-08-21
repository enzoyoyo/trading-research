#!/usr/bin/env python3
"""trading-research · fundamental_snapshot — 三市场基本面快照。

规则：空 parts 不是 ok；不得把空基本面结果生成 fundamentals EID。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or "/opt/homebrew/bin/python3"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

try:
    from us_company_evidence import collect_company_evidence
except ImportError:
    collect_company_evidence = None  # type: ignore[assignment]


def _no_proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[key] = ""
    env["no_proxy"] = "*"
    return env


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_py(code: str, timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [AKSHARE_PYTHON, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_no_proxy_env(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error_class": "timeout", "stderr_tail": ""}
    except FileNotFoundError:
        return {"status": "fail", "error_class": "python_not_found", "stderr_tail": AKSHARE_PYTHON}
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def parse_stdout(res: dict[str, Any]) -> Any | None:
    if res.get("status") != "pass":
        return None
    text = (res.get("stdout") or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text[:800], "parse_error": True}


def latest_record(records: list[Any]) -> Any:
    if not records:
        return None
    if not all(isinstance(x, dict) for x in records):
        return records[-1]
    date_keys = ("REPORT_DATE", "公告日期", "报告期", "日期", "date", "Date", "TRADE_DATE")
    def key_fn(row: dict[str, Any]) -> str:
        for key in date_keys:
            val = row.get(key)
            if val not in (None, ""):
                return str(val)
        return ""
    return max(records, key=key_fn)


def add_part(result: dict[str, Any], key: str, res: dict[str, Any], *, list_tail: bool = True) -> None:
    parsed = parse_stdout(res)
    if parsed is None:
        result["gaps"].append({
            "gap": f"{key} unavailable",
            "impact": "基本面/估值验证缺失",
            "severity": "high" if key in {"financial_indicators", "us_financial_indicators"} else "medium",
            "error_class": res.get("error_class") or ("subprocess_fail" if res.get("status") != "pass" else "empty_or_parse_fail"),
            "stderr_tail": res.get("stderr_tail"),
        })
        return
    if isinstance(parsed, dict) and parsed.get("error"):
        result["gaps"].append({"gap": f"{key} error", "impact": "该基本面子源不可用", "severity": "medium", "error_class": "source_error", "safe_summary": parsed.get("error")})
        return
    if isinstance(parsed, list):
        if not parsed:
            result["gaps"].append({"gap": f"{key} empty", "impact": "该基本面子源无返回", "severity": "medium", "error_class": "empty"})
            return
        result["parts"][key] = {"count": len(parsed), "latest": latest_record(parsed) if list_tail else parsed[:5]}
        return
    if isinstance(parsed, dict):
        if parsed:
            result["parts"][key] = parsed
        else:
            result["gaps"].append({"gap": f"{key} empty", "impact": "该基本面子源无返回", "severity": "medium", "error_class": "empty"})


def finalize(result: dict[str, Any]) -> dict[str, Any]:
    if result["parts"]:
        result["status"] = "partial" if result["gaps"] else "ok"
    else:
        result["status"] = "fail"
        result["gaps"].append({
            "gap": "fundamental parts empty",
            "impact": "不得生成 fundamentals EID；估值/财报无锚",
            "severity": "high",
            "error_class": "empty_fundamentals",
        })
    return result


def a_share_fundamentals(code: str) -> dict[str, Any]:
    result = {"market": "A", "code": code, "fetched_at": now(), "status": "draft", "parts": {}, "gaps": []}
    fin_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_financial_analysis_indicator(symbol='{code}', start_year='2020')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "financial_indicators", run_py(fin_code, timeout=45))
    val_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_zh_valuation_baidu(symbol='{code}', indicator='市盈率(TTM)', period='近一年')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "valuation_pe_ttm", run_py(val_code, timeout=30))
    return finalize(result)


def hk_share_fundamentals(code: str) -> dict[str, Any]:
    hk = code.zfill(5) if code.isdigit() else code
    result = {"market": "HK", "code": hk, "fetched_at": now(), "status": "draft", "parts": {}, "gaps": []}
    fin_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_hk_financial_indicator_em(symbol='{hk}')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "financial_indicators", run_py(fin_code, timeout=45))
    val_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_hk_valuation_baidu(symbol='{hk}', indicator='市盈率(TTM)', period='近一年')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "valuation_pe_ttm", run_py(val_code, timeout=30))
    return finalize(result)


def us_share_fundamentals(code: str) -> dict[str, Any]:
    sym = code.upper().replace(".US", "")
    result = {
        "market": "US",
        "code": sym,
        "fetched_at": now(),
        "status": "draft",
        "parts": {},
        "regulatory_evidence": [],
        "source_health": [],
        "gaps": [],
        "no_order_execution": True,
    }

    if collect_company_evidence is not None:
        collected = collect_company_evidence(f"{sym}.US")
        if isinstance(collected, dict):
            collected_parts = collected.get("parts") if isinstance(collected.get("parts"), dict) else {}
            for key in (
                "financial_report",
                "valuation",
                "insider_trades",
                "shareholder_snapshot",
                "institutional_portfolio",
                "sec_companyfacts",
            ):
                if key in collected_parts:
                    result["parts"][key] = collected_parts[key]
            result["regulatory_evidence"] = list(collected.get("regulatory_evidence") or [])
            result["source_health"] = list(collected.get("source_health") or [])
            result["gaps"].extend(collected.get("gaps") or [])
        else:
            result["gaps"].append({
                "gap": "US company evidence returned invalid payload",
                "impact": "LongBridge-first 基本面不可用",
                "severity": "high",
                "error_class": "schema_mismatch",
            })
    else:
        result["gaps"].append({
            "gap": "US company evidence adapter unavailable",
            "impact": "LongBridge-first 基本面不可用",
            "severity": "high",
            "error_class": "adapter_unavailable",
        })

    # AkShare remains a per-dimension fallback. Do not call it when LongBridge
    # or SEC already supplied the corresponding core dimension.
    if "financial_report" not in result["parts"] and "sec_companyfacts" not in result["parts"]:
        fin_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_financial_us_analysis_indicator_em(symbol='{sym}', indicator='年报')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
        add_part(result, "us_financial_indicators", run_py(fin_code, timeout=45))
    if "valuation" not in result["parts"]:
        val_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_us_valuation_baidu(symbol='{sym}', indicator='市盈率(TTM)', period='近一年')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
        add_part(result, "valuation_pe_ttm", run_py(val_code, timeout=30))
    return finalize(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate fundamental snapshot without false-positive empty EIDs.")
    parser.add_argument("--market", required=True, choices=["A", "HK", "US"])
    parser.add_argument("--code", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.market == "A":
        result = a_share_fundamentals(args.code)
    elif args.market == "HK":
        result = hk_share_fundamentals(args.code)
    else:
        result = us_share_fundamentals(args.code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
