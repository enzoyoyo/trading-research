# v1

```text
SetupLag = 4
SetupTarget = 9
CountdownLag = 2
CountdownTarget = 13
BuySetup_i = close_i < close_{i-4}
SellSetup_i = close_i > close_{i-4}
SetupCount_i = consecutive_count(DirectionSetup_i)
BuyCountdown_i = close_i ≤ low_{i-2}
SellCountdown_i = close_i ≥ high_{i-2}
SetupEnds = {i | closed_i ∧ SetupCount_i=9 ∧ no_prior_completion_in_same_unbroken_run}
ActiveSetup_i = max({j ∈ SetupEnds | j≤i})
CountdownCount_i = nonconsecutive_count(DirectionCountdown_h for h ∈ [ActiveSetup_i+1,i])
ConfirmedSetup_i = (SetupCount_i=9) ∧ closed_i
GhostSetup_i = (SetupCount_i=9) ∧ ¬closed_i
ConfirmedCountdown_i = (CountdownCount_i=13) ∧ closed_i
GhostCountdown_i = (CountdownCount_i=13) ∧ ¬closed_i
Fired_i = ConfirmedSetup_i if phase=setup else ConfirmedCountdown_i
Strength_i = SetupCount_i/9 if phase=setup else CountdownCount_i/13
RawGhost_i = {fired=false, strength=1, evidence=qualifying_bar_refs, input_fidelity=tick}
RawConfirmed_i = {fired=true, strength=1, evidence=qualifying_bar_refs, input_fidelity=tick}
Evidence_setup = {bar_ref of 9 qualifying bars}
Evidence_countdown = {bar_ref of 13 qualifying bars}
Repeat_i = false if target_index < i
InputFidelity = tick
```

| parameter | default | range | unit |
|---|---:|---:|---|
| direction | buy | {buy,sell} | enum |
| phase | setup | {setup,countdown} | enum |
