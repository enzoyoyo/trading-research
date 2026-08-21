# Polymarket Prediction-Market Signal Layer

## Purpose

Use Polymarket-style prediction-market data as a **read-only auxiliary probability signal** for event-driven research: elections, regulatory approvals, lawsuits, merger outcomes, macro releases, geopolitics, policy path, and other binary/conditional events that affect tradable assets.

This layer answers one narrow question:

> What probability and microstructure quality is the market assigning to the event right now?

It does **not** answer whether the event is fundamentally true, whether the asset is cheap, or whether to trade.

## Non-negotiable boundaries

- Read-only only. No wallet, no custody, no order placement, no market making, no copy trading.
- No private keys, cookies, account data, or paid-source text in skill files.
- Does not replace LongBridge, filings, company/issuer materials, exchange/regulator materials, Decision Compiler, Mira Quality Gates, or `no_order_execution`.
- Prediction-market prices are `market_pricing`, not verified facts.
- A prediction-market signal may raise watch priority or scenario prior, but it must never by itself increase action level above L1 or increase position cap.
- Thin markets, wide spreads, ambiguous resolution rules, stale markets, or wash-like activity must be haircutted or excluded.

## Source priority

| Source | Use | Reliability ceiling |
|---|---|---:|
| Polymarket official Gamma / CLOB / Data API public endpoints | Market/event metadata, prices, orderbook, trades, open interest | 0.75 |
| Polymarket official WebSocket / subgraph | Real-time activity and on-chain corroboration | 0.70 |
| Curated public datasets / open-source indexers | Historical calibration and backtest research | 0.65 |
| Third-party dashboards / hosted terminals | Discovery and convenience views | 0.45 |
| X/social summaries of Polymarket odds | Discovery only until original market URL is recovered | 0.25 |

Reliability ceilings are maximums. Apply additional haircut for low liquidity, wide spread, stale timestamps, unclear resolution criteria, or missing cross-check.

## Minimum evidence checklist

A prediction-market signal is usable only when all are present:

1. `market_url` or original market/event URL.
2. `market_question` and `event_slug` / `condition_id` if available.
3. `observed_at` timestamp.
4. `implied_probability` or bid/ask-derived probability with formula.
5. Liquidity quality: spread plus at least one of depth, volume, or open interest.
6. Resolution rule check: clear / ambiguous / disputed / unavailable.
7. Cross-check EID: at least one non-Polymarket source for the underlying event fact pattern when the signal affects a decision.

Missing any of the above → `readiness_impact=monitoring_only` and `max_action_level=L0`.

## Derived indicators

| Indicator | Meaning | Decision use |
|---|---|---|
| `implied_probability` | Market-implied YES probability | Scenario prior, not fact proof |
| `probability_momentum_1h/24h/7d` | Speed/direction of repricing | Detect consensus shift |
| `spread` | Bid/ask uncertainty | Wider spread lowers reliability |
| `depth_bid/depth_ask` | Tradable support around current probability | Thin depth lowers reliability |
| `depth_imbalance` | One-sided orderbook pressure | Microstructure clue only |
| `volume_24h` / `open_interest` | Attention and capital participation | Higher liquidity can raise confidence ceiling |
| `trade_flow_velocity` | Acceleration in matched trades | News-reaction clue |
| `resolution_risk` | Rule/settlement ambiguity | High risk caps at L0/watch |
| `cross_venue_gap` | Polymarket vs Kalshi/other venue gap | Flags consensus conflict; not an automatic trade |

## Decision Compiler mapping

Use this module signal shape:

```json
{
  "module": "prediction_market_prior",
  "max_action_level": "L1",
  "position_multiplier": 0.0,
  "hard_veto": false,
  "reason": "read_only_market_pricing_prior; cannot raise position cap"
}
```

Rules:

- If original market URL / timestamp is missing: `max_action_level=L0`, `position_multiplier=0.0`.
- If spread is wide, depth is thin, or resolution is ambiguous: `max_action_level=L0`, `position_multiplier=0.0`.
- If the market is liquid, current, and cross-checked: it may become `L1 watchlist/scenario_prior`, still with `position_multiplier=0.0`.
- Only after independent LongBridge / filing / official / exchange / regulator / company evidence supports the same thesis can other modules decide action level.
- Prediction-market signals never override `risk_regime`, `gamma`, `portfolio_risk_budget`, `account`, `Mira readiness`, or `no_order_execution`.

## Provenance and license constraints

Adopted as mechanisms, not code:

- https://github.com/Jon-Becker/prediction-market-analysis — schema, dataset/indexer, calibration discipline.
- https://github.com/SII-WANGZJ/Polymarket_data — raw/processed/quant/user parquet layering and unified YES perspective.
- https://github.com/Polymarket/real-time-data-client — official streaming event categories.
- https://github.com/Polymarket/py-sdk — read-only public market-data client concepts only.
- https://github.com/Polymarket/polymarket-subgraph — on-chain corroboration concepts.
- https://github.com/PaulieB14/polymarket-subgraph-analytics — microstructure query ideas.
- https://github.com/pmxt-dev/pmxt — cross-venue normalization concept.
- https://github.com/NYTEMODEONLY/polyterm — read-only terminal intelligence contract ideas.

Explicitly rejected:

- Trading/order/wallet/custody paths.
- Copy trading or market making.
- Hosted execution or private account operations.
- Default downloads of 100GB+ datasets.
- Direct GPL code migration from projects such as `warproxxx/poly_data`.
- Treating Polymarket probability as a replacement for fundamentals, LongBridge, filings, or Decision Compiler.

## Lightweight script path

For current-session use, prefer the no-dependency public API helper:

```bash
python3 scripts/polymarket_signal.py --query "Fed rate cut" --limit 3
```

Optional analyst-confirmed fields after manual evidence review:

```bash
python3 scripts/polymarket_signal.py \
  --query "Fed rate cut" \
  --resolution-rule-status clear \
  --resolution-risk low \
  --cross-check-eid E12
```

Script rules:
- Uses only public Gamma / CLOB / Data API endpoints.
- Does not read wallet keys, cookies, private APIs, or account data.
- Does not place/cancel orders or call trading endpoints.
- Defaults missing resolution-rule and cross-check evidence to L0, so it cannot silently promote a signal.

## Report wording

Use terse language:

```markdown
Prediction-market prior: watch-only. Polymarket implies 63% for <event>, but spread/depth/resolution risk caps it to L1 watchlist. It does not raise position cap. Need official/company/regulator confirmation before action.
```
