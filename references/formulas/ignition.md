# v1

```text
ΔV_i = cumulative_volume_i - cumulative_volume_{i-1}
M_t = median({ΔV_i | i ∈ [t-N,t-1]})
G_t = max(q_min, k×M_t)
ΔP_ticks = ticks(last_t) - ticks(last_{t-1})
ActiveBuyProxy_t = ticks(last_t) ≥ ticks(best_ask_{t-1})
ContextValid = same_nonempty(symbol_i) ∧ same_nonempty(session_id_i) ∧ (0<ts_ms_i-ts_ms_{i-1}≤gap_max)
Fired_t = ContextValid ∧ (ΔV_t > G_t) ∧ (ΔP_ticks ≥ move_min) ∧ ActiveBuyProxy_t
Strength_t = (1 if Fired_t else 0) if G_t=0 else min(1, ΔV_t / (2×G_t))
Evidence = {tick_ref_{t-1}, tick_ref_t}
InputFidelity = snapshot_diff
Approximation = adjacent_snapshot(Δcumulative_volume, Δlast_price, previous_best_ask)
ErrorSources = {unknown_trade_aggressor, within_3s_path_aliasing, book_trade_timestamp_skew, hidden_liquidity}
InvalidWhen = {symbol_changed, session_changed, missing_timestamp, nonmonotonic_timestamp, snapshot_gap_exceeded, cumulative_volume_reset, insufficient_intervals, missing_previous_best_ask, missing_book_side, nonfinite_price_or_volume}
FidelityLock = train_only_until_tick_by_tick_revalidation
```

| parameter | default | range | unit |
|---|---:|---:|---|
| volume_multiplier (k) | 3.0 | [1.0,100.0] | x_median |
| lookback_intervals (N) | 5 | [2,60] | intervals |
| min_trade_volume (q_min) | 10000 | [0,1000000000] | shares |
| min_price_move_ticks (move_min) | 1 | [1,100] | ticks |
| max_gap_ms (gap_max) | 6000 | [1,60000] | milliseconds |
| price_tick | 0.01 | [0.0001,1.0] | CNY |
