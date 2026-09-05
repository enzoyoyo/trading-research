#!/usr/bin/env python3
"""Calibration scorecard for trading-research decision memory.

Self-evolution feedback loop B: the binary eval suites tell us whether the
skill's *posture logic* still holds, but they say nothing about whether the
skill's *confidence is honest*. This script answers the one question every
forecasting system must answer: when the skill said "57% win rate", did it win
~57% of the time?

It reads the SAME decision memory that powers preflight (read-only, never
mutates), pairs each decision's predicted ``factors.estimated_win_rate`` with
the realised ``results.outcome``, and computes Brier score, a base-rate skill
score, reliability bins, and per-group calibration. ``calibration_materiality``
is advisory only and is derived conservatively: it never blocks validators and
never moves live sizing.

No broker access, no trade execution, no external secrets.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_memory_core import (
    action_bucket,
    connect,
    db_path,
    parse_time,
    verified_rows,
)

# Reliability bins over the [0, 1] predicted-probability range.
BIN_EDGES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
DEFAULT_MIN_SAMPLES = 12
# Calibration gap thresholds (predicted minus realised). Conservative: a real
# methodology look is only warranted when the gap is large AND well-sampled.
GAP_HIGH = 0.15
GAP_MEDIUM = 0.08
# Outcomes that map cleanly to a 0/1 win label. mixed/neutral are excluded from
# Brier scoring because they are not a clean win/loss for a win-rate forecast.
WIN_OUTCOMES = {"success"}
LOSS_OUTCOMES = {"failure"}
PAPER_TABLE = "calibration_samples_paper"
PENDING_TABLE = "calibration_pending_paper_predictions"


def predicted_win_rate(decision: dict[str, Any]) -> float | None:
    factors = decision.get("factors") or {}
    value = factors.get("estimated_win_rate")
    if not isinstance(value, bool) and isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
        return float(value)
    return None


def actual_win_label(result: dict[str, Any]) -> int | None:
    outcome = result.get("outcome")
    if outcome in WIN_OUTCOMES:
        return 1
    if outcome in LOSS_OUTCOMES:
        return 0
    return None


def is_no_edge_floor(decision: dict[str, Any]) -> bool:
    """Recognise a structured conviction-floor abstention without guessing from L0 alone."""
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    containers = [
        decision,
        payload,
        decision.get("factors") if isinstance(decision.get("factors"), dict) else {},
        decision.get("scores") if isinstance(decision.get("scores"), dict) else {},
        payload.get("factors") if isinstance(payload.get("factors"), dict) else {},
        payload.get("scores") if isinstance(payload.get("scores"), dict) else {},
    ]
    for container in containers:
        if container.get("conviction_floor_passed") is False:
            return True
        for key in ("edge_status", "selection_status", "abstention_reason"):
            value = str(container.get(key) or "").strip().lower().replace("-", "_").replace(" ", "_")
            if value in {"no_edge", "below_conviction_floor", "no_edge_floor"}:
                return True
    reason_text = json.dumps(
        [decision.get("reasons") or [], payload.get("reasons") or []],
        ensure_ascii=False,
    ).lower()
    return bool(re.search(
        r"\bno[_ -]?edge\b|below[_ -]?conviction[_ -]?floor|"
        r"\|vote\|\s*<\s*conviction[_ -]?floor",
        reason_text,
    ))


def abstention_reason(decision: dict[str, Any], result: dict[str, Any]) -> str | None:
    """Return a mutually exclusive reason for a predicted candidate not being scored."""
    if predicted_win_rate(decision) is None:
        return None
    if is_no_edge_floor(decision):
        return "no_edge_floor"
    outcome = str(result.get("outcome") or "").strip().lower()
    if outcome in {"mixed", "neutral"}:
        return "outcome_mixed_neutral"
    if actual_win_label(result) is None:
        return "other_unscorable"
    return None


def calibration_denominators(
    items: list[tuple[dict[str, Any], dict[str, Any]]],
    paper_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Disclose the full candidate denominator and mutually exclusive abstentions."""
    counts = {
        "no_edge_floor": 0,
        "outcome_mixed_neutral": 0,
        "other_unscorable": 0,
    }
    candidate_n = 0
    evaluated_n = 0
    for decision, result in items:
        if predicted_win_rate(decision) is None:
            continue
        candidate_n += 1
        reason = abstention_reason(decision, result)
        if reason is None:
            evaluated_n += 1
        else:
            counts[reason] += 1
    for pair in paper_candidates or []:
        predicted = pair.get("predicted")
        actual = pair.get("actual")
        if (
            isinstance(predicted, bool)
            or not isinstance(predicted, (int, float))
            or not 0.0 <= float(predicted) <= 1.0
        ):
            continue
        candidate_n += 1
        if actual in (0, 1) and not isinstance(actual, bool):
            evaluated_n += 1
        else:
            counts["other_unscorable"] += 1
    abstention_n = candidate_n - evaluated_n
    abstention_rate = round(abstention_n / candidate_n, 4) if candidate_n else None
    rates = {
        reason: (round(count / candidate_n, 4) if candidate_n else None)
        for reason, count in counts.items()
    }
    return {
        "candidate_n": candidate_n,
        "evaluated_n": evaluated_n,
        "abstention_n": abstention_n,
        "abstention_rate": abstention_rate,
        "abstention_by_reason": counts,
        "abstention_rate_by_reason": rates,
    }


def collect_pairs(items: list[tuple[dict[str, Any], dict[str, Any]]], source: str = "skill") -> list[dict[str, Any]]:
    """Pair predicted win rate with realised binary outcome where both exist."""
    pairs: list[dict[str, Any]] = []
    for decision, result in items:
        predicted = predicted_win_rate(decision)
        actual = actual_win_label(result)
        if predicted is None or actual is None or abstention_reason(decision, result) is not None:
            continue
        pairs.append(
            {
                "predicted": predicted,
                "actual": actual,
                "regime": decision.get("attack_state") or "unknown",
                "direction": decision.get("direction") or "unknown",
                "action_bucket": action_bucket(decision),
                "source": source,
            }
        )
    return pairs


def prediction_pairs(conn, window: int) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    """Read settled, point-in-time predictions without mixing horizons."""
    pending = int(
        conn.execute("SELECT count(*) FROM predictions WHERE status='open'").fetchone()[0]
    )
    unresolved = int(
        conn.execute("SELECT count(*) FROM predictions WHERE status='unresolved'").fetchone()[0]
    )
    known_horizons = [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT horizon_id FROM predictions ORDER BY horizon_id"
        ).fetchall()
    ]
    rows = conn.execute(
        "SELECT p.prediction_id, p.symbol, p.horizon_id, p.regime, "
        "p.calibration_bucket, p.probability, o.actual, o.settled_at "
        "FROM predictions p JOIN prediction_outcomes o "
        "ON o.prediction_id=p.prediction_id "
        "WHERE p.status='settled'",
    ).fetchall()
    all_pairs = [
        {
            "prediction_id": row["prediction_id"],
            "symbol": row["symbol"],
            "horizon_id": row["horizon_id"],
            "regime": row["regime"],
            "calibration_bucket": row["calibration_bucket"],
            "predicted": float(row["probability"]),
            "actual": int(row["actual"]),
            "settled_at": row["settled_at"],
        }
        for row in rows
    ]
    return all_pairs, pending, unresolved, known_horizons


def prediction_calibration(conn, window: int, min_samples: int) -> dict[str, Any]:
    pairs, pending, unresolved, known_horizons = prediction_pairs(conn, window)
    by_horizon: dict[str, Any] = {}
    for horizon in known_horizons:
        rows = [row for row in pairs if row["horizon_id"] == horizon]
        rows.sort(
            key=lambda row: parse_time(row["settled_at"])
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        rows = rows[:window]
        mean_predicted = statistics.mean(row["predicted"] for row in rows) if rows else None
        realised = statistics.mean(row["actual"] for row in rows) if rows else None
        by_horizon[horizon] = {
            "status": "ok" if len(rows) >= min_samples else "insufficient_n",
            "n": len(rows),
            "min_samples": min_samples,
            "mean_predicted": round(mean_predicted, 4) if mean_predicted is not None else None,
            "realised_rate": round(realised, 4) if realised is not None else None,
            "calibration_gap": (
                round(mean_predicted - realised, 4)
                if mean_predicted is not None and realised is not None
                else None
            ),
            "brier": brier(rows),
        }
    by_bucket: dict[str, Any] = {}
    for bucket in sorted({row["calibration_bucket"] for row in pairs}):
        rows = [row for row in pairs if row["calibration_bucket"] == bucket]
        rows.sort(
            key=lambda row: parse_time(row["settled_at"])
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        rows = rows[:window]
        by_bucket[bucket] = {
            "status": "ok" if len(rows) >= min_samples else "insufficient_n",
            "n": len(rows),
            "min_samples": min_samples,
            "brier": brier(rows),
        }
    return {
        "status": "no_settled_predictions" if not pairs else "available_by_horizon",
        "settled_binary_samples": len(pairs),
        "pending_predictions": pending,
        "unresolved_predictions": unresolved,
        "by_horizon": by_horizon,
        "by_calibration_bucket": by_bucket,
        "cross_horizon_aggregate": None,
        "cross_horizon_aggregate_reason": "forbidden_by_multi_horizon_prediction_contract",
        "materiality_eligible": False,
        "advisory": True,
    }


def paper_sample_audit(conn, window: int) -> dict[str, Any]:
    from paper_outcome_calibration_feed import validate_prediction_contract, net_lifecycle_outcome, lifecycle_module, CONTRACT_FIELD
    exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (PAPER_TABLE,)).fetchone()
    result = {"total": 0, "eligible": 0, "legacy": 0, "excluded": 0, "excluded_reasons": {}, "pairs": [], "window_candidates": 0, "window_excluded": 0}
    if not exists:
        return result
    rows = conn.execute(f"SELECT * FROM {PAPER_TABLE} ORDER BY outcome_time DESC, recorded_at DESC").fetchall()
    result["total"] = len(rows)
    result["window_candidates"] = min(max(window, 0), len(rows))
    for index, raw in enumerate(rows):
        row = dict(raw)
        reason = "eligible"
        contract = None
        if "proxy" in str(row.get("prediction_field") or ""):
            reason = "legacy_score_proxy"
        else:
            try:
                payload = json.loads(row.get("source_payload_json") or "{}")
                entry = payload.get("entry", {})
                contract, reason = validate_prediction_contract(entry)
                if contract:
                    aggregation = lifecycle_module().aggregate_lifecycles([entry] + payload.get("net_outcome_receipts", []))
                    complete = aggregation.get("complete_lifecycles") or []
                    actual, _receipts = net_lifecycle_outcome(complete[0], payload.get("net_outcome_receipts", [])) if len(complete) == 1 else (None, [])
                    if row.get("prediction_field") != CONTRACT_FIELD or (complete and (row.get("trade_lifecycle_id") != complete[0].get("trade_lifecycle_id") or row.get("outcome_time") != complete[0].get("completed_at"))) or row.get("decision_ref") != entry.get("proposal_id") or row.get("symbol") != entry.get("symbol") or float(row.get("predicted_p")) != contract["p"] or actual is None or actual != row.get("outcome"):
                        reason = "unverified_sample_contract_or_net_outcome"
            except (ValueError, TypeError, AttributeError, KeyError):
                reason = "unverified_sample_payload"
        if reason != "eligible":
            result["excluded"] += 1
            result["window_excluded"] += int(index < window)
            result["legacy"] += int(reason == "legacy_score_proxy")
            result["excluded_reasons"][reason] = result["excluded_reasons"].get(reason, 0) + 1
            continue
        result["eligible"] += 1
        if index < window:
            result["pairs"].append({"predicted": contract["p"], "actual": int(row["outcome"]), "regime": "paper", "direction": "paper", "action_bucket": "paper", "source": "paper", "decision_ref": row["decision_ref"], "symbol": row["symbol"], "horizon_id": contract["horizon_id"], "target": contract["target"], "strategy_version": contract["strategy_version"], "model_version": contract["model_version"]})
    return result


def paper_pairs(conn, window: int) -> tuple[list[dict[str, Any]], int]:
    audit = paper_sample_audit(conn, window)
    return audit["pairs"], audit["total"]


def paper_pending_stats(conn) -> dict[str, int]:
    from paper_outcome_calibration_feed import validate_prediction_contract
    result = {"pending": 0, "orphaned": 0, "legacy": 0, "excluded": 0, "settled": 0}
    table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (PENDING_TABLE,)).fetchone()
    if not table:
        return result
    for raw in conn.execute(f"SELECT * FROM {PENDING_TABLE}").fetchall():
        row = dict(raw)
        if row.get("settled"):
            result["settled"] += 1
            continue
        try:
            contract, _reason = validate_prediction_contract(json.loads(row.get("source_payload_json") or "{}"))
        except (ValueError, TypeError):
            contract = None
        if "proxy" in str(row.get("prediction_field") or "") or not contract or contract["p"] != row.get("predicted_p"):
            result["excluded"] += 1
            result["legacy"] += int("proxy" in str(row.get("prediction_field") or ""))
        elif row.get("orphaned"):
            result["orphaned"] += 1
        else:
            result["pending"] += 1
    return result


def brier(pairs: list[dict[str, Any]]) -> float | None:
    if not pairs:
        return None
    return round(statistics.mean((p["predicted"] - p["actual"]) ** 2 for p in pairs), 4)


def reliability_bins(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    for low, high in zip(BIN_EDGES, BIN_EDGES[1:]):
        # Last bin is inclusive of the upper edge so predicted == 1.0 lands.
        in_bin = [
            p
            for p in pairs
            if (low <= p["predicted"] < high) or (high == 1.0 and p["predicted"] == 1.0)
        ]
        if not in_bin:
            continue
        bins.append(
            {
                "bin": f"{low:.1f}-{high:.1f}",
                "n": len(in_bin),
                "mean_predicted": round(statistics.mean(p["predicted"] for p in in_bin), 3),
                "realised_win_rate": round(statistics.mean(p["actual"] for p in in_bin), 3),
            }
        )
    return bins


def group_calibration(pairs: list[dict[str, Any]], key: str, min_group: int = 3) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in pairs:
        groups.setdefault(str(p[key]), []).append(p)
    out: dict[str, Any] = {}
    for name, rows in groups.items():
        if len(rows) < min_group:
            continue
        mean_pred = statistics.mean(p["predicted"] for p in rows)
        realised = statistics.mean(p["actual"] for p in rows)
        out[name] = {
            "n": len(rows),
            "mean_predicted": round(mean_pred, 3),
            "realised_win_rate": round(realised, 3),
            "calibration_gap": round(mean_pred - realised, 3),
            "brier": brier(rows),
        }
    return out


def worst_group(grouped: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        {"group": name, **stats}
        for name, stats in grouped.items()
        if abs(stats.get("calibration_gap", 0.0)) >= GAP_MEDIUM
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: abs(c["calibration_gap"]))


def classify_confidence(gap: float | None) -> str:
    if gap is None:
        return "unknown"
    if gap >= GAP_MEDIUM:
        return "overconfident"
    if gap <= -GAP_MEDIUM:
        return "underconfident"
    return "well_calibrated"


def derive_materiality(
    pairs: list[dict[str, Any]], gap: float | None, min_samples: int, group_flag: dict[str, Any] | None
) -> tuple[str, list[str]]:
    """Advisory only. Mirrors the anti-overfit posture of the rest of the skill:
    a calibration finding is only material when it is large AND well-sampled."""
    drivers: list[str] = []
    if len(pairs) < min_samples or gap is None:
        return "none", drivers
    abs_gap = abs(gap)
    if abs_gap >= GAP_HIGH:
        drivers.append(f"global_calibration_gap={gap:+.3f}")
        materiality = "high"
    elif abs_gap >= GAP_MEDIUM:
        drivers.append(f"global_calibration_gap={gap:+.3f}")
        materiality = "medium"
    else:
        materiality = "none"
    if group_flag is not None:
        drivers.append(
            f"group_drift:{group_flag['group']}_gap={group_flag['calibration_gap']:+.3f}(n={group_flag['n']})"
        )
        # A well-sampled single-group drift can lift a flat global read to medium.
        if materiality == "none" and group_flag["n"] >= min_samples:
            materiality = "medium"
    return materiality, drivers


def build_scorecard(conn, window: int, min_samples: int, source: str = "skill") -> dict[str, Any]:
    if source not in {"skill", "paper", "all"}:
        raise ValueError(f"unknown source: {source}")

    skill_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    skill_pairs: list[dict[str, Any]] = []
    paper_sample_total = 0
    paper_pending_total = 0
    paper_orphaned_total = 0
    paper_bucket_pairs: list[dict[str, Any]] = []
    if source in {"skill", "all"}:
        skill_items = verified_rows(conn, window)
        skill_pairs = collect_pairs(skill_items, source="skill")
    if source in {"paper", "all"}:
        paper_bucket_pairs, paper_sample_total = paper_pairs(conn, window)
        pending_stats = paper_pending_stats(conn)
        paper_pending_total = pending_stats["pending"]
        paper_orphaned_total = pending_stats["orphaned"]

    if source in {"paper", "all"}:
        audit = paper_sample_audit(conn, window)
        groups = {}
        for pair in audit["pairs"]:
            key = "|".join(pair[k] for k in ("horizon_id", "target", "strategy_version", "model_version"))
            groups.setdefault(key, []).append(pair)
        buckets = {key: {"n": len(rows), "brier_score": brier(rows), "reliability_bins": reliability_bins(rows), "calibration_status": "ok" if len(rows) >= min_samples else "insufficient"} for key, rows in groups.items()}
        result = {
            "ok": True, "source": source, "materiality_eligible": False,
            "calibration_status": "insufficient" if not groups else "separate_buckets",
            "samples_total": audit["total"], "samples_with_prediction": len(audit["pairs"]),
            "candidate_n": audit["window_candidates"], "evaluated_n": len(audit["pairs"]),
            "candidate_scope": "stored_paper_samples_in_window",
            "excluded_n": audit["window_excluded"],
            "abstention_rate": None, "abstention_reason": "stored_samples_do_not_cover_all_research_candidates_or_no_calls",
            "paper_samples_total": audit["total"], "paper_eligible_samples": audit["eligible"],
            "paper_legacy_samples": audit["legacy"], "paper_excluded_samples": audit["excluded"],
            "paper_excluded_reasons": audit["excluded_reasons"],
            "paper_pending_predictions": pending_stats["pending"], "paper_orphaned_predictions": pending_stats["orphaned"],
            "paper_pending_legacy": pending_stats["legacy"], "paper_pending_excluded": pending_stats["excluded"],
            "paper_pending_settled": pending_stats["settled"], "paper_buckets": buckets,
            "brier_score": None, "reason": "paper metrics require homogeneous horizon, target and model/strategy version buckets",
            "calibration_materiality": "none", "drivers": [], "advisory": True,
            "prediction_ledger": prediction_calibration(conn, window, min_samples),
            "no_order_execution": True, "min_samples": min_samples,
        }
        if source == "all":
            result["skill_bucket"] = build_scorecard(conn, window, min_samples, source="skill")
        return result

    pairs = skill_pairs + paper_bucket_pairs
    denominator_disclosure = calibration_denominators(skill_items, paper_bucket_pairs)
    samples_total = (len(skill_items) if source in {"skill", "all"} else 0) + (paper_sample_total if source in {"paper", "all"} else 0)
    samples_with_prediction = len(pairs)
    materiality_eligible = source == "skill"
    ledger_calibration = prediction_calibration(conn, window, min_samples)
    if samples_with_prediction == 0:
        if denominator_disclosure["candidate_n"] > 0:
            reason = "no_evaluated_predictions_after_abstention"
        else:
            reason = "no decisions carry factors.estimated_win_rate + a validated outcome" if source == "skill" else "no paper samples carry both an explicit predicted probability and a closed outcome"
        return {
            "ok": True,
            "source": source,
            "materiality_eligible": materiality_eligible,
            "calibration_status": "insufficient",
            "samples_total": samples_total,
            "samples_with_prediction": 0,
            **denominator_disclosure,
            "paper_samples_total": paper_sample_total,
            "paper_pending_predictions": paper_pending_total,
            "paper_orphaned_predictions": paper_orphaned_total,
            "min_samples": min_samples,
            "reason": reason,
            "calibration_materiality": "none",
            "drivers": [],
            "prediction_ledger": ledger_calibration,
            "advisory": True,
            "no_order_execution": True,
        }

    base_rate = round(statistics.mean(p["actual"] for p in pairs), 4)
    mean_predicted = round(statistics.mean(p["predicted"] for p in pairs), 4)
    realised_win_rate = base_rate
    gap = round(mean_predicted - realised_win_rate, 4)
    brier_score = brier(pairs)
    # Brier of the naive "always predict the base rate" forecaster, for skill scoring.
    brier_baseline = round(statistics.mean((base_rate - p["actual"]) ** 2 for p in pairs), 4)
    skill_score = (
        round(1 - (brier_score / brier_baseline), 4) if brier_baseline and brier_score is not None else None
    )

    by_regime = group_calibration(pairs, "regime")
    by_direction = group_calibration(pairs, "direction")
    by_action_bucket = group_calibration(pairs, "action_bucket")
    group_flag = worst_group({**by_regime, **by_direction, **by_action_bucket})

    enough = samples_with_prediction >= min_samples
    materiality, drivers = derive_materiality(pairs, gap, min_samples, group_flag) if materiality_eligible else ("none", [])

    return {
        "ok": True,
        "source": source,
        "materiality_eligible": materiality_eligible,
        "calibration_status": "ok" if enough else "insufficient",
        "samples_total": samples_total,
        "samples_with_prediction": samples_with_prediction,
        **denominator_disclosure,
        "paper_samples_total": paper_sample_total,
        "paper_pending_predictions": paper_pending_total,
        "paper_orphaned_predictions": paper_orphaned_total,
        "min_samples": min_samples,
        "base_rate": base_rate,
        "mean_predicted": mean_predicted,
        "realised_win_rate": realised_win_rate,
        "calibration_gap": gap,
        "confidence_posture": classify_confidence(gap),
        "brier_score": brier_score,
        "brier_baseline": brier_baseline,
        "skill_score": skill_score,
        "skill_score_note": "1 - brier/brier_baseline; >0 beats the base-rate forecaster, <0 is worse than guessing",
        "reliability_bins": reliability_bins(pairs),
        "by_regime": by_regime,
        "by_direction": by_direction,
        "by_action_bucket": by_action_bucket,
        "worst_calibrated_group": group_flag,
        "calibration_materiality": materiality,
        "drivers": drivers,
        "prediction_ledger": ledger_calibration,
        "advisory": True,
        "materiality_note": "Skill bucket only: advisory and eligible for materiality gates after min_samples. Paper/all buckets are read-only reference and never trigger sizing/materiality." if materiality_eligible else "Paper/all bucket: reference-only, materiality_eligible=false, never triggers sizing/materiality.",
        "no_order_execution": True,
    }


def self_test() -> dict[str, Any]:
    """Seed an in-memory DB with a deliberately over-confident track record and
    assert the scorecard detects it. Mirrors trading_memory_core.cmd_self_test."""
    import argparse as _argparse

    from trading_memory_core import cmd_record_decision, cmd_record_result

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "memory.sqlite"
        conn = connect(path)
        # 10 decisions each predicting a confident 0.8 win rate, but only 3 win:
        # a clear +0.5 overconfidence gap the scorecard must surface.
        for i in range(10):
            won = i < 3
            decision = {
                "symbol": f"CAL{i}",
                "market": "US",
                "direction": "long",
                "action_level": "L2",
                "factors": {"estimated_win_rate": 0.8},
                "review_clock": "2099-01-01T00:00:00+00:00",
            }
            dpath = Path(td) / f"d{i}.json"
            dpath.write_text(json.dumps(decision), encoding="utf-8")
            out = cmd_record_decision(conn, _argparse.Namespace(payload=str(dpath), db=str(path)))
            result = {
                "return_pct": 5.0 if won else -5.0,
                "outcome": "success" if won else "failure",
            }
            rpath = Path(td) / f"r{i}.json"
            rpath.write_text(json.dumps(result), encoding="utf-8")
            cmd_record_result(
                conn,
                _argparse.Namespace(payload=str(rpath), decision_id=out["decision_id"], db=str(path)),
            )
        card = build_scorecard(conn, window=36, min_samples=5, source="skill")
        assert card["samples_with_prediction"] == 10, card
        assert card["candidate_n"] == 10 and card["evaluated_n"] == 10, card
        assert card["abstention_rate"] == 0.0, card
        assert card["calibration_gap"] > 0.3, card
        assert card["confidence_posture"] == "overconfident", card
        assert card["calibration_materiality"] == "high", card
        assert card["skill_score"] is not None and card["skill_score"] < 0, card
        conn.execute(
            "CREATE TABLE calibration_samples_paper (sample_id TEXT PRIMARY KEY, decision_ref TEXT, symbol TEXT, predicted_p REAL, outcome INTEGER, source TEXT, recorded_at TEXT, outcome_time TEXT, prediction_field TEXT, source_payload_json TEXT)"
        )
        conn.execute(
            "INSERT INTO calibration_samples_paper VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("paper:1", "paper-decision", "PAPER.US", 0.7, 1, "paper", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "win_rate_proxy", "{}"),
        )
        conn.execute(
            "CREATE TABLE calibration_pending_paper_predictions (proposal_ref TEXT PRIMARY KEY, symbol TEXT, predicted_p REAL, prediction_field TEXT, opened_at TEXT, source_file TEXT, recorded_at TEXT, source_payload_json TEXT, orphaned INTEGER NOT NULL DEFAULT 0, orphaned_at TEXT, orphaned_reason TEXT)"
        )
        conn.execute(
            "INSERT INTO calibration_pending_paper_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pending-1", "PEND.US", 0.61, "win_rate_proxy", "2026-01-01T00:00:00Z", "proposal.json", "2026-01-01T00:00:00Z", "{}", 0, None, None),
        )
        conn.execute(
            "INSERT INTO calibration_pending_paper_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pending-2", "GONE.US", 0.55, "win_rate_proxy", "2026-01-01T00:00:00Z", "proposal.json", "2026-01-01T00:00:00Z", "{}", 1, "2026-01-02T00:00:00Z", "position_not_in_latest_snapshot"),
        )
        paper_card = build_scorecard(conn, window=36, min_samples=5, source="paper")
        conn.close()
        assert paper_card["samples_with_prediction"] == 0 and paper_card["materiality_eligible"] is False, paper_card
        assert paper_card["paper_pending_predictions"] == 0, paper_card
        assert paper_card["paper_pending_legacy"] == 2, paper_card
        prediction = {
            "prediction_id": "PR-CAL-1",
            "decision_id": None,
            "registered_at": "2026-01-01T00:00:00Z",
            "symbol": "CAL",
            "market": "US",
            "longbridge_symbol": "CAL.US",
            "horizon_id": "swing_days",
            "direction": "up",
            "probability": 0.7,
            "as_of": "2026-01-01T00:00:00Z",
            "due_at": "2026-01-02T00:00:00Z",
            "regime": "neutral",
            "calibration_bucket": "CAL×swing_days×neutral×up×1pct",
            "reference_price": 100.0,
            "status": "settled",
            "resolution_json": '{"metric":"directional_return","threshold_pct":0}',
            "payload_json": "{}",
        }
        conn = connect(path)
        conn.execute(
            "INSERT INTO predictions VALUES (:prediction_id, :decision_id, :registered_at, :symbol, :market, "
            ":longbridge_symbol, :horizon_id, :direction, :probability, :as_of, :due_at, :regime, "
            ":calibration_bucket, :reference_price, :status, :resolution_json, :payload_json)",
            prediction,
        )
        conn.execute(
            "INSERT INTO prediction_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("PO-CAL-1", "PR-CAL-1", "2026-01-02T00:00:00Z", 105.0, 1, "success", "fixture", "{}"),
        )
        conn.commit()
        prediction_card = build_scorecard(conn, window=36, min_samples=5, source="skill")
        conn.close()
        assert prediction_card["prediction_ledger"]["by_horizon"]["swing_days"]["status"] == "insufficient_n"
        assert prediction_card["prediction_ledger"]["cross_horizon_aggregate"] is None
        return {"ok": True, "self_test": "passed", "calibration_gap": card["calibration_gap"], "paper_samples": paper_card["samples_with_prediction"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibration scorecard for trading-research decision memory")
    ap.add_argument("--db", help="SQLite DB path; default TRADING_MEMORY_DB or core default")
    ap.add_argument("--window", type=int, default=200, help="how many recent validated decisions to scan")
    ap.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    ap.add_argument("--source", choices=["skill", "paper", "all"], default="skill", help="calibration bucket; paper/all are reference-only and materiality_eligible=false")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        out = self_test()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    conn = connect(db_path(args))
    try:
        out = build_scorecard(conn, args.window, args.min_samples, args.source)
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False, indent=2 if args.json else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
