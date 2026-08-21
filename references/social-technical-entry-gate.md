# Social + Technical Entry Gate · 社媒观点 × 技术触发入场门

## 核心判断

当 X/社媒帖子同时包含“基本面叙事/估值锚/技术触发”时，不能把它当成一个整体交易信号。必须拆成三层：

1. **叙事/催化剂**：行业、供应链、政策、公司新闻，只能生成假设。
2. **估值/价格锚**：例如大资金认购价、私募配售价、历史支撑位，只能提供观察区间。
3. **技术触发**：例如 RSI14 < 30、回踩均线、成交量确认，才是短线执行条件。

结论：**估值锚 ≠ 买点；社媒观点 ≠ 仓位；技术触发未满足时只能 watchlist / no_trade。**

## 触发场景

必须启用本层，当用户提供或系统抓到以下类型帖子：

- “跌破某大资金成本/折扣价/配售价，可以低吸，但等 RSI/均线/成交量触发”。
- “某供应链新闻利好/利空 + 等技术指标确认再进”。
- KOL 同时给出“长期低点判断”和“短线入场条件”。
- 截图来源，不能直接点击原帖，需要截图 OCR + x_search / web_extract 复核。

## 标准流程

### 1. Source access

记录信息来源层级：

```yaml
source_access:
  screenshot: true|false
  x_original_url: "<url or null>"
  quoted_post_url: "<url or null>"
  source_status: "full|preview|paywall_gap|screenshot_only"
```

截图只能作为 `screenshot_observation`，不能直接当作原始证据。必须尽量用 `x_search` / `web_extract` 找原帖与引用帖。

### 2. Claim split

把帖子拆成可检验 claim：

```yaml
claim_map:
  price_claim:
    example: "GOOG broke below 350"
    verify_with: "market_data"
  valuation_anchor:
    example: "below BRK private-placement/discount price"
    verify_with: "original deal source / news / filing"
  technical_trigger:
    example: "wait for RSI14 < 30"
    verify_with: "kline calculation"
  fundamental_catalyst:
    example: "GOOGL talks with Samsung on TPU memory I/O die"
    verify_with: "original article / company source / industry source"
  author_opinion:
    example: "long-term decent low / high backtest win rate"
    verify_with: "treat as hypothesis unless backtest is supplied"
```

### 3. Verification gates

- **Price gate**：用行情源验证当前价、盘前/盘后状态、交易时段。
- **Technical gate**：本地计算指标，禁止只引用作者说法。
- **Anchor gate**：大资金成本/折扣价必须来自可复核来源；如果是 paywall，只能标注 `direction_verified_exact_limited`。
- **Catalyst gate**：供应链/公司新闻必须回抓原始来源。若原始来源 paywall，只能作为 `paywall_gap`，不得写成“已官方确认”。
- **Backtest gate**：作者声称“回测胜率很高”但未给样本、窗口、费用、walk-forward 时，最高只能 `hypothesis`，不能进入仓位加分。

### 4. Decision Compiler 映射

| 条件 | 动作影响 |
|---|---|
| 价格锚已触达，但技术触发未满足 | `watchlist` / L0-L1；不新增仓位 |
| 技术触发满足，但 catalyst 未验证 | 小仓位 paper-only；不提高真实仓位 |
| 技术触发 + catalyst + anchor 都验证，且无 regime/gamma hard veto | 可进入 L1/L2，由 Decision Compiler 统一裁决 |
| 作者 backtest 未披露 | 不作为 position_multiplier 加分 |
| 截图无法回抓原帖 | 只保留观察，不进入核心证据 |

## Case study · Balder GOOG / BRK / RSI14 screenshot, 2026-06-11

### Accessible text

- Screenshot post: Balder `@Balder13946731`, about 52m old when observed.
- Text: `$GOOG 已经跌破350，低于其卖给 $BRK 的折扣价。长期来说可能是一个不错的低点。但我会期望其 RSI14 低于30再进场，这样的回测胜率非常高。`
- Quoted post: Shay Boloor / `@StockSavvyShay`, 2026-06-11T13:08:22Z, says `$GOOGL` is reportedly in talks with Samsung to make the memory I/O die for Google's 10th-generation TPU planned for 2028; `$TSM` would still build the main compute engine, Samsung could win a key 2nm role.

### Verification snapshot

- X original for Balder found via x_search: `https://x.com/i/status/2065077036843143229`.
- Quoted X post extracted: `https://x.com/StockSavvyShay/status/2065058561449939160`.
- The Information original article is paywalled, so exact TPU/Samsung details are `paywall_gap` unless later verified by another independent source.
- CNBC/Barron’s confirm Alphabet private placement to Berkshire:
  - Class A: about `$351.81/share`.
  - Class C: about `$348.20/share`.
  - Barron’s describes a 6%+ discount.
- LongBridge CLI snapshot, 2026-06-11 CST:
  - `GOOG.US last = 344.500`, below 350 and below BRK Class C price.
  - `GOOGL.US last = 347.075`, below 350 and below BRK Class C price.
  - Computed daily `RSI14(GOOG) = 35.15`, so Balder’s own entry trigger `RSI14 < 30` is **not satisfied**.

### Decision lesson

This is a clean example of **watchlist, not entry**:

```text
price_anchor: passed
fundamental_catalyst: partially verified / paywall_gap
technical_trigger: failed (RSI14 still > 30)
author_backtest_claim: unverified
Decision impact: raise_watch_priority, no buy signal yet
```

## Reusable rule

When a trusted/monitored KOL gives a valuation-anchor + technical-trigger setup:

1. Add to watchlist immediately.
2. Do not buy until the explicit trigger is satisfied by independent calculation.
3. If the trigger is RSI-based, record current RSI and trigger threshold.
4. If the post also contains a fundamental catalyst, verify it separately; catalyst cannot replace trigger.
5. If the author claims backtest edge without details, log `backtest_unverified` and do not raise position multiplier.

## Output shape

```markdown
Social Technical Entry Gate:
- source_status: full / screenshot_only / paywall_gap
- price_anchor: pass/fail + source
- technical_trigger: pass/fail + calculation
- catalyst_verification: verified / partial / paywall_gap / contradicted
- backtest_claim: supplied / unsupplied / insufficient
- decision: no_trade / watchlist / paper_probe / actionable
- refresh_if: price crosses trigger, RSI threshold reached, original source verified, regime changes
```
