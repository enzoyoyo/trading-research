#!/usr/bin/env python3
"""Research-only quantile backtest and minimal cross-sectional attribution."""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import factor_engine as engine  # noqa: E402
import factor_panel  # noqa: E402

DEFAULT_RUN_DIR = Path.home() / ".cache" / "hermes" / "trading-research" / "factor-backtests"


def envelope(schema: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": schema, **fields, "no_order_execution": True}


def validate_config(
    *, bins: int, rebalance_days: int, fac_shift: int, fee_bps: float,
    slippage_bps: float, stamp_bps: float, stamp_direction: str,
) -> None:
    if bins < 3:
        raise ValueError("bins must be >=3")
    if rebalance_days < 1:
        raise ValueError("rebalance_days must be >=1")
    if fac_shift < 1:
        raise ValueError("fac_shift must be >=1")
    for name, value in (("fee_bps", fee_bps), ("slippage_bps", slippage_bps), ("stamp_bps", stamp_bps)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be a non-negative finite float")
    if stamp_direction not in ("sell", "both", "none"):
        raise ValueError("stamp_direction must be sell|both|none")


def assign_bins(rows: list[dict[str, Any]], bins: int, min_cross_section: int) -> list[list[dict[str, Any]]] | None:
    """Filter null first, then equal-count rank bins."""
    valid = [row for row in rows if engine.finite(row.get("factor"))]
    if len(valid) < max(min_cross_section, bins):
        return None
    ordered = sorted(valid, key=lambda row: (float(row["factor"]), str(row.get("symbol"))))
    result: list[list[dict[str, Any]]] = [[] for _ in range(bins)]
    for index, row in enumerate(ordered):
        bucket = min(bins - 1, index * bins // len(ordered))
        result[bucket].append(row)
    return result


def drift_weights(weights: dict[str, float], returns: dict[str, float]) -> dict[str, float]:
    values = {symbol: weight * (1.0 + returns[symbol]) for symbol, weight in weights.items() if symbol in returns}
    total = sum(values.values())
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in values.items()}


def portfolio_turnover(old_weights: dict[str, float], new_weights: dict[str, float]) -> float:
    symbols = set(old_weights) | set(new_weights)
    return sum(abs(new_weights.get(symbol, 0.0) - old_weights.get(symbol, 0.0)) for symbol in symbols)


def turnover_sides(old_weights: dict[str, float], new_weights: dict[str, float]) -> tuple[float, float]:
    symbols = set(old_weights) | set(new_weights)
    buys = sum(max(new_weights.get(symbol, 0.0) - old_weights.get(symbol, 0.0), 0.0) for symbol in symbols)
    sells = sum(max(old_weights.get(symbol, 0.0) - new_weights.get(symbol, 0.0), 0.0) for symbol in symbols)
    return buys, sells


def _time_split(returns: list[float], train_frac: float) -> tuple[list[float], list[float]]:
    split = max(1, min(len(returns) - 1, int(len(returns) * train_frac))) if len(returns) > 1 else len(returns)
    return returns[:split], returns[split:]


def epoch_return(
    rows: list[dict[str, Any]], entry_date: str, exit_date: str,
) -> tuple[float | None, bool, str | None]:
    """Settle at last available price; report interruption instead of filling zeros."""
    prices = {str(row.get("date")): float(row["close"]) for row in rows
              if engine.finite(row.get("close")) and float(row["close"]) > 0}
    if entry_date not in prices:
        return None, False, None
    candidates = sorted(trade_date for trade_date in prices if entry_date <= trade_date <= exit_date)
    if not candidates:
        return None, False, None
    last_date = candidates[-1]
    interrupted = last_date != exit_date
    return prices[last_date] / prices[entry_date] - 1.0, interrupted, last_date


def _metrics(returns: list[float], periods_per_year: float) -> dict[str, float | None]:
    if not returns:
        return {"total_return": None, "annualized_return": None, "annualized_volatility": None,
                "sharpe": None, "max_drawdown": None, "periods": 0}
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        nav *= 1.0 + value
        peak = max(peak, nav)
        if peak > 0:
            max_dd = min(max_dd, nav / peak - 1.0)
    annualized = nav ** (periods_per_year / len(returns)) - 1.0 if nav > 0 else None
    volatility = statistics.stdev(returns) * math.sqrt(periods_per_year) if len(returns) > 1 else None
    mean_annual = statistics.fmean(returns) * periods_per_year
    sharpe = mean_annual / volatility if volatility not in (None, 0) else None
    return {"total_return": nav - 1.0, "annualized_return": annualized,
            "annualized_volatility": volatility, "sharpe": sharpe,
            "max_drawdown": max_dd, "periods": len(returns)}


def _solve_linear3(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    augmented = [list(row) + [value] for row, value in zip(matrix, vector)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [left - factor * right for left, right in zip(augmented[row], augmented[column])]
    return [augmented[row][3] for row in range(3)]


def cross_section_attribution(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    valid = [row for row in rows if all(engine.finite(row.get(field)) for field in ("factor", "size", "fwd_return"))]
    if len(valid) < 4:
        return {"alpha": None, "beta_size": None, "beta_factor": None, "n": len(valid)}
    factors = engine._standardize([float(row["factor"]) for row in valid], "zscore")
    sizes = engine._standardize([float(row["size"]) for row in valid], "zscore")
    returns = [float(row["fwd_return"]) for row in valid]
    design = [[1.0, size, factor] for size, factor in zip(sizes, factors)]
    matrix = [[sum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)]
    vector = [sum(row[i] * value for row, value in zip(design, returns)) for i in range(3)]
    solved = _solve_linear3(matrix, vector)
    if solved is None:
        return {"alpha": None, "beta_size": None, "beta_factor": None, "n": len(valid)}
    return {"alpha": solved[0], "beta_size": solved[1], "beta_factor": solved[2], "n": len(valid)}


def build_attribution(
    panels: dict[str, list[dict[str, Any]]], factor_values: dict[str, dict[str, float | None]],
    formation_dates: list[str], horizon: int,
) -> dict[str, Any]:
    """Uses factor_engine.forward_return, the same function as IC evaluation."""
    size_values = {symbol: engine.size_proxy(rows) for symbol, rows in panels.items()}
    forward_values = {symbol: engine.forward_return(rows, horizon) for symbol, rows in panels.items()}
    series: list[dict[str, Any]] = []
    for formation_date in formation_dates:
        rows = [{"symbol": symbol, "factor": factor_values[symbol].get(formation_date),
                 "size": size_values[symbol].get(formation_date),
                 "fwd_return": forward_values[symbol].get(formation_date)}
                for symbol in panels]
        result = cross_section_attribution(rows)
        series.append({"date": formation_date, **result})
    valid = [row for row in series if row["beta_factor"] is not None]
    return {
        "status": "ok" if valid else "insufficient_data",
        "alpha_series_summary": statistics.fmean(row["alpha"] for row in valid) if valid else None,
        "beta_size": statistics.fmean(row["beta_size"] for row in valid) if valid else None,
        "beta_factor": statistics.fmean(row["beta_factor"] for row in valid) if valid else None,
        "sections": len(valid), "series": series,
    }


def run_backtest(
    market: str, panels: dict[str, list[dict[str, Any]]], factor_name: str, *,
    bins: int = 5, rebalance_days: int = 5, fac_shift: int = 1,
    fee_bps: float = 3.0, slippage_bps: float = 5.0, stamp_bps: float = 5.0,
    stamp_direction: str = "sell", min_cross_section: int = 30,
    train_frac: float = 0.7, attribution: bool = False,
) -> dict[str, Any]:
    validate_config(bins=bins, rebalance_days=rebalance_days, fac_shift=fac_shift,
                    fee_bps=fee_bps, slippage_bps=slippage_bps,
                    stamp_bps=stamp_bps, stamp_direction=stamp_direction)
    if not 0 < train_frac < 1:
        raise ValueError("train_frac must be between 0 and 1")
    if factor_name not in engine.FACTOR_METHODS:
        raise ValueError(f"unknown factor: {factor_name}")
    factor_values = {symbol: engine.compute_factor(rows, factor_name) for symbol, rows in panels.items()}
    dates = sorted({str(row.get("date")) for rows in panels.values() for row in rows if row.get("date")})
    price_by_symbol = {symbol: {str(row.get("date")): row for row in rows} for symbol, rows in panels.items()}
    epochs: list[dict[str, Any]] = []
    excluded_epochs = 0
    interrupted_count = 0
    limit_move_days = 0
    previous_weights: list[dict[str, float]] = [{} for _ in range(bins)]
    formation_dates: list[str] = []
    start = fac_shift
    for entry_index in range(start, max(start, len(dates) - 1), rebalance_days):
        exit_index = min(entry_index + rebalance_days, len(dates) - 1)
        if exit_index <= entry_index:
            continue
        formation_date, entry_date, exit_date = dates[entry_index - fac_shift], dates[entry_index], dates[exit_index]
        candidates: list[dict[str, Any]] = []
        for symbol in panels:
            factor = factor_values[symbol].get(formation_date)
            entry_row = price_by_symbol[symbol].get(entry_date)
            if engine.finite(factor) and entry_row and engine.finite(entry_row.get("close")):
                candidates.append({"symbol": symbol, "factor": factor})
        buckets = assign_bins(candidates, bins, min_cross_section)
        if buckets is None:
            excluded_epochs += 1
            continue
        formation_dates.append(formation_date)
        bin_records: list[dict[str, Any]] = []
        for bucket_index, bucket in enumerate(buckets):
            symbols = [str(row["symbol"]) for row in bucket]
            new_weights = {symbol: 1.0 / len(symbols) for symbol in symbols}
            turnover = portfolio_turnover(previous_weights[bucket_index], new_weights)
            buy_turnover, sell_turnover = turnover_sides(previous_weights[bucket_index], new_weights)
            symbol_returns: dict[str, float] = {}
            last_dates: dict[str, str | None] = {}
            for symbol in symbols:
                value, interrupted, last_date = epoch_return(panels[symbol], entry_date, exit_date)
                if value is None:
                    continue
                symbol_returns[symbol] = value
                last_dates[symbol] = last_date
                if interrupted:
                    interrupted_count += 1
                entry_row = price_by_symbol[symbol][entry_date]
                if market == "A" and engine.finite(entry_row.get("pct_change")) and abs(float(entry_row["pct_change"])) >= 9.5:
                    limit_move_days += 1
            gross = statistics.fmean(symbol_returns.values()) if symbol_returns else None
            trading_cost = turnover * (float(fee_bps) + float(slippage_bps)) / 10000.0
            stamp_base = sell_turnover if stamp_direction == "sell" else (turnover if stamp_direction == "both" else 0.0)
            stamp_cost = stamp_base * float(stamp_bps) / 10000.0
            cost = trading_cost + stamp_cost
            net = gross - cost if gross is not None else None
            previous_weights[bucket_index] = drift_weights(new_weights, symbol_returns) if symbol_returns else {}
            bin_records.append({"bin": bucket_index + 1, "symbols": symbols, "gross_return": gross,
                                "net_return": net, "turnover": turnover,
                                "buy_turnover": buy_turnover, "sell_turnover": sell_turnover,
                                "trading_cost": trading_cost, "stamp_cost": stamp_cost, "cost": cost,
                                "ending_weights": previous_weights[bucket_index], "last_dates": last_dates})
        epochs.append({"formation_date": formation_date, "entry_date": entry_date,
                       "exit_date": exit_date, "bins": bin_records})
    total_epoch_slots = len(epochs) + excluded_epochs
    excluded_share = excluded_epochs / total_epoch_slots if total_epoch_slots else 1.0
    if excluded_share > 0.30 or not epochs:
        return envelope("factor_backtest_run.v1", status="insufficient_data", market=market,
                        factor=factor_name, bins=None, ls_net=None, monotonicity_spearman=None,
                        turnover=None, costs_included="yes", train=None, test=None, attribution=None,
                        failure_modes=[{"type": "insufficient_cross_section", "excluded_epochs": excluded_epochs}],
                        robustness_checks=["no_lookahead_fac_shift>=1"], data_gaps=[])
    per_bin_returns = [[epoch["bins"][index]["net_return"] for epoch in epochs
                        if epoch["bins"][index]["net_return"] is not None] for index in range(bins)]
    periods_per_year = 252.0 / rebalance_days
    bin_metrics = [{"bin": index + 1, **_metrics(per_bin_returns[index], periods_per_year)} for index in range(bins)]
    annuals = [row["annualized_return"] for row in bin_metrics]
    monotonicity = engine.spearman(list(range(1, bins + 1)), [float(value) for value in annuals]) if all(value is not None for value in annuals) else None
    ls_returns = [0.5 * (epoch["bins"][-1]["net_return"] - epoch["bins"][0]["net_return"])
                  for epoch in epochs if epoch["bins"][-1]["net_return"] is not None and epoch["bins"][0]["net_return"] is not None]
    train_ls, test_ls = _time_split(ls_returns, train_frac)
    all_turnover = [record["turnover"] for epoch in epochs for record in epoch["bins"]]
    attribution_result = build_attribution(panels, factor_values, formation_dates, fac_shift + rebalance_days) if attribution else None
    failure_modes = [
        {"type": "suspended_or_delisted_count", "count": interrupted_count,
         "settlement": "last_available_price_no_zero_fill"},
        {"type": "limit_move_entry_days", "count": limit_move_days},
        {"type": "excluded_formation_epochs", "count": excluded_epochs},
    ]
    robustness = ["adjust_basis_qfq_snapshot", "survivorship_current_constituents",
                  f"limit_move_entry_days={limit_move_days}", "no_lookahead_fac_shift>=1",
                  "quantile_bins_break_factor_ties_by_symbol_order",
                  "low_cardinality_factor_spread_may_be_tie_driven"]
    if market == "A":
        robustness.extend([
            "short_leg_feasibility=not_available_in_A_share",
            "a_share_limit_detection_uniform_9_5pct_not_board_or_st_aware",
            "limit_move_days_counts_symbol_epoch_occurrences",
        ])
    return envelope("factor_backtest_run.v1", status="ok", market=market, factor=factor_name,
                    fac_shift=fac_shift, rebalance_days=rebalance_days, bins=bin_metrics,
                    ls_net=_metrics(ls_returns, periods_per_year), monotonicity_spearman=monotonicity,
                    turnover=statistics.fmean(all_turnover) if all_turnover else None,
                    costs_included="yes", train=_metrics(train_ls, periods_per_year),
                    test=_metrics(test_ls, periods_per_year), attribution=attribution_result,
                    failure_modes=failure_modes, robustness_checks=robustness,
                    data_gaps=[], epochs=epochs)


def _atomic_save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def self_test() -> None:
    validate_config(bins=3, rebalance_days=2, fac_shift=1, fee_bps=3.0,
                    slippage_bps=5.0, stamp_bps=5.0, stamp_direction="sell")
    with tempfile.TemporaryDirectory() as tmp:
        rows = [{"date": "2026-01-01", "close": 10.0},
                {"date": "2026-01-02", "close": 11.0}]
        value, interrupted, last = epoch_return(rows, "2026-01-01", "2026-01-03")
        assert abs(float(value) - 0.1) < 1e-12 and interrupted and last == "2026-01-02"
        result = envelope("factor_backtest_self_test.v1", ok=True)
        _atomic_save(Path(tmp) / "result.json", result)
        assert json.loads((Path(tmp) / "result.json").read_text())["ok"] is True


def _symbols(args: argparse.Namespace) -> list[str]:
    values = list(args.symbols or [])
    if args.symbols_file:
        values.extend(line.strip().split()[0] for line in Path(args.symbols_file).read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith("#"))
    values = list(dict.fromkeys(values))
    if not values:
        raise ValueError("at least one symbol is required")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--market", required=True, choices=sorted(factor_panel.MARKETS))
    run.add_argument("--symbols", nargs="*", default=[])
    run.add_argument("--symbols-file")
    run.add_argument("--factor", required=True, choices=sorted(engine.FACTOR_METHODS))
    run.add_argument("--bins", type=int, default=5)
    run.add_argument("--rebalance-days", type=int, default=5)
    run.add_argument("--fac-shift", type=int, default=1)
    run.add_argument("--fee-bps", type=float, default=3.0)
    run.add_argument("--slippage-bps", type=float, default=5.0)
    run.add_argument("--stamp-bps", type=float, default=5.0)
    run.add_argument("--stamp-direction", choices=["sell", "both", "none"], default="sell")
    run.add_argument("--min-cross-section", type=int, default=30)
    run.add_argument("--train-frac", type=float, default=0.7)
    run.add_argument("--attribution", action="store_true")
    run.add_argument("--save", action="store_true")
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
        panels, gaps, _basis = engine.load_panels(args.market, symbols)
        if gaps or not panels:
            result = envelope("factor_backtest_run.v1", status="insufficient_data", market=args.market,
                              factor=args.factor, bins=None, ls_net=None, monotonicity_spearman=None,
                              turnover=None, costs_included="yes", train=None, test=None, attribution=None,
                              failure_modes=[], robustness_checks=["no_lookahead_fac_shift>=1"], data_gaps=gaps)
        else:
            result = run_backtest(args.market, panels, args.factor, bins=args.bins,
                                  rebalance_days=args.rebalance_days, fac_shift=args.fac_shift,
                                  fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                                  stamp_bps=args.stamp_bps, stamp_direction=args.stamp_direction,
                                  min_cross_section=args.min_cross_section, train_frac=args.train_frac,
                                  attribution=args.attribution)
        if args.save:
            root = Path(os.environ.get("FACTOR_BACKTEST_DIR", str(DEFAULT_RUN_DIR))).expanduser()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = root / f"fb_{stamp}_{args.factor}.json"
            _atomic_save(path, result)
            result["saved_path"] = str(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps(envelope("factor_backtest_error.v1", ok=False, status="error",
                                  data_gaps=[{"reason_code": "error", "gap": str(exc)}]), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None, allow_nan=False))
    return 0 if result.get("status") != "insufficient_data" else 1


if __name__ == "__main__":
    raise SystemExit(main())
