#!/usr/bin/env python3
"""Markdown rendering and redaction for the daily trading journal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from daily_journal_data import (
    SCHEMA_VERSION,
    aggregate_entries,
    aggregate_rejected,
    atomic_write,
    dedupe_count,
    iter_jsonl,
)

MIN_CALIBRATION_SAMPLE = 12
REDACT_KEYWORDS = ("token", "credential", "auth")

def redact_text(text: str) -> str:
    """Line-level redaction: any line mentioning a credential keyword or the
    literal absolute home directory is replaced wholesale, never partially
    leaked. Self-test asserts a token_path fixture line never survives this."""
    home = str(Path.home())
    home_lower = home.lower()
    out = []
    for line in text.split("\n"):
        low = line.lower()
        if any(k in low for k in REDACT_KEYWORDS) or (home and home_lower in low):
            out.append("[redacted]")
        else:
            out.append(line)
    return "\n".join(out)

def fmt(value: Any, default: str = "data_gap") -> str:
    if value is None:
        return default
    return str(value)

def render_day_markdown(d: str, bucket: dict[str, Any], calibration_available: bool) -> str:
    lines: list[str] = [f"# Daily Trading Journal · {d}", ""]

    # 1. 盘前预测
    lines.append("## 盘前预测")
    ps = bucket["policy_summary"]
    if ps:
        lines.append(
            f"- Universe: {ps['universe_count']} symbols ({ps['universe_core_count']} core); "
            f"max_positions={fmt(ps['max_positions'])}; "
            f"max_notional_per_order_pct={fmt(ps['max_notional_per_order_pct'])}; "
            f"daily_new_orders_limit={fmt(ps['daily_new_orders_limit'])}"
        )
    else:
        lines.append("- Universe/risk gate: data_gap (no decision packet found for this trading day)")
    if bucket["regime_profiles"]:
        lines.append(f"- Observed regime_profile (from exit-engine diagnostics): {', '.join(sorted(bucket['regime_profiles']))}")
    else:
        lines.append("- Observed regime_profile: data_gap (no exit-engine diagnostics this day)")
    if bucket["entries"]:
        lines.append("")
        lines.append("| symbol | side | cycles | qty | limit | win_rate_proxy | confidence | thesis | invalidation |")
        lines.append("|---|---|---:|---|---|---|---|---|---|")
        for e in aggregate_entries(bucket["entries"]):
            lines.append(
                f"| {e['symbol']} | {e['side']} | {e['count']} | {e['qty_range']} | {e['price_range']} | "
                f"{e['win_rate_range']} | {e['confidence']} | {fmt(e['thesis'])} | {fmt(e['invalidation'])} |"
            )
    else:
        lines.append("- New entry proposals: none generated this day")

    # 2. 盘中执行
    lines.append("")
    lines.append("## 盘中执行")
    if bucket["orders_submitted"]:
        lines.append("Submitted orders:")
        for o in bucket["orders_submitted"]:
            lines.append(
                f"- {fmt(o.get('time_hhmm'))} {fmt(o['symbol'])} {fmt(o['side'])} {fmt(o['quantity'])} @ {fmt(o['price'])} "
                f"(status={o['status']}, returncode={o['returncode']})"
            )
    else:
        lines.append("- Submitted orders: none")
    if bucket["orders_rejected"]:
        lines.append("Rejected at validation:")
        for r in aggregate_rejected(bucket["orders_rejected"]):
            lines.append(
                f"- {fmt(r.get('time_range'))} {r['symbol']} {r['side']} ×{r['count']} attempt{'s' if r['count'] != 1 else ''}, "
                f"qty {r['qty_range']}, limit {r['price_range']}: {', '.join(r['reasons'])}"
            )
    if bucket["orders_replaced"]:
        lines.append("Replaced (price/qty amendments):")
        for o in bucket["orders_replaced"]:
            lines.append(
                f"- {fmt(o.get('time_hhmm'))} proposal={o['proposal_id']}: {fmt(o['old_price'])} -> {fmt(o['new_price'])}, "
                f"qty={fmt(o['quantity'])}, reason={fmt(o['reason'])}"
            )
    if bucket["exit_orders"]:
        lines.append("Mechanical exit orders submitted:")
        for o, n in dedupe_count(bucket["exit_orders"], ("symbol", "quantity", "limit_price", "exit_reason")):
            suffix = f" (x{n} scan cycles)" if n > 1 else ""
            lines.append(f"- {fmt(o.get('time_hhmm'))} {o['symbol']} sell {fmt(o['quantity'])} @ {fmt(o['limit_price'])} (reason={fmt(o['exit_reason'])}){suffix}")
    diag = bucket["exit_diag"]
    if diag["checked"]:
        lines.append(
            f"- Exit-engine risk sweep: checked={diag['checked']}, no_exit={diag['no_exit']}, "
            f"skipped={diag['skipped']}, other={diag['other']}"
        )
    if bucket["outcomes_reconciled"]:
        lines.append("Not-filled reconciliations:")
        for o, n in dedupe_count(bucket["outcomes_reconciled"], ("symbol", "reason", "status")):
            suffix = f" (x{n})" if n > 1 else ""
            lines.append(f"- {o['symbol']}: {o['reason']}{suffix}")

    # 3. 盘后复盘与归因
    lines.append("")
    lines.append("## 盘后复盘与归因")
    snap = bucket["snapshot"]
    if snap:
        lines.append(
            f"- Day-end equity (net_assets): {fmt(snap['net_assets'])}; "
            f"market_value={fmt(snap['market_value'])}; unrealized_pnl={fmt(snap['unrealized_pnl'])} "
            f"({fmt(snap['unrealized_pnl_pct'])}%); positions={fmt(snap['position_count'])}"
        )
    else:
        lines.append("- Day-end equity: data_gap (no position snapshot logged this day)")
    if bucket["outcomes_closed"]:
        lines.append("")
        lines.append("| symbol | side | qty | exit_price | exit_reason | R | realized_pnl |")
        lines.append("|---|---|---:|---:|---|---:|---:|")
        for c in bucket["outcomes_closed"]:
            pnl = c["realized_pnl"] if c["realized_pnl"] is not None else "data_gap"
            lines.append(
                f"| {c['symbol']} | {c['side']} | {fmt(c['quantity'])} | {fmt(c['exit_price'])} | "
                f"{fmt(c['exit_reason'])} | {fmt(c['r_multiple'])} | {pnl} |"
            )
    else:
        lines.append("- Closed trades: none this day")
    if calibration_available:
        if bucket["calibration_scored"]:
            hits = sum(1 for c in bucket["calibration_scored"] if c["outcome"] == 1)
            lines.append(
                f"- Prediction hit judgment (calibration bucket, authoritative source): "
                f"{len(bucket['calibration_scored'])} scored, {hits} hit"
            )
            for c in bucket["calibration_scored"]:
                lines.append(f"  - {c['symbol']}: predicted={c['predicted']}, outcome={'win' if c['outcome'] == 1 else 'loss'}")
        else:
            lines.append("- Prediction hit judgment: no calibration-bucket rows scored this day")
    else:
        lines.append("- Prediction hit judgment: data_gap (calibration bucket unreachable)")

    # 4. 经验沉淀
    lines.append("")
    lines.append("## 经验沉淀")
    if bucket["hypotheses"]:
        for h in bucket["hypotheses"]:
            lines.append(f"- Hypothesis `{h['hypothesis_id']}` -> {h['status']}: {h['statement']}")
    else:
        lines.append("- Hypothesis registry changes: none")
    error_labels = sorted({c["exit_reason"] for c in bucket["outcomes_closed"] if c.get("exit_reason")})
    if error_labels:
        lines.append(f"- Exit-reason labels observed today: {', '.join(error_labels)}")
    else:
        lines.append("- Exit-reason labels observed today: none")
    if bucket["data_gaps"]:
        lines.append(f"- Data gaps: {', '.join(sorted(bucket['data_gaps']))}")

    return redact_text("\n".join(lines) + "\n")


def build_index_row(d: str, bucket: dict[str, Any]) -> dict[str, Any]:
    closed = bucket["outcomes_closed"]
    matched_pnls = [c["realized_pnl"] for c in closed if c["realized_pnl"] is not None]
    data_gaps = sorted(bucket["data_gaps"])
    if not bucket["policy_summary"]:
        data_gaps.append("no_decision_packet")
    snap = bucket["snapshot"]
    row = {
        "date": d,
        "schema_version": SCHEMA_VERSION,
        "proposals": len(aggregate_entries(bucket["entries"])),
        "orders_submitted": len(bucket["orders_submitted"]),
        "orders_filled": sum(1 for o in bucket["outcomes_open"]) + sum(1 for c in closed),
        "closed_trades": len(closed),
        "realized_pnl": round(sum(matched_pnls), 2) if matched_pnls else (0.0 if not closed else None),
        "day_equity": snap["net_assets"] if snap else None,
        "day_equity_change_pct": None,
        "predictions_scored": len(bucket["calibration_scored"]),
        "prediction_hits": sum(1 for c in bucket["calibration_scored"] if c["outcome"] == 1),
        "error_labels": sorted({c["exit_reason"] for c in closed if c.get("exit_reason")}),
        "data_gaps": sorted(set(data_gaps)),
    }
    return row


def fill_equity_change(rows_by_date: dict[str, dict[str, Any]]) -> None:
    sorted_dates = sorted(rows_by_date.keys())
    prev_equity = None
    for d in sorted_dates:
        row = rows_by_date[d]
        eq = row.get("day_equity")
        if eq is not None and prev_equity is not None and prev_equity != 0:
            row["day_equity_change_pct"] = round((eq - prev_equity) / prev_equity * 100, 3)
        elif eq is not None and prev_equity is None:
            row["data_gaps"] = sorted(set(row.get("data_gaps", []) + ["no_prior_day_equity"]))
        if eq is not None:
            prev_equity = eq


# --------------------------------------------------------------------------
# Index / README maintenance
# --------------------------------------------------------------------------

def load_index(index_path: Path) -> dict[str, dict[str, Any]]:
    rows = {}
    for r in iter_jsonl(index_path):
        if r.get("date"):
            rows[r["date"]] = r
    return rows


def write_index(index_path: Path, rows_by_date: dict[str, dict[str, Any]]) -> None:
    lines = [json.dumps(rows_by_date[d], ensure_ascii=False, sort_keys=True) for d in sorted(rows_by_date.keys())]
    content = redact_text("\n".join(lines) + ("\n" if lines else ""))
    atomic_write(index_path, content)


def render_readme(rows_by_date: dict[str, dict[str, Any]]) -> str:
    dates = sorted(rows_by_date.keys())
    lines = ["# Trading Journal · 记分板", ""]
    lines.append(f"- 运行天数: {len(dates)}")
    total_pnl = sum(r["realized_pnl"] for r in rows_by_date.values() if r.get("realized_pnl") is not None)
    lines.append(f"- 累计 realized PnL（部分数据可能因缺 entry 记录标 data_gap）: {round(total_pnl, 2)}")
    total_scored = sum(r.get("predictions_scored", 0) for r in rows_by_date.values())
    total_hits = sum(r.get("prediction_hits", 0) for r in rows_by_date.values())
    if total_scored >= MIN_CALIBRATION_SAMPLE:
        hit_rate = round(total_hits / total_scored * 100, 1)
        lines.append(f"- 预测配对数: {total_scored}, 命中率: {hit_rate}%")
    else:
        lines.append(f"- 预测配对数: {total_scored}（insufficient_sample，阈值 {MIN_CALIBRATION_SAMPLE}）")
    lines.append("")
    lines.append("## 最近 30 日")
    lines.append("| date | proposals | orders_submitted | closed_trades | realized_pnl | day_equity | day_equity_change_pct | predictions | hits | error_labels |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for d in dates[-30:]:
        r = rows_by_date[d]
        lines.append(
            f"| {d} | {r['proposals']} | {r['orders_submitted']} | {r['closed_trades']} | "
            f"{fmt(r['realized_pnl'])} | {fmt(r['day_equity'])} | {fmt(r['day_equity_change_pct'])} | "
            f"{r['predictions_scored']} | {r['prediction_hits']} | {', '.join(r['error_labels']) or 'none'} |"
        )
    return redact_text("\n".join(lines) + "\n")
