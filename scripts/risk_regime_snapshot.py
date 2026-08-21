#!/usr/bin/env python3
"""trading-research · risk_regime_snapshot — 美股杀杠杆六信号自动快照。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from market_router import classify
except ImportError:  # pragma: no cover
    classify = None

STATE_ROOT = Path(os.environ.get("TRADING_RESEARCH_STATE_DIR") or (Path.home() / ".cache" / "hermes" / "trading-research"))
RISK_STATE_DIR = STATE_ROOT / "risk_regime"
HISTORY_DIR = RISK_STATE_DIR / "history"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval={interval}&includePrePost=false"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct_change(old: float | None, new: float | None) -> float | None:
    if old in (None, 0) or new is None:
        return None
    return round(((new - old) / old) * 100.0, 2)


def bps_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None:
        return None
    return round((new - old) * 100.0, 2)


def percentile_rank(values: list[float], value: float | None) -> float | None:
    if not values or value is None:
        return None
    below = sum(1 for item in values if item <= value)
    return round(below / len(values), 3)


def tail(values: list[float], size: int) -> list[float]:
    return values[-size:] if len(values) >= size else values[:]


def fetch_yahoo_closes(symbol: str, range_: str = "3mo", interval: str = "1d") -> dict[str, Any]:
    url = YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=""), range_=range_, interval=interval)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    payload = json.load(urllib.request.urlopen(req, timeout=25))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo chart empty for {symbol}")
    body = result[0]
    closes_raw = (((body.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    timestamps = body.get("timestamp") or []
    closes: list[float] = []
    points: list[dict[str, Any]] = []
    for ts, close in zip(timestamps, closes_raw):
        if close is None:
            continue
        val = float(close)
        closes.append(val)
        points.append({
            "ts": datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(),
            "close": round(val, 4),
        })
    if not closes:
        raise RuntimeError(f"Yahoo chart has no closes for {symbol}")
    return {
        "symbol": symbol,
        "source": "yahoo_chart",
        "points": points,
        "closes": closes,
        "last": round(closes[-1], 4),
        "prev": round(closes[-2], 4) if len(closes) >= 2 else None,
        "five_back": round(closes[-6], 4) if len(closes) >= 6 else None,
        "twenty_mean": round(mean(tail(closes, 20)), 4) if closes else None,
        "percentile_3m": percentile_rank(closes, closes[-1]),
    }


def signal_dict(
    *,
    key: str,
    title: str,
    layer: str,
    triggered: bool,
    source: str,
    reason: str,
    metrics: dict[str, Any],
    mode: str = "confirmed",
    comparable_history: bool | None = None,
) -> dict[str, Any]:
    out = {
        "key": key,
        "title": title,
        "layer": layer,
        "triggered": triggered,
        "source": source,
        "mode": mode,
        "reason": reason,
        "metrics": metrics,
    }
    if comparable_history is not None:
        out["comparable_history"] = comparable_history
    return out


def build_vix_signal(series: dict[str, Any]) -> dict[str, Any]:
    last = series["last"]
    prev = series["prev"]
    five_back = series["five_back"]
    pct_1d = pct_change(prev, last)
    pct_5d = pct_change(five_back, last)
    triggers: list[str] = []
    if last >= 20:
        triggers.append("level>=20")
    if pct_1d is not None and pct_1d >= 10:
        triggers.append("1d>=10%")
    if pct_5d is not None and pct_5d >= 20:
        triggers.append("5d>=20%")
    triggered = len(triggers) >= 2 or (last >= 25 and len(triggers) >= 1)
    reason = "VIX 未见明显波动冲击"
    if triggered:
        reason = f"VIX {last:.2f}，1日 {pct_1d}% / 5日 {pct_5d}% ，已出现波动率冲击"
    return signal_dict(
        key="vix_spike",
        title="VIX 暴涨",
        layer="volatility",
        triggered=triggered,
        source="Yahoo ^VIX",
        reason=reason,
        metrics={
            "last": last,
            "pct_1d": pct_1d,
            "pct_5d": pct_5d,
            "percentile_3m": series.get("percentile_3m"),
            "trigger_rules": triggers,
        },
    )


def build_gold_signal(gold: dict[str, Any], index_: dict[str, Any]) -> dict[str, Any]:
    gld_1d = pct_change(gold["prev"], gold["last"])
    gld_5d = pct_change(gold["five_back"], gold["last"])
    idx_1d = pct_change(index_["prev"], index_["last"])
    idx_5d = pct_change(index_["five_back"], index_["last"])
    triggered = bool(
        gld_5d is not None and idx_5d is not None and gld_5d <= -1.5 and idx_5d <= -1.0
    ) or bool(
        gld_1d is not None and idx_1d is not None and gld_1d <= -0.75 and idx_1d <= -1.0 and (gld_5d or 0) < 0 and (idx_5d or 0) < 0
    )
    reason = "黄金和指数未出现同步下跌"
    if triggered:
        reason = f"GLD 5日 {gld_5d}%、{index_['symbol']} 5日 {idx_5d}% ，更像流动性挤兑而非温和 risk-off"
    return signal_dict(
        key="gold_equity_liquidation",
        title="黄金与指数同跌",
        layer="cross_asset",
        triggered=triggered,
        source=f"Yahoo {gold['symbol']} + {index_['symbol']}",
        reason=reason,
        metrics={
            "gold_pct_1d": gld_1d,
            "gold_pct_5d": gld_5d,
            "index_pct_1d": idx_1d,
            "index_pct_5d": idx_5d,
        },
    )


def build_aapl_signal(leader: dict[str, Any], index_: dict[str, Any]) -> dict[str, Any]:
    lead_1d = pct_change(leader["prev"], leader["last"])
    lead_5d = pct_change(leader["five_back"], leader["last"])
    idx_1d = pct_change(index_["prev"], index_["last"])
    idx_5d = pct_change(index_["five_back"], index_["last"])
    rel_1d = round((lead_1d or 0) - (idx_1d or 0), 2) if lead_1d is not None and idx_1d is not None else None
    rel_5d = round((lead_5d or 0) - (idx_5d or 0), 2) if lead_5d is not None and idx_5d is not None else None
    triggered = bool(lead_5d is not None and rel_5d is not None and lead_5d <= -5.0 and rel_5d <= -3.0) or bool(
        lead_1d is not None and rel_1d is not None and lead_1d <= -3.0 and rel_1d <= -2.0
    )
    reason = f"{leader['symbol']} 未出现明显的流动性式领跌"
    if triggered:
        reason = f"{leader['symbol']} 5日 {lead_5d}%、相对 {index_['symbol']} 跑输 {abs(rel_5d or 0)}pct，领导股流动性开始失灵"
    return signal_dict(
        key="apple_liquidity_break",
        title="Apple 暴跌",
        layer="leadership",
        triggered=triggered,
        source=f"Yahoo {leader['symbol']} + {index_['symbol']}",
        reason=reason,
        metrics={
            "leader_pct_1d": lead_1d,
            "leader_pct_5d": lead_5d,
            "relative_1d_vs_index": rel_1d,
            "relative_5d_vs_index": rel_5d,
        },
    )


def build_yield_signal(ten_year: dict[str, Any], thirty_year: dict[str, Any]) -> dict[str, Any]:
    tnx_1d = bps_change(ten_year["prev"], ten_year["last"])
    tnx_5d = bps_change(ten_year["five_back"], ten_year["last"])
    tyx_1d = bps_change(thirty_year["prev"], thirty_year["last"])
    tyx_5d = bps_change(thirty_year["five_back"], thirty_year["last"])
    triggered = bool((tyx_5d or 0) >= 15) or bool((tyx_1d or 0) >= 10) or bool((tyx_5d or 0) >= 10 and (tnx_5d or 0) >= 10)
    reason = "长端利率未出现明显冲击"
    if triggered:
        reason = f"30Y 5日 {tyx_5d}bp、10Y 5日 {tnx_5d}bp，折现率与股债对冲压力同步抬升"
    return signal_dict(
        key="long_end_yield_shock",
        title="长端利率暴涨",
        layer="cross_asset",
        triggered=triggered,
        source=f"Yahoo {ten_year['symbol']} + {thirty_year['symbol']}",
        reason=reason,
        metrics={
            "ten_year_last": ten_year["last"],
            "thirty_year_last": thirty_year["last"],
            "ten_year_1d_bp": tnx_1d,
            "ten_year_5d_bp": tnx_5d,
            "thirty_year_1d_bp": tyx_1d,
            "thirty_year_5d_bp": tyx_5d,
        },
    )


def build_skew_signal(skew: dict[str, Any]) -> dict[str, Any]:
    last = skew["last"]
    prev = skew["prev"]
    pct_1d = pct_change(prev, last)
    mean_20d = skew.get("twenty_mean")
    triggered = last >= 145 or bool(mean_20d is not None and last >= 140 and last - mean_20d >= 5)
    reason = "SKEW 未到明显左尾定价极端"
    if triggered:
        reason = f"SKEW {last:.2f}，高于 20日均值 {mean_20d}，左尾保护需求偏强"
    return signal_dict(
        key="skew_left_tail",
        title="Skew 极度左偏",
        layer="options_structure",
        triggered=triggered,
        source="Yahoo ^SKEW",
        reason=reason,
        metrics={
            "last": last,
            "pct_1d": pct_1d,
            "mean_20d": mean_20d,
            "percentile_3m": skew.get("percentile_3m"),
        },
    )


def run_gamma_snapshot(symbol: str, *, near: bool) -> dict[str, Any]:
    cmd = [sys.executable, str(SCRIPTS / "options_gamma.py"), symbol, "--json"]
    if near:
        cmd.insert(-1, "--near")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"options_gamma failed for {symbol}")
    return json.loads(proc.stdout)


def load_previous_snapshot(index_symbol: str) -> dict[str, Any] | None:
    path = HISTORY_DIR / f"{index_symbol.upper()}.jsonl"
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def save_snapshot(index_symbol: str, payload: dict[str, Any]) -> str:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{index_symbol.upper()}.jsonl"
    if path.exists():
        lines = [line for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        if lines:
            try:
                latest = json.loads(lines[-1])
            except json.JSONDecodeError:
                latest = None
            if latest:
                latest_agg = latest.get("aggregate") or {}
                latest_near = latest.get("near") or {}
                payload_agg = payload.get("aggregate") or {}
                payload_near = payload.get("near") or {}
                same_signature = all([
                    latest_agg.get("total_net_gex") == payload_agg.get("total_net_gex"),
                    latest_agg.get("put_wall") == payload_agg.get("put_wall"),
                    latest_agg.get("call_wall") == payload_agg.get("call_wall"),
                    latest_near.get("total_net_gex") == payload_near.get("total_net_gex"),
                    latest_near.get("put_wall") == payload_near.get("put_wall"),
                    latest_near.get("call_wall") == payload_near.get("call_wall"),
                ])
                if same_signature:
                    return str(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return str(path)


def build_gex_signal(index_symbol: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    gaps: list[str] = []
    aggregate = run_gamma_snapshot(index_symbol, near=False)
    near = run_gamma_snapshot(index_symbol, near=True)
    previous = load_previous_snapshot(index_symbol)
    current_payload = {
        "generated_at": iso_now(),
        "index_symbol": index_symbol.upper(),
        "aggregate": {
            "symbol": aggregate.get("symbol"),
            "spot": aggregate.get("spot"),
            "total_net_gex": aggregate.get("total_net_gex"),
            "regime": aggregate.get("regime"),
            "put_wall": aggregate.get("put_wall"),
            "call_wall": aggregate.get("call_wall"),
            "gamma_flip": aggregate.get("gamma_flip"),
            "source": aggregate.get("source"),
        },
        "near": {
            "symbol": near.get("symbol"),
            "spot": near.get("spot"),
            "total_net_gex": near.get("total_net_gex"),
            "regime": near.get("regime"),
            "put_wall": near.get("put_wall"),
            "call_wall": near.get("call_wall"),
            "gamma_flip": near.get("gamma_flip"),
            "source": near.get("source"),
        },
    }
    current_total = float((current_payload["aggregate"].get("total_net_gex") or 0))
    prev_total = None
    confirmed_flags: list[str] = []
    comparable = False
    put_wall_shift = None
    flip_shift = None
    if previous and isinstance(previous, dict):
        prev_agg = previous.get("aggregate") or {}
        if prev_agg.get("total_net_gex") is not None:
            comparable = True
            prev_total = float(prev_agg.get("total_net_gex") or 0)
            prev_put_wall = prev_agg.get("put_wall")
            prev_flip = prev_agg.get("gamma_flip")
            put_wall_shift = pct_change(prev_put_wall, current_payload["aggregate"].get("put_wall"))
            flip_shift = pct_change(prev_flip, current_payload["aggregate"].get("gamma_flip"))
            if prev_total > 0 and current_total < 0:
                confirmed_flags.append("positive_to_negative")
            if prev_total > 0 and current_total <= prev_total * 0.7:
                confirmed_flags.append("support_down_30pct")
            if put_wall_shift is not None and put_wall_shift <= -1.0:
                confirmed_flags.append("put_wall_down_1pct")
            if flip_shift is not None and flip_shift <= -1.0:
                confirmed_flags.append("gamma_flip_down_1pct")
    confirmed_outflow = comparable and (
        "positive_to_negative" in confirmed_flags or (
            "support_down_30pct" in confirmed_flags and (
                "put_wall_down_1pct" in confirmed_flags or "gamma_flip_down_1pct" in confirmed_flags
            )
        )
    )

    spot = current_payload["aggregate"].get("spot")
    put_wall = current_payload["aggregate"].get("put_wall")
    gamma_flip = current_payload["aggregate"].get("gamma_flip")
    aggregate_regime = current_payload["aggregate"].get("regime")
    near_regime = current_payload["near"].get("regime")
    proxy_flags: list[str] = []
    if aggregate_regime == "negative_gamma":
        proxy_flags.append("aggregate_negative_gamma")
    if near_regime == "negative_gamma":
        proxy_flags.append("near_negative_gamma")
    if isinstance(spot, (int, float)) and isinstance(put_wall, (int, float)) and spot < put_wall:
        proxy_flags.append("spot_below_put_wall")
    if isinstance(spot, (int, float)) and isinstance(gamma_flip, (int, float)) and spot < gamma_flip:
        proxy_flags.append("spot_below_gamma_flip")
    proxy_triggered = len(proxy_flags) >= 2 or ("near_negative_gamma" in proxy_flags and "spot_below_put_wall" in proxy_flags)
    triggered = confirmed_outflow or proxy_triggered
    mode = "stable"
    if confirmed_outflow:
        mode = "confirmed_outflow"
    elif proxy_triggered:
        mode = "dealer_support_weak_proxy"
        if not comparable:
            gaps.append("指数 GEX 暂无同口径前值，只能写 dealer support weak proxy，不能硬写出逃")
    reason = "指数 GEX 未见明显恶化"
    if confirmed_outflow:
        reason = f"{index_symbol.upper()} 同口径历史显示 GEX 支撑下降：{', '.join(confirmed_flags)}"
    elif proxy_triggered:
        reason = f"{index_symbol.upper()} 当前结构偏脆：{', '.join(proxy_flags)}"
    signal = signal_dict(
        key="index_gex_regime",
        title="指数 GEX 弱化",
        layer="options_structure",
        triggered=triggered,
        source=f"options_gamma.py {index_symbol.upper()} aggregate+near",
        reason=reason,
        metrics={
            "aggregate_regime": aggregate_regime,
            "near_regime": near_regime,
            "aggregate_total_net_gex": current_total,
            "near_total_net_gex": current_payload["near"].get("total_net_gex"),
            "spot": spot,
            "put_wall": put_wall,
            "gamma_flip": gamma_flip,
            "prev_aggregate_total_net_gex": prev_total,
            "put_wall_shift_pct": put_wall_shift,
            "gamma_flip_shift_pct": flip_shift,
            "confirmed_flags": confirmed_flags,
            "proxy_flags": proxy_flags,
        },
        mode=mode,
        comparable_history=comparable,
    )
    return signal, current_payload, gaps


def regime_from_signals(signals: list[dict[str, Any]]) -> tuple[str, list[str], float, str, str]:
    triggered = [signal for signal in signals if signal["triggered"]]
    layers = sorted({signal["layer"] for signal in triggered})
    count = len(triggered)
    layer_count = len(layers)
    if count <= 1:
        return "normal", layers, 1.0, "L3", "按个股逻辑正常决策"
    if count == 2:
        return "stress_building", layers, 0.7, "L3", "总仓先打 7 折；禁止无证据追高"
    if count == 3 and layer_count >= 2:
        return "deleveraging_watch", layers, 0.5, "L2", "暂停 L3 加仓；先做仓位收缩再谈 alpha"
    if count >= 4 and layer_count >= 3 and count < 5:
        return "active_deleveraging", layers, 0.3, "L1", "最高只给观察/极小试错；若跌破 Put Wall 未收回，不接盘"
    if count >= 5:
        return "forced_liquidation", layers, 0.1, "L1", "优先保命与去杠杆；除非关键位修复，否则不给新多头执行"
    return "deleveraging_watch", layers, 0.5, "L2", "系统性风险已成形，先缩仓再谈加仓"


def collect(subject: str, market: str | None, no_store: bool, index_symbol: str) -> dict[str, Any]:
    identity = classify(subject) if classify else {"query": subject, "market": market or "US", "symbol": subject}
    inferred_market = market or identity.get("market") or "US"
    data_gaps: list[str] = []
    signals: list[dict[str, Any]] = []

    vix = fetch_yahoo_closes("^VIX")
    gld = fetch_yahoo_closes("GLD")
    spy = fetch_yahoo_closes(index_symbol.upper())
    aapl = fetch_yahoo_closes("AAPL")
    tnx = fetch_yahoo_closes("^TNX")
    tyx = fetch_yahoo_closes("^TYX")
    skew = fetch_yahoo_closes("^SKEW")

    signals.append(build_vix_signal(vix))
    signals.append(build_gold_signal(gld, spy))
    signals.append(build_aapl_signal(aapl, spy))
    signals.append(build_yield_signal(tnx, tyx))
    signals.append(build_skew_signal(skew))

    gex_signal, gex_snapshot, gex_gaps = build_gex_signal(index_symbol.upper())
    signals.append(gex_signal)
    data_gaps.extend(gex_gaps)

    history_path = None
    if not no_store:
        history_path = save_snapshot(index_symbol.upper(), gex_snapshot)

    risk_regime, layers, position_multiplier, max_action_level, regime_impact = regime_from_signals(signals)
    triggered = [signal for signal in signals if signal["triggered"]]
    signal_map = {signal["key"]: signal for signal in signals}
    sources = [
        "Yahoo chart API (^VIX, GLD, SPY, AAPL, ^TNX, ^TYX, ^SKEW)",
        f"options_gamma.py {index_symbol.upper()} --json",
        f"options_gamma.py {index_symbol.upper()} --near --json",
    ]
    notes = [
        f"运行态历史写入 repo 外缓存：{HISTORY_DIR}",
        "GEX 只有同一指数 / 同一脚本 / 同一 aggregate+near 口径前值，才允许写 confirmed outflow",
    ]
    if inferred_market not in {"US", "HK"}:
        notes.append("当前标的不是美股/港股；本 risk regime 仍可作为跨市场风险背景，但优先级低于本地制度性结构")

    return {
        "subject": subject,
        "market": inferred_market,
        "generated_at": iso_now(),
        "applicable": True,
        "index_symbol": index_symbol.upper(),
        "state_dir": str(RISK_STATE_DIR),
        "history_path": history_path,
        "risk_regime": risk_regime,
        "triggered_count": len(triggered),
        "layers_triggered": layers,
        "layer_count": len(layers),
        "triggered_signals": [signal["title"] for signal in triggered],
        "triggered_signal_keys": [signal["key"] for signal in triggered],
        "position_multiplier": position_multiplier,
        "position_multiplier_band": "x0.0-0.2" if risk_regime == "forced_liquidation" else f"x{position_multiplier}",
        "max_long_action_level": max_action_level,
        "regime_impact": regime_impact,
        "drivers": [signal["reason"] for signal in triggered[:3]],
        "signals": signal_map,
        "gex_snapshot": gex_snapshot,
        "gex_comparison_basis": f"{index_symbol.upper()} / options_gamma.py / aggregate 45d + near expiry",
        "data_gaps": data_gaps,
        "sources": sources,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="自动抓取杀杠杆六信号，输出 risk regime")
    parser.add_argument("subject", nargs="?", default="SPY", help="标的或查询词，如 TSLA / SPY / AAPL")
    parser.add_argument("--market", default=None, help="可选：A/HK/US")
    parser.add_argument("--index-symbol", default="SPY", help="指数/GEX 锚定符号，默认 SPY")
    parser.add_argument("--no-store", action="store_true", help="只计算不落盘历史快照")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认即 JSON）")
    args = parser.parse_args()

    try:
        result = collect(args.subject, args.market, args.no_store, args.index_symbol)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "subject": args.subject}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
