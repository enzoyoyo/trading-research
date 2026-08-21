# Data Retention Policy

Use this reference when the user asks about System A disk usage, data cleanup, archival, or why a directory under `~/.hermes/longbridge-paper-trading` keeps growing.

## Why

System A (paper-trading execution) accumulates a timestamped artifact on nearly every scan cycle. Over a month this is roughly 150MB across ~7000 files, and almost all of it is either a re-derivable snapshot or a machine intermediate that's already been consumed by a downstream learning step. `scripts/data_retention.py` lightens this without touching anything of lasting value.

## Retention table

| Dir | Rule | Reason |
|---|---|---|
| `journal/*.jsonl`, `config/`, `scripts/`, `docs/`, `dashboard/`, `signals/`, root `*.md`, `.git` | Never touched (not in the allowlist at all) | The real ledger and the code |
| `reports/*.md` (`daily_decision_*`, `paper_trade_review_*`, narrative recaps) | Kept forever | maintainer-designated high-value review conclusions |
| `reports/*.json` | 30 days, then archived | Machine intermediate |
| `decision_packets/` | 30 days, then archived | Pre-market snapshots; already summarized in daily journal/learning packets |
| `state/` | Only timestamped `*_20*.json*` files, 14 days, then archived. Non-timestamped files (`universe_candidates.jsonl`, `risk_regime.json`) are never touched | `state/` is the single largest directory and almost pure snapshot; the untimestamped files are live pointers, not history |
| `proposals/` | 90 days, then archived | The calibration bucket's pending-prediction bookkeeping needs to re-read prediction fields for a while after they're made |
| `learning_packets/` | 60 days, then archived | System B has already consumed these, but the materiality lookback window needs them to stick around a while |
| `runs/`, `run_logs/` | 14 days, then archived | Pure execution logs |
| Skill-side `__pycache__` (anywhere under this skill repo) | Deleted immediately, no time window | Build noise, never has archival value |
| Skill-side `~/.hermes/work/trading-research-autoevolve/eval-candidates/` | 60 days, then archived | Evaluation staging area outside System A entirely |

Every file-level rule additionally requires the filename to carry a `_20YY`-shaped timestamp segment before it is considered archival-eligible at all — this is the same defensive gate the plan specifies for `state/`, applied uniformly so a non-timestamped "current pointer" file (e.g. `run_logs/current_run.env`) can never be accidentally swept up regardless of its age.

## Mechanism

- **Allowlist, not denylist-of-everything**: the tool only ever knows about eight named subdirectories (the seven System A rows above, plus `archive/` as its own write destination) and the two skill-side paths. Any path outside those roots raises `PermissionError` instead of being touched — this is enforced in code (`guard_path`), not just documented.
- **Archive format**: `archive/<rule>/<YYYY-MM>.tar.gz`, grouped by each file's own mtime month. If a tar for that dir+month already exists from an earlier run, a new file is created with a short random suffix (`<YYYY-MM>_<id>.tar.gz`) rather than attempting to append to an existing gzip stream.
- **Verify before delete**: after writing a tar to a temp file, it is reopened and its member count is checked against the expected file count before the tar is moved into place (atomic `os.replace`) and before any original file is unlinked. A verification failure aborts that batch and leaves the originals untouched.
- **Manifest**: `archive/manifest.jsonl` gets one line per archived batch — `timestamp_utc`, `dir`, `month`, `file_count`, `byte_count`, `tar_path`, `sha256` (of the tar file itself). This is the audit trail; always check it after any `--apply` run.
- **Default is `--dry-run`**: reports pending file/byte counts and the tar paths that *would* be created, per rule, plus a top-level `totals` block. Nothing is written or deleted. `--apply` is required to actually act, and even then only ever removes files that passed the timestamp gate and age window for their own rule.
- **Overrides**: `--keep-days-<rule>` (e.g. `--keep-days-state 21`) overrides a single rule's window for one run without editing the table above.
- **`--json`**: machine-readable output for the weekly digest to consume.

## Operating discipline

1. Always run `--dry-run --json` first and read the `totals` block before ever running `--apply`.
2. Check `archive/manifest.jsonl` after every `--apply` run — `sha256` and `tar_path` must both be present and the tar must be openable.
3. `--apply` is a main-session / maintainer decision, not something automation runs unattended today. The weekly self-optimization loop only ever calls `--dry-run --json` (see `references/adaptive-self-optimization.md`); if the reported pending set is unusually large (see threshold below), that becomes a prompt for the maintainer or the main session to review and decide whether to run `--apply`.
4. Threshold for flagging in the weekly digest: pending set `> 5000` files or `> 200MB`. Below that, routine growth; above it, worth a look before it compounds.

## Self-test

`python3 scripts/data_retention.py --self-test` builds a fully synthetic mixed old/new file tree in a tempdir (never touches real data), and asserts: the denylisted-by-construction dirs are unreachable (a `guard_path` call against a decoy `journal/` path raises), each rule's time-window boundary is correct, non-timestamped files always survive, tar archives are readable and match their manifest entry, and a second `--apply` run against the same tree is a no-op (idempotent) since nothing is left eligible.
