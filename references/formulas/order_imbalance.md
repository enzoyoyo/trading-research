# v1

```text
B_t = Σ_{p∈bid L1..L5} Q_t^bid(p)
A_t = Σ_{p∈ask L1..L5} Q_t^ask(p)
R_t^bid = B_t / A_t
R_t^ask = A_t / B_t
R_t = R_t^side
Valid_t = (B_t > 0) ∧ (A_t > 0) ∧ (B_t + A_t ≥ q_min)
Fired_t = Valid_t ∧ (R_t > r)
Strength_t = min(1, R_t / (2×r))
Evidence = {tick_ref_t} if Fired_t else ∅
InputFidelity = tick
```

| parameter | default | range | unit |
|---|---:|---:|---|
| side | bid | {bid,ask} | enum |
| ratio_threshold (r) | 3.0 | [1.0,100.0] | ratio |
| min_total_volume (q_min) | 10000 | [0,1000000000] | shares |
