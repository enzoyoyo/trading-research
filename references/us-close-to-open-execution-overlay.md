# US Close-to-Open Execution Overlay · 美股收盘买开盘卖执行层

> 主落点声明：编译进 `execution_window`；本层只降维和收紧。

## 目的

把“判断要不要买 / 要不要卖”与“猜日内什么时候买 / 什么时候卖”拆开：

- 原问题：`方向判断 × 入场时点 × 出场时点`，维度高，容易把研究判断和盘中噪音混在一起。
- 降维后：只判断一件事——**这只美股是否值得隔夜持有到明早开盘前后**。
- 执行窗口固定为：**美股收盘前最后 10 分钟建仓观察/执行 + 次日开盘后前 10 分钟退出/重判**。

这不是新交易系统，也不是自动下单规则；它只是 `Decision Compiler` 之后的 **US-only execution_window overlay**，用于把已通过研究和风控的美股观点压缩成更简单的隔夜执行计划。

## 适用范围与硬边界

| 项目 | 规则 |
|---|---|
| 市场 | **仅限美股 US-listed equities / liquid US ETFs** |
| 不适用 | A股、港股、可转债、期货、加密货币；不得把本文件套到 `a-share-short-term-layer.md` |
| 动作权限 | 只给 advisory 计划；保持 `no_order_execution` |
| 决策层级 | 不新增 L0-L5；不提高 `action_level` 或 `position_cap` |
| 上游依赖 | 必须先过 LongBridge/行情、risk_regime、Gamma（如适用）、Mira、Decision Compiler |
| 下游输出 | 只裁决“是否适合 close-to-open 执行”，不替代中线/波段观点 |

**铁律**：如果用户问 A股/港股，回到对应市场框架；不要引用本 overlay。

## 研究依据摘要

| 证据 | 对本 overlay 的启发 | 限制 |
|---|---|---|
| FRBNY Staff Report *The Overnight Drift* | 美股指数期货在隔夜特定时段存在显著正漂移；隔夜收益不是线性时间风险补偿 | 主要是 ES/指数层面，不等于单票无条件有效 |
| Lou/Polk/Skouras *A Tug of War* | 个股 overnight 与 intraday return 可分解；一些收益在 overnight 赚，一些在 intraday 反转 | 需要区分个股、策略、投资者结构；不能直接当买入信号 |
| 中国市场 overnight anomaly 论文 | A股隔夜收益机制可能与美股相反，且受散户注意力/T+1等制度影响 | 正是本 overlay 禁止套用 A股的原因 |
| Alpha Architect 成本批评 | 买收卖开毛收益可能被 bid-ask、滑点、佣金吞掉 | 必须估算净收益，不允许只看 gross edge |
| End-of-day reversal / close auction research | 美股尾盘与收盘竞价可能有临时价格压力，次日可反转 | 尾盘强弱不必然是基本面，需辨别被动流/再平衡/挤压 |
| NYSE/Nasdaq auction docs | 美股收盘/开盘竞价有明确 cutoff 和 imbalance 信息 | “最后10分钟”是观察/执行窗口，不等同任何券商都能无滑点成交 |

## 为什么它是“低 1 维度”的问题

普通短线交易要同时回答：

```text
买什么？为什么买？什么时候买？什么时候卖？盘中变了怎么办？
```

本 overlay 只回答：

```text
今天收盘前，是否愿意承担隔夜风险？明早开盘前10分钟是否兑现或重判？
```

因此它把盘中择时噪音压缩成两个固定窗口：

1. **Close decision window**：收盘前 10-20 分钟完成买入资格审查。
2. **Pre-open read gate**：次日开盘前把隔夜收益是否守住、市场是否确认、波动率是否确认先分成 strong / neutral / weak。
3. **Open resolution window**：次日开盘后 0-10 分钟兑现隔夜命题，或明确升级为新的日内/波段决策。

关键变化：
- 不是追求买在最低、卖在最高；
- 而是判断“隔夜这段风险是否值得拿”；
- 明早开盘 10 分钟后还想继续持有，必须重新生成新的 intraday/swing decision，不能把 close-to-open 交易偷换成死拿。
- `pre_open_read=strong` 只把退出窗口从开盘瞬间推迟到 09:40；不是新增买入信号，也不是允许 09:40 后继续拿。

## 触发条件

当用户说以下语义，且标的是美股时，读取本文件：

- “收盘买开盘卖”
- “close-to-open / overnight trade”
- “美股隔夜拿一下”
- “尾盘买，明早卖”
- “收盘前10分钟 + 开盘10分钟”
- “降低择时复杂度”

若用户未明确美股，但标的是 `*.US` 或美股 ticker，可使用；若标的是 A/H，禁止使用。

## 执行前门槛

### 1. Security scope gate

```yaml
execution_window_scope:
  market: US
  instrument: common_stock | liquid_etf
  exclude: [A_share, HK_stock, OTC, microcap, low_liquidity, leveraged_etf_unless_explicit]
```

最低流动性建议：
- 日均成交额足够覆盖计划仓位，保守起点：`avg_dollar_volume_20d >= 50M USD`；
- bid-ask spread 可接受，保守起点：`spread_bps <= 20`；
- 盘前/盘后流动性不是主证据，除非标的是 mega-cap / highly liquid ETF。

### 2. Upstream decision gate

本 overlay 只能接收已通过上游判断的标的：

| 上游模块 | 要求 |
|---|---|
| `risk_regime` | 非 `active_deleveraging` / `forced_liquidation` |
| `fundamentals/news` | 有明确隔夜命题或趋势延续理由 |
| `gamma` | 若 near-expiry 负 gamma 且贴近 put wall，最高只能 watch / tiny trial |
| `data_quality` | 缺关键报价、财报/事件时间、成交量或期权墙位时，不给 close-to-open 执行 |
| `Decision Compiler` | 上游至少允许 L1；如果上游 L0，本 overlay 不能复活交易 |

### 3. Catalyst / setup gate

close-to-open 交易必须属于以下之一，否则不做：

| Setup | 典型特征 | 说明 |
|---|---|---|
| Earnings / guidance continuation | 财报/指引后尾盘仍被吸收，卖压衰减 | 避免财报前裸赌，优先财报后反应 |
| Sector overnight continuation | 同板块龙头/ETF尾盘走强，期货/盘后消息支持 | 需 SPY/QQQ/SMH 等环境不冲突 |
| Analyst / company event | 升级、产品、监管、订单、并购消息在盘后/次日早盘可继续发酵 | 必须有原始来源或可靠新闻 |
| End-of-day pressure reversal | 尾盘异常卖压疑似被动流/再平衡，基本面未变 | 需要成交量和新闻排除真利空 |
| Macro event window | CPI/FOMC/NFP 等确定事件前后 | 必须单独标注 binary event risk，默认降仓 |

## 固定窗口

### Close decision window：15:40-16:00 ET

| 时间 | 任务 |
|---|---|
| 15:40-15:50 | 冻结判断：是否通过上游 gate；不再临时加故事 |
| 15:50-16:00 | 观察/执行窗口：看尾盘承接、VWAP、盘口/imbalance（可得时） |
| 16:00 | 记录 entry proxy：close / final 10-min VWAP / auction fill assumption |

注意：
- NYSE closing auction 的 MOC/LOC 关键 cutoff 通常在 **15:50 ET**；Nasdaq closing cross 的 MOC/LOC cutoff 常见为 **15:55 ET**。不同交易所/券商细节要以实时规则和券商支持为准。
- 因此“最后10分钟”首先是**观察和决策窗口**；是否用 MOC/LOC/limit/VWAP 要看券商和标的流动性。
- 不允许为了买到而追高失控；超过计划滑点则放弃。

### Open resolution window：09:30-09:40 ET

开盘前必须先跑 `pre_open_read`。这条来自 Balder 2026-06-25 的可验证结构：他公开说“最多只预测到开盘 10 分钟；pre-read 强可以拿到 9:40，否则立刻卖”。因此本 overlay 把盘前读数写成**退出管理门**，不是新 alpha。

```yaml
pre_open_read:
  timestamp: <ET time before 09:30>
  status: strong | neutral | weak
  inputs:
    index_premarket: {SPY: <%>, QQQ: <%>, sector_etf: <%|null>}
    basket_health: {green_count: <n>, total: <n>, avg_premarket_return: <%>, winners_holding: true|false}
    volatility_confirmation: {UVXY: <%>, VIX_proxy: <%|null>, confirms_selloff: true|false|null}
    known_flow_events: [CTA_rebalance, index_rebalance, quarter_end, macro_print, none]
    liquidity_spread: {premarket_volume_ok: true|false, spread_ok: true|false}
  action_bias:
    strong: allow_hold_until_09_40_if_upstream_allows
    neutral: trim_or_exit_by_open_range
    weak: exit_at_open_or_no_extension
```

判定纪律：
- **strong**：SPY/QQQ 或对应板块盘前确认，候选篮子大多数绿盘且平均收益为正，昨晚 winners 没有明显回吐，价差/量能可交易，UVXY/波动率没有确认风险扩散。
- **weak**：指数/板块不确认、候选篮子绿盘比例低、平均收益回吐、UVXY/波动率确认卖压、或已知 CTA/再平衡卖盘与价格动作同向。
- **neutral**：信号混杂；默认更接近退出而不是贪持。
- UVXY “太低”只能说明波动率没有同步确认下跌，或 hedge 可能便宜；它不是大盘必反弹证据。若价格下跌但 UVXY 低位，标注 `vol_confirmation=not_confirming_selloff` 或 `hedge_mispricing`，最多影响 hedge/退出优先级，不能单独提高仓位。

| 时间 | 任务 |
|---|---|
| 09:30-09:35 | 观察 opening range、gap、成交量和是否兑现隔夜命题 |
| 09:35-09:40 | 执行退出/减仓/重判；close-to-open trade 到此结束 |
| 09:40 后 | 若继续持有，必须转为新的 intraday/swing decision |

退出原则：
- `pre_open_read=strong` 且 gap up / winners holding：可观察到 09:40，但 09:40 前必须兑现或重判；
- `pre_open_read=weak`：开盘立即退出或不延长，不把昨晚 picks 变成日内主观单；
- gap up 达到预期但开盘 5-10 分钟不能延续：兑现；
- gap down 且跌破前日 close / overnight invalidation：退出，不把隔夜单变长线单；
- 强势高开并持续放量上穿 opening range：可以重新评估为日内/波段，但必须另走 Decision Compiler，不算原 close-to-open 交易。

## 计算口径

```yaml
close_to_open_trade:
  entry_proxy: close_t | vwap_15_50_16_00 | auction_fill
  pre_open_read: strong | neutral | weak
  exit_proxy: open_t1 | vwap_09_30_09_40 | opening_range_exit
  gross_return: exit_proxy / entry_proxy - 1
  estimated_cost_bps: spread_bps_entry + spread_bps_exit + slippage_bps + fees_bps
  net_return: gross_return - estimated_cost_bps / 10000
  max_hold_time: next_session_09_40_ET
```

回测或复盘必须分开记录：
- gross vs net；
- earnings vs non-earnings；
- mega-cap vs mid/small cap；
- positive close momentum vs forced close pressure reversal；
- market regime（normal / active_deleveraging / negative_gamma）。

## Decision Compiler 映射

本 overlay 输出 `execution_window` 模块信号，只能降级：

```yaml
module_signal:
  module: execution_window
  market_scope: US_only
  window: close_to_open
  max_action_level: L0|L1|L2|L3
  position_multiplier: 0.0|0.25|0.5|1.0
  hard_veto: false
  reason: "eligible_close_to_open | pre_open_read_strong | pre_open_read_weak_exit | missing_us_scope | cost_edge_negative | binary_event_risk | liquidity_gap | no_overnight_catalyst"
```

映射规则：

| 状态 | 动作上限 |
|---|---|
| 非美股 / A股 / 港股 | 对 close-to-open overlay 为 L0；回到对应市场框架 |
| 无隔夜催化或 setup | L0 |
| 成本后 edge 不足或 spread 太宽 | L0/L1 |
| binary event 未拆情景 | L0/L1 |
| `pre_open_read=weak` 或 UVXY/波动率确认风险扩散 | L0/L1；开盘退出/不延长 |
| `pre_open_read=strong` 但没有新的 intraday/swing 决策 | 只允许观察到 09:40，不提高动作等级/仓位 |
| 上游允许 L1 且 setup 成立但数据不完整 | 最高 L1 watch/tiny trial |
| 上游允许 L2/L3，流动性/成本/催化/风险均通过 | 可给 L2/L3 计划，但不得超过上游动作等级和仓位上限 |

## 输出格式

```markdown
US Close-to-Open Overlay:
- verdict: eligible / watch_only / no_trade
- upstream_action_cap: L__
- execution_window_cap: L__
- entry_window: 15:50-16:00 ET
- pre_open_read: strong / neutral / weak；inputs: SPY/QQQ、basket green_count/avg、UVXY/vol_confirmation、known_flow、spread/liquidity
- exit_window: 09:30-09:40 ET next session
- entry_proxy: close / final_10m_vwap / auction_fill assumption
- exit_rule: sell by 09:40 unless new Decision Compiler run upgrades to intraday/swing
- max_position_cap: __% or __ of upstream cap
- invalidation: 前日 close、关键均线、put wall、sector ETF、news falsifier
- cost_check: spread/slippage/fees estimated; gross edge must survive costs
- do_not_apply_to: A-share/HK
```

## Pitfalls

1. **把毛收益当净收益**：Alpha Architect 的成本批评必须进入复盘；频繁交易会被 spread/slippage 吃掉。
2. **把隔夜单变成长线套牢单**：09:40 后还拿，必须重新裁决。
3. **把 A股/港股套进来**：制度不同，禁止泛化。
4. **尾盘被动流误判成基本面**：尾盘强弱要与新闻、板块、成交、期权结构交叉验证。
5. **财报前裸赌**：除非用户明确要事件赌局，否则财报/宏观二元事件默认降仓或不做。
6. **负 gamma 近 put wall 还硬拿隔夜**：这种环境隔夜缺口风险放大，优先降级。
7. **把 pre-open optimistic 当新买入理由**：它只允许赢家最多观察到 09:40；不能让 L0 复活，不能延长到日内/波段。
8. **把 UVXY 低位解读成大盘必反弹**：UVXY 只是波动率/hedge 线索；价格跌而 UVXY 不确认，说明风险信号混杂，不能单独提高仓位。

## Source anchors

- Federal Reserve Bank of New York Staff Report No. 917, *The Overnight Drift*.
- Lou, Polk, Skouras, *A Tug of War: Overnight versus Intraday Expected Returns*.
- An, Huang, Li, *The Asymmetric Overnight Return Anomaly in the Chinese Stock Market* — used as non-US warning, not as US signal.
- Alpha Architect, *Trading Costs Wipe Out the Overnight Return Anomaly*.
- Alpha Architect summary of *End of Day Reversal*.
- NYSE Auctions / Trading Information pages; Nasdaq Opening and Closing Cross FAQ.
- X `@Balder13946731` 2026-06-24/25 public posts + subscriber screenshot: “最多只预测到开盘10分钟；pre-read 强可以拿到 9:40，否则立刻卖”；2026-06-25 `Pre-Open Read` 卡片用 SPY/QQQ 盘前、候选篮子绿盘数/平均涨幅和 winners holding 判定 `OPTIMISTIC`。
