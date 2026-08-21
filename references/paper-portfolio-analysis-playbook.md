# Paper Portfolio Analysis Playbook

Use this reference when 用户 asks to analyze LongBridge/System A 模拟仓, pick the highest-win-rate US ticket, or rank current paper holdings by alpha.

## Objective
Produce a read-only decision memo from the paper portfolio state. Do not submit/cancel/replace orders. Treat the paper system as evidence and state, not as permission to trade.

## Read-only sequence
1. Prove account scope first:
   - Run the paper gate (`~/.hermes/scripts/longbridge-paper-gate.sh`) or equivalent gate checks.
   - Required pass state: `paper_account_gate=pass`, `account_channel=lb_papertrading`, isolated paper token path.
2. Refresh paper portfolio artifacts, in this order when available:
   - `~/.hermes/longbridge-paper-trading/scripts/paper_position_snapshot.py`
   - `~/.hermes/longbridge-paper-trading/scripts/paper_decision_packet.py`
   - `~/.hermes/longbridge-paper-trading/scripts/paper_learning_report.py`
   - `~/.hermes/longbridge-paper-trading/scripts/paper_trade_review.py`
   - `~/.hermes/longbridge-paper-trading/scripts/paper_fill_reconciler.py --json` dry-run only unless the user explicitly asks for local journal repair.
3. Treat **broker positions as the source of truth** for current holdings.
   - Local `paper_trade_review` / `paper_outcomes.jsonl` can contain stale open observations or quantity drift.
   - If broker positions disagree with local review, rank and size from broker positions; report the mismatch as a data-quality gap.
4. Refresh live market state and quotes for:
   - broker-held symbols;
   - the configured paper universe;
   - relevant ETFs / hedges / regime proxies.
5. Refresh risk context:
   - `state/risk_regime.json` if recent;
   - `scripts/dispersion_crowding.py --json` for crowding/dispersion;
   - market temperature / anomalies / news where available.
6. Inspect latest generated proposal:
   - If the latest `generated_*.json` has zero orders and diagnostics are `no_signal`, do **not** invent a new ticket just to answer the request.
   - Prefer: “highest-win-rate ticket is hold existing winner; no new add.”
7. Rank holdings by alpha using actual evidence:
   - realized/unrealized broker P/L;
   - fresh catalyst quality;
   - current day relative strength;
   - intraday fade vs persistence;
   - regime/crowding penalty;
   - data-quality gaps.
8. For any actionable L1+ judgment, record Decision Memory with `factors.estimated_win_rate` and `price_at_decision` so `completeness=full`.

## Interpretation rules
- “最高胜率的票” can be an existing-position hold. It does not have to be a fresh buy.
- If the system has no new signal, state that plainly. Do not force a new order candidate.
- In `stress_building`, `dispersion_extreme`, or `single_stock_speculation_hot` states, chasing fresh longs is lower quality than holding/harvesting a verified winner.
- If an existing winner is up strongly but fading from the open, classify as `hold_existing_no_add`, not `fresh_buy`.
- New-ticket candidates require both current setup and system signal; watch-only candidates should be labeled watch-only.

## Output format
Use the concise trading-research house style:
1. one-line conclusion;
2. paper gate + no-order status;
3. table of broker-held positions;
4. top 2-3 alpha holdings with action line;
5. why no new add / where the risk boundary is;
6. data-quality gaps;
7. Decision Memory write status when recorded.

## Counterfactual attribution taxonomy

System A owns the paper-trading shadow ledger (its counterfactual "what if every signal had been followed exactly" account). This skill treats the ledger's output as read-only evidence to label during review — it never computes, backfills, or reads System A's internal data files directly.

Shadow ledger PnL decomposes into five signed components:

| Component | Definition |
|---|---|
| `missed_signals_pnl` | PnL from a signal that should have triggered a trade but did not |
| `noise_trades_pnl` | PnL from a trade with no supporting signal |
| `early_exit_pnl` | PnL forfeited by exiting before the signal's target/stop resolved |
| `late_exit_pnl` | PnL lost by holding past the signal's exit trigger |
| `overtrading_pnl` | PnL cost of trades beyond the signal-implied trade count |

Reconciliation: `missed_signals_pnl + noise_trades_pnl + early_exit_pnl + late_exit_pnl + overtrading_pnl == shadow_total_pnl - real_total_pnl`. If the five components do not sum to that gap, treat the attribution as a data-quality gap, not a review finding — do not force a reconciliation by adjusting one component.

This skill's role in the loop: read the five components from System A's output, attach the matching failure tag to the affected Decision Memory record, and feed the pattern into `learning_digest`'s failure-tag vocabulary. No new script here; System A owns the ledger's implementation.

## Behavioral bias diagnostics

Four behavioral biases, each requires quantitative evidence before a severity label is assigned. No numeric evidence → no severity; write `insufficient_evidence` instead of guessing.

| Bias | Evidence required | Severity bands |
|---|---|---|
| Disposition effect (处置效应) | Median holding days for closed winners vs median holding days for closed losers | low: loser/winner ratio <1.3x; medium: 1.3x-2x; high: >2x |
| Overtrading | Turnover rate vs realized return contribution per trade | low: turnover tracks return; medium: turnover 2x-4x the signal-implied rate; high: >4x with flat/negative marginal contribution |
| Chasing (追涨) | Distribution of the prior-5-trading-day return at entry | low: entries spread across the distribution; medium: >50% of entries in the top quintile of prior-5-day returns; high: >75% |
| Anchoring (锚定) | Whether add/hold decisions correlate with distance from cost basis after controlling for signal state | low: decisions track signal state; medium: decisions correlate with cost basis independent of signal; high: decisions contradict signal state near cost basis |

Rules:
- Diagnostics feed Rolling Review and `learning_digest` only; they never change position size or action level directly, and never trigger a materiality gate on their own.
- Require the same sample-size floor Rolling Review already uses (`min_samples`) before labeling a bias — a single trade is an anecdote, not a diagnosis.
- These four are read-only pattern labels, not a new Decision Compiler module; they cannot introduce a new `module_signal`.

## Common pitfalls
- Counting stale local open observations as current holdings.
- Treating submitted orders as filled without broker confirmation.
- Ranking from old trade-review quantities when broker quantity differs.
- Interpreting a zero-order proposal as a failure; it is often the correct “no new signal” state.
- Letting a strong catalyst override crowding/dispersion stress. Risk regime still caps action level.
