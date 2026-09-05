# v1

```text
M_j = median({Q_i^ask(p) | i ∈ [j-N,j-1], p ∈ L1..L5, Q_i^ask(p)>0})
G_j = max(q_min, k×M_j)
Wall_j(p) = Q_j^ask(p)
Q_j(p) ≥ Q_{j+1}(p) ≥ ... ≥ Q_k(p)
ticks(last_j) ≤ ticks(last_{j+1}) ≤ ... ≤ ticks(last_k)
RemovedFraction_{j,k}(p) = (Wall_j(p)-Q_k(p)) / Wall_j(p)
ExecutedFraction_{j,k}(p) = (cumulative_volume_k-cumulative_volume_j) / Wall_j(p)
SweepSteps_{j,k} = k-j
PositiveTradeSteps_{j,k} = Σ_{h=j+1..k} 1[cumulative_volume_h-cumulative_volume_{h-1}>0]
ContextValid = same_nonempty(symbol_i) ∧ same_nonempty(session_id_i) ∧ (0<ts_ms_i-ts_ms_{i-1}≤gap_max) ∧ nondecreasing(cumulative_volume_i)
Qualified_{j,k}(p) = (Wall_j(p)>G_j) ∧ nonincreasing(Q_{j..k}(p)) ∧ nondecreasing(ticks(last_{j..k})) ∧ (RemovedFraction_{j,k}(p)≥b_min) ∧ (ExecutedFraction_{j,k}(p)≥e_min) ∧ (ticks(last_k)>ticks(p)) ∧ (S_min≤SweepSteps_{j,k}≤S_max) ∧ (PositiveTradeSteps_{j,k}≥S_min)
Fired_{j,k}(p) = ContextValid ∧ (k=t_latest) ∧ Qualified_{j,k}(p) ∧ ¬Qualified_{j,k-1}(p)
ExecutionStrength = min(1, ExecutedFraction/(2×e_min))
Strength = min(1, (RemovedFraction+ExecutionStrength)/2)
Evidence = {tick_ref_j,...,tick_ref_k}
InputFidelity = snapshot_diff
Approximation = nonincreasing_same_ask_price_depth_plus_total_interval_trades_plus_price_cross
ErrorSources = {cancel_vs_execution_ambiguity, trades_at_other_prices, within_3s_ordering_loss, hidden_liquidity, independent_price_jump}
InvalidWhen = {symbol_changed, session_changed, missing_timestamp, nonmonotonic_timestamp, snapshot_gap_exceeded, cumulative_volume_reset, fewer_than_N_baseline_snapshots, insufficient_sweep_steps, increasing_wall_depth, decreasing_snapshot_price, missing_book_side, level5_queue_exit, nonfinite_price_or_volume}
FidelityLock = train_only_until_tick_by_tick_trade_revalidation
```

| parameter | default | range | unit |
|---|---:|---:|---|
| wall_multiplier (k) | 3.0 | [1.0,100.0] | x_median |
| lookback_snapshots (N) | 5 | [1,60] | snapshots |
| min_wall_volume (q_min) | 10000 | [0,1000000000] | shares |
| max_sweep_snapshots (S_max) | 3 | [2,20] | snapshots |
| min_sweep_steps (S_min) | 2 | [1,20] | transitions |
| break_fraction (b_min) | 0.8 | [0.5,1.0] | fraction |
| min_execution_fraction (e_min) | 0.5 | [0.1,1.0] | fraction |
| max_gap_ms (gap_max) | 6000 | [1,60000] | milliseconds |
| price_tick | 0.01 | [0.0001,1.0] | CNY |
