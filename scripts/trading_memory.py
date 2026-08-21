#!/usr/bin/env python3
"""CLI wrapper for trading-research decision memory."""
from __future__ import annotations

import argparse

from trading_memory_core import (
    cmd_pending,
    cmd_preflight,
    cmd_record_decision,
    cmd_record_result,
    cmd_review,
    cmd_self_test,
    cmd_stats,
    cmd_think,
    connect,
    db_path,
    print_result,
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Trading decision memory for trading-research skill")
    ap.add_argument("--db", help="SQLite DB path; default TRADING_MEMORY_DB or ~/.cache/hermes/trading-research/memory/trading_memory.sqlite")
    ap.add_argument("--json", action="store_true", help="Pretty JSON output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def allow_trailing_json(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        # Accept both `trading_memory.py --json preflight ...` and
        # `trading_memory.py preflight ... --json`. The skill docs use the latter.
        parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return parser

    p = allow_trailing_json(sub.add_parser("preflight"))
    p.add_argument("--symbol", required=True)
    p.add_argument("--market", required=True)
    p.add_argument("--direction", required=True, choices=["long", "short", "watch", "reduce", "avoid", "buy", "build", "add", "sell"])
    p.add_argument("--window", type=int, default=36)

    p = allow_trailing_json(sub.add_parser("record-decision"))
    p.add_argument("--payload", required=True)

    p = allow_trailing_json(sub.add_parser("record-result"))
    p.add_argument("--decision-id", required=True)
    p.add_argument("--payload", required=True)

    p = allow_trailing_json(sub.add_parser("review"))
    p.add_argument("--window", type=int, default=36)
    p.add_argument("--min-samples", type=int, default=12)
    p.add_argument("--cooldown-minutes", type=int, default=360)
    p.add_argument("--pattern-min-count", type=int, default=3)
    p.add_argument("--pattern-min-share", type=float, default=0.20)
    p.add_argument("--max-adjustment-delta", type=float, default=0.15)
    p.add_argument("--force", action="store_true")

    p = allow_trailing_json(sub.add_parser("think"))
    p.add_argument("--window", type=int, default=36)
    p.add_argument("--min-samples", type=int, default=12)
    p.add_argument("--cooldown-minutes", type=int, default=360)
    p.add_argument("--pattern-min-count", type=int, default=3)
    p.add_argument("--pattern-min-share", type=float, default=0.20)
    p.add_argument("--max-adjustment-delta", type=float, default=0.15)
    p.add_argument("--force", action="store_true")

    p = allow_trailing_json(sub.add_parser("pending"))
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--due-only", action="store_true")

    allow_trailing_json(sub.add_parser("stats"))
    allow_trailing_json(sub.add_parser("self-test"))
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "self-test":
        print_result(cmd_self_test(), args.json)
        return 0
    conn = connect(db_path(args))
    try:
        if args.cmd == "preflight":
            out = cmd_preflight(conn, args)
        elif args.cmd == "record-decision":
            out = cmd_record_decision(conn, args)
        elif args.cmd == "record-result":
            out = cmd_record_result(conn, args)
        elif args.cmd == "review":
            out = cmd_review(conn, args)
        elif args.cmd == "think":
            out = cmd_think(conn, args)
        elif args.cmd == "pending":
            out = cmd_pending(conn, args)
        elif args.cmd == "stats":
            out = cmd_stats(conn, args)
        else:
            raise SystemExit(f"unknown command {args.cmd}")
        print_result(out, args.json)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
