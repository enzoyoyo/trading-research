#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Raw importance scores. normalize() converts them to 100-point weights.
# Keep fields explicit so references/method-rotation-matrix.md and runtime output do not drift.
BASE = {
    "A_short": {"huayuan": 5, "serenity": 0, "youzi_emotion": 34, "wyckoff": 24, "livermore": 8, "factor": 2, "poisson": 8, "options_gamma": 0, "supply_chain_xray": 0, "early_stage_quality": 0, "endogenous_microstructure": 3, "macro_policy_news_account": 8, "counter_consensus": 3, "expected_returns": 1, "bottleneck_scorecard": 0, "a_share_short_term": 15},
    "A_swing": {"huayuan": 14, "serenity": 3, "youzi_emotion": 10, "wyckoff": 18, "livermore": 14, "factor": 3, "poisson": 7, "options_gamma": 0, "supply_chain_xray": 2, "early_stage_quality": 1, "endogenous_microstructure": 5, "macro_policy_news_account": 12, "counter_consensus": 5, "expected_returns": 4, "bottleneck_scorecard": 2, "a_share_short_term": 7},
    "A_fundamental": {"huayuan": 24, "serenity": 4, "youzi_emotion": 3, "wyckoff": 10, "livermore": 6, "factor": 14, "poisson": 6, "options_gamma": 0, "supply_chain_xray": 3, "early_stage_quality": 2, "endogenous_microstructure": 3, "macro_policy_news_account": 12, "counter_consensus": 6, "expected_returns": 7, "bottleneck_scorecard": 3, "a_share_short_term": 2},
    "HK": {"huayuan": 18, "serenity": 7, "youzi_emotion": 3, "wyckoff": 12, "livermore": 10, "factor": 7, "poisson": 6, "options_gamma": 3, "supply_chain_xray": 3, "early_stage_quality": 2, "endogenous_microstructure": 6, "macro_policy_news_account": 22, "counter_consensus": 6, "expected_returns": 5, "bottleneck_scorecard": 3, "a_share_short_term": 0},
    "US_bigtech": {"huayuan": 14, "serenity": 14, "youzi_emotion": 0, "wyckoff": 9, "livermore": 9, "factor": 6, "poisson": 5, "options_gamma": 6, "supply_chain_xray": 4, "early_stage_quality": 2, "endogenous_microstructure": 10, "macro_policy_news_account": 18, "counter_consensus": 4, "expected_returns": 5, "bottleneck_scorecard": 3, "a_share_short_term": 0},
    "US_supply_chain": {"huayuan": 8, "serenity": 22, "youzi_emotion": 0, "wyckoff": 10, "livermore": 6, "factor": 3, "poisson": 8, "options_gamma": 6, "supply_chain_xray": 12, "early_stage_quality": 6, "endogenous_microstructure": 8, "macro_policy_news_account": 12, "counter_consensus": 5, "expected_returns": 4, "bottleneck_scorecard": 8, "a_share_short_term": 0},
    "US_early_theme": {"huayuan": 6, "serenity": 16, "youzi_emotion": 0, "wyckoff": 7, "livermore": 5, "factor": 2, "poisson": 12, "options_gamma": 4, "supply_chain_xray": 14, "early_stage_quality": 20, "endogenous_microstructure": 8, "macro_policy_news_account": 12, "counter_consensus": 8, "expected_returns": 5, "bottleneck_scorecard": 8, "a_share_short_term": 0},
    "US_options_gamma": {"huayuan": 7, "serenity": 8, "youzi_emotion": 0, "wyckoff": 12, "livermore": 8, "factor": 2, "poisson": 10, "options_gamma": 19, "supply_chain_xray": 2, "early_stage_quality": 1, "endogenous_microstructure": 8, "macro_policy_news_account": 13, "counter_consensus": 5, "expected_returns": 3, "bottleneck_scorecard": 5, "a_share_short_term": 0},
    "US_deleveraging": {"huayuan": 7, "serenity": 4, "youzi_emotion": 0, "wyckoff": 10, "livermore": 7, "factor": 1, "poisson": 6, "options_gamma": 22, "supply_chain_xray": 0, "early_stage_quality": 0, "endogenous_microstructure": 7, "macro_policy_news_account": 24, "counter_consensus": 8, "expected_returns": 3, "bottleneck_scorecard": 6, "a_share_short_term": 0},
    "US_flow_rotation": {"huayuan": 9, "serenity": 12, "youzi_emotion": 0, "wyckoff": 7, "livermore": 6, "factor": 3, "poisson": 6, "options_gamma": 8, "supply_chain_xray": 3, "early_stage_quality": 2, "endogenous_microstructure": 24, "macro_policy_news_account": 15, "counter_consensus": 5, "expected_returns": 4, "bottleneck_scorecard": 5, "a_share_short_term": 0},
    "event": {"huayuan": 11, "serenity": 8, "youzi_emotion": 3, "wyckoff": 9, "livermore": 6, "factor": 2, "poisson": 18, "options_gamma": 6, "supply_chain_xray": 2, "early_stage_quality": 2, "endogenous_microstructure": 9, "macro_policy_news_account": 12, "counter_consensus": 7, "expected_returns": 4, "bottleneck_scorecard": 5, "a_share_short_term": 0},
}

METHOD_LABELS = {
    "huayuan": "华源叙事/财报",
    "serenity": "Serenity供应链",
    "youzi_emotion": "游资情绪",
    "wyckoff": "威科夫量价",
    "livermore": "利弗莫尔趋势",
    "factor": "因子/组合",
    "poisson": "泊松择时",
    "options_gamma": "期权Gamma",
    "supply_chain_xray": "供应链X-Ray",
    "early_stage_quality": "早期主题Quality",
    "endogenous_microstructure": "内生结构/拥挤度",
    "macro_policy_news_account": "宏观政策新闻/账户",
    "counter_consensus": "逆共识",
    "expected_returns": "预期收益",
    "bottleneck_scorecard": "瓶颈评分卡",
    "a_share_short_term": "A股短线",
}
SCENARIO_LABELS = {
    "A_short": "A股超短",
    "A_swing": "A股趋势/波段",
    "A_fundamental": "A股基本面",
    "HK": "港股",
    "US_bigtech": "美股大科技",
    "US_supply_chain": "美股AI供应链",
    "US_early_theme": "美股早期技术主题",
    "US_options_gamma": "美股期权/Gamma主导",
    "US_deleveraging": "美股杀杠杆/系统风险",
    "US_flow_rotation": "美股流动性/拥挤度轮动",
    "event": "事件驱动/财报前后",
}

SEVERE_RISK_REGIMES = {"active_deleveraging", "forced_liquidation"}
OFFENSIVE_METHODS = (
    "youzi_emotion",
    "early_stage_quality",
    "a_share_short_term",
    "livermore",
)
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
RISK_SNAPSHOT_SCHEMA = "risk_regime_snapshot.v1"


def markdown_matrix() -> str:
    """Render the documentation matrix from the runtime source of truth."""
    methods = list(METHOD_LABELS)
    lines = [
        "| " + " | ".join(["场景", *[METHOD_LABELS[key] for key in methods]]) + " |",
        "|" + "|".join(["---", *["---:" for _key in methods]]) + "|",
    ]
    for scenario, weights in BASE.items():
        values = [SCENARIO_LABELS[scenario], *[str(weights[key]) for key in methods]]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)

OPTIONS_KEYWORDS = [
    "option", "options", "gamma", "gex", "put wall", "call wall", "gamma flip",
    "zero gamma", "implied volatility", "skew", "dealer", "vix",
]
SUPPLY_CHAIN_KEYWORDS = ["ai", "semiconductor", "chip", "power", "cooling", "hbm", "optical", "supply", "asic", "accelerator", "光模块", "cpo", "算力", "先进封装", "高速互联", "soxx", "smh", "半导体背离", "semis divergence", "费城半导体"]
EARLY_STAGE_KEYWORDS = ["early", "early-stage", "emerging", "nascent", "prototype", "pre-revenue", "萌芽", "导入初期", "早期", "新兴技术", "产业化初期", "还没放量", "小票"]
# Real deleveraging/systemic-stress semantics only. Do NOT add bare entity/option
# words here (tickers, commodities, vol-index names, options jargon) — a lone
# "aapl"/"vix"/"gold"/"gex"/"skew" mention is not evidence of an actual
# deleveraging event and previously caused every AAPL/GEX/VIX research request
# to misroute into this macro scenario (see method-router-deleveraging-keyword-misroute).
DELEVERAGING_KEYWORDS = ["deleveraging", "kill leverage", "杀杠杆", "去杠杆", "systemic deleveraging", "margin call", "margin spiral", "forced selling", "forced liquidation", "流动性踩踏", "流动性挤兑", "强平", "爆仓", "basis trade unwind", "long-end yield", "30y", "30年", "长端利率", "term premium"]
FLOW_ROTATION_KEYWORDS = ["pair trade", "pair-trade", "consensus", "consensus premium", "crowding", "crowded", "positioning", "leverage", "passive", "etf", "arb", "arbitrage", "creation redemption", "lockup", "secondary", "ipo", "issuance", "增发", "解禁", "被动资金", "虹吸", "private qt", "secondary bull", "次级牛市", "补涨", "gross margin", "semi short saas", "long semi short saas"]
# vix/gold are genuine macro barometers (risk-off gauge / safe-haven asset) so they
# stay here; aapl/apple/skew/gex are single-name or pure options-structure jargon
# with no macro-policy meaning and were removed (same over-broad-word bug as above).
MACRO_POLICY_KEYWORDS = ["macro", "policy", "fed", "rate", "inflation", "tariff", "regulation", "liquidity", "宏观", "政策", "监管", "利率", "通胀", "关税", "流动性", "vix", "gold", "黄金", "长端利率", "杀杠杆"]
COUNTER_CONSENSUS_KEYWORDS = ["counter consensus", "逆共识", "contra consensus", "contrarian", "反向", "市场共识", "盲区", "定价错误", "mispricing"]
SCORECARD_KEYWORDS = ["scorecard", "评分卡", "score", "rank", "排名", "priority", "优先级", "bottleneck score", "瓶颈评分"]
A_SHORT_KEYWORDS = ["打板", "涨停板", "连板", "短线", "做t", "右侧", "龙头战法", "情绪周期", "龙虎榜"]


def keyword_in_text(text: str, keyword: str) -> bool:
    """Match keywords without letting short ASCII tokens create false positives.

    Example: `ai` must match the token AI, not the `ai` inside `pair trade`.
    CJK keywords still use substring matching because Chinese text has no spaces.
    """
    if any("\u4e00" <= ch <= "\u9fff" for ch in keyword):
        return keyword in text
    escaped = re.escape(keyword)
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None


def has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword_in_text(text, k) for k in keywords)


def has_macro_policy_context(theme: str, explicit: bool = False) -> bool:
    t = theme.lower()
    return explicit or has_any(t, MACRO_POLICY_KEYWORDS)


def choose(market: str, horizon: str, theme: str, event: bool, options_heavy: bool = False) -> str:
    t = theme.lower()
    if market == "US" and has_any(t, DELEVERAGING_KEYWORDS):
        return "US_deleveraging"
    if market == "US" and (options_heavy or has_any(t, OPTIONS_KEYWORDS)):
        return "US_options_gamma"
    if market == "US" and has_any(t, FLOW_ROTATION_KEYWORDS):
        return "US_flow_rotation"
    if event:
        return "event"
    if market == "A":
        return {"short": "A_short", "swing": "A_swing", "fundamental": "A_fundamental"}.get(horizon, "A_swing")
    if market == "HK":
        return "HK"
    if market == "US":
        if has_any(t, EARLY_STAGE_KEYWORDS):
            return "US_early_theme"
        return "US_supply_chain" if has_any(t, SUPPLY_CHAIN_KEYWORDS) else "US_bigtech"
    return "event" if event else "A_swing"


def normalize(weights: dict[str, int]) -> dict[str, int]:
    total = sum(weights.values()) or 1
    exact = {k: v * 100 / total for k, v in weights.items()}
    out = {k: int(v) for k, v in exact.items()}
    diff = 100 - sum(out.values())
    if diff > 0:
        ranked = sorted(exact, key=lambda k: (exact[k] - int(exact[k]), exact[k]), reverse=True)
        for k in ranked[:diff]:
            out[k] += 1
    elif diff < 0:
        ranked = sorted(exact, key=lambda k: (exact[k] - int(exact[k]), exact[k]))
        for k in ranked[:abs(diff)]:
            out[k] -= 1
    return out


def overlay(weights: dict[str, int], enabled: bool) -> dict[str, int]:
    out = dict(weights)
    if enabled:
        out["macro_policy_news_account"] = out.get("macro_policy_news_account", 0) + 5
    return out


def detect_method_overrides(theme: str) -> dict[str, bool]:
    """Detect which v2.5+ methods should be boosted based on theme keywords."""
    t = theme.lower()
    return {
        "boost_counter_consensus": has_any(t, COUNTER_CONSENSUS_KEYWORDS),
        "boost_scorecard": has_any(t, SCORECARD_KEYWORDS),
        "boost_a_short": has_any(t, A_SHORT_KEYWORDS),
        "boost_supply_chain_xray": has_any(t, SUPPLY_CHAIN_KEYWORDS),
        "boost_early_stage_quality": has_any(t, EARLY_STAGE_KEYWORDS),
    }


def apply_overrides(weights: dict[str, int], overrides: dict[str, bool]) -> dict[str, int]:
    out = dict(weights)
    if overrides.get("boost_counter_consensus"):
        out["counter_consensus"] = out.get("counter_consensus", 0) + 4
    if overrides.get("boost_scorecard"):
        out["bottleneck_scorecard"] = out.get("bottleneck_scorecard", 0) + 4
    if overrides.get("boost_a_short"):
        out["a_share_short_term"] = out.get("a_share_short_term", 0) + 4
    if overrides.get("boost_supply_chain_xray"):
        out["supply_chain_xray"] = out.get("supply_chain_xray", 0) + 4
    if overrides.get("boost_early_stage_quality"):
        out["early_stage_quality"] = out.get("early_stage_quality", 0) + 4
    return out


def _risk_snapshot_path() -> Path:
    state_root = Path(
        os.environ.get("TRADING_RESEARCH_STATE_DIR")
        or (Path.home() / ".cache" / "hermes" / "trading-research")
    )
    return state_root / "risk_regime" / "current.json"


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def load_current_risk_snapshot(
    *,
    snapshot_json: str | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load a current-session snapshot first, otherwise the local current file.

    Invalid, future, stale, or non-local-day snapshots fail open to the original
    method weights. An explicit session payload never falls back to an older cache.
    """
    source = "session_snapshot" if snapshot_json is not None else "cached_current"
    audit: dict[str, Any] = {
        "applied": False,
        "status": "missing",
        "source": source,
        "risk_regime": None,
        "tightened_methods": {},
    }
    try:
        if snapshot_json is not None:
            payload = json.loads(snapshot_json)
        else:
            snapshot_path = path or _risk_snapshot_path()
            if not snapshot_path.is_file():
                return None, audit
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        audit["status"] = "invalid"
        return None, audit

    if not isinstance(payload, dict) or payload.get("schema_version") != RISK_SNAPSHOT_SCHEMA:
        audit["status"] = "invalid"
        return None, audit
    generated_at = _parse_timestamp(payload.get("generated_at"))
    stale_after = _parse_timestamp(payload.get("stale_after"))
    evaluated_at = now or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    else:
        evaluated_at = evaluated_at.astimezone(timezone.utc)
    expected_date = evaluated_at.astimezone(LOCAL_TIMEZONE).date().isoformat()
    snapshot_date = payload.get("snapshot_date")
    if (
        generated_at is None
        or stale_after is None
        or generated_at > evaluated_at
        or stale_after <= generated_at
        or evaluated_at >= stale_after
        or snapshot_date != expected_date
    ):
        audit["status"] = "stale" if generated_at is not None and stale_after is not None else "invalid"
        return None, audit

    regime = payload.get("risk_regime")
    if not isinstance(regime, str) or not regime.strip():
        audit["status"] = "invalid"
        return None, audit
    audit["status"] = "current"
    audit["risk_regime"] = regime
    return payload, audit


def apply_risk_regime_tightening(
    weights: dict[str, int],
    snapshot: dict[str, Any] | None,
    audit: dict[str, Any],
) -> tuple[dict[str, int], dict[str, Any]]:
    out = dict(weights)
    result_audit = dict(audit)
    regime = snapshot.get("risk_regime") if snapshot else None
    if regime not in SEVERE_RISK_REGIMES:
        if snapshot is not None:
            result_audit["status"] = "not_severe"
        return out, result_audit

    tightened: dict[str, dict[str, int]] = {}
    for method in OFFENSIVE_METHODS:
        before = out.get(method, 0)
        after = min(before, BASE["US_deleveraging"][method])
        out[method] = after
        if after < before:
            tightened[method] = {"raw_before": before, "raw_after": after}
    result_audit.update({
        "applied": True,
        "status": "applied",
        "risk_regime": regime,
        "tightened_methods": tightened,
    })
    return out, result_audit


def top_weights(weights: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    return sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:n]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["A", "HK", "US", "unknown"])
    ap.add_argument("--horizon", default="swing", choices=["short", "swing", "fundamental"])
    ap.add_argument("--theme", default="")
    ap.add_argument("--event", action="store_true")
    ap.add_argument("--options-heavy", action="store_true")
    ap.add_argument("--macro-policy", action="store_true")
    ap.add_argument(
        "--risk-snapshot-json",
        help="validated current-session risk_regime_snapshot.v1 JSON; overrides cached current.json",
    )
    ap.add_argument("--matrix-markdown", action="store_true", help="render the runtime raw-score matrix used in documentation")
    args = ap.parse_args()
    if args.matrix_markdown:
        print(markdown_matrix())
        raise SystemExit(0)
    scenario = choose(args.market, args.horizon, args.theme, args.event, args.options_heavy)
    macro = has_macro_policy_context(args.theme, args.macro_policy)
    overrides = detect_method_overrides(args.theme)
    raw_weights = apply_overrides(overlay(BASE[scenario], macro), overrides)
    risk_snapshot, risk_audit = load_current_risk_snapshot(snapshot_json=args.risk_snapshot_json)
    raw_weights, risk_audit = apply_risk_regime_tightening(raw_weights, risk_snapshot, risk_audit)
    weights = normalize(raw_weights)
    print(json.dumps({
        "scenario": scenario,
        "macro_policy_context": macro,
        "method_overrides": overrides,
        "risk_regime_hook": risk_audit,
        "weights": weights,
        "top_weights": top_weights(weights),
    }, ensure_ascii=False, indent=2))
