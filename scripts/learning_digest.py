#!/usr/bin/env python3
"""Weekly learning digest for trading-research.

Self-evolution feedback loop C. Loops A and B and the daily ledger each emit
machine JSON; nobody reads JSON every day. This script is the human-facing
close of the loop: once a week it aggregates the ledger, the calibration
scorecard, the decision-memory review and the staged eval candidates into one
short plain-Chinese digest the user can actually read — what the skill learned,
whether it is fooling itself, and what still needs a human call.

Read-only. No broker access. No trade execution. No file edits to the skill.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from calibration_scorecard import build_scorecard
from self_optimization_ledger import health_report, ledger_path, read_rows
from trading_memory_core import connect, db_path, review_stats, verified_rows

DEFAULT_DIGEST_DIR = Path("~/.hermes/work/trading-research-autoevolve/digests").expanduser()

# Failure tags in plain Chinese — the user reads outcomes, not jargon.
FAILURE_TAG_PLAIN = {
    "quick_loss": "进场就被快速打损",
    "chase_reversal": "追高/追反转被套",
    "fake_breakout": "假突破上当",
    "flow_against": "资金流和方向相反",
    "news_against": "消息面利空",
    "smart_money_against": "主力在反向操作",
    "model_signal_against": "模型信号相反",
    "low_coverage_loss": "数据不全就下手亏的",
    "execution_risk_high": "执行/流动性风险高",
    "entry_too_early": "入场太早",
    "direction_invalidated": "方向看错了",
    "over_optimistic": "判断太乐观",
    "over_conservative": "判断太保守",
    "take_profit_too_early": "止盈太早",
    "risk_warning_insufficient": "风险提示不够",
    "timeout_failure": "拖太久没动作",
    "watch_missed_opportunity": "光看没上、错过",
    "avoid_missed_opportunity": "回避了结果涨了",
    "symbol_drag": "某些票一直拖后腿",
    "regime_misread": "看错大环境",
    "data_gap_mispriced": "数据缺口定价错",
}


def plain_tag(tag: str) -> str:
    return FAILURE_TAG_PLAIN.get(tag, tag)


def calibration_headline(card: dict[str, Any]) -> str:
    if card.get("calibration_status") != "ok":
        return "样本还不够，预测准不准这周先不下结论。"
    gap = card.get("calibration_gap") or 0.0
    posture = card.get("confidence_posture")
    pts = abs(round(gap * 100))
    if posture == "overconfident":
        return f"有点自我感觉良好：嘴上胜率比实际高约 {pts} 个点，下手前打个折。"
    if posture == "underconfident":
        return f"反而太保守：实际比预估好约 {pts} 个点，该出手时别缩。"
    return "预测和实际基本对得上，信心是诚实的。"


def memory_block(stats: dict[str, Any]) -> dict[str, Any]:
    top_failures = [
        {"tag": tag, "plain": plain_tag(tag), "count": count}
        for tag, count in list(stats.get("failure_tags", {}).items())[:5]
    ]
    drags = [
        {"symbol": d.get("symbol"), "sum_return_pct": d.get("sum_return_pct"), "failure": d.get("failure")}
        for d in (stats.get("biggest_drag_symbols") or [])[:5]
        if (d.get("sum_return_pct") or 0) < 0
    ]
    return {
        "sample_count": stats.get("sample_count", 0),
        "win_rate": stats.get("win_rate", 0),
        "decayed_win_rate": stats.get("decayed_win_rate", 0),
        "avg_return_pct": stats.get("avg_return_pct"),
        "top_failures": top_failures,
        "drag_symbols": drags,
    }


def candidate_block(conn, window: int) -> dict[str, Any]:
    """Best-effort: how many guard candidates are waiting for a human call."""
    try:
        from eval_candidate_generator import generate

        report = generate(conn, window, min_count=3, min_share=0.2, include_packet=True)
        return {
            "verified_guard_count": report.get("verified_guard_count", 0),
            "compiler_gap_count": report.get("compiler_gap_count", 0),
            "manual_review_count": len(report.get("manual_review") or []),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": type(exc).__name__, "message": str(exc)}


def build_digest(conn, ledger_rows: list[dict[str, Any]], window: int) -> dict[str, Any]:
    stats = review_stats(verified_rows(conn, window))
    card = build_scorecard(conn, window=window, min_samples=12)
    mem = memory_block(stats)
    cand = candidate_block(conn, window)
    health = health_report(ledger_rows)

    recent = ledger_rows[-7:]
    materiality_counts: dict[str, int] = {}
    for row in recent:
        key = row.get("materiality") or "none"
        materiality_counts[key] = materiality_counts.get(key, 0) + 1
    versions = [r.get("version") for r in recent if r.get("version")]

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "calibration_headline": calibration_headline(card),
        "calibration": {
            "status": card.get("calibration_status"),
            "gap": card.get("calibration_gap"),
            "confidence_posture": card.get("confidence_posture"),
            "skill_score": card.get("skill_score"),
            "materiality": card.get("calibration_materiality"),
        },
        "memory": mem,
        "candidates": cand,
        "ledger": {
            "runs_last_7": len(recent),
            "materiality_counts": materiality_counts,
            "latest_version": versions[-1] if versions else None,
            "health_ok": health.get("ok"),
            "health_issues": health.get("issues") or [],
        },
        "no_order_execution": True,
    }


def render_markdown(digest: dict[str, Any]) -> str:
    mem = digest["memory"]
    cand = digest["candidates"]
    led = digest["ledger"]
    lines: list[str] = []
    lines.append(f"# trading-research 周学习摘要（{digest['generated_at'][:10]}）")
    lines.append("")
    lines.append(f"**一句话**：{digest['calibration_headline']}")
    lines.append("")

    lines.append("## 战绩")
    if mem["sample_count"]:
        lines.append(f"- 已复盘 {mem['sample_count']} 笔；胜率 {mem['win_rate']}，近端加权胜率 {mem['decayed_win_rate']}。")
        if mem["avg_return_pct"] is not None:
            lines.append(f"- 平均每笔收益 {mem['avg_return_pct']}%。")
    else:
        lines.append("- 本窗口还没有已验证的复盘记录。")
    lines.append("")

    lines.append("## 最常踩的坑")
    if mem["top_failures"]:
        for f in mem["top_failures"]:
            lines.append(f"- {f['plain']}（{f['count']} 次）")
    else:
        lines.append("- 暂无明显复发的失败模式。")
    lines.append("")

    if mem["drag_symbols"]:
        lines.append("## 拖后腿的票")
        for d in mem["drag_symbols"]:
            lines.append(f"- {d['symbol']}：累计 {d['sum_return_pct']}%，失败 {d['failure']} 次")
        lines.append("")

    lines.append("## 待你拍板")
    parts = []
    if isinstance(cand.get("verified_guard_count"), int):
        parts.append(f"{cand['verified_guard_count']} 条可固化的护栏候选")
    if isinstance(cand.get("compiler_gap_count"), int) and cand["compiler_gap_count"]:
        parts.append(f"{cand['compiler_gap_count']} 条暴露编译器漏洞（要改方法论，不是加测试）")
    if isinstance(cand.get("manual_review_count"), int) and cand["manual_review_count"]:
        parts.append(f"{cand['manual_review_count']} 条需人工映射")
    lines.append(f"- {'；'.join(parts)}。都已暂存，等你确认后才会进 golden set。" if parts else "- 本周没有需要人工确认的候选。")
    lines.append("")

    lines.append("## 系统健康")
    if led["health_ok"]:
        lines.append(f"- 一切正常（近 {led['runs_last_7']} 次自检）。当前版本 {led['latest_version'] or '未知'}。")
    else:
        for issue in led["health_issues"]:
            if isinstance(issue.get("consecutive_days"), int):
                detail = f"连续 {issue['consecutive_days']} 天"
            elif isinstance(issue.get("consecutive_runs"), int):
                detail = f"连续 {issue['consecutive_runs']} 次"
            elif isinstance(issue.get("from"), int) and isinstance(issue.get("to"), int):
                detail = f"{issue['from']} → {issue['to']}"
            else:
                detail = "详情见结构化 ledger health"
            lines.append(f"- ⚠️ {issue.get('type')}：{detail}")
    lines.append("")
    lines.append("> 本摘要只读、不下单、不自动改 skill；所有改动仍要过 materiality + before/after eval 门。")
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    import argparse as _argparse
    import tempfile

    from trading_memory_core import cmd_record_decision, cmd_record_result, now_iso

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "memory.sqlite"
        conn = connect(path)
        for i in range(8):
            won = i < 5
            decision = {
                "symbol": "DGT",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "factors": {"estimated_win_rate": 0.6},
                "review_clock": now_iso(),
            }
            dpath = Path(td) / f"d{i}.json"
            dpath.write_text(json.dumps(decision), encoding="utf-8")
            out = cmd_record_decision(conn, _argparse.Namespace(payload=str(dpath), db=str(path)))
            result = {
                "return_pct": 4.0 if won else -6.0,
                "outcome": "success" if won else "failure",
                "failure_tags": [] if won else ["fake_breakout"],
            }
            rpath = Path(td) / f"r{i}.json"
            rpath.write_text(json.dumps(result), encoding="utf-8")
            cmd_record_result(
                conn,
                _argparse.Namespace(payload=str(rpath), decision_id=out["decision_id"], db=str(path)),
            )
        ledger_rows = [
            {"materiality": "none", "version": "2.20", "status": "no_necessary_upgrade",
             "sources_checked": {"github": "refreshed", "grok": "available"}, "eval_pass_count": 5}
        ]
        digest = build_digest(conn, ledger_rows, window=36)
        conn.close()
        md = render_markdown(digest)
        assert digest["memory"]["sample_count"] == 8, digest
        assert "周学习摘要" in md and "战绩" in md, md
        assert "假突破上当" in md, md  # plain-Chinese failure tag rendered
        return {"ok": True, "self_test": "passed", "headline": digest["calibration_headline"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly plain-Chinese learning digest for trading-research")
    ap.add_argument("--db", help="SQLite DB path; default TRADING_MEMORY_DB or core default")
    ap.add_argument("--window", type=int, default=200)
    ap.add_argument("--json", action="store_true", help="emit structured JSON instead of markdown")
    ap.add_argument("--out", help="write the markdown digest to this path (default: print)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    rows = read_rows(ledger_path())
    conn = connect(db_path(args))
    try:
        digest = build_digest(conn, rows, args.window)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(digest, ensure_ascii=False, indent=2))
        return 0

    md = render_markdown(digest)
    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "written": str(out_path)}, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
