# Intelligent Research Contract · 智能投研质量合同

目标：让 `trading-research` 从“模板化分析”升级为“证据可追溯、推理可审计、动作可执行”的投研系统。适用于 A 股 / 港股 / 美股任意代码或公司名。

## 1. 强制研究链与决策链

Tier 2 必须按顺序显式产出：

`宏观 → 行业 → 公司 → 情绪 → 资金结构 → 机会`

每层必须有 `state/as_of/evidence_ids/data_gaps/implication`。任何层缺失、过期或冲突，只能降低 readiness / action ceiling / position cap；后层不得越级补分。宏观事件与行业请求也必须说明向公司/资产篮子的传导，不能把“不适用”当作跳过证据。

然后按：

`机会 → 评分 → 概率 → 风险 → 计划`

- 评分只解释证据，不是最终动作；禁止另造综合总分替代 Compiler。
- 概率必须绑定 `horizon_id/as_of/due_at/resolution`，并登记到 `prediction_ledger.py`；未知写 unknown。
- 风险姿态默认 `neutral`；只有预定义极端证据才可形成倾斜候选，仍须 Compiler 裁决。
- 计划必须有触发、失效、`no_trade_if`、review clock 与至少 3 条 premortem。
- 最终动作仍只由 Decision Compiler 产生，保持 `no_order_execution`。

## 2. 最小数据集

| 市场 | 最小必查数据 | 缺失后的最高动作 |
|---|---|---|
| A 股 | 身份/交易所、实时或收盘价、K 线、成交额/换手、涨跌停/炸板/连板/龙虎榜、资金流、公告/财报、板块梯队、ST/停牌/监管风险 | 缺行情或公告：最高观察；缺情绪结构：最高试错 |
| 港股 | 身份、LongBridge 行情、成交额/流动性、南向、HKEX/披露易公告、财报、沽空/CCASS/ADR 或 AH 映射（可得则查） | 流动性缺口：最高观察/轻仓；公告缺口：最高观察 |
| 美股 | 身份、LongBridge 行情/盘前盘后、SEC/IR/财报/guidance、成交额、四象限宏观、期权活跃票的 Gamma、新闻/社媒交叉验证、被动/发行/解禁供给 | 缺 SEC/财报：最高观察；Gamma 触发但未查：不得激进接盘 |

## 3. 证据账本 Evidence Ledger

所有影响结论的事实必须写入证据账本，并分配唯一 EID。

| 字段 | 要求 |
|---|---|
| EID | E1/E2/E3...，报告内引用 |
| 证据类型 | 行情/公告/财报/新闻/社媒/账户/宏观/Gamma/资金流/被动资金/发行供给 |
| 来源 | 工具名、URL 或文件名；不能写“网上看到” |
| 时间戳 | 行情分钟级、新闻 72h、财报报告期、龙虎榜交易日、Gamma 延迟约 15m |
| 原始事实 | 不要先解释，先记录事实 |
| 变量归类 | 叙事增强/叙事证伪/财报验证/交易确认/四象限状态/拥挤风险/被动资金/发行供给/执行窗口/宏观政策/账户座位 |
| 方向/强度 | Bull/Bear/Neutral；强度 -2~+2 |
| 可靠性/新鲜度 | 0~1，低于 0.5 不能主导结论 |
| 交叉验证 | 独立来源 EID；同源转载不算 |
| 决策影响 | 提高/降低/不改变仓位，并说明原因 |

硬规则：
- L1+ 结论至少引用 3 条独立核心 EID；少于 3 条只能输出 L0/观察/数据不足。
- Grok/X/社媒只能作为线索，不得单独提高仓位。
- 多源冲突时，先降级再解释，不得挑一个喜欢的数字。

## 4. 宏观四象限 Macro Dashboard

每次美股/宏观问题都必须单列：`liquidity / economy / inflation-rates / sentiment`。
输出必须包含：`current_state / recent_trend / next_trigger / market_implication`，最后合成 `attack | neutral | defend`。

## 5. 假设账本 Hypothesis Ledger

每个可交易观点必须回答四问：
1. 谁在付钱：客户、政策、资金、交易对手还是估值重定价？
2. 钱如何进报表：收入、毛利、现金流、资产效率，还是只进估值？
3. 什么时候验证：财报、公告、订单、龙虎榜、Gamma 墙、政策节点、价格关键位？
4. 错了看什么：一个可观察 falsifier，不是“走势不好”。

## 6. 内生市场结构 Endogenous Market Structure

每个可交易观点必须额外回答：
1. 叙事现在处于萌芽、扩散、拥挤还是反噬？
2. 仓位是否同质、杠杆是否高、是否存在 forced seller？
3. 被动/ETF/AP 机制是否在放大量价？
4. 是否存在 IPO / lockup / secondary / convert / mega financing 供给冲击？
5. 当前更像 leader 牛市，还是 #2/#3 的次级牛市？

## 7. 冲突账本 Conflict Ledger

冲突不能被“综合来看”抹平，必须裁决。

| 冲突类型 | 裁决 |
|---|---|
| 基本面好但市场结构破位 | 中线观点可保留，短线不得接盘 |
| 新闻利好但未公告/财报验证 | 只观察，不提高仓位 |
| 情绪强但财报证伪 | 只能短线，不得包装成长线；严重时回避 |
| Gamma 跌破 Put Wall 且未收回 | 禁止激进接盘，等重新站回或波动结构修复 |
| 流动性弱但情绪强 | 把上涨先解释为估值/情绪扩张，不追高 |
| AI 叙事强但资源/利率上行 | 不直接套“AI = 通缩”；先降久期暴露 |
| 共识交易过拥挤 | 事件只是火柴，结构才是燃料；先减仓/配对 |
| IPO / lockup / secondary / mega financing | 先下调流动性倍率，确认吸收后再升级动作 |
| 港股基本面好但流动性断层 | 仓位封顶，放大滑点假设 |
| 宏观 risk-off 但个股趋势强 | 下调仓位倍率，不忽视系统风险 |
| 账户上下文未知但用户问加仓/减仓 | 拆成“若空仓/若已持有”，不得给账户级数量 |

## 8. 仓位上限计算器

`最终仓位上限 = 基础动作上限 × 数据质量倍率 × 宏观政策倍率 × 杀杠杆倍率 × 内生结构倍率 × 市场结构倍率 × 账户座位倍率 × 流动性倍率`，再应用红线 cap。

| 项目 | 倍率/封顶 |
|---|---|
| 数据完整且可交叉验证 | ×1 |
| 关键数据部分缺口 | ×0.7 或最高试错 |
| 关键行情/公告/财报缺口 | ×0.3 或最高观察 |
| 四象限 = neutral / defend | ×0.6-0.8 / ×0.2-0.5 |
| `fragile_crowded` / `unwind_active` | ×0.5-0.8 / ×0-0.4 |
| 市场结构破位 / A 股退潮 / Put Wall 下方 | ×0 或最高观察 |
| IPO/增发/解禁/巨额融资虹吸 | 流动性倍率 ×0.4-0.8 |
| 账户重仓/保证金压力/币种错配 | ×0-0.5 |
| 港股流动性断层 | ×0.3-0.7 |

## 9. 输出纪律

- 若账户上下文未知，不直接说“加仓/减仓/清仓”；改成“若空仓 / 若已持有”。
- 若工具失败，写缺口、影响、下一步，不脑补。
- 每个行动必须同时有触发条件、证伪条件、review clock。
- 结论先行，但结论必须能回链到 EID、宏观四象限、内生结构、冲突裁决和仓位计算器。

## 10. OKX venue adapter handoff（v2.48）

当研究涉及 OKX CEX、Unified Tokenized Stocks、Wallet/DEX 或外部策略状态时：

1. 先声明 `venue/channel/mode`，并按 `okx-research-execution-supervision.md` 使用互斥的 CEX 或 Wallet identity。
2. 公共市场快照只构成 Evidence；私有账户/订单/成交必须由独立只读适配器采集，禁止在研究 payload 内放凭据。
3. 入场评分缺任一关键因子、EID 或 freshness 时，输出 `insufficient_data`，不得用默认 0/中位数补齐。
4. 账实漂移、heartbeat 丢失、WS stale、REST baseline 未完成或 item-level 部分失败，必须进入 DataGap/Conflict Ledger 并阻断新开仓。
5. `pause_required` 与 `pause_effective` 分开；研究层只能建议暂停，不能声称已改变策略状态。
6. 最终报告继续使用既有 Evidence Ledger、Mira readiness、Decision Compiler 和 Memory；不得新增 OKX 专属动作系统。
