# v1

```text
side ∈ {bid, ask}
H_t = {Q_i^side(p) | i ∈ [t-N,t-1], p ∈ L1..L5, Q_i^side(p) > 0}
M_t = median(H_t) if H_t ≠ ∅ else 0
ΔQ_t(p) = Q_t^side(p) - Q_{t-1}^side(p)
G_median = k × M_t
G_qty = q_min
G_notional(p) = a_min / p
UpperLocked_t = (ticks(last_t)=ticks(limit_up_t)) ∧ (Q_t^bid(limit_up_t)>0)
LowerLocked_t = (ticks(last_t)=ticks(limit_down_t)) ∧ (Q_t^ask(limit_down_t)>0)
LimitLocked_t = is_limit_locked_t ∨ UpperLocked_t ∨ LowerLocked_t
LimitContextValid_t = field_present(is_limit_locked_t) ∨ (field_present(last_t) ∧ (field_present(limit_up_t) ∨ field_present(limit_down_t)))
Eligible_t(p) = (ΔQ_t(p) > G_median) ∧ (ΔQ_t(p) ≥ G_qty) ∧ (p×ΔQ_t(p) ≥ a_min)
ContextValid = same_nonempty(symbol_i) ∧ same_nonempty(session_id_i) ∧ strictly_increasing(ts_ms_i) ∧ LimitContextValid_t
Fired_t = ContextValid ∧ (∃p: Eligible_t(p)) ∧ (session_offset_t ≥ open_buffer) ∧ ¬LimitLocked_t
G_eff(p) = max(G_median, G_qty, G_notional(p))
Strength_t = 1 if G_eff(p*)=0 else min(1, ΔQ_t(p*) / (2×G_eff(p*)))
p* = argmax_p(Strength_t, ΔQ_t(p), ticks(p))
Evidence = {tick_ref_{t-1}, tick_ref_t}
InputFidelity = tick
```

| parameter | default | range | unit |
|---|---:|---:|---|
| side | bid | {bid,ask} | enum |
| net_add_threshold (k) | 2.0 | [1.0,10.0] | x_median |
| lookback_snapshots (N) | 5 | [2,60] | snapshots |
| min_abs_volume (q_min) | 10000 | [0,1000000000] | shares |
| min_notional (a_min) | 1000000 | [0,1000000000000] | CNY |
| opening_buffer_seconds (open_buffer) | 180 | [0,3600] | seconds |
| price_tick | 0.01 | [0.0001,1.0] | CNY |
