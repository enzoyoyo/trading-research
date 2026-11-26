# v1

```text
side ∈ {bid, ask}
M_j = median({Q_i^side(p) | i ∈ [j-N,j-1], p ∈ L1..L5, Q_i^side(p)>0})
G_j = max(q_min, k×M_j)
Added_j(p) = Q_j^side(p) - Q_{j-1}^side(p)
Removed_{j,k}(p) = max(0, Q_j^side(p) - Q_k^side(p))
Cancelled_{j,k}(p) = min(Added_j(p), Removed_{j,k}(p))
CancelRatio_{j,k}(p) = Cancelled_{j,k}(p) / Added_j(p)
MatchedRatio_{j,k}(p) = (cumulative_volume_k-cumulative_volume_j) / Cancelled_{j,k}(p)
Lifetime_{j,k} = k-j
ContextValid = same_nonempty(symbol_i) ∧ same_nonempty(session_id_i) ∧ (0<ts_ms_i-ts_ms_{i-1}≤gap_max) ∧ nondecreasing(cumulative_volume_i)
Qualified_{j,k}(p) = (Cancelled_{j,k}(p)>0) ∧ (Added_j(p)>G_j) ∧ (CancelRatio_{j,k}(p)≥c_min) ∧ (MatchedRatio_{j,k}(p)≤m_max) ∧ (1≤Lifetime_{j,k}≤L_max)
Fired_{j,k}(p) = ContextValid ∧ (k=t_latest) ∧ Qualified_{j,k}(p) ∧ ¬Qualified_{j,k-1}(p)
LargeStrength = 1 if G_j=0 else min(1, Added_j(p)/(2×G_j))
Strength = min(1, (LargeStrength + CancelRatio + (1-MatchedRatio))/3)
Evidence = {tick_ref_{j-1}, tick_ref_j, tick_ref_k}
InputFidelity = snapshot_diff
Approximation = same_price_level_snapshot_appearance_disappearance_minus_total_interval_trades
ErrorSources = {no_order_id, repricing_vs_cancel_ambiguity, level5_queue_exit, trades_at_other_prices, within_3s_invisible_lifecycle}
InvalidWhen = {symbol_changed, session_changed, missing_timestamp, nonmonotonic_timestamp, snapshot_gap_exceeded, cumulative_volume_reset, fewer_than_N_baseline_snapshots, missing_book_side, negative_depth, nonfinite_price_or_volume}
FidelityLock = train_only_until_tick_by_tick_order_and_trade_revalidation
```

| parameter | default | range | unit |
|---|---:|---:|---|
| side | bid | {bid,ask} | enum |
| large_order_multiplier (k) | 3.0 | [1.0,100.0] | x_median |
| lookback_snapshots (N) | 5 | [1,60] | snapshots |
| min_order_volume (q_min) | 10000 | [0,1000000000] | shares |
| max_lifetime_snapshots (L_max) | 2 | [1,20] | snapshots |
| cancel_fraction (c_min) | 0.8 | [0.5,1.0] | fraction |
| max_trade_match_fraction (m_max) | 0.2 | [0.0,0.5] | fraction |
| max_gap_ms (gap_max) | 6000 | [1,60000] | milliseconds |
| price_tick | 0.01 | [0.0001,1.0] | CNY |
