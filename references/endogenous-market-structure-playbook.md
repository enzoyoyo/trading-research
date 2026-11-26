# Endogenous Market Structure Playbook · 内生市场结构与拥挤度打法

来源边界：本框架吸收自 `@Franktradinglog` 与 `@Balder13946731` 的公开帖子中可验证、可迁移的部分；不使用付费群、Substack 付费内容或无引用的传闻。

核心命题：价格大波动往往不是“新闻本身”造成，而是 `仓位结构 + 杠杆分布 + 被动资金 + 发行/解禁供给 + 叙事拥挤度` 先把燃料堆好，事件只是火柴。

## 1. 六个强制扫描

| 扫描项 | 要问什么 | 典型信号 | 对动作的影响 |
|---|---|---|---|
| 叙事阶段 Narrative Stage | 这是萌芽、扩散、拥挤还是反噬？ | 好消息不再涨、坏消息开始跌；FOMO 叙事高度统一 | 拥挤阶段只适合减仓、配对或等回撤 |
| 共识溢价 Consensus Premium | 价格里是否已经包含“别人也会继续相信”的溢价？ | shallow pullback、同质 holder、多数人同一逻辑 | 溢价越高，事件错一点就越容易砸穿 |
| 持仓与杠杆 Positioning & Leverage | 谁在持有？是不是同一类 HF / CTA / vol-control / 零售杠杆？ | VIXEQ/VIX、相关性上升、单一主题高杠杆 | 先降杠杆，再谈观点 |
| 被动/ETF Mechanics | 成交量是不是 AP 套利/creation-redemption 放大的？ | 指数大波动但更多是 ETF arb；指数纳入/调仓窗口 | 不把被动流量误判成主动基本面买盘 |
| 发行供给 Issuance Overhang | IPO、secondary、convert、债券、lockup 会不会抽走流动性？ | 解禁、增发、发债、mega IPO、员工/VC 抛压 | 先给流动性折扣，再决定能不能追 |
| 轮动结构 Rotation Regime | 当前是 leader 趋势牛，还是 #2/#3 的次级牛市？ | `AMD > NVDA`、`MRVL > AVGO` 这类 catch-up | 优先 relative-value / pair trade，不盲追 leader |

## 2. 结构状态标签

| 标签 | 定义 | 默认动作 |
|---|---|---|
| `uncrowded_constructive` | 叙事早期、买方分散、杠杆不高 | 允许顺势做多 |
| `crowded_but_supported` | 很拥挤，但还有被动/基本面/业绩承接 | 可以持有，但不追高 |
| `fragile_crowded` | 同侧仓位多、边际买家弱、利好不涨 | 只降风险/做配对 |
| `unwind_active` | 破位 + 杠杆松动 + 事件触发 | 优先防守、等 forced seller 出清 |

## 3. 供给与被动资金的解释顺序

1. 先分清是 `主动看多买盘` 还是 `被动/套利成交`。
2. 若存在 IPO / lockup / secondary / 发债 / convert / index inclusion：
   - 先问资金是“吸走别处流动性”还是“给本票新增承接”。
   - 再问供给是一次性冲击，还是持续 overhang。
3. `private QT`：当 mega financing / mega IPO 同时出现，要把它当成 QT 类流动性吸收，而不只是个股故事。

## 4. Frank 可迁移规则

1. 不做 `dead bull / dead bear`：观点必须服从新信息。
2. 配对优先匹配市值、beta、主题，不要拿小票去对冲超大盘 leader。
3. 当“long semi short SaaS”这类共识交易被全市场拥挤时，风险不再是基本面，而是 unwind 节奏。
4. 量大不一定是出货；SPY/QQQ 量放大要先排除 ETF/AP arb 机械成交。

## 5. Balder 可迁移规则

1. AI capex 大年里，`融资 -> 流动性虹吸 -> dream stock 压力` 要单独评估。
2. `#2 > #1` 的次级牛市经常发生在 leader 太拥挤、市场开始找补涨时。
3. 对 mega IPO / 高估值 pre-IPO，先问“谁来接盘”而不是先问“故事多性感”。
4. AI/半导体分化时，先拆 `capex_payer_future_monetizer` 与 `capex_receiver_current_cashflow`：高利率/折现率上行可能奖励当前收钱方、惩罚远期变现方；但这只是相对解释，不是单独买点。
5. 鸽派表态后若低波/防御/权重跌、高 beta/动量/Meme 涨，优先解释为 risk-on 轮动和仓位再平衡；先区分“好鸽派”与“坏鸽派”，不要机械套用降息利好所有资产。

## 6. 报告最小输出格式

```yaml
endogenous_market_structure:
  state: fragile_crowded
  narrative_stage: crowded
  consensus_premium: high
  leverage_state: elevated
  passive_flow_state: mixed
  issuance_overhang: high
  rotation_regime: secondary_bull
  decision_impact: reduce size / prefer pair trade / wait for absorption
```

## 7. 冲突裁决

- 基本面看多 + 内生结构脆弱：中线观点可以保留，短线动作降级。
- 叙事继续发酵 + 利好不涨：先尊重拥挤度，不追高。
- 供给 overhang 明确 + 被动承接不足：先下调流动性倍率。
- leader 过热 + #2/#3 开始补涨：优先 relative trade，不急着下“行业龙头失效”结论。
