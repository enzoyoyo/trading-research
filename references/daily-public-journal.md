# Daily Public Journal

Use this reference when the user asks for a daily trading log, a public/auditable record of System A's paper-trading day, or a running scoreboard of predictions vs. outcomes.

## Objective

Produce one publish-ready page per trading day — pre-market prediction, intraday execution, post-market review/attribution, lessons learned — assembled from System A's scattered per-run artifacts. The point is a continuously verifiable public track record over 1-2 months, not a new decision surface. This is a pure reporting layer: it never produces a `module_signal`, never changes an action level, and never triggers a materiality gate.

## Data flow

```
System A (read-only)                          skill decision memory (read-only)
  proposals/generated_*.json          ─┐         trading_memory_core.py
  proposals/exit_generated_*.json     ─┤           -> calibration_samples_paper (prediction hit judgment)
  decision_packets/paper_decision_*   ─┤         hypothesis_registry.py
  journal/paper_orders.jsonl          ─┼──►  scripts/daily_journal.py  ──►  ~/.hermes/trading-journal/
  journal/paper_outcomes.jsonl        ─┤         (compose)                    daily/YYYY-MM-DD.md
  journal/paper_position_snapshots.*  ─┘                                      index.jsonl
                                                                               README.md
```

`daily_journal.py` never writes into System A; its only write target is the independent `~/.hermes/trading-journal/` data home (its own local git repo, no remote).

## Trading-day key

US/Eastern calendar date, since System A's universe is US-heavy. Any `.HK`-suffixed symbol is bucketed by its own Asia/Hong_Kong trading date instead of being force-merged into the US day. `--compose --days N` walks back N US-Eastern weekdays (Mon-Fri) from now; it does not consult a holiday calendar, so a market holiday with zero real activity simply renders an all-`data_gap` page rather than a fabricated one.

## Page contract

Every page has these four sections. A section with no data for that day writes `data_gap` (or a plain "none") — it is never left empty and never invents a number to fill the gap.

1. **盘前预测** — that day's decision-packet summary (universe size/core count, position/order-count risk gate values) plus the observed `regime_profile` values seen in that day's exit-engine diagnostics; each distinct new-entry proposal (symbol/side/quantity/limit/thesis/invalidation/win_rate_proxy). Because System A's proposal engine re-scans roughly every 30 minutes, repeated same-symbol/side signals across the day are aggregated into one row with a `cycles` count and quantity/price/win-rate ranges instead of one row per scan.
2. **盘中执行** — the orders timeline: submitted / rejected-at-validation / replaced / mechanical-exit orders, each with its real reason (`exit_reason`, session-guard rejection text, risk-breach type). Every line is timestamped at HH:MM in the order's own market local time (US/Eastern, or Asia/Hong_Kong for a `.HK` symbol — same per-symbol timezone rule as the Trading-day key above; a replace record carries no symbol so it renders in US/Eastern). Identical repeated actions across scan cycles are collapsed to one line with an `(xN scan cycles)` suffix rather than listed N times — this keeps the page readable without hiding the repetition count; the timestamp shown for a collapsed exit-order line is the first occurrence's HH:MM, not a range. Rejected-at-validation retries re-quote a slightly different limit price and quantity every cycle, so a plain field-match dedupe still leaves near-duplicate rows; these are instead grouped by `(symbol, side, frozenset(reasons))` into one line reporting an attempt count plus quantity/limit-price/HH:MM ranges (e.g. `14:33-15:33 XLF.US buy ×18 attempts, qty 1628-1677, limit 55.48-55.72: max_positions_reached, cash_max_qty_exceeded_or_unavailable`), matching the range-aggregation style used for pre-market entry proposals.
3. **盘后复盘与归因** — day-end portfolio equity (`net_assets`) and its day-over-day change; a table of that day's closed trades (entry/exit/R/realized PnL) with realized dollar PnL computed by FIFO-matching each close against the earlier same-symbol open records in `paper_outcomes.jsonl` (pure accounting arithmetic on real logged fields, not a prediction claim); and prediction-hit judgment sourced *only* from the existing calibration bucket (`calibration_samples_paper`) — this composer never recomputes or invents a win/loss probability of its own.
4. **经验沉淀** — hypothesis-registry entries whose status changed that day (read-only query via `hypothesis_registry.py`), and the set of `exit_reason` labels actually observed among that day's closed trades. No behavioral-bias severity labels here — those require a rolling-window sample size per `paper-portfolio-analysis-playbook.md` and are the wrong granularity for a single day.

## index.jsonl contract

One JSON line per date (upserted on recompose), schema `daily_journal.v1`:

```
{date, schema_version, proposals, orders_submitted, orders_filled, closed_trades,
 realized_pnl, day_equity, day_equity_change_pct, predictions_scored, prediction_hits,
 error_labels[], data_gaps[]}
```

`proposals` counts distinct (symbol, side) entry ideas that day, not raw scan-cycle repeats. `realized_pnl` sums only FIFO-matched closes; if any close that day could not be matched to an earlier open (e.g. a backfilled position whose original open was never logged), that gap is recorded in `data_gaps` rather than silently omitted from the sum.

## README.md scoreboard

Regenerated in full on every compose run: days recorded, cumulative realized PnL (noting it is partial wherever `data_gaps` mark unmatched closes), prediction pair count and hit rate (must show `insufficient_sample` below 12 paired predictions instead of a misleading percentage), and a table of the most recent 30 days.

## Redaction (hard rule)

Any composed line containing `token`, `credential`, `auth`, or the literal absolute home directory path is replaced wholesale with `[redacted]` before it is ever written to disk — this applies to every daily page, `index.jsonl`, and `README.md`. `--self-test` includes a fixture proposal/snapshot record carrying a real `token_path`-shaped value and asserts it never survives into the composed output.

## Publish policy

`--publish` runs `git init` (idempotent, first run only) inside `~/.hermes/trading-journal/` if it is not already a repo, then `git add -A && git commit` locally after a successful compose. It never pushes. A `--push` flag exists in the CLI for the user's own later use once a remote is configured by hand; this skill's own automation (Daily Loop step 8) never passes `--push`.

## Boundaries

- Read-only against System A in full, and read-only against this skill's own calibration bucket / hypothesis registry.
- Never submits, cancels, or replaces an order; never writes into System A.
- Prediction-accuracy authority stays with the calibration bucket (`calibration_scorecard.py` / `calibration_samples_paper`); this composer only cites it, never recomputes a probability.
- The five-component shadow-ledger PnL decomposition (`missed_signals_pnl` etc.) and the four behavioral-bias diagnostics from `paper-portfolio-analysis-playbook.md` are out of scope here — wrong data ownership and wrong time granularity respectively for a single day's page.
