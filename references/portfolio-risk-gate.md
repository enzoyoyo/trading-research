# Portfolio Risk Gate

## Purpose

Prevent single-stock alpha analysis from overriding account-level risk/survival. Before recommending a new buy/add in an existing brokerage account, or discussing individual tickers while already holding related positions, first decide whether the account can carry more risk — then analyze the new ticker.

## Trigger

Use when any of these appear:
- 用户 asks to analyze LongBridge/长桥/券商 holdings, positions, or portfolio.
- 用户 asks whether to buy/add/catch a falling knife while already holding related positions.
- Portfolio has negative cash, margin, financing/leverage, risk level, margin-call fields, or high same-theme concentration.
- Candidate ticker or existing holdings are high-beta themes: AI, semiconductor, aerospace/space, small-cap growth, optical module, options-driven names.
- 用户 asks about tomorrow's trading plan while also having brokerage exposure.

## Fixed workflow

1. Read account context first, before single-stock opinion:
   - `longbridge check`
   - `hermes mcp test longbridge` when available
   - `longbridge portfolio --format json`
   - `longbridge positions --format json`
2. If the current agent session's `mcp_longbridge_*` tools fail but `longbridge check` / MCP health check are OK:
   - Do not repeatedly retry the broken tool bridge.
   - Downgrade to LongBridge CLI immediately.
   - Label the report `longbridge_tier=cli` and state which MCP-only dimensions are missing.
   - Do not claim LongBridge itself is unavailable.
3. Compute account capacity before ticker views:
   - `net_asset = overview.total_asset`
   - `gross_exposure = overview.market_cap` (equivalently `sum(holding market_value_usd)` across positions)
   - `gross_net_ratio = gross_exposure / net_asset`
   - `cash_net_pct = overview.total_cash / net_asset`
   - single-position net-asset weight: `market_value_usd / net_asset`
   - same-theme gross and net exposure
4. If the user mentions a specific ticker as already bought or maybe bought, verify whether it is in the brokerage account instead of assuming:
   - `longbridge order --symbol SYMBOL --format json`
   - `longbridge order executions --symbol SYMBOL --format json`
   - If empty, say explicitly that the ticker was not found in today's orders/executions.
5. Run market regime and candidate-ticker evidence only after the portfolio gate.
6. Feed portfolio risk into the Decision Compiler as a module signal.

## Hard gates

| Condition | Default action |
|---|---|
| `gross_net_ratio >= 1.5` | Do not add high-beta exposure unless it is a hedge |
| `total_cash < 0` and `abs(cash_net_pct) >= 50%` | Reduce financing/negative cash before new buys |
| Single position `>= 50%` of net asset | Do not add same-direction risk; address concentration first |
| Same-theme high-beta exposure `>= 100%` of net asset | New same-theme ticker defaults to L0 |
| Market regime is `active_deleveraging` (defensive regime) and portfolio uses financing | High-beta new buys are hard veto |

## Decision Compiler module

```json
{
  "module": "brokerage_portfolio_margin",
  "max_action_level": "L0",
  "position_multiplier": 0.0,
  "hard_veto": true,
  "repair_signal": "Reduce gross/net toward <=1.3-1.5 and significantly reduce negative cash before adding high-beta exposure"
}
```

## Output discipline

- First answer: 账户能不能继续承担风险？ ("Can the account carry more risk?")
- Then answer: is the ticker attractive?
- Use tables for portfolio and action lines; do not write each holding as a long paragraph.
- Separate account-level action from ticker-level action.
- Give repair math when possible: exposure reduction needed to reach 150%/130% gross-net, and cash repair to -50% net asset.
- Do not treat a one-day bounce inside deleveraging as risk repair.
- If the ticker is not found in positions/orders, explicitly say so instead of assuming the user already owns it.
- Never place orders; output only risk bounds, action level, and repair signals.
