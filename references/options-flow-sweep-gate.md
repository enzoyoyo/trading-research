# Options-Flow Sweep Gate

## When to use
Use this reference when a trading/paper-trading task includes third-party options-flow alerts such as Helion, CheddarFlow, Unusual Whales, SpotGamma-style screenshots, or fields like:

- `SWEEP DETECTED`
- `At the Offer` / `At the Ask`
- call/put sweep
- delta / IV
- premium
- delta-equivalent shares / shares to buy
- historical flow win rate
- gamma squeeze / squeeze probability

## Core interpretation
A single options-flow alert is not a trade thesis. Treat it as a structured evidence component inside the Decision Compiler or paper-trading `decision_fusion`, not as a standalone buy/sell signal.

### Field mapping
| Field | Meaning | Decision use |
|---|---|---|
| Sweep | Large order split/routed across venues for fast execution | Urgency/size signal; stronger than ordinary small prints |
| At the Offer / Ask | Buyer paid the ask | For calls, bullish underlying clue; for puts, bearish clue |
| Delta | Option sensitivity to underlying | Used to estimate delta-equivalent share exposure |
| Share equivalent | `contracts × 100 × delta` | Proxy for dealer initial hedge pressure |
| Premium | Total option dollars paid | Capital-at-risk / conviction proxy |
| DTE | Days to expiration | Shorter DTE near ATM has higher gamma relevance |
| IV | Implied volatility | Very high IV reduces attractiveness; can indicate expensive premium |
| Historical win rate | Vendor/platform stat for similar flows | Useful only if methodology/source is known; should be calibrated against own paper outcomes |

## Scoring rules
- `call + at_offer + bullish` is constructive for long-underlying setups.
- `put + at_offer + bearish` is negative for long-underlying setups and can veto if premium/share-equivalent dominate.
- Large premium and large delta-equivalent shares add evidence, especially when short DTE and delta is roughly 0.35–0.65.
- Missing historical win rate should cap confidence; a screenshot alone must not create a high-confidence trade.
- Opposite-direction flow clusters may veto a candidate even if technicals are good.
- Options-flow may filter, annotate, or downgrade; it must not directly submit orders, bypass account gates, or increase position sizing without empirical calibration.

## Implementation pattern
For System A paper trading, the durable pattern is:

```text
third-party alert/screenshot
→ normalize into JSONL (`signals/options_flow.jsonl`)
→ `scripts/quant/options_flow.py`
→ `scripts/quant/decision_fusion.py`
→ proposal `metadata.decision_fusion.components.options_flow`
→ paper outcome review / calibration
```

Example JSONL row:

```json
{
  "symbol": "GOOG.US",
  "option_type": "call",
  "expiry": "2026-07-02",
  "strike": 347.5,
  "price_location": "at_offer",
  "direction": "bullish",
  "delta": "54%",
  "iv": "38%",
  "premium": "$2.5M",
  "share_equivalent": "250,794",
  "notional": "$87.3M",
  "historical_win_rate": "68%",
  "source": "Helion Options",
  "observed_at": "2026-06-29T10:38:17-04:00"
}
```

## Evidence discipline
Before treating a flow as more than a clue, try to cross-check:

1. Is it a single-leg directional trade or part of a spread/roll/hedge?
2. Is volume greater than open interest, suggesting new opening activity?
3. Is the flow repeated across strikes/expiries or isolated?
4. Does price action confirm after the print?
5. Does the underlying have enough liquidity/float constraints for dealer hedging to matter?
6. Is there a catalyst/news/macro context aligned with the flow?

## Pitfalls
- Do not assume all unusual options activity is informed; a large portion can be hedging, spreads, rolls, or dealer inventory management.
- Do not equate `premium` with profit probability; expensive IV can reduce realized payoff.
- Do not use vendor historical win rate blindly; record it and calibrate it against the local paper-trading journal.
- Do not let a screenshot raise action level by itself. It can raise watch priority or support an already-valid candidate only after other gates pass.
