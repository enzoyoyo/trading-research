# LongBridge Paper Trading Gate

## Purpose
Allow simulated / paper-trading experiments without ever touching the live brokerage account. This reference is mandatory when 用户 asks to connect LongBridge 模拟仓, paper account, 自动模拟交易, or daily portfolio experiments.

## Hard boundary
- Live account order execution is always forbidden.
- Paper order execution is allowed only after an explicit `paper_account_gate=pass`.
- If any check returns `account_channel != lb_papertrading`, stop. Do not submit, cancel, replace, DCA, or create alerts that could affect a real account.
- Never rely on `LONGBRIDGE_ENV=staging` as a substitute for a paper account token. Staging/canary endpoints do not turn a live token into a paper token.

## Paper account gate
Run all checks before any paper-order automation:

```bash
longbridge auth status --format json
longbridge assets --format json
longbridge order --format json
```

Pass conditions:
1. `auth status.account.account_channel == "lb_papertrading"`.
2. Pretty output for account/asset/position commands shows `Account: Demo A/C (simulated account)`.
3. No command is using the normal Hermes/LongBridge live profile unless the token itself is paper.
4. Today-order check is read-only and returns parseable JSON.

Fail conditions:
- `account_channel == "lb"` or missing → live/default account. Stop.
- OAuth/CLI/MCP auth cannot prove paper status → stop.
- Paper funds are unavailable/invalid → stop and ask 用户 to enable/reset in LongBridge developer center.
- If a token created under the paper-only HOME resolves to an Integrated A/C / live `H...` account, treat it as a contaminated candidate: delete that isolated token immediately and restart auth. Never keep a live token inside the paper profile.

## Device-flow pitfall
The LongBridge device authorization page may default to the live Integrated A/C even when a Demo A/C exists. Selecting Demo after the device code is already approved is too late: the CLI may still receive a live `lb` token.

Correct sequence:
1. Open the new device URL.
2. Click **Switch Account** before approving.
3. Select **Demo A/C**.
4. Only then approve/allow the device code.
5. Immediately run the paper gate; if it returns `lb`, delete the isolated token and retry.

## Setup path
Official docs state: paper and live accounts share App Key & Secret but use different Access Tokens; trading permissions are tied to the Access Token.

Default setup:
1. 用户 opens `https://open.longbridge.com/dashboard/`.
2. Enable paper account / 模拟账户 in Developer Center.
3. Generate a paper account Agent Auth Code or paper Access Token.
4. Store paper credentials in an isolated location, never overwriting the live CLI token:
   - OAuth probe: `HOME=/tmp/longbridge-paper-probe-home longbridge auth login --auth-code <CODE>`.
   - Stable automation profile: create a dedicated paper-only env/config path, then run the gate above.
5. Only after `account_channel == lb_papertrading`, create cron / scripts.

## Local paper-trading implementation
- Paper HOME: `${HOME}/.hermes/longbridge-paper-home`.
- Workspace: `${HOME}/.hermes/longbridge-paper-trading/`.
- Gate: `${HOME}/.hermes/scripts/longbridge-paper-gate.sh`.
- Learning cycle: configure the independent runner for the intended HK/US sessions and verify official trading days before any proposal or executor step. Outside eligible sessions, exit quietly; this package contains no installed schedule or job identity.
- Daily decision guard: the external runner must inject official HK/US trading-day and eligible-session status before research or proposal generation; `allowed=false` stops the run. Schedules and job identifiers are configured outside this package.
- Dry-run strategy: `~/.hermes/longbridge-paper-trading/scripts/daily_paper_strategy.py`.
- Signal generator: `~/.hermes/longbridge-paper-trading/scripts/paper_signal_generator.py`; creates proposal JSON only; never submits orders.
- Order executor: `~/.hermes/longbridge-paper-trading/scripts/paper_trade_executor.py`; default dry-run; writes `journal/paper_orders.jsonl` before any possible submit.
- Learning report: `~/.hermes/longbridge-paper-trading/scripts/paper_learning_report.py`; summarizes validation quality, paper submissions, closed-trade outcomes, win rate, average R multiple, and signal-level results.
- Position snapshot: `~/.hermes/longbridge-paper-trading/scripts/paper_position_snapshot.py`; records open Demo position value and unrealized P/L for the learning loop.
- Capability inventory: `~/.hermes/longbridge-paper-trading/scripts/paper_capability_inventory.py`; records available skills/MCP/tools before decisions.
- Decision packet: `~/.hermes/longbridge-paper-trading/scripts/paper_decision_packet.py`; compiles gate, positions, assets, market status, quotes, source roster, and recent reports before decisions.
- Learning review packet: `~/.hermes/longbridge-paper-trading/scripts/paper_learning_review.py`; turns outcomes/snapshots into `learning_packets/*.json` for `trading-research` materiality/conflict-gated upgrades.
- Demo config must be checked at runtime (`config/paper_policy.json`); actual simulated submit still requires gate pass + isolated token + explicit `--execute` + limit-order validation. Live account execution remains forbidden.

## Two-system boundary
- System A (`~/.hermes/longbridge-paper-trading`) may execute Demo paper orders after this gate passes.
- System B (`~/.hermes/skills/trading-research`) never executes orders; it receives System A learning packets and upgrades analysis logic only after materiality/conflict gates pass.
- See `references/two-system-trading-architecture.md` for the full architecture.

## Daily paper portfolio experiment rules
- Objective is not raw win rate alone. Optimize a scorecard: win rate + average win/loss + max drawdown + exposure discipline + turnover cost.
- Universe default: liquid US/HK ETFs and megacap equities; avoid illiquid names, OTC, and unsupported paper sessions.
- Frequency default: once per market day after open liquidity stabilizes; no intraday overtrading loop.
- Sizing default: risk-budget based, max 5 positions, max 25% per position, cash reserve >= 20%, no compounding until 20+ paper trades are logged.
- Every order must write an audit row before submission: timestamp, thesis, signal IDs, quote bid/ask, intended limit, max loss, invalidation, expected holding period.
- Every close/update must write outcome labels: fill, slippage proxy, return, R-multiple, whether thesis or timing failed.

## Output
Before enabling cron, report:

```json
{
  "paper_account_gate": "pass|fail",
  "account_channel": "lb_papertrading|lb|unknown",
  "allowed_actions": ["research", "paper_order"]
}
```

If gate fails, allowed actions are `research` only.
