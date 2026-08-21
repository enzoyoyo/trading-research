# Hypothesis Lifecycle Registry

## Purpose

Research work regularly produces claims that are not yet actionable: a factor
that clears a naive IC threshold but has not been checked against a
cross-sectional random control (`factor-validation-strict-gate.md`), a
`train_only`/`noise`/`reversed_strict` factor state, or any other market
thesis capped at `readiness_level: research_hypothesis` (`data-contracts.md`
`ResearchReadiness`). Without a ledger these get reported once, capped to L0,
and then vanish — the next session has no memory that the hypothesis exists,
what killed it, or whether new evidence should reopen it.

`scripts/hypothesis_registry.py` is that ledger: create it once, keep
attaching evidence and status changes, and periodically surface anything
untouched for too long so it gets a deliberate retire/promote decision
instead of silent abandonment.

This is bookkeeping, not a decision input. The registry never talks to the
Decision Compiler directly and never changes `max_action_level` or
`position_multiplier` on its own.

## Runtime storage

The skill directory only contains the script. Runtime data goes to:

- Registry file: `~/.cache/hermes/trading-research/memory/hypotheses.json`

Environment override: `TRADING_RESEARCH_HYPOTHESES_PATH`.

Writes are atomic (`tempfile` + `os.replace`), so a crash mid-write cannot
leave a truncated or corrupt registry.

## CLI

```bash
python3 scripts/hypothesis_registry.py create \
  --statement "因子X在A股制造业子板块 IC>0.02，但缺同宇宙随机对照" \
  --status open --tag factor_x --source-module quant_robustness \
  --note "registered pending random-control test"

python3 scripts/hypothesis_registry.py update --id hyp_... \
  --status train_only --note "random control failed; train-only overfit signature"

python3 scripts/hypothesis_registry.py link-evidence --id hyp_... \
  --evidence-id decision:D123 --evidence-id report:factor_x_run1

python3 scripts/hypothesis_registry.py list --status train_only
python3 scripts/hypothesis_registry.py search --query "随机对照"
python3 scripts/hypothesis_registry.py stale --days 30
python3 scripts/hypothesis_registry.py --self-test
```

## Status enum

`open | confirmed_alive | train_only | reversed_strict | noise | retired`

The four non-`open`/`retired` states mirror the factor four-state taxonomy in
`factor-validation-strict-gate.md` §4, but the registry is not limited to
factors — any research hypothesis (macro thesis, event-reaction residual,
narrative call) can use the same states once it has been checked against a
null/random baseline or an OOS split.

- `open` — registered, not yet classified.
- `confirmed_alive` — cleared the random-control null and OOS split; still
  must be re-submitted as a fresh `module_signal` to the Decision Compiler to
  affect an action level, the registry entry itself grants nothing.
- `train_only` / `reversed_strict` / `noise` — failed validation; capped at
  hypothesis/watch, never a `module_signal` input, never raises position
  multiplier. Kept in the registry (not deleted) so a later re-test with new
  data has a record of what was already tried and why it failed.
- `retired` — no longer worth tracking; excluded from `stale`.

## Hypothesis record

```json
{
  "hypothesis_id": "hyp_02203782fd50",
  "statement": "因子X在A股制造业子板块 IC>0.02，但缺同宇宙随机对照",
  "status": "train_only",
  "source_module": "quant_robustness",
  "tags": ["factor_x", "quant_robustness"],
  "evidence_ids": ["decision:D123", "report:factor_x_run1"],
  "notes": [{"at": "2026-07-06T06:00:00Z", "text": "registered pending random-control test"}],
  "created_at_utc": "2026-07-06T06:00:00Z",
  "updated_at_utc": "2026-07-06T06:05:00Z"
}
```

`hypothesis_id` is derived from `sha256(statement|created_at)[:12]`; it is
stable once created and is the key `update`/`link-evidence` operate on.

## Staleness discipline

`stale --days N` (default 30) lists every non-`retired` hypothesis whose
`updated_at_utc` is older than N days. Run this during a periodic review
(weekly self-optimization pass or before a new research session on the same
theme) and force each hit into one of: `link-evidence` with new data,
`update --status` to reclassify, or `update --status retired` with a closing
note. A hypothesis is never silently dropped by simply not looking at it.

## Boundaries

- No order execution; the registry stores text and ids, not trades.
- No automatic promotion: only a human/agent explicit `update --status
  confirmed_alive` moves a hypothesis out of hypothesis/watch, and even then
  it must re-enter the Decision Compiler as its own `module_signal` — the
  registry cannot raise `max_action_level` or `position_multiplier` by itself.
- Tighten-only: this ledger only adds tracking discipline; it never widens
  any cap defined in `decision-compiler.md`'s Cap & Tighten-Only Registry.
