# v1

```text
L = ticks(limit_up_{t-1})
Seal_{t-1} = Σ_{p: ticks(p)=L} Q_{t-1}^bid(p)
Seal_t = Σ_{p: ticks(p)=L} Q_t^bid(p)
InferredLocked_{t-1} = (ticks(last_{t-1})=L) ∧ (Seal_{t-1}>0) ∧ (ask_levels_{t-1}=∅)
LockState_{t-1} = is_limit_locked_{t-1} if field_present else InferredLocked_{t-1}
WasLocked_{t-1} = LockState_{t-1} ∧ (Seal_{t-1}>0) ∧ (Seal_{t-1}≥q_min)
DropFraction_t = max(0, Seal_{t-1}-Seal_t) / Seal_{t-1}
Opened_t = ticks(last_t) < L
ContextValid = same_nonempty(symbol_i) ∧ same_nonempty(session_id_i) ∧ (0<ts_ms_t-ts_ms_{t-1}≤gap_max)
Fired_t = ContextValid ∧ WasLocked_{t-1} ∧ (Opened_t ∨ DropFraction_t≥d_min)
Strength_t = max(DropFraction_t, 1 if Opened_t else 0)
Evidence = {tick_ref_{t-1}, tick_ref_t}
InputFidelity = snapshot_diff
Approximation = adjacent_snapshot(limit_price_bid_queue_drop_or_last_price_open)
ErrorSources = {within_3s_open_reseal_aliasing, queue_replenishment, queue_reordering, missing_deeper_levels}
InvalidWhen = {symbol_changed, session_changed, missing_timestamp, nonmonotonic_timestamp, snapshot_gap_exceeded, limit_up_price_change, invalid_limit_price, insufficient_snapshots, missing_book_side, negative_depth, nonfinite_price_or_volume}
FidelityLock = train_only_until_higher_frequency_limit_queue_revalidation
```

| parameter | default | range | unit |
|---|---:|---:|---|
| seal_drop_fraction (d_min) | 0.5 | [0.1,1.0] | fraction |
| min_seal_volume (q_min) | 10000 | [0,1000000000] | shares |
| max_gap_ms (gap_max) | 6000 | [1,60000] | milliseconds |
| price_tick | 0.01 | [0.0001,1.0] | CNY |
