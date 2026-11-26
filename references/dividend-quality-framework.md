# Dividend Quality Framework · 分红质量与收益陷阱检查

## 目的

回答“股息高不高”之外更重要的问题：**这份分红能否被现金流覆盖、能否跨周期持续、当前高股息是否只是价格下跌制造的收益陷阱。**

本框架是 `fundamentals:dividend_quality` 子框架，不新增 Decision Compiler module、不单独生成买卖动作。

## 触发

- 用户问股息率、分红安全、红利股、除息、派息增长、收益型组合。
- 高股息是当前 thesis 的主要部分。
- 估值比较中股息率显著高于自身/行业历史。

## 数据源

优先使用 LongBridge：
- `dividend` / `dividend_detail`：历史与方案。
- `financial_report_latest` / `financial_statement`：EPS、利润、现金流、负债。
- `business_segments` / `consensus`：业务周期与未来盈利覆盖。
- `corp_action` / `filings`：回购、拆股、特别股息、融资与正式披露。

兜底：公司 IR、交易所公告、SEC/CNINFO/HKEX 原文；Yahoo 等公开聚合只能交叉验证，必须标时间戳与口径。

## 六项检查

### 1. Yield basis
- `forward_yield` 与 `trailing_yield` 分开。
- 区分普通股息、特别股息、资本返还、回购。
- 用除息前后价格口径一致的数据；不把一次性特别股息年化。

### 2. Earnings payout coverage
- `payout_ratio_eps = ordinary_dividend / normalized_eps`。
- 净亏损、重大一次性损益或周期顶部时，EPS payout 不能单独使用。
- 金融、REIT、公用事业等行业按当地常用覆盖指标补充，不套统一 40/60/80 阈值。

### 3. Cash-flow coverage
- `payout_ratio_fcf = cash_dividends_paid / normalized_free_cash_flow`。
- 检查经营现金流、维持性 capex、营运资金波动、租赁/利息与少数股东分配。
- 连续借债/发股维持分红，记 `distribution_funding_risk`。

### 4. Growth and continuity
- 5 年 CAGR 必须使用完整年度普通股息；不足 5 年就写样本不足。
- `consecutive_growth_years` 不能把“持平”算增长；特别股息不计入连续增长。
- 同时看削减/暂停历史和管理层正式分红政策。

### 5. Balance-sheet and cyclicality
- 净负债、利息覆盖、再融资到期、信用评级变化。
- 商品/航运/半导体/地产等周期行业用中周期利润和现金流，不用单年峰值。
- 汇率、资本管制、监管资本要求和税制只在与用户市场/身份相关时展开。

### 6. Yield-trap and event timing
- 股息率飙升若主要来自价格暴跌，先查盈利下修、资产减值、监管限制、融资压力。
- `ex_dividend_date` 是现金流时点，不是无风险套利；除息、税、汇率、借券成本要分开。
- 财报/董事会尚未正式批准的 forward dividend 最高是 guidance/forecast。

## 质量状态

| 状态 | 条件 | 决策影响 |
|---|---|---|
| `durable` | EPS+FCF 双覆盖、资产负债表稳、跨周期记录可复核 | 可支持 fundamentals，但不自动提高 L 级 |
| `adequate` | 覆盖基本成立，增长/周期存在一般不确定性 | 正常进入 working view |
| `fragile` | 单一覆盖、FCF 波动、债务/周期压力、政策未确认 | 最高 L1/watch；降低收益假设 |
| `yield_trap_risk` | 分红靠融资/资产出售、现金流不覆盖、削减风险或价格崩跌驱动 | fundamentals 收紧；必要时进入 L4 讨论 |
| `data_gap` | 历史、现金流或方案原文缺失 | 不给“安全分红”结论 |

## 最小 Evidence/Calculation Ledger

- 报告期、币种、普通/特别股息口径。
- trailing/forward dividend、EPS、FCF、cash dividends paid。
- EPS payout 与 FCF payout 公式、来源 EID、异常调整。
- 5 年年度序列与 CAGR；连续增长/削减年份。
- ex-date/payment date、批准状态、数据新鲜度。
- balance-sheet/cyclicality red flags 和 falsifier。

## 与外部 stock-analysis 的迁移边界

参考其 yield、payout、5Y CAGR、连续增长、ex-date 产品结构；不复制固定 0–100 safety score。原因：原实现只用 trailing EPS、把持平误计为连续增长、可能把未完整年度纳入 CAGR，也没有 FCF/债务/周期/特别股息质量门。现框架保留可解释计算，最终仍由 Mira readiness 与 Decision Compiler 裁决。
