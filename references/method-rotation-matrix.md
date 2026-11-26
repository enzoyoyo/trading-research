# Method Rotation Matrix · 交易法动态配权矩阵

## 为什么需要轮动配权

同一套交易法不能固定权重。不同市场、周期、信息质量下，最有效的方法不同：A 股短线重情绪周期，美股产业链重 Serenity/SEC/IR/capex，港股重流动性/南向/公告，期权活跃票必须加 Gamma 结构；而这次升级后，宏观四象限与内生市场结构也必须进入配权层。

**但无论选什么方法，先做 Step 0：参与者流第一性原理检查。** 先回答谁是边际买卖双方、他们的信念强度、什么改变了他们的逻辑（`references/participant-flow-motivation.md`），再根据参与者流判断选择什么方法最合适。参与者结构清晰且稳定→方法论权重生效；参与者结构剧烈变化→先等结构稳定，方法暂时降权。

## 基础配权

> 表中数字是**原始重要性分数**，不是最终百分比；`scripts/method_router.py` 会把原始分数 normalize 到 100 后输出。v2.5 以后新增的 `counter_consensus / expected_returns / bottleneck_scorecard / a_share_short_term`，以及显式化后的 `supply_chain_xray / early_stage_quality` 都在脚本中参与归一化。报告披露用脚本输出的 normalized weights，但主回复只露 Top 5，避免把方法论表格塞给用户。

<!-- runtime-matrix:start -->
| 场景 | 华源叙事/财报 | Serenity供应链 | 游资情绪 | 威科夫量价 | 利弗莫尔趋势 | 因子/组合 | 泊松择时 | 期权Gamma | 供应链X-Ray | 早期主题Quality | 内生结构/拥挤度 | 宏观政策新闻/账户 | 逆共识 | 预期收益 | 瓶颈评分卡 | A股短线 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A股超短 | 5 | 0 | 34 | 24 | 8 | 2 | 8 | 0 | 0 | 0 | 3 | 8 | 3 | 1 | 0 | 15 |
| A股趋势/波段 | 14 | 3 | 10 | 18 | 14 | 3 | 7 | 0 | 2 | 1 | 5 | 12 | 5 | 4 | 2 | 7 |
| A股基本面 | 24 | 4 | 3 | 10 | 6 | 14 | 6 | 0 | 3 | 2 | 3 | 12 | 6 | 7 | 3 | 2 |
| 港股 | 18 | 7 | 3 | 12 | 10 | 7 | 6 | 3 | 3 | 2 | 6 | 22 | 6 | 5 | 3 | 0 |
| 美股大科技 | 14 | 14 | 0 | 9 | 9 | 6 | 5 | 6 | 4 | 2 | 10 | 18 | 4 | 5 | 3 | 0 |
| 美股AI供应链 | 8 | 22 | 0 | 10 | 6 | 3 | 8 | 6 | 12 | 6 | 8 | 12 | 5 | 4 | 8 | 0 |
| 美股早期技术主题 | 6 | 16 | 0 | 7 | 5 | 2 | 12 | 4 | 14 | 20 | 8 | 12 | 8 | 5 | 8 | 0 |
| 美股期权/Gamma主导 | 7 | 8 | 0 | 12 | 8 | 2 | 10 | 19 | 2 | 1 | 8 | 13 | 5 | 3 | 5 | 0 |
| 美股杀杠杆/系统风险 | 7 | 4 | 0 | 10 | 7 | 1 | 6 | 22 | 0 | 0 | 7 | 24 | 8 | 3 | 6 | 0 |
| 美股流动性/拥挤度轮动 | 9 | 12 | 0 | 7 | 6 | 3 | 6 | 8 | 3 | 2 | 24 | 15 | 5 | 4 | 5 | 0 |
| 事件驱动/财报前后 | 11 | 8 | 3 | 9 | 6 | 2 | 18 | 6 | 2 | 2 | 9 | 12 | 7 | 4 | 5 | 0 |
<!-- runtime-matrix:end -->

## 动态调权规则

- 华源叙事/财报：中长线、基本面、财报/公告、估值争议大。
- Serenity：AI/半导体/电力/云/材料/设备，出现 capex、长约、涨价、交期、锁产能。
- Liquidity-Valuation / Capex Cashflow Duration：AI 大科技 vs 半导体供应链明显分化，或利率/折现率变化后出现 Capex 花钱方/收钱方轮动；作为宏观+基本面+内生结构 overlay，不新增独立权重列。
- Prosperity Davis Double（景气度·戴维斯双击）：选景气行业龙头、判断戴维斯双击与周期长度时叠加在「华源叙事/财报 + Serenity」之上的选股 overlay。它把标的归为成长/消费/周期型再套四维双门槛，复用 Serenity 的瓶颈/量价证据，**不新增独立权重列**；其「不做 DCF/宏观降权」只在方法内部生效，由 regime 决定本次是否主导，绝不覆盖 risk_regime。详见 `prosperity-davis-double-framework.md`。
- 游资情绪：A 股短线、连板、龙虎榜、涨跌停、题材主线。
- 威科夫量价：买点/卖点/是否追/是否破位，量价背离、关键位。
- 利弗莫尔趋势：大级别趋势、突破关键点、顺势加仓/止损。
- 因子/组合：多标的排序、组合、风格轮动、回撤控制。
- 泊松择时：明确事件窗口，或用户问“现在是不是好位置”。详见 `poisson-method.md`。
- 期权Gamma结构：用户提供 GEX/Put Wall/Call Wall/Gamma Flip；美股期权活跃。
- 供应链X-Ray：分析新兴技术产业链时，需做 L0-L4 映射、置信度分级、投资逻辑分层。
- 早期主题Quality：萌芽期/导入初期标的，用四维度（赛道/格局/定价权/市值-TAM错配）替代传统7模块评分。
- 内生市场结构/拥挤度：pair trade、consensus premium、passive flow、ETF 套利、IPO/lockup/secondary、#2>#1 补涨。
- 宏观政策新闻/账户：用户要求结合实时新闻、政策、宏观、Longbridge、IBKR、持仓、仓位。
- 美股杀杠杆/系统风险：只在真实去杠杆/强平语境触发——margin call、强平、爆仓、流动性踩踏/挤兑、basis trade unwind、系统性 deleveraging 词组（`scripts/method_router.py` `DELEVERAGING_KEYWORDS`）。VIX/GEX/Skew/黄金/个股代码（如 AAPL）等单独实体词或期权结构词**不触发**本场景，需与去杠杆语义共同出现才判定为系统性风险；否则按各自场景路由（个股→大科技/供应链等，期权结构→美股期权/Gamma主导）。
- 实测风险态后置钩子：本次会话的 `risk_regime_snapshot.v1` 优先于 repo 外 `risk_regime/current.json` 缓存；仅当北京时间当日、未过 `stale_after` 且状态为 `active_deleveraging/forced_liquidation` 时，对 `youzi_emotion/early_stage_quality/a_share_short_term/livermore` 的**原始分**按 `US_deleveraging` 行逐项取 min，再统一 normalize。缺失、无效、未来或过期快照 fail-open 回基础矩阵；钩子只做 min，不做 max，输出 `risk_regime_hook.tightened_methods.{raw_before,raw_after}` 供审计。
- 港股离岸市场结构 Overlay：港股场景（港股标的/恒科/南向/高股息港股/港股ETF）叠加 `hk-offshore-market-playbook.md` 作为市场结构 overlay（离岸身份第一性/资金阵营三分/流动性横截面分层/收益资产 vs 波动率资产），**不新增独立权重列**，只影响港股行的方法解释与收紧、不覆盖 `risk_regime`。详见 `hk-offshore-market-playbook.md`。

## 冲突裁决

风控红线 > 座位合法性 > 宏观四象限/regime > 内生市场结构 > 最新可验证事实 > 多源确认 > 供应链X-Ray映射完整度 > 早期主题Quality阈值 > 账户/宏观/政策仓位上限 > Gamma执行窗口 > 财报验证 > 叙事强度 > 技术形态。

## 输出必须披露

主回复只披露 Top 5 方法权重和一句配权理由；完整权重写入文件/附录。

```markdown
本次方法配权 Top 5：
- {{METHOD_1}}：__%
- {{METHOD_2}}：__%
- {{METHOD_3}}：__%
- {{METHOD_4}}：__%
- {{METHOD_5}}：__%

配权理由：{{WHY_THIS_ROUTING}}
```

禁止在主回复默认展开 12+ 项完整方法表，除非用户要求完整审计。
