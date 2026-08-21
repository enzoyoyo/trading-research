# Event → Reaction Memory Data Foundation

## Purpose

This reference locks the Phase 0/1 data contract for the event-reaction memory system.

The system separates two layers:

1. **Relationship layer** — continuous daily returns estimate prior co-movement (`relationship_graph.py`). It does not need event labels.
2. **Event layer** — registered events and post-event reactions (`event_reaction_journal.py`) measure whether actual reaction deviated from the relationship prior.

This is decision support, not an automated alpha factor. Residuals are hypotheses until future paper records prove forward hit rate.

## Runtime storage

The skill directory must stay clean. Runtime data goes to:

- Event/reaction log: `~/.cache/hermes/trading-research/events/event_reactions.jsonl`
- Relationship snapshots: `~/.cache/hermes/trading-research/relationships/relationship_graph_YYYYMMDD.json`
- Latest relationship graph copy: `~/.cache/hermes/trading-research/relationships/latest.json`

Environment overrides:

- `EVENT_REACTION_LOG`
- `RELATIONSHIP_GRAPH_DIR`
- `TRADING_RESEARCH_STATE_DIR`

## Event journal CLI

```bash
python3 scripts/event_reaction_journal.py register --payload event.json --json
python3 scripts/event_reaction_journal.py backfill --event-id EVT-... --horizon T1 --json
python3 scripts/event_reaction_journal.py pending-backfill --json
python3 scripts/event_reaction_journal.py list --since 2026-01-01 --json
```

### Idempotency

- Re-registering the same `event_id` returns `status=exists` and does not append a duplicate event.
- Re-backfilling the same `(event_id, symbol, horizon)` returns `status=exists` for that row and does not append a duplicate reaction.

### Event record

`register` accepts the PRD shape and adds `record_kind: event` when missing.

Minimum required fields:

```json
{
  "event_id": "EVT-20260617-FOMC",
  "event_type": "macro_fomc",
  "event_label": "FOMC rate decision",
  "event_date": "2026-06-17",
  "primary_symbols": ["SPY.US"],
  "related_symbols": ["QQQ.US", "NVDA.US"]
}
```

### Reaction record

`backfill` writes one append-only row per `(event_id, symbol, horizon)`:

```json
{
  "record_kind": "reaction",
  "event_id": "EVT-20260617-FOMC",
  "symbol": "NVDA.US",
  "role": "primary|related",
  "horizon": "T0|T1|T3",
  "ref_date": "2026-06-18",
  "raw_return_pct": 2.1,
  "benchmark_symbol": "SPY.US",
  "benchmark_return_pct": 0.6,
  "beta_source": "relationship_graph|benchmark_fallback|unavailable",
  "beta_vs_benchmark": 1.58,
  "abnormal_return_pct": 1.152,
  "iv_before": null,
  "iv_after": null,
  "iv_change": null,
  "realized_vol_5d_after": null,
  "data_gaps": [],
  "computed_at_utc": "..."
}
```

Abnormal return formula:

```text
abnormal = raw_return_pct - beta_vs_benchmark * benchmark_return_pct
```

If `relationships/latest.json` is missing or lacks a beta for the symbol, beta falls back to `1.0`, `beta_source=benchmark_fallback`, and `data_gaps` includes `beta_unavailable_used_raw_excess`.

IV fields intentionally remain `null` when an IV source is unavailable; missing data is logged in `data_gaps` rather than guessed.

## Relationship graph CLI

```bash
python3 scripts/relationship_graph.py build --json
python3 scripts/relationship_graph.py build --universe-file my_universe.txt --slow-days 120 --fast-days 60 --json
python3 scripts/relationship_graph.py show --pair NVDA.US,MU.US --json
```

Default seed universe:

```text
SPY.US, QQQ.US, NVDA.US, TSLA.US, AAPL.US, MU.US, COHR.US, XLE.US, UVXY.US, 0700.HK, 9988.HK
```

Snapshot fields:

- `market_beta[]`: symbol beta/corr vs benchmark.
- `pairs[]`: ordered pair beta/corr/r2 using 60d and 120d windows.
- `relationship_regime_shift`: true when 60d beta diverges materially from 120d beta.
- `data_gaps[]`: every unavailable symbol, insufficient aligned days, or regime fetch failure.
- Relationship graph K-line fetch uses `scripts/longbridge_query.py`; it now falls back from longport SDK to LongBridge CLI `kline` when SDK is missing or times out, so an SDK outage must not silently create an empty graph.

## Boundaries

- No order execution.
- No dependency on System A paper trading.
- No historical backtest claims.
- No automatic sizing/weight changes from event residuals in Phase 0/1.
- All registered events remain in the log, including no-signal and failed cases, to avoid survivorship bias.
