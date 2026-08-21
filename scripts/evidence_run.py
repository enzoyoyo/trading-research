#!/usr/bin/env python3
"""trading-research · evidence_run v10 — 统一证据管线。

v10 关键约束：
- argparse CLI，`--help` 不触发取数。
- `--no-store` / `--state-dir` 可控制 risk regime 历史落盘。
- 空 fundamentals 不生成 EID；只进入 Data Gap Ledger。
- 输出 commands_run / macro_dashboard / endogenous_market_structure / x_frontline_signals。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from market_router import classify
except ImportError:
    classify = None

LONGBRIDGE_BIN = os.environ.get("LONGBRIDGE_BIN") or shutil.which("longbridge")

def ensure_longbridge() -> str:
    if not LONGBRIDGE_BIN:
        raise RuntimeError("longbridge CLI not found; set LONGBRIDGE_BIN or install on PATH")
    return LONGBRIDGE_BIN

AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or "/opt/homebrew/bin/python3"
SECRET_RE = re.compile(r"(?i)(token|secret|api[_-]?key|access[_-]?token)[=:]\s*[^\s,]+")
COMMANDS_RUN: list[dict[str, Any]] = []
FILING_EID_BASE = 300
FILING_EID_MAX = 8


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scrub(text: str) -> str:
    return SECRET_RE.sub(r"\1=<redacted>", text or "")


def command_preview(cmd: list[str]) -> list[str]:
    return [scrub(str(part))[:500] for part in cmd]


def run_cmd(cmd: list[str], timeout: int = 30, env: dict | None = None) -> dict[str, Any]:
    started = now()
    record: dict[str, Any] = {"cmd": command_preview(cmd), "started_at": started, "timeout": timeout}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        record.update({"status": "fail", "error_class": "command_not_found"})
        COMMANDS_RUN.append(record)
        return {"cmd": cmd, "status": "fail", "started_at": started, "error": "command_not_found"}
    except subprocess.TimeoutExpired:
        record.update({"status": "fail", "error_class": "timeout"})
        COMMANDS_RUN.append(record)
        return {"cmd": cmd, "status": "fail", "started_at": started, "error": "timeout"}
    status = "pass" if proc.returncode == 0 else "fail"
    stdout = scrub(proc.stdout or "")
    stderr = scrub(proc.stderr or "")
    record.update({
        "status": status,
        "returncode": proc.returncode,
        "stdout_tail": stdout[-500:] if stdout else "",
        "stderr_tail": stderr[-800:] if stderr else "",
    })
    COMMANDS_RUN.append(record)
    return {"cmd": cmd, "status": status, "returncode": proc.returncode, "started_at": started, "stdout": stdout, "stderr": stderr}


def parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def summarize_json(obj: Any) -> Any:
    if isinstance(obj, list):
        return [summarize_json(x) for x in obj[:3]]
    if isinstance(obj, dict):
        keep = {}
        for k, v in obj.items():
            if any(s in str(k).lower() for s in ("token", "secret", "key", "account", "member")):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                keep[k] = v
            elif isinstance(v, dict):
                keep[k] = summarize_json(v)
            elif isinstance(v, list):
                keep[k] = summarize_json(v)
            if len(keep) >= 24:
                break
        return keep
    return obj


def eid(
    n: int,
    typ: str,
    source: str,
    raw_fact: str,
    variable: str = "trading_confirmation",
    direction: str = "Neutral",
    reliability: float = 0.7,
) -> dict[str, Any]:
    return {
        "eid": f"E{n}",
        "type": typ,
        "source": source,
        "timestamp": now(),
        "raw_fact": raw_fact,
        "variable": variable,
        "direction": direction,
        "strength": 0,
        "reliability": reliability,
        "freshness": 1.0,
        "cross_check": [],
        "decision_impact": "unchanged",
    }


def source_health(source: str, status: str, safe_summary: Any, *, error_class: str | None = None) -> dict[str, Any]:
    out = {"source": source, "status": status, "checked_at": now(), "safe_summary": safe_summary}
    if error_class:
        out["error_class"] = error_class
    return out


def normalize_child_health(row: dict[str, Any]) -> dict[str, Any]:
    """Complete child SourceHealth rows without exposing raw request metadata."""
    normalized = dict(row)
    normalized.setdefault("checked_at", now())
    normalized.setdefault("source_family", normalized.get("provider_family") or "unknown")
    normalized.setdefault(
        "safe_summary",
        {
            "status": normalized.get("status"),
            "error_class": normalized.get("error_class"),
        },
    )
    return normalized


def fundamental_source_family_flag(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Cap claims by canonical underlying families, never transport labels."""
    families: set[str] = set()
    for row in evidence:
        if not isinstance(row, dict) or row.get("type") not in {"financial", "filing"}:
            continue
        family = str(row.get("source_family") or "").strip()
        if family and family != "mixed" and not family.startswith("unknown"):
            families.add(family)
    if not families or len(families) >= 3:
        return None
    cap = "L0" if len(families) == 1 else "L1"
    return {
        "type": "single_source_family" if len(families) == 1 else "insufficient_source_families",
        "source_family": next(iter(families)) if len(families) == 1 else None,
        "source_families": sorted(families),
        "independent_source_count": len(families),
        "max_action_cap": cap,
        "impact": "传输路径、多份申报或聚合重述不构成独立交叉验证",
    }


def unknown_source_family_flag(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    affected: list[str] = []
    for row in evidence:
        if not isinstance(row, dict) or row.get("type") not in {"financial", "filing"}:
            continue
        family = str(row.get("source_family") or "").strip()
        if not family or family == "mixed" or family.startswith("unknown"):
            eid_value = str(row.get("eid") or "").strip()
            if eid_value:
                affected.append(eid_value)
    if not affected:
        return None
    return {
        "type": "unknown_source_family",
        "evidence_refs": list(dict.fromkeys(affected)),
        "max_action_cap": "L0",
        "impact": "未知底层来源不得制造独立证据票数",
    }


def build_red_flags(
    regime_snapshot: dict[str, Any] | None,
    gaps: list[dict[str, Any]],
    fundamental_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    red_flags: list[dict[str, Any]] = []
    if isinstance(regime_snapshot, dict) and regime_snapshot.get("risk_regime") in {
        "deleveraging_watch", "active_deleveraging", "forced_liquidation"
    }:
        red_flags.append({
            "type": "risk_regime_override",
            "risk_regime": regime_snapshot.get("risk_regime"),
            "max_long_action_level": regime_snapshot.get("max_long_action_level"),
            "regime_impact": regime_snapshot.get("regime_impact"),
        })
    if any(g.get("error_class") == "empty_fundamentals" or "基本面" in g.get("gap", "") for g in gaps):
        red_flags.append({"type": "fundamental_gap", "max_action_cap": "no_fundamentals_eid", "impact": "不得把基本面写成已验证"})
    unknown_flag = unknown_source_family_flag(fundamental_evidence)
    if unknown_flag:
        red_flags.append(unknown_flag)
    family_flag = fundamental_source_family_flag(fundamental_evidence)
    if family_flag:
        red_flags.append(family_flag)
    return red_flags


def suggested_module_signals(
    red_flags: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    as_of: str,
) -> list[dict[str, Any]]:
    """Map source-coverage defects to the existing data_quality contract."""
    coverage_flags = [
        row for row in red_flags
        if isinstance(row, dict)
        and row.get("type") in {"single_source_family", "insufficient_source_families", "unknown_source_family"}
    ]
    if not coverage_flags:
        return []
    caps = [str(row.get("max_action_cap")) for row in coverage_flags]
    max_action_level = "L0" if "L0" in caps else "L1"
    evidence_refs = list(dict.fromkeys(
        str(row.get("eid"))
        for row in evidence
        if isinstance(row, dict)
        and row.get("type") in {"financial", "filing"}
        and row.get("eid")
    ))
    try:
        observed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        observed = datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    else:
        observed = observed.astimezone(timezone.utc)
    return [{
        "module": "data_quality",
        "sub_framework": "source_coverage",
        "max_action_level": max_action_level,
        "position_multiplier": 0.0 if max_action_level == "L0" else 0.5,
        "hard_veto": False,
        "evidence_refs": evidence_refs,
        "observed_at": observed.isoformat(),
        "stale_after": (observed + timedelta(hours=24)).isoformat(),
        "reason": ";".join(str(row.get("type")) for row in coverage_flags),
        "repair_signal": "collect_independent_underlying_source_families",
        "tighten_only": True,
        "cannot_raise_upstream": True,
        "no_order_execution": True,
    }]


# ── longbridge ─────────────────────

def longbridge_quote(identity: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    sym = identity.get("longbridge_symbol")
    if not sym:
        return source_health("LongBridge", "skipped", "no longbridge symbol"), None, {"gap": "LongBridge symbol unavailable", "impact": "无法抓实时行情", "fallback": "AkShare/web 兜底", "severity": "medium"}
    cmd = [ensure_longbridge(), "quote", str(sym), "--format", "json"]
    res = run_cmd(cmd, timeout=30)
    if res.get("status") != "pass":
        return source_health("LongBridge", "fail", res.get("error") or res.get("stderr") or "quote failed", error_class=res.get("error") or "subprocess_fail"), None, {"gap": "LongBridge quote failed", "impact": "行情新鲜度下降", "fallback": "AkShare/Yahoo/web", "severity": "high"}
    parsed = parse_json(res.get("stdout", ""))
    summary = summarize_json(parsed) if parsed is not None else res.get("stdout", "")[:800]
    return source_health("LongBridge", "ok", summary), eid(1, "quote", f"LongBridge {sym}", json.dumps(summary, ensure_ascii=False)[:1200]), None


# ── akshare (绕代理) ───────────────

def _no_proxy_env() -> dict:
    e = os.environ.copy()
    for v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        e[v] = ""
    e["no_proxy"] = "*"
    return e


def akshare_a_quote(code: str, exchange: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    results: dict[str, Any] = {"source": "AkShare-A", "status": "partial", "checked_at": now(), "parts": {}}
    eids: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    snap_code = f"""import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    df = ak.stock_zh_a_spot_em()
    row = df[df['代码'] == '{code}']
    print(row.to_json(orient='records', force_ascii=False) if not row.empty else '[]')
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    snap = run_cmd([AKSHARE_PYTHON, "-c", snap_code], timeout=30, env=_no_proxy_env())
    if snap.get("status") == "pass" and snap.get("stdout", "").strip() not in ("", "[]") and '"error"' not in snap.get("stdout", "")[:40]:
        results["parts"]["snapshot"] = "ok"
        eids.append(eid(2, "quote", "AkShare spot", snap["stdout"][:800], variable="trading_confirmation"))
    else:
        gaps.append({"gap": "A-share spot unavailable", "impact": "盘中结构判断受限", "fallback": "LongBridge/web", "severity": "high"})

    hist_code = f"""import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    df = ak.stock_zh_a_hist(symbol='{code}', period='daily', start_date='20260301', end_date='{datetime.now().strftime('%Y%m%d')}', adjust='qfq')
    print(df.tail(10).to_json(orient='records', force_ascii=False))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    hist = run_cmd([AKSHARE_PYTHON, "-c", hist_code], timeout=30, env=_no_proxy_env())
    if hist.get("status") == "pass" and hist.get("stdout", "").strip() not in ("", "[]") and '"error"' not in hist.get("stdout", "")[:40]:
        results["parts"]["kline"] = "ok"
        eids.append(eid(3, "kline", "AkShare hist", hist["stdout"][:800], variable="trend_structure"))
    else:
        gaps.append({"gap": "A-share kline unavailable", "impact": "K线结构无法判断", "fallback": "LongBridge kline", "severity": "medium"})

    mkt = "sz" if exchange in ("SZ", "SZSE") else "sh"
    flow_code = f"""import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    df = ak.stock_individual_fund_flow(stock='{code}', market='{mkt}')
    print(df.tail(10).to_json(orient='records', force_ascii=False))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    flow = run_cmd([AKSHARE_PYTHON, "-c", flow_code], timeout=30, env=_no_proxy_env())
    if flow.get("status") == "pass" and flow.get("stdout", "").strip() not in ("", "[]") and '"error"' not in flow.get("stdout", "")[:40]:
        results["parts"]["fund_flow"] = "ok"
        eids.append(eid(4, "fund_flow", "AkShare fund_flow", flow["stdout"][:800], variable="money_flow_confirmation"))
    else:
        gaps.append({"gap": "A-share fund flow unavailable", "impact": "资金流向缺失", "fallback": "东方财富/web", "severity": "medium"})

    today = datetime.now().strftime("%Y%m%d")
    zt_code = f"""import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    zt = ak.stock_zt_pool_em(date='{today}')
    zb = ak.stock_zt_pool_zbgc_em(date='{today}')
    zt_match = zt[zt['代码'] == '{code}'] if not zt.empty else None
    zb_match = zb[zb['代码'] == '{code}'] if not zb.empty else None
    print(json.dumps({{"zt": zt_match is not None and not zt_match.empty, "zb": zb_match is not None and not zb_match.empty}}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    zt = run_cmd([AKSHARE_PYTHON, "-c", zt_code], timeout=30, env=_no_proxy_env())
    if zt.get("status") == "pass" and '"error"' not in zt.get("stdout", "")[:40]:
        results["parts"]["zt_pool"] = "ok"
        eids.append(eid(5, "sentiment", "AkShare zt_pool", zt["stdout"][:300], variable="crowding_risk", direction="Bull" if '"zt": true' in (zt.get("stdout") or "") else "Neutral"))
    else:
        gaps.append({"gap": "A-share zt/zb pool unavailable", "impact": "涨停结构无法判断", "fallback": "同花顺/web", "severity": "medium"})

    lhb_code = f"""import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    df = ak.stock_lhb_detail_em(start_date='20260501', end_date='{datetime.now().strftime('%Y%m%d')}')
    match = df[df['代码'] == '{code}']
    print(match.tail(20).to_json(orient='records', force_ascii=False) if not match.empty else '[]')
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    lhb = run_cmd([AKSHARE_PYTHON, "-c", lhb_code], timeout=30, env=_no_proxy_env())
    if lhb.get("status") == "pass" and lhb.get("stdout", "").strip() not in ("", "[]") and '"error"' not in lhb.get("stdout", "")[:40]:
        results["parts"]["lhb"] = "ok"
        eids.append(eid(6, "sentiment", "AkShare lhb", lhb["stdout"][:800], variable="dragon_tiger_list"))
    else:
        gaps.append({"gap": "A-share dragon-tiger list empty or unavailable", "impact": "近一月无龙虎榜记录不代表缺失", "fallback": "东方财富/web", "severity": "low"})

    if not results["parts"]:
        results["status"] = "fail"
    return results, eids, gaps


def akshare_hk_quote(code: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    results: dict[str, Any] = {"source": "AkShare-HK", "status": "partial", "checked_at": now(), "parts": {}}
    eids: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    south_code = """import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    df = ak.stock_hsgt_fund_flow_summary_em()
    print(df.to_json(orient='records', force_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=False))
"""
    south = run_cmd([AKSHARE_PYTHON, "-c", south_code], timeout=30, env=_no_proxy_env())
    if south.get("status") == "pass" and '"error"' not in south.get("stdout", "")[:40]:
        results["parts"]["southbound"] = "ok"
        eids.append(eid(10, "fund_flow", "AkShare southbound", south["stdout"][:800], variable="southbound_flow"))
    else:
        gaps.append({"gap": "southbound flow unavailable", "impact": "南向情绪缺位", "fallback": "港交所/web", "severity": "medium"})

    hk_hist = f"""import warnings; warnings.filterwarnings('ignore')
import akshare as ak
import json
try:
    df = ak.stock_hk_hist(symbol='{code}', period='daily', start_date='20260301', end_date='{datetime.now().strftime('%Y%m%d')}', adjust='qfq')
    print(df.tail(10).to_json(orient='records', force_ascii=False))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
"""
    hist = run_cmd([AKSHARE_PYTHON, "-c", hk_hist], timeout=30, env=_no_proxy_env())
    if hist.get("status") == "pass" and hist.get("stdout", "").strip() not in ("", "[]") and '"error"' not in hist.get("stdout", "")[:40]:
        results["parts"]["kline"] = "ok"
        eids.append(eid(11, "kline", "AkShare hk_hist", hist["stdout"][:800], variable="trend_structure"))
    else:
        gaps.append({"gap": "HK kline unavailable", "impact": "K线结构缺失", "fallback": "LongBridge kline", "severity": "medium"})

    if not results["parts"]:
        results["status"] = "fail"
    return results, eids, gaps


# ── live intel ─────────────────────

def live_plan(target: str) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    cmd = [sys.executable, str(SCRIPTS / "live_intel_run.py"), target, "--plan-only"]
    res = run_cmd(cmd, timeout=20)
    if res.get("status") != "pass":
        return source_health("live_intel_run", "fail", res.get("stderr") or "plan failed", error_class=res.get("error") or "subprocess_fail"), {"gap": "live_intel plan failed", "impact": "实时情报检索计划缺失", "fallback": "手动 web_search", "severity": "medium"}, []
    parsed = parse_json(res.get("stdout", ""))
    plan = parsed.get("plan", {}) if isinstance(parsed, dict) else {}
    x_frontline_stub = [{
        "source": "Hermes Grok/X",
        "status": "plan_only",
        "author_identity": "unknown",
        "identity_confidence": "low",
        "posted_at": "unknown",
        "observed_at": now(),
        "originality": "unknown",
        "claim_type": "unknown",
        "decision_variable": "x_frontline_clue",
        "cross_check_eids": [],
        "reliability_ceiling": 0.5,
        "decision_impact": "needs_verification",
    }]
    return source_health("live_intel_run", "ok", {"queries": len(plan.get("queries", [])), "x_frontline_rule": "clue_only_needs_identity_crosscheck_time"}), None, x_frontline_stub


# ── WindClaw A-share backup ───────

def windclaw_health(identity: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if identity.get("market") != "A":
        return None, None
    cmd = [sys.executable, str(SCRIPTS / "windclaw_bridge.py"), "health", "--json"]
    res = run_cmd(cmd, timeout=10)
    parsed = parse_json(res.get("stdout", "")) if res.get("status") == "pass" else None
    if isinstance(parsed, dict) and parsed.get("ok"):
        return source_health("WindClaw", "ok", {"session": parsed.get("session"), "session_source": parsed.get("session_source"), "native_mcp_servers": parsed.get("native_mcp_servers"), "tools": parsed.get("tools")}), None
    return source_health("WindClaw", "fail", summarize_json(parsed) if parsed else (res.get("stderr") or res.get("error") or "WindClaw unavailable"), error_class=res.get("error") or "unavailable"), {"gap": "WindClaw A-share backup unavailable", "impact": "A股 Wind 备用交叉验证缺失", "fallback": "LongBridge/AkShare/web_search", "severity": "low"}


# ── options/gamma ──────────────────

def options_gamma(identity: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if identity.get("market") != "US":
        return None, None, None
    symbol = str(identity.get("symbol", "")).replace(".US", "")
    cmd = [sys.executable, str(SCRIPTS / "options_gamma.py"), symbol, "--max-days", "45", "--json"]
    res = run_cmd(cmd, timeout=45)
    if res.get("status") != "pass":
        return source_health("CBOE gamma", "fail", res.get("stderr") or "gamma failed", error_class=res.get("error") or "subprocess_fail"), None, {"gap": "CBOE gamma unavailable", "impact": "美股期权结构缺口", "fallback": "券商/web", "severity": "medium"}
    parsed = parse_json(res.get("stdout", ""))
    summary = summarize_json(parsed) if parsed is not None else res.get("stdout", "")[:800]
    return source_health("CBOE gamma", "ok", summary), eid(20, "gamma", "options_gamma.py", json.dumps(summary, ensure_ascii=False)[:1200], variable="execution_window", reliability=0.75), None


# ── risk regime snapshot ───────────

def risk_regime_snapshot(identity: dict[str, Any], target: str, *, no_store: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if identity.get("market") not in {"US", "HK"}:
        return None, None, None, None
    cmd = [sys.executable, str(SCRIPTS / "risk_regime_snapshot.py"), target, "--market", identity.get("market", "US"), "--json"]
    if no_store:
        cmd.append("--no-store")
    res = run_cmd(cmd, timeout=90)
    if res.get("status") != "pass":
        return source_health("risk_regime_snapshot", "fail", res.get("stderr") or "risk regime failed", error_class=res.get("error") or "subprocess_fail"), None, {"gap": "risk regime snapshot unavailable", "impact": "无法自动识别杀杠杆/流动性踩踏 regime", "fallback": "手动检查 VIX/黄金/AAPL/利率/Skew/GEX", "severity": "high"}, None
    parsed = parse_json(res.get("stdout", ""))
    if not isinstance(parsed, dict):
        return source_health("risk_regime_snapshot", "fail", "non-json output", error_class="parse_fail"), None, {"gap": "risk regime snapshot parse failed", "impact": "无法把系统性风险并入证据链", "fallback": "手动写 regime", "severity": "high"}, None
    summary = {
        "risk_regime": parsed.get("risk_regime"),
        "triggered_count": parsed.get("triggered_count"),
        "triggered_signals": parsed.get("triggered_signals", [])[:4],
        "max_long_action_level": parsed.get("max_long_action_level"),
        "position_multiplier": parsed.get("position_multiplier_band") or parsed.get("position_multiplier"),
    }
    direction = "Bear" if parsed.get("risk_regime") in {"deleveraging_watch", "active_deleveraging", "forced_liquidation"} else "Neutral"
    gap = None
    if parsed.get("data_gaps"):
        gap = {"gap": "risk regime snapshot has partial gaps", "impact": "GEX 等系统信号部分只能给 proxy 描述", "fallback": "保守处理仓位与动作等级", "severity": "medium"}
    return source_health("risk_regime_snapshot", "ok", summary), eid(21, "risk_regime", "risk_regime_snapshot.py", json.dumps(summary, ensure_ascii=False)[:1200], variable="risk_regime", direction=direction, reliability=0.82), gap, parsed


# ── method weights ─────────────────

def method_weights(identity: dict[str, Any], target: str) -> dict[str, Any]:
    market = identity.get("market") if identity.get("market") in {"A", "HK", "US"} else "unknown"
    cmd = [sys.executable, str(SCRIPTS / "method_router.py"), "--market", market, "--theme", target]
    res = run_cmd(cmd, timeout=10)
    return parse_json(res.get("stdout", "")) if res.get("status") == "pass" else {"ok": False, "raw": summarize_json(res)}


# ── fundamentals snapshot ──────────

def fundamental_snapshot(identity: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    market = identity.get("market") if identity.get("market") in {"A", "HK", "US"} else "US"
    symbol = str(identity.get("symbol", "") or identity.get("query", ""))
    cmd = [sys.executable, str(SCRIPTS / "fundamental_snapshot.py"), "--market", market, "--code", symbol, "--json"]
    res = run_cmd(cmd, timeout=240 if market == "US" else 70)
    parsed = parse_json(res.get("stdout", "")) if res.get("stdout") else None
    if isinstance(parsed, dict):
        status = parsed.get("status")
        parts = parsed.get("parts") or {}
        summary = summarize_json(parsed)
        if status in {"ok", "partial"} and parts:
            if market != "US":
                return [source_health("fundamental_snapshot", status, summary)], [
                    eid(30, "financial", "fundamental_snapshot.py", json.dumps(summary, ensure_ascii=False)[:1200], variable="financial_validation", reliability=0.78)
                ], []

            raw_child_health = parsed.get("source_health")
            child_health = raw_child_health if isinstance(raw_child_health, list) else []
            health = [normalize_child_health(row) for row in child_health if isinstance(row, dict)]
            if not health:
                health = [source_health("fundamental_snapshot", status, summary)]

            evidence_items: list[dict[str, Any]] = []
            core_keys = {
                "financial_report",
                "valuation",
                "sec_companyfacts",
                "us_financial_indicators",
                "valuation_pe_ttm",
            }
            core_parts = {key: value for key, value in parts.items() if key in core_keys}
            if core_parts:
                family_by_key = {
                    "us_financial_indicators": "akshare_eastmoney",
                    "valuation_pe_ttm": "akshare_baidu",
                }
                core_families = sorted({
                    str(value.get("source_family") or family_by_key.get(key) or "unknown_aggregator")
                    for key, value in core_parts.items()
                    if isinstance(value, dict)
                })
                financial = eid(
                    30,
                    "financial",
                    "LongBridge/SEC via fundamental_snapshot.py",
                    json.dumps(summarize_json(core_parts), ensure_ascii=False)[:1200],
                    variable="financial_validation",
                    reliability=0.82,
                )
                financial.update({
                    "claim_type": "reported_metric",
                    "source_kind": "normalized_company_fundamentals",
                    "provider_family": "normalized_company_fundamentals",
                    "source_family": core_families[0] if len(core_families) == 1 else "mixed",
                    "source_families": core_families,
                    "underlying_fact_key": None,
                    "independent_source_count": 1,
                })
                evidence_items.append(financial)

            seen_facts: set[str] = set()
            regulatory = parsed.get("regulatory_evidence") if isinstance(parsed.get("regulatory_evidence"), list) else []
            traceable: list[dict[str, Any]] = []
            accession_missing_count = 0
            for raw in regulatory:
                if not isinstance(raw, dict):
                    continue
                fact_key = raw.get("underlying_fact_key")
                accession = raw.get("accession_number")
                if not fact_key or not accession:
                    accession_missing_count += 1
                    continue
                if str(fact_key) in seen_facts:
                    continue
                seen_facts.add(str(fact_key))
                traceable.append(raw)
            traceable.sort(
                key=lambda row: (
                    str(row.get("filed_at") or row.get("provider_published_at") or ""),
                    str(row.get("accession_number") or ""),
                ),
                reverse=True,
            )
            for offset, raw in enumerate(traceable[:FILING_EID_MAX]):
                fact_key = raw["underlying_fact_key"]
                item = eid(
                    FILING_EID_BASE + offset,
                    "filing",
                    "SEC EDGAR via " + "+".join(raw.get("retrieval_paths") or ["longbridge_cli"]),
                    json.dumps(summarize_json(raw), ensure_ascii=False)[:1200],
                    variable="official_disclosure",
                    reliability=0.94,
                )
                item.update({
                    "claim_type": "fact",
                    "source_speaker": "regulator",
                    "verification_status": "verified",
                    "evidence_category": "verified_fact",
                    "provider_family": raw.get("provider_family") or raw.get("provider") or "unknown",
                    "source_family": raw.get("source_family") or "sec_edgar",
                    "underlying_fact_key": fact_key,
                    "accession_number": raw.get("accession_number"),
                    "form_type": raw.get("form_type"),
                    "document_url": raw.get("document_url"),
                    "retrieval_paths": list(raw.get("retrieval_paths") or []),
                    "independent_source_count": int(raw.get("independent_source_count") or 1),
                })
                evidence_items.append(item)
            raw_child_gaps = parsed.get("gaps")
            child_gaps = raw_child_gaps if isinstance(raw_child_gaps, list) else []
            gaps = [row for row in child_gaps if isinstance(row, dict)]
            if accession_missing_count:
                gaps.append({
                    "gap": "regulatory filings lack accession-level lineage",
                    "error_class": "accession_missing",
                    "impact": "不得生成官方 filing EID",
                    "severity": "high",
                    "count": accession_missing_count,
                })
            if len(traceable) > FILING_EID_MAX:
                gaps.append({
                    "gap": "regulatory filing evidence truncated to bounded EID range",
                    "error_class": "filing_eid_limit",
                    "impact": "其余申报保留在原始快照，不进入本次 evidence ledger",
                    "severity": "low",
                    "truncated_count": len(traceable) - FILING_EID_MAX,
                })
            return health, evidence_items, gaps
        return [source_health("fundamental_snapshot", "fail", summary, error_class="empty_fundamentals")], [], [{"gap": "基本面数据缺失或为空", "impact": "不得生成 fundamentals EID；估值无锚", "fallback": "LongBridge financial-report/valuation 或 SEC/IR/web", "severity": "high", "source_summary": summary, "error_class": "empty_fundamentals"}]
    return [source_health("fundamental_snapshot", "fail", res.get("stderr") or res.get("error") or "fundamentals unavailable", error_class=res.get("error") or "parse_fail")], [], [{"gap": "基本面数据缺失", "impact": "估值无锚", "fallback": "LongBridge/SEC/IR/web", "severity": "high", "error_class": res.get("error") or "parse_fail"}]


# ── identity ───────────────────────

def resolve_identity(target: str, market: str | None) -> dict[str, Any]:
    identity = classify(target) if classify else {"query": target, "market": "unknown", "symbol": target}
    if market and market != "auto":
        identity["market"] = market
        identity["confidence"] = "manual"
    return identity


# ── main ───────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a reproducible trading-research evidence archive.")
    parser.add_argument("target", nargs="?", default="TSLA")
    parser.add_argument("--market", choices=["auto", "A", "HK", "US"], default="auto")
    parser.add_argument("--json", action="store_true", help="Accepted for compatibility; output is always JSON.")
    parser.add_argument("--no-store", action="store_true", help="Pass --no-store to risk_regime_snapshot to avoid history writes.")
    parser.add_argument("--state-dir", help="Set TRADING_RESEARCH_STATE_DIR for child scripts.")
    parser.add_argument("--source-tier", choices=["auto", "mcp", "cli", "sdk"], default="auto")
    parser.add_argument("--timeout", type=int, default=120, help="Reserved global timeout hint for future sources.")
    parser.add_argument("--fail-on-empty-fundamentals", action="store_true")
    args = parser.parse_args()

    if args.state_dir:
        os.environ["TRADING_RESEARCH_STATE_DIR"] = args.state_dir

    identity = resolve_identity(args.target, None if args.market == "auto" else args.market)
    data_sources: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    x_frontline_signals: list[dict[str, Any]] = []
    regime_snapshot: dict[str, Any] | None = None

    lb_health, lb_eid, lb_gap = longbridge_quote(identity)
    data_sources.append(lb_health)
    if lb_eid:
        evidence.append(lb_eid)
    if lb_gap:
        gaps.append(lb_gap)

    mkt = identity.get("market", "unknown")
    if mkt == "A":
        ak_health, ak_eids, ak_gaps = akshare_a_quote(identity["symbol"], identity.get("exchange", "SH"))
        data_sources.append(ak_health)
        evidence.extend(ak_eids)
        gaps.extend(ak_gaps)
        wc_health, wc_gap = windclaw_health(identity)
        if wc_health:
            data_sources.append(wc_health)
        if wc_gap:
            gaps.append(wc_gap)
    elif mkt == "HK":
        ak_health, ak_eids, ak_gaps = akshare_hk_quote(identity["symbol"])
        data_sources.append(ak_health)
        evidence.extend(ak_eids)
        gaps.extend(ak_gaps)

    plan_health, plan_gap, x_plan = live_plan(args.target)
    data_sources.append(plan_health)
    x_frontline_signals.extend(x_plan)
    if plan_gap:
        gaps.append(plan_gap)

    gamma_health, gamma_eid, gamma_gap = options_gamma(identity)
    if gamma_health:
        data_sources.append(gamma_health)
    if gamma_eid:
        evidence.append(gamma_eid)
    if gamma_gap:
        gaps.append(gamma_gap)

    regime_health, regime_eid, regime_gap, regime_snapshot = risk_regime_snapshot(identity, args.target, no_store=args.no_store)
    if regime_health:
        data_sources.append(regime_health)
    if regime_eid:
        evidence.append(regime_eid)
    if regime_gap:
        gaps.append(regime_gap)

    fund_health, fund_evidence, fund_gaps = fundamental_snapshot(identity)
    data_sources.extend(fund_health)
    evidence.extend(fund_evidence)
    gaps.extend(fund_gaps)

    methods = method_weights(identity, args.target)

    provenance_evidence = fund_evidence if identity.get("market") == "US" else []
    red_flags = build_red_flags(regime_snapshot, gaps, provenance_evidence)
    module_signals = suggested_module_signals(red_flags, provenance_evidence, as_of=now())

    status = "evidence_ready" if evidence and not gaps else ("partial_evidence" if evidence else "draft_context_only")
    output = {
        "schema_version": "evidence_archive.v2",
        "artifact_type": "evidence_archive",
        "target": args.target,
        "generated_at": now(),
        "identity": identity,
        "requested_source_tier": args.source_tier,
        "commands_run": COMMANDS_RUN,
        "data_sources": data_sources,
        "macro_dashboard": {},
        "endogenous_market_structure": {},
        "x_frontline_signals": x_frontline_signals,
        "evidence_ledger": evidence,
        "hypothesis_ledger": [],
        "conflict_ledger": [],
        "method_signal_matrix": methods,
        "risk_regime_snapshot": regime_snapshot,
        "position_cap_calc": {"status": "requires_decision_compiler", "base_rule": "apply red lines before weighted score"},
        "critical_gaps": gaps,
        "red_flags": red_flags,
        "suggested_module_signals": module_signals,
        "report_contract_status": status,
        "no_order_execution": True,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.fail_on_empty_fundamentals and not any(item.get("type") == "financial" for item in fund_evidence):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
