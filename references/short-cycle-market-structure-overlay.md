# Short-Cycle Market Structure Overlay

## Purpose

This US-only layer answers **whether an already-selected direction is executable today**. It does not select direction, replace the multi-factor framework, or create a second action ladder.

Responsibility split:

| Layer | Question |
|---|---|
| Original `trading-research` multi-factor stack | Should this direction be selected? |
| Short-Cycle Market Structure Overlay | Can it be executed now, must the close-to-open trade end, and is the option instrument tradable? |
| Decision Compiler | What is the final L0–L5 action after all caps/vetoes? |

The overlay consumes an immutable upstream decision and emits only existing compiler modules:

- `execution_window`
- `gamma`
- `data_quality`

It may tighten an upstream decision; it cannot increase `max_action_level` or position size.

## Entry points

Run the deterministic evaluator with a JSON payload on stdin:

```bash
python3 scripts/short_cycle_structure.py < payload.json
```

Or pass a file:

```bash
python3 scripts/short_cycle_structure.py payload.json
```

Canonical fixture regression:

```bash
python3 /tmp/hermes-verify-short-cycle.py
```

`validate_skill.py` runs the same committed fixtures directly, so the temporary harness is not required for normal validation.

## Required input boundary

```json
{
  "schema_version": "short_cycle_structure.v1",
  "market_scope": "US_only",
  "checkpoint": "close | pre_open | open_0940 | eod_review",
  "observed_at": "RFC3339",
  "upstream": {
    "decision_id": "immutable id",
    "direction": "long | short",
    "action_cap": "L0 | L1 | L2 | L3",
    "position_multiplier": 0.5,
    "thesis_valid": true
  }
}
```

- `market_scope` other than `US_only` returns `inapplicable`/L0 for this overlay.
- `upstream.action_cap=L0` or `thesis_valid=false` cannot be revived.
- The script never sends orders and never writes broker/account state.

## Checkpoint 1 — close and pre-open

Continue using `references/us-close-to-open-execution-overlay.md` for the 15:50–16:00 ET entry window, 07:00–09:25 ET read, and the exit-at-open rule.

This overlay consumes that `pre_open.status`:

- `strong` / `neutral`: proceed to 09:40 continuity check if other gates permit.
- `weak`: `exit_at_open`, `execution_window.max_action_level=L0`.
- `unavailable`: no favorable inference; incomplete later checkpoints may cap at L1 or L0.

A public Balder post on 2026-07-08 explicitly identified 09:30–09:40 opening continuity as an unfinished bridge between the pre-open card and the 09:40 sell rule. This is a design clue, not validation evidence. The actual gate below is explicit and regression-tested.

## Checkpoint 2 — 09:30–09:40 continuity

At exactly 09:40 ET, evaluate direction-aware soft components:

1. `gap_hold`: price still on the selected side of prior close;
2. `vwap_acceptance`: price accepted on the selected side of VWAP;
3. `opening_range_location`: price is in the directionally favorable half of the 09:30–09:40 range;
4. `relative_volume`: at or above the explicit policy threshold;
5. `breadth_sector`: both breadth and sector confirm.

Default policy:

```json
{
  "min_relative_volume": 0.8,
  "continuity_pass_score": 0.70,
  "continuity_fail_score": 0.40
}
```

These defaults are testable local hypotheses, not universal alpha claims.

Decision mapping:

| Result | Overlay verdict | Meaning |
|---|---|---|
| score ≥ 0.70 | `recompile_intraday` | Close-to-open thesis ends; run a new intraday/swing Decision Compiler pass |
| 0.40 ≤ score < 0.70 | `data_gap` | No extension; at most L1 pending repair |
| score < 0.40 | `exit_or_reduce_by_0940` | End the close-to-open trade |
| ≥2 missing components | `data_gap` | Missingness cannot become bullish evidence |

A pass never means “auto-hold.” `must_recompile_after_0940=true` is mandatory.

## Checkpoint 3 — SPX gamma range

Required fields:

- `spot`
- `put_wall`
- `gamma_flip`
- `call_wall`
- `net_gex`
- `vrp`
- `observed_at`
- `source_delay_minutes`
- `option_session`
- `expiry_scope`
- `comparable_to_prior`

Integrity checks:

1. Require `put_wall <= gamma_flip <= call_wall` when all walls are supplied.
2. Classify spot as `below_put_wall`, `put_to_flip`, `flip_to_call`, or `above_call_wall`.
3. Distinguish `0DTE/near` from `aggregate_45d`.
4. Add declared source delay to timestamp age.
5. Treat non-comparable gamma magnitude as context only.

Freshness classes:

| Class | Default rule | Authority |
|---|---|---|
| `confirmed_live` | RTH + 0DTE/near + effective age ≤180s | may tighten strongly |
| `delayed_reference` | effective age ≤1200s | may tighten; never relax |
| `stale_for_checkpoint` | older or unknown | data gap; cannot authorize extension |
| `invalid` | malformed/misordered walls | data gap |

For a long thesis, confirmed negative gamma below put wall plus negative VRP is a breakdown-amplification state and may hard-veto to L0. A stale version of the same observation caps at L1 and returns `data_gap`; it cannot masquerade as live confirmation.

Positive gamma, a call wall, or a pin regime cannot independently lift the upstream cap. Dealer inventory is not directly observed, gamma signs can differ by model, and Cboe research finds aggregate 0DTE hedging flow is often balanced; use this as a conditional structure gate, not a deterministic prophecy.

## Checkpoint 4 — option execution quality

The option branch is compiled separately from the underlying thesis.

Hard quality inputs:

- valid bid/ask (`bid > 0`, `ask >= bid`);
- quote age;
- bid/ask top size relative to requested contracts;
- spread as percentage of midpoint;
- expected edge in bps;
- estimated slippage and fees;
- order type.

Default policy:

```json
{
  "max_option_quote_age_seconds": 5,
  "max_option_spread_pct_mid": 0.10
}
```

Cost hurdle:

```text
market order: full spread bps + slippage bps + fees bps
limit order:  half spread bps + slippage bps + fees bps
```

Outputs:

| Permission | Meaning |
|---|---|
| `eligible` | fresh BBO, sufficient executable top size, acceptable spread, positive post-friction edge |
| `limit_only` | book is usable but marketable execution is not justified; accept non-fill |
| `no_trade` | stale/malformed quote, insufficient size, spread too wide, or unknown/negative cost edge |

Volume and open interest remain secondary context. They are not universal hard thresholds, especially for 0DTE. The primary evidence is BBO freshness, spread, size, fill and realized cost.

Critical separation:

- `underlying_module_signals`: original thesis + execution/gamma constraints only.
- `option_module_signals`: same constraints plus `data_quality:option_execution`.
- A bad option contract blocks the option instrument; it does not erase a valid equity thesis.

SEC DERA’s 2025 0DTE limit-order study supports this separation: near-money 0DTE fill probability rises materially close to expiration, while very aggressive fills can produce unfavorable price impact. Execution quality is not directional alpha.

## Checkpoint 5 — EOD review and bounded soft-weight suggestion

`checkpoint=eod_review` is review-only and must not be fed into current-day order execution.

With real observed prices/costs, the script computes:

- close→open net bps;
- open→09:40 bps;
- 09:40→close bps;
- actual-exit net bps;
- 09:40-exit counterfactual net bps;
- estimated-vs-realized execution cost error;
- attribution labels such as `late_exit_vs_0940` or `execution_cost_underestimated`.

Soft reweighting requirements:

- explicit closed outcomes only;
- default minimum 12 samples **per component**;
- Brier/error score by component;
- non-negative normalized weights;
- maximum absolute change 0.15 per review;
- `applied=false`: output is advisory until reviewed and persisted through the normal Decision Memory/self-optimization path.

Never reweight:

- original multi-factor direction-selection weights;
- safety/account gates;
- compiler action registry;
- quote freshness, malformed-BBO, or spread-validity hard rules.

Insufficient samples return `insufficient_samples` and no recommendation.

## Output contract

```json
{
  "upstream_unchanged": true,
  "checkpoint_verdict": "eligible | exit_at_open | exit_or_reduce_by_0940 | recompile_intraday | review_only | data_gap | inapplicable | no_trade",
  "continuity": {},
  "gamma_range": {},
  "option_execution_quality": {},
  "instrument_permissions": {
    "underlying": "upstream_only",
    "option_contract": "not_requested | eligible | limit_only | no_trade"
  },
  "underlying_module_signals": [],
  "option_module_signals": [],
  "must_recompile_after_0940": true,
  "eod_review": {},
  "no_order_execution": true
}
```

Feed either `underlying_module_signals` or `option_module_signals` into the existing Decision Compiler with the immutable upstream modules. Do not combine both branches into one compiler input.

## Evidence hierarchy

1. **Primary mechanics:** SEC/Cboe and observable BBO/fills.
2. **Local recorded outcomes:** Decision Memory and EOD closed cases.
3. **Public X/KOL posts:** method-discovery clues only.
4. **Subscriber-only or inaccessible posts:** `paywall_gap`; no inference.

Public research log for the 2026-07-10 upgrade is retained outside the runtime skill workspace. Canonical source URLs and adoption limits are recorded in `references/source-map.md`.

## Regression command

```bash
python3 scripts/validate_skill.py
python3 scripts/validate_scenarios.py
python3 scripts/self_optimization_check.py --json --run-validators --skip-network
```
