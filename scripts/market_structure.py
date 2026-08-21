#!/usr/bin/env python3
"""trading-research · market_structure — 市场结构分析（按市场差异化）"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from datetime import datetime, timezone
from typing import Any

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from market_router import classify
except ImportError:
    classify = None

AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or sys.executable

def _no_proxy_env() -> dict:
    e = os.environ.copy()
    for v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        e[v] = ""
    e["no_proxy"] = "*"
    return e

def run_py(code: str, timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run([AKSHARE_PYTHON, "-c", code], capture_output=True, text=True, timeout=timeout, env=_no_proxy_env())
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error": "timeout"}
    return {"status": "pass" if proc.returncode == 0 else "fail", "stdout": proc.stdout or "", "stderr": proc.stderr or ""}

def a_share_structure(code: str) -> dict[str, Any]:
    """A股市场结构：涨停/炸板/连板高度/板块梯队/龙虎榜/情绪温度"""
    result = {"market": "A", "code": code, "fetched_at": datetime.now(timezone.utc).isoformat(), "parts": {}}
    today = datetime.now().strftime("%Y%m%d")
    struct_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
out = {{}}
try:
    zt = ak.stock_zt_pool_em(date='{today}')
    out['zt_count'] = len(zt)
    self_zt = zt[zt['代码'] == '{code}'] if not zt.empty else None
    out['is_zt'] = bool(self_zt is not None and not self_zt.empty)
    if out['is_zt']:
        row = self_zt.iloc[0].to_dict()
        out['zt_detail'] = {{k: str(v) for k, v in row.items()}}
except Exception as e:
    out['zt_error'] = str(e)
try:
    zb=ak.stock_zt_pool_zbgc_em(date='{today}')
    self_zb=zb[zb['代码']=='{code}'] if not zb.empty else None
    out['is_zb'] = bool(self_zb is not None and not self_zb.empty)
except Exception as e:
    out['zb_error']=str(e)
try:
    df=ak.stock_lhb_detail_em(start_date='20260501',end_date='{today}')
    match=df[df['代码']=='{code}']
    out['lhb_count']=len(match)
    out['lhb_latest']=match.iloc[-1][['交易日期','买方营业部','卖方营业部','净买额']].to_dict() if not match.empty else None
except Exception as e:
    out['lhb_error']=str(e)
print(json.dumps(out,ensure_ascii=False,default=str))
"""
    res = run_py(struct_code, timeout=45)
    if res["status"] == "pass":
        try:
            result["parts"]["structure"] = json.loads(res["stdout"])
        except Exception:
            result["parts"]["structure"] = {"raw": res["stdout"][:500]}
    else:
        result["parts"]["structure"] = {"error": res.get("stderr") or "unknown"}
    return result

def hk_share_structure(code: str) -> dict[str, Any]:
    """港股市场结构：南向资金、流动性"""
    result = {"market": "HK", "code": code, "fetched_at": datetime.now(timezone.utc).isoformat(), "parts": {}}
    south_code = """import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df=ak.stock_hsgt_fund_flow_summary_em()
    print(df.to_json(orient='records',force_ascii=False))
except Exception as e:
    print(json.dumps({'error':str(e)}))
"""
    res = run_py(south_code, timeout=30)
    if res["status"] == "pass":
        try:
            data = json.loads(res["stdout"])
            if isinstance(data, list):
                result["parts"]["southbound"] = {"count": len(data), "data": data}
        except Exception:
            result["parts"]["southbound"] = {"raw": res["stdout"][:500]}
    return result

def us_share_structure(code: str) -> dict[str, Any]:
    """美股市场结构：期权/Gamma/风险 Regime/盘前盘后"""
    result = {"market": "US", "code": code, "fetched_at": datetime.now(timezone.utc).isoformat(), "parts": {}}
    gamma_script = SCRIPTS / "options_gamma.py"
    regime_script = SCRIPTS / "risk_regime_snapshot.py"
    if gamma_script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(gamma_script), code, "--max-days", "45", "--json"],
                capture_output=True, text=True, timeout=45
            )
            if proc.returncode == 0:
                result["parts"]["gamma"] = json.loads(proc.stdout) if proc.stdout.strip() else {}
            else:
                result["parts"]["gamma"] = {"error": proc.stderr or "gamma failed"}
        except Exception as e:
            result["parts"]["gamma"] = {"error": str(e)}
    if regime_script.exists():
        try:
            proc = subprocess.run(
                [sys.executable, str(regime_script), code, "--market", "US", "--json"],
                capture_output=True, text=True, timeout=90
            )
            if proc.returncode == 0:
                parsed = json.loads(proc.stdout) if proc.stdout.strip() else {}
                result["parts"]["risk_regime"] = {
                    "risk_regime": parsed.get("risk_regime"),
                    "triggered_count": parsed.get("triggered_count"),
                    "triggered_signals": parsed.get("triggered_signals"),
                    "max_long_action_level": parsed.get("max_long_action_level"),
                    "regime_impact": parsed.get("regime_impact"),
                    "gex_mode": ((parsed.get("signals") or {}).get("index_gex_regime") or {}).get("mode"),
                }
            else:
                result["parts"]["risk_regime"] = {"error": proc.stderr or "risk regime failed"}
        except Exception as e:
            result["parts"]["risk_regime"] = {"error": str(e)}
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", default="TSLA")
    ap.add_argument("--market", choices=["A","HK","US","unknown"], default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    identity = classify(args.target) if classify else {"query": args.target, "market": args.market or "US", "symbol": args.target}
    mkt = args.market or identity.get("market", "US")
    code = identity.get("symbol", args.target)
    if mkt == "A":
        result = a_share_structure(code)
    elif mkt == "HK":
        result = hk_share_structure(code)
    else:
        result = us_share_structure(code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
