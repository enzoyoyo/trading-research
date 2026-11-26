#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from market_router import classify
except Exception:
    classify = None

DEFAULT_HERMES_MODEL = os.environ.get("HERMES_GROK_MODEL", "grok-4.3")
DEFAULT_HERMES_PROVIDER = os.environ.get("HERMES_GROK_PROVIDER", "xai-oauth")
DEFAULT_HERMES_TOOLSETS = os.environ.get("HERMES_LIVE_INTEL_TOOLSETS", "web")
HERMES_BIN = os.environ.get("HERMES_BIN") or shutil.which("hermes")
SEARCH_FALLBACK_SCRIPT = ROOT / "scripts" / "multi_source_search.py"


def require_hermes_bin() -> str:
    if HERMES_BIN:
        return HERMES_BIN
    raise RuntimeError("Hermes CLI not found; install `hermes`, add it to PATH, or set HERMES_BIN")


def market_of(query: str) -> dict[str, Any]:
    if classify:
        return classify(query)
    return {"query": query, "market": "unknown", "symbol": query, "confidence": "low", "reason": "router_unavailable"}


def build_query_plan(query: str, market: str | None = None) -> dict[str, Any]:
    routed = market_of(query)
    if market:
        routed["market"] = market.upper()
        routed["confidence"] = "manual"
    symbol = routed.get("symbol", query)
    mkt = routed.get("market", "unknown")
    common = [
        f"{query} {symbol} latest news last 72 hours stock bullish bearish catalyst",
        f"{query} {symbol} policy regulation macro interest rates liquidity inflation risk latest",
        f"{query} {symbol} earnings guidance announcement risk latest",
        f"{query} {symbol} valuation revenue profit cash flow latest quarter",
        f"{query} {symbol} longbridge realtime quote trading session kline volume turnover",
        f"{query} {symbol} ibkr portfolio position average cost cash margin order history read only",
    ]
    scoped = []
    sources = ["Hermes logged-in Grok/X", "Longbridge if available", "web_search/web_extract"]
    market_structure_vars: list[str] = []

    if mkt == "A":
        scoped = [
            f"{query} {symbol} 东方财富 同花顺 最新行情 涨跌幅 成交额 换手率 量比 今日",
            f"{query} {symbol} 涨停 跌停 炸板 连板 高度 龙虎榜 板块梯队 今日",
            f"{query} {symbol} 巨潮资讯 交易所公告 业绩预告 问询函 最新",
            f"{query} {symbol} 产业政策 监管 补贴 利率 流动性 A股",
            f"{query} {symbol} WindClaw 万得 个股速览 资金面 研报共识 公告 备用交叉验证",
            f"{query} {symbol} a-stock-data 腾讯行情 东财概念板块 巨潮公告 orgId 直连公开源交叉验证",
        ]
        sources = [*sources, "a_stock_data_bridge.py quote/concept/announcements/fund-flow", "WindClaw quote/data/document/reference if available"]
        market_structure_vars = ["limit_up_down_state", "dragon_tiger_list", "sector_ladder", "t_plus_one_constraint", "a_stock_data_bridge_cross_check", "windclaw_cross_check"]
    elif mkt == "HK":
        scoped = [
            f"{query} {symbol} HKEX announcement southbound flow latest",
            f"{query} {symbol} 港交所 披露易 南向资金 成交额 折价",
            f"{query} {symbol} 港股 流动性 南向 政策监管 宏观美元利率",
        ]
        market_structure_vars = ["southbound_flow", "hk_liquidity_gap", "disclosure_event"]
    elif mkt == "US":
        scoped = [
            f"{query} {symbol} SEC 10-Q 8-K earnings call guidance latest",
            f"{query} {symbol} supply chain capex order backlog lead time price increase",
            f"{query} {symbol} Put Wall Call Wall Gamma Flip Aggregate GEX implied volatility skew",
            f"{query} {symbol} TGA RRP net liquidity NFCI SOFR EFFR GDP recession probability CPI PCE VIX SKEW breadth fear greed latest",
            f"{query} {symbol} positioning leverage pair trade consensus premium passive ETF arbitrage creation redemption latest",
            f"{query} {symbol} IPO lockup secondary offering issuance debt raise index inclusion passive flow latest",
            f"{query} {symbol} secondary bull leader laggard catch-up rotation #2 beats #1 latest",
            f"{query} {symbol} VIX gold SPX AAPL long-end yield 30Y skew index GEX deleveraging liquidity squeeze latest",
            f"{query} {symbol} Fed rates macro liquidity dollar real yield inflation regulation policy",
        ]
        market_structure_vars = [
            "options_gamma_structure",
            "put_wall_call_wall_status",
            "gamma_flip_state",
            "deleveraging_cluster",
            "macro_dashboard_state",
            "positioning_crowding_state",
            "passive_flow_suction",
            "issuance_lockup_overhang",
            "rotation_regime",
            "narrative_stage",
            "vix_spike",
            "long_end_yield_shock",
            "index_gex_regime",
        ]

    return {
        "target": query,
        "routed": routed,
        "queries": common + scoped,
        "preferred_sources": sources,
        "fallback_search": {
            "policy": "plan_first_then_explicit_external_search; discovery_candidates_are_not_evidence",
            "script": "scripts/multi_source_search.py",
            "profiles": ["news", "filing", "rumor", "hot"],
            "privacy": "never_send_secrets_internal_hosts_or_unpublished_material; no_cookie_or_env_read",
        },
        "decision_variables": [
            "narrative_delta", "financial_validation", "price_volume_confirmation",
            "news_event_delta", "policy_regulatory_delta", "macro_dashboard_state",
            "macro_liquidity_regime", "positioning_crowding_state", "passive_flow_suction",
            "issuance_lockup_overhang", "rotation_regime", "narrative_stage",
            "x_frontline_clue", "x_identity_gate", "x_cross_check_gate", "x_time_gate",
            "longbridge_market_context", "ibkr_readonly_account_context",
            "crowding_risk", "event_window", *market_structure_vars,
            "falsifier", "position_impact",
        ],
        "grok_access_rule": "Use Hermes logged-in Grok via xai-oauth provider; never call grok2api/local gateway.",
        "search_evidence_rule": "Search titles/snippets/provider counts are discovery-only; fetch original URLs before Evidence Ledger grading.",
    }


def build_grok_prompt(query: str, plan: dict[str, Any]) -> str:
    return f"""你是交易实时情报搜索员。请使用 Hermes 当前已登录的 Grok / X / 全网搜索能力，围绕 {query} 做最新投研情报搜集。

查询计划：{json.dumps(plan, ensure_ascii=False)}

要求：
1. 分成 bullish / bearish / neutral；
2. 标注来源、时间、可信度；
3. 区分事实、传闻、观点；
4. 只保留会改变胜率、赔率、仓位、证伪条件的信息；
5. 必须单列新闻事件、政策/监管、宏观四象限、内生市场结构、微观基本面、Longbridge行情、WindClaw A股备用验证（A股适用）、IBKR只读账户上下文（无数据就写 unavailable）；
6. 宏观四象限必须按 liquidity / economy / inflation-rates / sentiment 分开写，并合成 `attack|neutral|defend`；
7. 内生市场结构必须按 narrative_stage / crowding / leverage / passive_flow / issuance_overhang / rotation_regime 分开写；
8. 如果涉及期权/Gamma结构，必须说明 Spot 与 Put Wall、Call Wall、Gamma Flip 的关系，以及是否属于“跌破 Put Wall 不接盘”的情形；
9. 如果出现 VIX、黄金、Apple、长端利率、Skew、指数 GEX 等多项共振，必须单列 `risk_regime`，判断这更像普通回撤、流动性挤兑，还是主动/被动杀杠杆；
10. 如果出现 IPO/secondary/lockup/mega financing/index inclusion/passive flow，必须单列这是 temporary flow 还是 structural overhang；
11. 明确需要用公告/财报/市场数据交叉验证的点；
12. X/Grok 线索必须按 `references/x-frontline-intelligence.md` 标注身份、时间、原创性、claim_type、交叉验证状态；未验证线索只能生成假设或提高观察优先级，不能提高仓位；
13. 输出中文，结构化；
14. 不要调用 grok2api，不要引用本地 Grok gateway，不要生成任何真实下单指令；
15. 结尾必须明确写：`非下单指令；IBKR 仅可作为只读账户上下文，未接入则 unavailable`。"""


def hermes_command(prompt: str, model: str, provider: str, toolsets: str = DEFAULT_HERMES_TOOLSETS) -> list[str]:
    # Keep live-intel child agents under the provider tool limit. Without an
    # explicit toolset, Hermes may load the full desktop profile (200+ tools)
    # and xAI rejects the request before Grok can answer.
    return [require_hermes_bin(), "chat", "-Q", "-t", toolsets, "--provider", provider, "-m", model, "-q", prompt]


def health(model: str, provider: str, timeout: float = 60.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(hermes_command("Return exactly: OK_HERMES_GROK_SEARCH_READY", model, provider), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "provider": provider, "model": model, "error": "hermes command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "provider": provider, "model": model, "error": "timeout"}
    output = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0 and "OK_HERMES_GROK_SEARCH_READY" in output, "provider": provider, "model": model, "returncode": proc.returncode, "output_tail": output[-800:]}


def ask_hermes_grok(query: str, plan: dict[str, Any], model: str, provider: str, timeout: float) -> dict[str, Any]:
    try:
        proc = subprocess.run(hermes_command(build_grok_prompt(query, plan), model, provider), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "provider": provider, "model": model, "error": "hermes command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "provider": provider, "model": model, "error": "timeout", "fallback": "use web_search/web_extract/market data skills"}
    return {
        "ok": proc.returncode == 0 and bool((proc.stdout or "").strip()),
        "provider": provider,
        "model": model,
        "returncode": proc.returncode,
        "content": (proc.stdout or "").strip(),
        "stderr_tail": (proc.stderr or "")[-800:] or None,
        "fallback": None if proc.returncode == 0 else "use web_search/web_extract/market data skills",
    }


def run_search_fallback(query: str, plan: dict[str, Any], timeout: float = 15.0, runner: Any = subprocess.run) -> dict[str, Any]:
    market = str((plan.get("routed") or {}).get("market") or "unknown")
    if market not in {"A", "HK", "US", "unknown"}:
        market = "unknown"
    command = [
        sys.executable,
        str(SEARCH_FALLBACK_SCRIPT),
        query,
        "--market", market,
        "--profile", "news",
        "--allow-external-search",
        "--timeout", str(max(1.0, min(timeout, 30.0))),
        "--json",
    ]
    try:
        proc = runner(command, capture_output=True, text=True, timeout=max(10.0, min(timeout + 5.0, 40.0)))
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "mode": "search_fallback_failed", "error": type(exc).__name__, "no_order_execution": True}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "mode": "search_fallback_failed",
            "error": "invalid_json",
            "returncode": proc.returncode,
            "stderr_tail": (proc.stderr or "")[-400:] or None,
            "no_order_execution": True,
        }
    if not isinstance(payload, dict):
        return {"ok": False, "mode": "search_fallback_failed", "error": "invalid_payload", "no_order_execution": True}
    payload["invoked_by"] = "live_intel_run_grok_failure"
    payload["no_order_execution"] = True
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="TSLA")
    parser.add_argument("--market", choices=["A", "HK", "US", "unknown"], default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--model", default=DEFAULT_HERMES_MODEL)
    parser.add_argument("--provider", default=DEFAULT_HERMES_PROVIDER)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--no-search-fallback", action="store_true", help="Do not run stateless multi-source fallback when Grok fails")
    args = parser.parse_args()
    if args.health:
        out = health(args.model, args.provider, timeout=min(args.timeout, 90.0))
    else:
        plan = build_query_plan(args.target, args.market)
        if args.plan_only:
            out = {
                "ok": True,
                "mode": "plan_only",
                "plan": plan,
                "hermes_grok_provider": args.provider,
                "hermes_grok_model": args.model,
                "hermes_command_preview": f"hermes chat -Q --provider {args.provider} -m {args.model} -q <prompt>",
                "prompt": build_grok_prompt(args.target, plan),
            }
        else:
            out = {
                "ok": False,
                "mode": "hermes_grok_live_intel",
                "plan": plan,
                "hermes_grok_health": health(args.model, args.provider, timeout=60.0),
                "no_order_execution": True,
            }
            if out["hermes_grok_health"].get("ok"):
                out["grok"] = ask_hermes_grok(args.target, plan, args.model, args.provider, args.timeout)
            else:
                out["grok"] = {"ok": False, "reason": "hermes_logged_in_grok_unavailable", "fallback": "multi_source_search"}
            if out["grok"].get("ok"):
                out["ok"] = True
            elif not args.no_search_fallback:
                out["search_fallback"] = run_search_fallback(args.target, plan, timeout=min(args.timeout, 15.0))
                out["ok"] = bool(out["search_fallback"].get("ok"))
            else:
                out["search_fallback"] = {"ok": False, "mode": "disabled", "data_gap": "live_intel_primary_failed_and_search_fallback_disabled", "no_order_execution": True}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
