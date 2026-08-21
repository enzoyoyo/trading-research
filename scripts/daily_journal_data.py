#!/usr/bin/env python3
"""Read-only data loading and aggregation for the daily trading journal."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_SYSTEM_A_ROOT = Path.home() / ".hermes" / "longbridge-paper-trading"
DEFAULT_JOURNAL_ROOT = Path.home() / ".hermes" / "trading-journal"
SCHEMA_VERSION = "daily_journal.v1"
US_TZ = ZoneInfo("America/New_York")
HK_TZ = ZoneInfo("Asia/Hong_Kong")
FILENAME_TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
STDOUT_ORDER_RE = re.compile(r"(Buy|Sell) order:\s*([\d.]+)\s+([A-Z0-9]+\.[A-Z]{2})\s*@\s*([\d.]+)")

def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def market_tz(symbol: str | None) -> ZoneInfo:
    if symbol and symbol.upper().endswith(".HK"):
        return HK_TZ
    return US_TZ


def market_of(symbol: str | None) -> str:
    return "HK" if symbol and symbol.upper().endswith(".HK") else "US"


def trading_date(dt_utc: datetime, symbol: str | None) -> str:
    return dt_utc.astimezone(market_tz(symbol)).date().isoformat()


def fmt_hhmm(dt_utc: datetime, symbol: str | None) -> str:
    """HH:MM in the symbol's market local time (US/Eastern, or Asia/Hong_Kong for .HK)."""
    return dt_utc.astimezone(market_tz(symbol)).strftime("%H:%M")


def filename_ts(path: Path) -> datetime | None:
    m = FILENAME_TS_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None



def load_json_safe(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def files_in_window(dir_path: Path, prefix: str, start: datetime, end: datetime) -> list[Path]:
    if not dir_path.exists():
        return []
    out = []
    for p in dir_path.glob(f"{prefix}*.json"):
        ts = filename_ts(p)
        if ts is not None and start <= ts <= end:
            out.append(p)
    return sorted(out)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def recent_trading_dates(n: int, as_of: datetime | None = None) -> list[str]:
    """Most recent N US-Eastern weekdays (Mon-Fri), ascending. Deliberately
    calendar-agnostic (no holiday table): a holiday with zero real activity
    simply renders an all-data_gap page instead of a fabricated one."""
    as_of = as_of or datetime.now(timezone.utc)
    d = as_of.astimezone(US_TZ).date()
    dates: list[str] = []
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(dates)


# --------------------------------------------------------------------------
# Day bucket aggregation
# --------------------------------------------------------------------------

def new_day_bucket() -> dict[str, Any]:
    return {
        "entries": [],
        "exit_orders": [],
        "exit_diag": {"checked": 0, "no_exit": 0, "skipped": 0, "other": 0},
        "regime_profiles": set(),
        "orders_rejected": [],
        "orders_submitted": [],
        "orders_replaced": [],
        "outcomes_open": [],
        "outcomes_closed": [],
        "outcomes_reconciled": [],
        "snapshot": None,
        "hypotheses": [],
        "calibration_scored": [],
        "policy_summary": None,
        "data_gaps": set(),
    }


def gather_proposals(dates: set[str], root: Path, buckets: dict[str, dict[str, Any]], start: datetime, end: datetime) -> None:
    for path in files_in_window(root / "proposals", "generated_", start, end):
        doc = load_json_safe(path)
        if not doc:
            continue
        gen_at = parse_utc(doc.get("generated_at_utc")) or filename_ts(path)
        if gen_at is None:
            continue
        for order in doc.get("orders") or []:
            symbol = order.get("symbol")
            d = trading_date(gen_at, symbol)
            if d not in dates:
                continue
            fusion = ((order.get("metadata") or {}).get("decision_fusion")) or {}
            buckets[d]["entries"].append({
                "symbol": symbol,
                "side": order.get("side"),
                "quantity": order.get("quantity"),
                "limit_price": order.get("limit_price"),
                "thesis": order.get("thesis"),
                "invalidation": order.get("invalidation"),
                "win_rate_proxy": fusion.get("win_rate_proxy"),
                "composite_score": fusion.get("composite_score"),
                "confidence": fusion.get("confidence"),
                "market": market_of(symbol),
            })


def gather_exit_proposals(dates: set[str], root: Path, buckets: dict[str, dict[str, Any]], start: datetime, end: datetime) -> None:
    for path in files_in_window(root / "proposals", "exit_generated_", start, end):
        doc = load_json_safe(path)
        if not doc:
            continue
        gen_at = parse_utc(doc.get("generated_at_utc")) or filename_ts(path)
        if gen_at is None:
            continue
        for order in doc.get("orders") or []:
            symbol = order.get("symbol")
            d = trading_date(gen_at, symbol)
            if d not in dates:
                continue
            meta = order.get("metadata") or {}
            buckets[d]["exit_orders"].append({
                "symbol": symbol,
                "quantity": order.get("quantity"),
                "limit_price": order.get("limit_price"),
                "exit_reason": order.get("exit_reason"),
                "market": market_of(symbol),
                "time_hhmm": fmt_hhmm(gen_at, symbol),
            })
            if meta.get("regime_profile"):
                buckets[d]["regime_profiles"].add(meta["regime_profile"])
        for diag in doc.get("diagnostics") or []:
            symbol = diag.get("symbol")
            d = trading_date(gen_at, symbol)
            if d not in dates:
                continue
            status = diag.get("status") or "other"
            bucket = buckets[d]["exit_diag"]
            bucket["checked"] += 1
            bucket[status if status in ("no_exit", "skipped") else "other"] += 1
            regime = (diag.get("checks") or {}).get("regime_profile")
            if regime:
                buckets[d]["regime_profiles"].add(regime)


def gather_decision_packets(dates: set[str], root: Path, buckets: dict[str, dict[str, Any]], start: datetime, end: datetime) -> None:
    latest_for_date: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for path in files_in_window(root / "decision_packets", "paper_decision_packet_", start, end):
        doc = load_json_safe(path)
        if not doc:
            continue
        gen_at = parse_utc(doc.get("generated_at_utc")) or filename_ts(path)
        if gen_at is None:
            continue
        d = trading_date(gen_at, None)
        if d not in dates:
            continue
        prev = latest_for_date.get(d)
        if prev is None or gen_at > prev[0]:
            latest_for_date[d] = (gen_at, doc)
    for d, (_, doc) in latest_for_date.items():
        policy = doc.get("policy_summary") or {}
        buckets[d]["policy_summary"] = {
            "universe_count": len(policy.get("universe") or []),
            "universe_core_count": len(policy.get("universe_core") or []),
            "max_positions": policy.get("max_positions"),
            "max_notional_per_order_pct": policy.get("max_notional_per_order_pct"),
            "daily_new_orders_limit": policy.get("daily_new_orders_limit"),
        }


def parse_order_symbol(stdout: str) -> tuple[str | None, str | None, str | None, str | None]:
    m = STDOUT_ORDER_RE.search(stdout or "")
    if not m:
        return None, None, None, None
    side, qty, symbol, price = m.groups()
    return side, qty, symbol, price


def gather_orders(dates: set[str], root: Path, buckets: dict[str, dict[str, Any]]) -> None:
    for rec in iter_jsonl(root / "journal" / "paper_orders.jsonl"):
        ts = parse_utc(rec.get("timestamp_utc"))
        if ts is None:
            continue
        event_type = rec.get("event_type")
        if event_type in ("paper_order_validation", "paper_order_pre_submit"):
            proposal = rec.get("proposal") or {}
            symbol = proposal.get("symbol")
            d = trading_date(ts, symbol)
            if d not in dates:
                continue
            if rec.get("valid") is False:
                buckets[d]["orders_rejected"].append({
                    "symbol": symbol,
                    "side": proposal.get("side"),
                    "quantity": proposal.get("quantity"),
                    "limit_price": proposal.get("limit_price"),
                    "reasons": rec.get("reasons") or [],
                    "time_hhmm": fmt_hhmm(ts, symbol),
                })
        elif event_type == "paper_order_submit_result":
            result = rec.get("result") or {}
            side, qty, symbol, price = parse_order_symbol(result.get("stdout") or "")
            d = trading_date(ts, symbol)
            if d not in dates:
                continue
            buckets[d]["orders_submitted"].append({
                "proposal_id": rec.get("proposal_id"),
                "symbol": symbol,
                "side": side,
                "quantity": qty,
                "price": price,
                "status": rec.get("status"),
                "returncode": result.get("returncode"),
                "time_hhmm": fmt_hhmm(ts, symbol),
            })
        elif event_type == "paper_order_replace_result":
            d = trading_date(ts, None)
            if d not in dates:
                continue
            buckets[d]["orders_replaced"].append({
                "proposal_id": rec.get("proposal_id"),
                "old_price": rec.get("old_price"),
                "new_price": rec.get("new_price"),
                "quantity": rec.get("quantity"),
                "reason": rec.get("reason"),
                "time_hhmm": fmt_hhmm(ts, None),
            })


def compute_realized_pnl(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """FIFO-match each day's closed/reduce_submitted outcome against the
    matching earlier 'open' outcome(s) for the same symbol to derive a dollar
    realized PnL. Purely accounting arithmetic over real logged fields — not a
    prediction-accuracy claim (that stays owned by the calibration bucket)."""
    stacks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    results: dict[int, dict[str, Any]] = {}
    for idx, r in enumerate(rows):
        if r.get("event_type") != "paper_trade_outcome":
            continue
        status = r.get("status")
        symbol = r.get("symbol")
        if status == "open":
            try:
                qty = float(r.get("quantity") or 0)
                entry_price = float(r.get("entry_price") or 0)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            stacks[symbol].append({"entry_price": entry_price, "side": r.get("side"), "remaining": qty})
        elif status in ("closed", "reduce_submitted"):
            try:
                close_qty = float(r.get("quantity") or 0)
                exit_price = float(r.get("exit_price") or 0)
            except (TypeError, ValueError):
                results[idx] = {"realized_pnl": None, "matched": False}
                continue
            stack = stacks.get(symbol) or []
            remaining = close_qty
            pnl_total = 0.0
            matched_any = False
            while remaining > 1e-9 and stack:
                lot = stack[0]
                take = min(lot["remaining"], remaining)
                if lot["side"] == "Sell":
                    pnl_total += (lot["entry_price"] - exit_price) * take
                else:
                    pnl_total += (exit_price - lot["entry_price"]) * take
                lot["remaining"] -= take
                remaining -= take
                matched_any = True
                if lot["remaining"] <= 1e-9:
                    stack.pop(0)
            if matched_any and remaining <= 1e-9:
                results[idx] = {"realized_pnl": round(pnl_total, 2), "matched": True}
            elif matched_any:
                results[idx] = {"realized_pnl": round(pnl_total, 2), "matched": "partial"}
            else:
                results[idx] = {"realized_pnl": None, "matched": False}
    return results


def gather_outcomes(dates: set[str], root: Path, buckets: dict[str, dict[str, Any]]) -> None:
    rows = sorted(iter_jsonl(root / "journal" / "paper_outcomes.jsonl"), key=lambda r: r.get("timestamp_utc") or "")
    pnl_by_idx = compute_realized_pnl(rows)
    for idx, r in enumerate(rows):
        ts = parse_utc(r.get("timestamp_utc"))
        if ts is None:
            continue
        event_type = r.get("event_type")
        symbol = r.get("symbol")
        d = trading_date(ts, symbol)
        if d not in dates:
            continue
        if event_type == "paper_trade_outcome":
            status = r.get("status")
            if status == "open":
                buckets[d]["outcomes_open"].append({
                    "symbol": symbol,
                    "side": r.get("side"),
                    "quantity": r.get("quantity"),
                    "entry_price": r.get("entry_price"),
                    "sleeve": r.get("sleeve"),
                })
            elif status in ("closed", "reduce_submitted"):
                pnl = pnl_by_idx.get(idx, {"realized_pnl": None, "matched": False})
                buckets[d]["outcomes_closed"].append({
                    "symbol": symbol,
                    "side": r.get("side"),
                    "quantity": r.get("quantity"),
                    "exit_price": r.get("exit_price"),
                    "exit_reason": r.get("exit_reason"),
                    "r_multiple": r.get("r_multiple"),
                    "status": status,
                    "realized_pnl": pnl["realized_pnl"],
                    "pnl_matched": pnl["matched"],
                })
                if not pnl["matched"]:
                    buckets[d]["data_gaps"].add(f"no_matching_entry_for_close:{symbol}")
        elif event_type == "paper_trade_outcome_reconciliation":
            buckets[d]["outcomes_reconciled"].append({
                "symbol": symbol,
                "reason": r.get("reason"),
                "status": r.get("status"),
            })


def gather_snapshots(dates: set[str], root: Path, buckets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Returns date -> last snapshot of that day (US-Eastern bucketed, since
    equity is portfolio-level, not per-symbol)."""
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for r in iter_jsonl(root / "journal" / "paper_position_snapshots.jsonl"):
        ts = parse_utc(r.get("timestamp_utc"))
        if ts is None:
            continue
        d = trading_date(ts, None)
        prev = latest.get(d)
        if prev is None or ts > prev[0]:
            latest[d] = (ts, r)
    for d, (_, r) in latest.items():
        if d in dates:
            assets = (r.get("portfolio") or {}).get("assets") or []
            net_assets = None
            total_cash = None
            if assets:
                try:
                    net_assets = float(assets[0].get("net_assets"))
                    total_cash = float(assets[0].get("total_cash"))
                except (TypeError, ValueError):
                    pass
            buckets[d]["snapshot"] = {
                "net_assets": net_assets,
                "total_cash": total_cash,
                "market_value": (r.get("portfolio") or {}).get("market_value"),
                "unrealized_pnl": (r.get("portfolio") or {}).get("unrealized_pnl"),
                "unrealized_pnl_pct": (r.get("portfolio") or {}).get("unrealized_pnl_pct"),
                "position_count": (r.get("portfolio") or {}).get("position_count"),
            }
    return {d: v[1] for d, v in latest.items()}


def gather_hypothesis_changes(dates: set[str], buckets: dict[str, dict[str, Any]]) -> None:
    try:
        from hypothesis_registry import load_registry, registry_path  # type: ignore
    except Exception:
        return
    try:
        rows = load_registry(registry_path())
    except Exception:
        return
    for row in rows:
        ts = parse_utc(str(row.get("updated_at_utc") or ""))
        if ts is None:
            continue
        d = ts.astimezone(US_TZ).date().isoformat()
        if d in dates:
            buckets[d]["hypotheses"].append({
                "hypothesis_id": row.get("hypothesis_id"),
                "statement": row.get("statement"),
                "status": row.get("status"),
            })


def gather_calibration(dates: set[str], buckets: dict[str, dict[str, Any]]) -> bool:
    """Reads calibration_samples_paper (predicted vs realised, already scored
    by paper_outcome_calibration_feed.py). Returns whether the bucket itself
    was reachable (False => data_gap, distinct from 'reachable but empty')."""
    try:

        from trading_memory_core import connect, db_path  # type: ignore
    except Exception:
        return False

    class _Args:
        db = None

    try:
        conn = connect(db_path(_Args()))
    except Exception:
        return False
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_samples_paper'"
        ).fetchone()
        if not table:
            return True
        rows = conn.execute(
            "SELECT decision_ref, symbol, predicted_p, outcome, outcome_time FROM calibration_samples_paper"
        ).fetchall()
    except Exception:
        return False
    finally:
        conn.close()
    for row in rows:
        ts = parse_utc(row["outcome_time"])
        if ts is None:
            continue
        d = trading_date(ts, row["symbol"])
        if d in dates:
            buckets[d]["calibration_scored"].append({
                "symbol": row["symbol"],
                "predicted": row["predicted_p"],
                "outcome": row["outcome"],
                "decision_ref": row["decision_ref"],
            })
    return True

def _freeze(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def dedupe_count(items: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[tuple[dict[str, Any], int]]:
    """System A's risk/exit engine re-emits the same pending action every scan
    cycle (roughly every 30 min) until it clears. Collapse identical repeats
    into one line with a cycle count so the page stays readable and honest
    about repetition without hiding it."""
    order: list[tuple] = []
    counts: dict[tuple, int] = defaultdict(int)
    representative: dict[tuple, dict[str, Any]] = {}
    for item in items:
        key = tuple(_freeze(item.get(f)) for f in key_fields)
        if key not in counts:
            order.append(key)
            representative[key] = item
        counts[key] += 1
    return [(representative[k], counts[k]) for k in order]


def _num_range(values: list[float]) -> str:
    if not values:
        return "data_gap"
    lo, hi = min(values), max(values)
    return f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"


def aggregate_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Groups repeated same-symbol/side entry signals across the day's scan
    cycles into one row with ranges, instead of one row per cycle."""
    order: list[tuple] = []
    groups: dict[tuple, dict[str, Any]] = {}
    for e in entries:
        key = (e.get("symbol"), e.get("side"))
        if key not in groups:
            order.append(key)
            groups[key] = {
                "symbol": e.get("symbol"), "side": e.get("side"), "count": 0,
                "quantities": [], "prices": [], "win_rates": [], "confidences": set(),
                "thesis": e.get("thesis"), "invalidation": e.get("invalidation"),
            }
        g = groups[key]
        g["count"] += 1
        for field, bucket_name in (("quantity", "quantities"), ("limit_price", "prices"), ("win_rate_proxy", "win_rates")):
            try:
                if e.get(field) is not None:
                    g[bucket_name].append(float(e[field]))
            except (TypeError, ValueError):
                pass
        if e.get("confidence"):
            g["confidences"].add(e["confidence"])
    out = []
    for key in order:
        g = groups[key]
        out.append({
            "symbol": g["symbol"], "side": g["side"], "count": g["count"],
            "qty_range": _num_range(g["quantities"]), "price_range": _num_range(g["prices"]),
            "win_rate_range": _num_range(g["win_rates"]),
            "confidence": "/".join(sorted(g["confidences"])) if g["confidences"] else "data_gap",
            "thesis": g["thesis"], "invalidation": g["invalidation"],
        })
    return out


def aggregate_rejected(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rejected-at-validation retries re-quote a slightly different limit price
    each scan cycle, so a plain (symbol, side, quantity, reasons) dedupe still
    leaves near-duplicate rows. Group by (symbol, side, frozenset(reasons))
    instead and report an attempt count plus qty/limit ranges, matching the
    range-aggregation style used for pre-market entry proposals."""
    order: list[tuple] = []
    groups: dict[tuple, dict[str, Any]] = {}
    for it in items:
        reasons = tuple(sorted(it.get("reasons") or []))
        key = (it.get("symbol"), it.get("side"), reasons)
        if key not in groups:
            order.append(key)
            groups[key] = {
                "symbol": it.get("symbol"), "side": it.get("side"), "reasons": list(reasons),
                "count": 0, "quantities": [], "prices": [], "times": [],
            }
        g = groups[key]
        g["count"] += 1
        for field, bucket_name in (("quantity", "quantities"), ("limit_price", "prices")):
            try:
                if it.get(field) is not None:
                    g[bucket_name].append(float(it[field]))
            except (TypeError, ValueError):
                pass
        if it.get("time_hhmm"):
            g["times"].append(it["time_hhmm"])
    out = []
    for key in order:
        g = groups[key]
        times = sorted(g["times"])
        time_range = "data_gap" if not times else (times[0] if times[0] == times[-1] else f"{times[0]}-{times[-1]}")
        out.append({
            "symbol": g["symbol"], "side": g["side"], "count": g["count"],
            "qty_range": _num_range(g["quantities"]), "price_range": _num_range(g["prices"]),
            "reasons": g["reasons"], "time_range": time_range,
        })
    return out
