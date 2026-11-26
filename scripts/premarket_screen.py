#!/usr/bin/env python3
"""Research-only premarket factor screen that emits watch priority, never actions."""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import data_freshness_guard as freshness  # noqa: E402
import factor_engine  # noqa: E402
import factor_panel  # noqa: E402
import hypothesis_registry  # noqa: E402


def envelope(schema: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": schema, **fields, "no_order_execution": True}


def _direction(value: str) -> int | None:
    if value == "+":
        return 1
    if value == "-":
        return -1
    return None


def _zscore(values: dict[str, float | None]) -> dict[str, float | None]:
    valid = {key: float(value) for key, value in values.items() if factor_engine.finite(value)}
    if not valid:
        return {key: None for key in values}
    center = statistics.fmean(valid.values())
    scale = statistics.pstdev(valid.values())
    return {key: ((valid[key] - center) / scale if scale else 0.0) if key in valid else None for key in values}


def _signal(*, module: str, reason: str, trade_date: str, hard_veto: bool) -> dict[str, Any]:
    return {
        "module": module, "sub_framework": "premarket_factor_screen",
        "max_action_level": "L0", "entry_permission": "BLOCK",
        "holding_directive": "HOLD", "position_multiplier": 0.0,
        "hard_veto": hard_veto, "evidence_refs": ["DATA-GAP-premarket-factor-screen"],
        "observed_at": trade_date, "stale_after": trade_date, "reason": reason,
        "repair_signal": "refresh_factor_panel_and_rerun",
        "tighten_only": True, "cannot_raise_upstream": True,
        "no_order_execution": True,
    }


def _confirmed_map(registry_rows: list[dict[str, Any]], factors: list[str], market: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in registry_rows:
        tags = {str(tag) for tag in row.get("tags") or []}
        matches = [name for name in factors if name in tags]
        if row.get("status") == "confirmed_alive" and market in tags:
            for name in matches:
                result[name] = row
    return result


def _sentiment_flags(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    flags: list[str] = []
    for key in ("sentiment_phase", "phase", "market_phase", "fund_flow_state", "money_flow_state"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            flags.append(f"{key}={value}")
    for key in ("risk_flags", "data_gaps"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            flags.append(f"sentiment_{key}={len(value)}")
    return flags


def screen_panels(
    *, market: str, trade_date: str, panels: dict[str, list[dict[str, Any]]],
    registry_rows: list[dict[str, Any]], factors: list[str], conviction_floor: int = 2,
    sentiment_payload: dict[str, Any] | None = None, min_cross_section: int = 30,
) -> dict[str, Any]:
    if conviction_floor < 1 or min_cross_section < 2:
        raise ValueError("conviction_floor must be >=1 and min_cross_section must be >=2")
    unknown = sorted(set(factors) - set(factor_engine.FACTOR_METHODS))
    if unknown:
        raise ValueError(f"unknown factors: {unknown}")
    latest_by_symbol = {
        symbol: max((str(row.get("date")) for row in rows if row.get("date")), default="")
        for symbol, rows in panels.items()
    }
    as_of_values = sorted({value for value in latest_by_symbol.values() if value})
    as_of = as_of_values[0] if len(as_of_values) == 1 else None
    freshness_result = freshness.validate_as_of_alignment(as_of=as_of, target_trade_date=trade_date, observed_at=as_of)
    guard_status = freshness_result["status"]
    data_gaps: list[Any] = list(freshness_result.get("data_gaps") or [])
    if len(as_of_values) != 1:
        guard_status = "blocked"
        data_gaps.append({"gap": "panel_as_of_dates_not_aligned", "severity": "high",
                          "impact": "横截面不是同一交易日，禁止排序"})
    confirmed = _confirmed_map(registry_rows, factors, market)
    factors_used = []
    for name, row in confirmed.items():
        direction = str(factor_engine.FACTOR_METHODS[name]["direction_hypothesis"])
        if _direction(direction) is None:
            data_gaps.append({"gap": f"confirmed_factor_without_vote_direction:{name}", "severity": "high",
                              "impact": "该因子只展示，不进入 vote"})
            continue
        factors_used.append({"name": name, "registry_status": "confirmed_alive",
                             "hypothesis_id": row.get("hypothesis_id"), "direction": direction})
    usable_names = [row["name"] for row in factors_used]
    hypothesis_displayed = [name for name in factors if name not in usable_names]

    raw_by_factor: dict[str, dict[str, float | None]] = {}
    for name in factors:
        raw_by_factor[name] = {}
        for symbol, rows in panels.items():
            values = factor_engine.compute_factor(rows, name)
            latest = latest_by_symbol.get(symbol)
            raw_by_factor[name][symbol] = values.get(latest) if latest else None
    scores = {name: _zscore(values) for name, values in raw_by_factor.items()}
    common_flags = _sentiment_flags(sentiment_payload)
    rows_out: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for symbol in sorted(panels):
        factor_scores = {name: scores[name].get(symbol) for name in factors}
        confirmed_values = [factor_scores[name] for name in usable_names]
        null_count = sum(value is None for value in confirmed_values)
        vote = 0
        for item in factors_used:
            value = factor_scores[item["name"]]
            if value is None or value == 0:
                continue
            vote += 1 if float(value) * int(_direction(item["direction"]) or 0) > 0 else -1
        reason = None
        if null_count >= 3:
            reason = "null_factors>=3"
            excluded.append({"symbol": symbol, "reason": reason})
        elif abs(vote) < conviction_floor:
            reason = "no_edge"
        row_flags = list(common_flags)
        if reason:
            row_flags.append(reason)
        rows_out.append({"symbol": symbol, "factor_scores": factor_scores, "null_count": null_count,
                         "vote": vote if usable_names else None, "edge_status": reason or "edge",
                         "watch_priority_rank": None, "risk_flags": row_flags})

    ranking_status = "ok"
    ranking_blockers: list[str] = []
    valid_cross_section = sum(
        any(factor_engine.finite(scores[name].get(symbol)) for name in usable_names)
        for symbol in panels
    )
    if not usable_names:
        ranking_blockers.append("no_confirmed_factors")
    if valid_cross_section < min_cross_section:
        ranking_blockers.append("insufficient_cross_section")
    if guard_status == "blocked":
        ranking_blockers.append("freshness_blocked")
    elif guard_status == "degraded":
        ranking_blockers.append("freshness_degraded")
    if not usable_names:
        ranking_status = "no_confirmed_factors_ranking_unavailable"
    elif valid_cross_section < min_cross_section:
        ranking_status = "insufficient_cross_section_ranking_unavailable"
        data_gaps.append({"gap": "premarket_cross_section_below_minimum", "severity": "high",
                          "available": valid_cross_section, "required": min_cross_section,
                          "impact": "有效历史标的不足，禁止 vote/rank"})
    elif guard_status == "blocked":
        ranking_status = "freshness_blocked_ranking_unavailable"
    elif guard_status == "degraded":
        ranking_status = "freshness_degraded_watch_only"
    else:
        eligible = [row for row in rows_out if row["edge_status"] == "edge"]
        eligible.sort(key=lambda row: (-abs(int(row["vote"])), -int(row["vote"]), row["symbol"]))
        for rank, row in enumerate(eligible, 1):
            row["watch_priority_rank"] = rank
    signals: list[dict[str, Any]] = []
    if guard_status == "blocked":
        signals.append(_signal(module="data_quality", reason="freshness_blocked", trade_date=trade_date, hard_veto=True))
    elif guard_status == "degraded":
        signals.append(_signal(module="research_readiness", reason="freshness_degraded", trade_date=trade_date, hard_veto=False))
    if sentiment_payload and (sentiment_payload.get("status") in {"blocked", "insufficient_data"}):
        signals.append(_signal(module="research_readiness", reason="sentiment_input_not_actionable", trade_date=trade_date, hard_veto=False))
    return envelope(
        "premarket_factor_screen.v1", market=market, trade_date=trade_date,
        freshness={**freshness_result, "status": guard_status}, ranking_status=ranking_status,
        ranking_blockers=ranking_blockers,
        factors_used=factors_used, hypothesis_factors_displayed=hypothesis_displayed,
        valid_cross_section=valid_cross_section, min_cross_section=min_cross_section,
        rows=rows_out, excluded=excluded, watch_priority_only=True, position_multiplier=0.0,
        compiler_effect="tighten_only" if signals else "none", suggested_module_signals=signals,
        data_gaps=data_gaps,
    )


def _load_sentiment(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sentiment file must contain a JSON object")
    return payload


def _symbols(args: argparse.Namespace) -> list[str]:
    values = list(args.symbols or [])
    if args.symbols_file:
        values.extend(line.strip().split()[0] for line in Path(args.symbols_file).read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError("at least one symbol is required")
    return values


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        panels: dict[str, list[dict[str, Any]]] = {}
        for index in range(5):
            symbol = f"S{index}"
            rows = []
            for day in range(25):
                rows.append({"date": f"2026-01-{day + 1:02d}", "close": 100 + index * day,
                             "low": 99 + index * day, "high": 101 + index * day,
                             "turnover_rate": 1 + index, "amount": 100000 + index})
            panels[symbol] = rows
        registry_path = base / "hypotheses.json"
        previous = os.environ.get("TRADING_RESEARCH_HYPOTHESES_PATH")
        os.environ["TRADING_RESEARCH_HYPOTHESES_PATH"] = str(registry_path)
        try:
            hypothesis_registry.save_registry(registry_path, [])
            result = screen_panels(market="A", trade_date="20260125", panels=panels,
                                   registry_rows=hypothesis_registry.load_registry(registry_path),
                                   factors=["mom_20_1"], conviction_floor=2)
            assert result["ranking_status"] == "no_confirmed_factors_ranking_unavailable"
            assert result["position_multiplier"] == 0.0
        finally:
            if previous is None:
                os.environ.pop("TRADING_RESEARCH_HYPOTHESES_PATH", None)
            else:
                os.environ["TRADING_RESEARCH_HYPOTHESES_PATH"] = previous


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=f"--factors: {factor_engine.FACTORS_HELP}"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--market", required=True, choices=sorted(factor_panel.MARKETS))
    run.add_argument("--symbols", nargs="*", default=[])
    run.add_argument("--symbols-file")
    run.add_argument("--trade-date", required=True)
    run.add_argument("--sentiment-file")
    run.add_argument("--factors", default="all", help=factor_engine.FACTORS_HELP)
    run.add_argument("--conviction-floor", type=int, default=2)
    run.add_argument("--min-cross-section", type=int, default=30)
    run.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list == ["--self-test"]:
        self_test()
        print(json.dumps({"ok": True, "self_test": "passed"}))
        return 0
    args = build_parser().parse_args(args_list)
    try:
        symbols = _symbols(args)
        panels, panel_gaps, _basis = factor_engine.load_panels(args.market, symbols)
        selected = list(factor_engine.DEFAULT_FACTORS) if args.factors == "all" else [item for item in args.factors.split(",") if item]
        registry_rows = hypothesis_registry.load_registry(hypothesis_registry.registry_path())
        result = screen_panels(market=args.market, trade_date=args.trade_date, panels=panels,
                               registry_rows=registry_rows, factors=selected,
                               conviction_floor=args.conviction_floor,
                               sentiment_payload=_load_sentiment(args.sentiment_file),
                               min_cross_section=args.min_cross_section)
        result["data_gaps"] = panel_gaps + result["data_gaps"]
        if panel_gaps:
            result["freshness"]["status"] = "blocked"
            result["ranking_status"] = "freshness_blocked_ranking_unavailable"
            result.setdefault("ranking_blockers", []).extend(["panel_gaps", "freshness_blocked"])
            result["ranking_blockers"] = list(dict.fromkeys(result["ranking_blockers"]))
            for row in result["rows"]:
                row["watch_priority_rank"] = None
            if not result["suggested_module_signals"]:
                result["suggested_module_signals"].append(
                    _signal(module="data_quality", reason="panel_gaps", trade_date=args.trade_date, hard_veto=True))
            result["compiler_effect"] = "tighten_only"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps(envelope("premarket_factor_screen_error.v1", ok=False, status="error",
                                  data_gaps=[{"reason_code": "error", "gap": str(exc)}]), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
