#!/usr/bin/env python3
"""dispersion_crowding.py — 杠杆拥挤 / 离散度回归雷达（CBOE 免费源）。

把 用户 的实盘框架 operationalize：判断市场是否已堆出"所有人加杠杆、押在彼此
独立的单票上"的脆弱结构，以及相关性是否正在回归 1（=多杀多强平进行中）。

两个核心指标（CBOE 官方、免费、无 key，与 options_gamma.py 同一套 endpoint）：
  - COR1M（CBOE 1 个月隐含相关性指数）：衡量预期成分股相关性。
      地量 = 资金分散押在各自故事上、杠杆拥挤埋雷；任何冲击会让相关性瞬间弹向 1，
      所有票一起跌、所有杠杆同时平仓。"暴跌本质上就是相关性回归 1 的过程"。
  - VIXEQ（CBOE 标普 500 成分股波动率指数）：个股层面隐含波动率。
      VIXEQ − VIX 溢价 = 单股投机 / 杠杆的最直接读数（call 买得越疯，溢价越极致）。

数据源：
  - 实时报价 https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{SYM}.json
  - 历史日线 https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_{SYM}.json
  （VIXEQ 自 2014、COR1M 自 2006、VIX 自 1990；约 15 分钟延迟）

纪律：取数失败一律写缺口，绝不补数字。call/put ratio 等辅助情绪指标默认留手填入口。

用法：
  python3 dispersion_crowding.py
  python3 dispersion_crowding.py --json
  python3 dispersion_crowding.py --lookback 504   # 自定义"近端"分位窗口（交易日）
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

_QUOTE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json"
_HIST_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_{sym}.json"

# COR1M 分位门槛（越低越拥挤）；VIXEQ-VIX 溢价分位门槛（越高越投机）。
COR_EXTREME_PCTILE = 0.05      # ≤ 历史 5% 分位：离散度极端、结构性埋雷
COR_CROWDING_PCTILE = 0.25     # ≤ 历史 25% 分位：拥挤累积
COR_UNWIND_1D_PCT = 15.0       # COR1M 单日涨幅 ≥ 15%：相关性正在回归 1
COR_UNWIND_5D_PCT = 30.0       # COR1M 5 日涨幅 ≥ 30%：回归 1 进行中
PREMIUM_HOT_PCTILE = 0.80      # VIXEQ-VIX 溢价 ≥ 历史 80% 分位：单股投机过热


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clear_proxy_env() -> None:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)


def percentile_rank(values: list[float], value: float | None) -> float | None:
    """value 在 values 中的分位（≤ 占比）。"""
    if not values or value is None:
        return None
    below = sum(1 for item in values if item <= value)
    return round(below / len(values), 4)


def pct_change(old: float | None, new: float | None) -> float | None:
    if old in (None, 0) or new is None:
        return None
    return round(((new - old) / old) * 100.0, 2)


def fetch_index(sym: str) -> dict[str, Any]:
    """取单个 CBOE 指数的现值 + 历史收盘序列。失败抛异常由上层转缺口。"""
    quote = _get_json(_QUOTE_URL.format(sym=sym))
    qdata = quote.get("data") or {}
    last = qdata.get("current_price")
    hist = _get_json(_HIST_URL.format(sym=sym))
    rows = hist.get("data") or []
    closes: list[float] = []
    for row in rows:
        c = row.get("close")
        if c is None:
            continue
        try:
            closes.append(float(c))
        except (TypeError, ValueError):
            continue
    if not closes:
        raise RuntimeError(f"{sym} 历史序列为空")
    last_val = float(last) if last is not None else closes[-1]
    return {
        "symbol": sym,
        "source": "cboe_delayed",
        "asof": quote.get("timestamp"),
        "last": round(last_val, 4),
        "prev": round(closes[-2], 4) if len(closes) >= 2 else None,
        "five_back": round(closes[-6], 4) if len(closes) >= 6 else None,
        "closes": closes,
    }


def safe_fetch(sym: str) -> dict[str, Any]:
    try:
        _clear_proxy_env()
        return fetch_index(sym)
    except Exception as exc:  # noqa: BLE001 — 任何取数失败都转缺口
        return {"symbol": sym, "source": "cboe_delayed", "error": str(exc)[:200], "closes": []}


def _window_pctiles(series: dict[str, Any], lookback: int) -> dict[str, Any]:
    closes = series.get("closes") or []
    last = series.get("last")
    near = closes[-lookback:] if len(closes) > lookback else closes
    return {
        "pctile_all": percentile_rank(closes, last),
        "pctile_lookback": percentile_rank(near, last),
        "chg_1d_pct": pct_change(series.get("prev"), last),
        "chg_5d_pct": pct_change(series.get("five_back"), last),
        "history_points": len(closes),
    }


def classify_state(cor: dict[str, Any], premium_pctile: float | None) -> dict[str, Any]:
    """把 COR1M 读数 + VIXEQ-VIX 溢价合成离散度拥挤状态。"""
    p_all = cor.get("pctile_all")
    chg1 = cor.get("chg_1d_pct")
    chg5 = cor.get("chg_5d_pct")

    unwind = (chg1 is not None and chg1 >= COR_UNWIND_1D_PCT) or (
        chg5 is not None and chg5 >= COR_UNWIND_5D_PCT
    )
    spec_hot = premium_pctile is not None and premium_pctile >= PREMIUM_HOT_PCTILE

    if unwind:
        state = "correlation_unwind_active"
        meaning = "相关性正在回归 1：所有票一起跌、杠杆被迫同时平仓（多杀多进行中）"
        regime_hint = "active_deleveraging | forced_liquidation"
        action = "只观察/减仓/回避；半导体若被杠杆盘恐慌杀跌应在另一侧择机重建，但先降 beta、开保护"
    elif p_all is not None and p_all <= COR_EXTREME_PCTILE:
        state = "dispersion_extreme"
        meaning = "离散度极端、结构性埋雷：人人加杠杆押彼此独立单票，任何冲击都可能引爆相关性回归 1"
        regime_hint = "deleveraging_watch"
        action = "纪律先行：先降 beta、开保护，禁止在拥挤叠杠杆极值上追多"
    elif (p_all is not None and p_all <= COR_CROWDING_PCTILE) or spec_hot:
        state = "crowding_building"
        meaning = "拥挤累积中：相关性偏低 / 单股投机溢价偏高，脆弱性上升但尚未触发"
        regime_hint = "stress_building"
        action = "总仓打折、优先配对/relative value，不在 leader 上盲目追高"
    else:
        state = "benign"
        meaning = "离散度/拥挤无极端读数；按个股逻辑与多因子常规决策"
        regime_hint = "normal"
        action = "正常决策；继续监控 COR1M 是否被压到地量"
    return {
        "state": state,
        "meaning": meaning,
        "regime_hint": regime_hint,
        "decision_impact": action,
        "single_stock_speculation_hot": bool(spec_hot),
        "correlation_regression_in_progress": bool(unwind),
    }


def build_snapshot(lookback: int) -> dict[str, Any]:
    cor_raw = safe_fetch("COR1M")
    vixeq_raw = safe_fetch("VIXEQ")
    vix_raw = safe_fetch("VIX")

    gaps: list[str] = []
    indices: dict[str, Any] = {}

    cor: dict[str, Any] = {}
    if cor_raw.get("closes"):
        cor = {**_window_pctiles(cor_raw, lookback), "last": cor_raw["last"], "asof": cor_raw.get("asof")}
        indices["COR1M"] = cor
    else:
        gaps.append(f"COR1M 取数失败：{cor_raw.get('error')}")

    premium = None
    premium_pctile = None
    if vixeq_raw.get("closes") and vix_raw.get("closes"):
        vixeq_last = vixeq_raw["last"]
        vix_last = vix_raw["last"]
        premium = round(vixeq_last - vix_last, 4)
        # 溢价历史序列：按重叠尾部对齐（两序列长度不同，取共同尾部长度）。
        n = min(len(vixeq_raw["closes"]), len(vix_raw["closes"]))
        prem_series = [
            vixeq_raw["closes"][-n:][i] - vix_raw["closes"][-n:][i] for i in range(n)
        ]
        premium_pctile = percentile_rank(prem_series, premium)
        indices["VIXEQ"] = {"last": vixeq_last, "asof": vixeq_raw.get("asof"),
                            "pctile_all": percentile_rank(vixeq_raw["closes"], vixeq_last)}
        indices["VIX"] = {"last": vix_last, "asof": vix_raw.get("asof")}
        indices["VIXEQ_minus_VIX"] = {
            "premium": premium,
            "premium_pctile": premium_pctile,
            "note": "VIXEQ-VIX 溢价 = 单股投机/杠杆读数；分位越高越投机过热",
        }
    else:
        if not vixeq_raw.get("closes"):
            gaps.append(f"VIXEQ 取数失败：{vixeq_raw.get('error')}")
        if not vix_raw.get("closes"):
            gaps.append(f"VIX 取数失败：{vix_raw.get('error')}")

    if not cor:
        return {
            "asof": iso_now(),
            "tool": "dispersion_crowding",
            "indices": indices,
            "state": {"state": "unavailable", "meaning": "COR1M 缺失，无法判定离散度状态",
                      "regime_hint": "unavailable",
                      "decision_impact": "缺核心相关性读数 → 结论限保守档，转用 risk_regime_snapshot 与期权墙",
                      "single_stock_speculation_hot": False,
                      "correlation_regression_in_progress": False},
            "gaps": gaps,
            "auxiliary_manual_inputs": _aux_template(),
        }

    state = classify_state(cor, premium_pctile)
    return {
        "asof": iso_now(),
        "tool": "dispersion_crowding",
        "indices": indices,
        "state": state,
        "gaps": gaps,
        "auxiliary_manual_inputs": _aux_template(),
    }


def _aux_template() -> dict[str, Any]:
    """辅助情绪指标无稳定免费源时的手填入口（不脑补、留空即缺口）。"""
    return {
        "call_put_ratio": None,
        "note": "call/put ratio 与其它情绪指标为 用户 框架中的辅助项；如有读数手填，否则视为缺口",
    }


def render_human(snap: dict[str, Any]) -> str:
    st = snap["state"]
    lines = ["# 杠杆拥挤 / 离散度回归雷达", f"as of {snap['asof']}", ""]
    idx = snap.get("indices", {})
    cor = idx.get("COR1M")
    if cor:
        lines.append(
            f"COR1M：{cor['last']}  | 历史分位 {cor.get('pctile_all')}  近端分位 {cor.get('pctile_lookback')}"
            f"  | 1d {cor.get('chg_1d_pct')}%  5d {cor.get('chg_5d_pct')}%"
        )
    prem = idx.get("VIXEQ_minus_VIX")
    if prem:
        vixeq = idx.get("VIXEQ", {})
        vix = idx.get("VIX", {})
        lines.append(
            f"VIXEQ {vixeq.get('last')} − VIX {vix.get('last')} = 溢价 {prem['premium']}"
            f"  | 溢价分位 {prem.get('premium_pctile')}"
        )
    lines += [
        "",
        f"状态：{st['state']}",
        f"含义：{st['meaning']}",
        f"映射 regime：{st['regime_hint']}",
        f"动作影响：{st['decision_impact']}",
    ]
    if snap.get("gaps"):
        lines += ["", "缺口："] + [f"- {g}" for g in snap["gaps"]]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="杠杆拥挤 / 离散度回归雷达（CBOE）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--lookback", type=int, default=252,
                        help="近端分位窗口（交易日，默认 252≈1 年）")
    args = parser.parse_args()
    snap = build_snapshot(max(20, args.lookback))
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(render_human(snap))


if __name__ == "__main__":
    main()
