#!/usr/bin/env python3
"""Generic data freshness and trading-date guards for trading-research.

Market-agnostic helpers: date parsing, as-of vs target alignment, weekday checks,
and structured degrade payloads. Does not fetch market data or execute orders.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from typing import Any

DATE_PATTERNS = (
    re.compile(r"^(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})$"),
    re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})$"),
    re.compile(r"^(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})$"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_trade_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for pattern in DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        try:
            return date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        except ValueError:
            return None
    return None


def format_trade_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def is_weekend(value: date) -> bool:
    return value.weekday() >= 5


def _coerce_trade_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-":
        return parse_trade_date(text[:10])
    compact = text.replace("-", "").replace("/", "")[:8]
    return parse_trade_date(compact)


def validate_as_of_alignment(
    *,
    as_of: str | None,
    target_trade_date: str | None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Fail-closed when formal conclusions would use a mismatched trade date."""
    target = parse_trade_date(target_trade_date)
    as_of_date = _coerce_trade_date(as_of)
    observed = _coerce_trade_date(observed_at)
    gaps: list[dict[str, str]] = []
    status = "pass"
    if target is None:
        gaps.append(
            {
                "gap": "target_trade_date_invalid",
                "severity": "high",
                "impact": "无法绑定正式结论到目标交易日",
            }
        )
        status = "blocked"
    if as_of and target and as_of_date and as_of_date != target:
        gaps.append(
            {
                "gap": "as_of_target_trade_date_mismatch",
                "severity": "high",
                "impact": "as_of 与目标交易日不一致，禁止写入正式结论",
            }
        )
        status = "blocked"
    if target and is_weekend(target):
        gaps.append(
            {
                "gap": "target_trade_date_weekend",
                "severity": "medium",
                "impact": "目标日期为周末，A/H/US 常规交易日校验未通过",
            }
        )
        if status == "pass":
            status = "degraded"
    if observed and target and observed < target:
        gaps.append(
            {
                "gap": "observed_before_target_trade_date",
                "severity": "high",
                "impact": "观测时间早于目标交易日，禁止冒充收盘后数据",
            }
        )
        status = "blocked"
    return {
        "status": status,
        "target_trade_date": format_trade_date(target) if target else None,
        "as_of_trade_date": format_trade_date(as_of_date) if as_of_date else None,
        "observed_trade_date": format_trade_date(observed) if observed else None,
        "data_gaps": gaps,
        "may_write_formal_conclusion": status == "pass",
    }


def degrade_payload(*, reason: str, impact: str, severity: str = "high") -> dict[str, Any]:
    return {
        "ok": False,
        "degraded": True,
        "reason": reason,
        "data_gaps": [{"gap": reason, "severity": severity, "impact": impact}],
        "may_write_formal_conclusion": False,
        "checked_at": now_iso(),
    }


def self_test() -> dict[str, Any]:
    aligned = validate_as_of_alignment(
        as_of="2026-08-19",
        target_trade_date="20260819",
        observed_at="2026-08-19T07:00:00Z",
    )
    blocked = validate_as_of_alignment(
        as_of="2026-08-18",
        target_trade_date="20260819",
        observed_at="2026-08-19T07:00:00Z",
    )
    weekend = validate_as_of_alignment(
        as_of="2026-08-16",
        target_trade_date="20260816",
        observed_at="2026-08-16T07:00:00Z",
    )
    checks = [
        aligned["status"] == "pass",
        blocked["status"] == "blocked",
        weekend["status"] == "degraded",
        parse_trade_date("20260819") == date(2026, 8, 19),
        parse_trade_date("bad") is None,
    ]
    return {
        "ok": all(checks),
        "self_test": "passed" if all(checks) else "failed",
        "aligned_status": aligned["status"],
        "blocked_status": blocked["status"],
        "weekend_status": weekend["status"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Data freshness and trading-date guard")
    parser.add_argument("--target-trade-date", default=None)
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--observed-at", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        payload = self_test()
    else:
        payload = validate_as_of_alignment(
            as_of=args.as_of,
            target_trade_date=args.target_trade_date,
            observed_at=args.observed_at,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json or args.self_test else None))
    return 0 if payload.get("ok", payload.get("status") in {"pass", "degraded"}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
