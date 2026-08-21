#!/usr/bin/env python3
"""A-share market sentiment cycle analyzer (A股专项，按需加载).

Quantifies limit-up/limit-down ecology, board ladder, sector diffusion, and
emotion phase labels. Does not execute orders or emit buy/sell instructions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from data_freshness_guard import (  # noqa: E402
    degrade_payload,
    format_trade_date,
    now_iso,
    parse_trade_date,
    validate_as_of_alignment,
)

AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or "/opt/homebrew/bin/python3"
EMOTION_PHASES = ("ice", "start", "ferment", "climax", "divergence", "retreat")
FLOW_STATES = ("first_inflow", "continuous_inflow", "recovery_inflow", "outflow", "mixed", "unknown")


def _no_proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[key] = ""
    env["no_proxy"] = "*"
    return env


def run_akshare(code: str, timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [AKSHARE_PYTHON, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_no_proxy_env(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error": "timeout"}
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def classify_emotion_phase(metrics: dict[str, Any]) -> dict[str, Any]:
    zt = int(metrics.get("zt_count") or 0)
    zb = int(metrics.get("zb_count") or 0)
    dt = int(metrics.get("dt_count") or 0)
    max_height = int(metrics.get("max_board_height") or 0)
    zb_rate = float(metrics.get("zb_rate") or 0.0)
    premium = metrics.get("yesterday_zt_premium_pct")

    reasons: list[str] = []
    if dt >= 20 or (zt <= 15 and max_height <= 2 and (premium is None or premium < 0)):
        phase = "ice"
        reasons.append("跌停偏多或涨停/高度/溢价同时偏弱")
    elif zt >= 80 and zb_rate >= 0.35:
        phase = "divergence"
        reasons.append("涨停数量高但炸板率偏高，高位分歧")
    elif zt >= 70 and max_height >= 4:
        phase = "climax"
        reasons.append("涨停数量与连板高度同时处于高位")
    elif dt >= 10 and zt <= 40:
        phase = "retreat"
        reasons.append("涨停收缩且跌停抬升")
    elif 25 <= zt <= 70 and max_height >= 3:
        phase = "ferment"
        reasons.append("主线高度打开且涨停扩散")
    elif zt >= 15 and max_height >= 2:
        phase = "start"
        reasons.append("涨停与高度从低位抬升")
    else:
        phase = "start" if zt >= 10 else "ice"
        reasons.append("指标处于过渡区，按弱/强默认归类")

    confidence = "high"
    if metrics.get("data_gaps"):
        confidence = "low"
    elif premium is None or metrics.get("dt_count") is None:
        confidence = "medium"

    return {
        "phase": phase,
        "confidence": confidence,
        "reasons": reasons,
        "threshold_notes": {
            "zt_count": zt,
            "zb_rate": round(zb_rate, 4),
            "max_board_height": max_height,
            "dt_count": dt,
            "yesterday_zt_premium_pct": premium,
        },
    }


def classify_fund_flow_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify multi-day main-net flow using same-source daily rows only."""
    if not rows:
        return {
            "state": "unknown",
            "basis": "empty_series",
            "notes": ["缺少同源逐日主力净流入序列"],
        }
    nets = [row.get("main_net") for row in rows if isinstance(row.get("main_net"), (int, float))]
    if len(nets) < 3:
        return {
            "state": "unknown",
            "basis": "insufficient_history",
            "sample_days": len(nets),
            "notes": ["至少需要 3 个同源交易日样本"],
        }
    recent = nets[-5:]
    positive = [value for value in recent if value > 0]
    negative = [value for value in recent if value < 0]
    if len(positive) >= 3 and all(value > 0 for value in recent[-3:]):
        state = "continuous_inflow"
    elif len(negative) >= 3 and recent[-1] > 0 and sum(recent[:-1]) < 0:
        state = "recovery_inflow"
    elif recent[-1] > 0 and sum(recent[:-1]) <= 0:
        state = "first_inflow"
    elif recent[-1] < 0 and sum(recent[:-1]) > 0:
        state = "outflow"
    else:
        state = "mixed"
    return {
        "state": state,
        "basis": "main_net_daily_same_source",
        "sample_days": len(recent),
        "recent_main_net": recent,
        "notes": ["仅基于同源日级主力净流入，不得与分钟快照混算 5/20 日累计"],
    }


def build_sector_ladder(zt_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in zt_rows:
        sector = str(row.get("sector") or row.get("所属行业") or "unknown")
        by_sector[sector].append(row)
    ranked = sorted(
        (
            {
                "sector": sector,
                "zt_count": len(items),
                "max_height": max(int(item.get("board_height") or item.get("连板数") or 1) for item in items),
                "leaders": sorted(
                    items,
                    key=lambda item: (
                        int(item.get("board_height") or item.get("连板数") or 1),
                        float(item.get("amount") or item.get("成交额") or 0),
                    ),
                    reverse=True,
                )[:3],
            }
            for sector, items in by_sector.items()
        ),
        key=lambda item: (item["zt_count"], item["max_height"]),
        reverse=True,
    )
    return {
        "top_sectors": ranked[:8],
        "sector_count": len(ranked),
        "dispersion_score": round(len(ranked) / max(len(zt_rows), 1), 4),
    }


def analyze_metrics(
    *,
    trade_date: str,
    metrics: dict[str, Any],
    zt_rows: list[dict[str, Any]],
    fund_flow_rows: list[dict[str, Any]] | None = None,
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    freshness = freshness or validate_as_of_alignment(
        as_of=trade_date,
        target_trade_date=trade_date,
        observed_at=now_iso(),
    )
    emotion = classify_emotion_phase(metrics)
    sector_ladder = build_sector_ladder(zt_rows)
    fund_flow = classify_fund_flow_state(fund_flow_rows or [])
    may_write = freshness.get("may_write_formal_conclusion", False) and not metrics.get("data_gaps")
    return {
        "ok": True,
        "module": "a_share_sentiment_cycle",
        "market": "A",
        "trade_date": trade_date,
        "observed_at": now_iso(),
        "freshness_guard": freshness,
        "metrics": metrics,
        "emotion_cycle": emotion,
        "sector_ladder": sector_ladder,
        "fund_flow_state": fund_flow,
        "may_write_formal_conclusion": may_write,
        "routing": {
            "scope": "A_share_short_term_only",
            "must_not_apply_to": ["HK", "US", "macro_only", "long_horizon_fundamental"],
        },
        "lookback_contract": {
            "record_predictions_with": "scripts/prediction_ledger.py",
            "settle_with": "scripts/record_due_results.py",
            "regime_bucket_field": "regime",
            "append_only": True,
        },
        "data_gaps": list(metrics.get("data_gaps") or []) + list(freshness.get("data_gaps") or []),
        "no_order_execution": True,
    }


def fetch_live_snapshot(trade_date: str, symbol: str | None = None) -> dict[str, Any]:
    code = f"""import warnings, json
warnings.filterwarnings('ignore')
import akshare as ak
out = {{'trade_date': '{trade_date}', 'data_gaps': []}}
try:
    zt = ak.stock_zt_pool_em(date='{trade_date}')
    rows = []
    for _, row in zt.iterrows():
        item = {{k: (None if str(v) == 'nan' else v) for k, v in row.to_dict().items()}}
        board = item.get('连板数') or item.get('连板天数') or 1
        try:
            board = int(board)
        except Exception:
            board = 1
        item['board_height'] = board
        item['sector'] = item.get('所属行业')
        rows.append(item)
    out['zt_rows'] = rows
    out['zt_count'] = len(rows)
    out['max_board_height'] = max([r.get('board_height', 1) for r in rows], default=0)
except Exception as exc:
    out['data_gaps'].append({{'gap': 'zt_pool_unavailable', 'severity': 'high', 'impact': str(exc)}})
    out['zt_rows'] = []
    out['zt_count'] = 0
    out['max_board_height'] = 0
try:
    zb = ak.stock_zt_pool_zbgc_em(date='{trade_date}')
    out['zb_count'] = len(zb)
except Exception as exc:
    out['data_gaps'].append({{'gap': 'zb_pool_unavailable', 'severity': 'medium', 'impact': str(exc)}})
    out['zb_count'] = None
try:
    dt = ak.stock_zt_pool_dtgc_em(date='{trade_date}')
    out['dt_count'] = len(dt)
except Exception as exc:
    out['data_gaps'].append({{'gap': 'dt_pool_unavailable', 'severity': 'medium', 'impact': str(exc)}})
    out['dt_count'] = None
zt = out.get('zt_count') or 0
zb = out.get('zb_count') or 0
out['zb_rate'] = (zb / zt) if zt else 0.0
out['yesterday_zt_premium_pct'] = None
fund_flow_rows = []
"""
    if symbol:
        code += f"""
try:
    ff = ak.stock_individual_fund_flow(stock='{symbol}', market='sh' if '{symbol}'.startswith('6') else 'sz')
    fund_rows = []
    for _, row in ff.tail(10).iterrows():
        fund_rows.append({{
            'date': str(row.get('日期')),
            'main_net': float(row.get('主力净流入-净额')) if row.get('主力净流入-净额') is not None else None,
        }})
    out['fund_flow_rows'] = fund_rows
except Exception as exc:
    out['data_gaps'].append({{'gap': 'fund_flow_history_unavailable', 'severity': 'medium', 'impact': str(exc)}})
    out['fund_flow_rows'] = []
"""
    else:
        code += "\nout['fund_flow_rows'] = []\n"
    code += "print(json.dumps(out, ensure_ascii=False, default=str))"
    res = run_akshare(code)
    if res["status"] != "pass":
        return degrade_payload(
            reason="akshare_fetch_failed",
            impact=res.get("stderr") or res.get("error") or "A股情绪结构抓取失败",
        )
    try:
        payload = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return degrade_payload(reason="akshare_payload_invalid", impact="AkShare 返回不可解析 JSON")
    metrics = {
        "zt_count": payload.get("zt_count"),
        "zb_count": payload.get("zb_count"),
        "dt_count": payload.get("dt_count"),
        "max_board_height": payload.get("max_board_height"),
        "zb_rate": payload.get("zb_rate"),
        "yesterday_zt_premium_pct": payload.get("yesterday_zt_premium_pct"),
        "data_gaps": payload.get("data_gaps") or [],
    }
    return analyze_metrics(
        trade_date=trade_date,
        metrics=metrics,
        zt_rows=payload.get("zt_rows") or [],
        fund_flow_rows=payload.get("fund_flow_rows") or [],
    )


def self_test() -> dict[str, Any]:
    trade_date = "20260819"
    mock_rows = [
        {"sector": "AI", "board_height": 4, "amount": 100},
        {"sector": "AI", "board_height": 2, "amount": 80},
        {"sector": "低空", "board_height": 2, "amount": 60},
    ]
    metrics = {
        "zt_count": 58,
        "zb_count": 12,
        "dt_count": 4,
        "max_board_height": 4,
        "zb_rate": 12 / 58,
        "yesterday_zt_premium_pct": 1.8,
        "data_gaps": [],
    }
    snapshot = analyze_metrics(
        trade_date=trade_date,
        metrics=metrics,
        zt_rows=mock_rows,
        fund_flow_rows=[
            {"main_net": -2.0},
            {"main_net": -1.0},
            {"main_net": -0.5},
            {"main_net": 1.2},
        ],
    )
    blocked = validate_as_of_alignment(
        as_of="20260818",
        target_trade_date=trade_date,
        observed_at="2026-08-19T07:00:00Z",
    )
    checks = [
        snapshot["ok"] is True,
        snapshot["emotion_cycle"]["phase"] in EMOTION_PHASES,
        snapshot["sector_ladder"]["top_sectors"][0]["sector"] == "AI",
        snapshot["fund_flow_state"]["state"] == "recovery_inflow",
        blocked["may_write_formal_conclusion"] is False,
    ]
    return {
        "ok": all(checks),
        "self_test": "passed" if all(checks) else "failed",
        "sample_phase": snapshot["emotion_cycle"]["phase"],
        "sample_flow_state": snapshot["fund_flow_state"]["state"],
        "blocked_guard": blocked["status"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A-share sentiment cycle analyzer")
    parser.add_argument("--trade-date", default=format_trade_date(date.today()))
    parser.add_argument("--symbol", default=None, help="Optional A-share code for fund-flow state")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        payload = self_test()
    else:
        parsed = parse_trade_date(args.trade_date)
        if parsed is None:
            payload = degrade_payload(reason="invalid_trade_date", impact="trade-date 无效")
        else:
            payload = fetch_live_snapshot(format_trade_date(parsed), args.symbol)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json or args.self_test else None))
    ok = bool(payload.get("ok")) or payload.get("self_test") == "passed"
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
