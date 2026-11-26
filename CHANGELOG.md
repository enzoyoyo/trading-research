# Changelog

## v2.74

### 修复
- 期权组合计算：多头蝶式按对价计算的净支出必须位于 (0, 翼宽 × 乘数 × 组数)，否则判为 blocked，不再输出"最大亏损 0"或"最大盈利 0"。看涨、看跌蝶式同一标准。
- 合约过期先于报价规则判断：已结算报 `contract:expired:<合约>`，已过最后交易时刻报 `contract:no_longer_trading:<合约>`；因合约过期导致的到期情景不再报 `scenario:not_future`。

### 文档与测试
- 说明铁蝶、断翼蝶、卖出蝶是不同结构，本模块不支持以 `kind=butterfly` 输入。
- 新增回归测试：净收入蝶式、净支出超过翼宽、等于上限、按组数放大上限、看跌蝶式、正常蝶式几何，以及四个过期原因用例。

### Fixed
- Option structures: a long butterfly's natural debit must lie in (0, wing × multiplier × units); otherwise it is blocked instead of reporting "max loss 0" or "max profit 0". Call and put flies share the rule.
- Contract expiry is checked before quote rules: settled contracts report `contract:expired:<id>`, contracts past their last trading time report `contract:no_longer_trading:<id>`; an expiry scenario that is past because the contract expired no longer reports `scenario:not_future`.

### Docs and tests
- Document that iron, broken-wing, and short butterflies are different structures not accepted as `kind=butterfly`.
- Regression tests for a net-credit fly, a debit above the wing width, a debit equal to the ceiling, the ceiling scaling with units, a put fly, normal fly geometry, and four expiry-reason cases.

## v2.73

### 新增
- A 股盘后与情绪复盘的本地快照审查命令，支持同源 1/5/20 日资金窗口、五个交易日涨停生态、阈值敏感性和一字板参与度提示。
- 分别统计涨停股票日与去重股票数，避免将重复涨停次数解释为不同股票数量。
- 离线示例、数据合同说明和回归测试。

### 改进
- 按市场与意图隔离复盘分支，仅用于 A 股盘后或情绪复盘；其他市场、原日级连续流入分类、决策编译器与仓位权限保持不变。
- 检查来源、日期、会话时钟、单位、行业分类与覆盖范围；缺失或不一致数据保留为缺口，不补零，不输出看似有效的分类。
- 补充一次采集、本地计算、前次计划逐项验证和次日验证条件的复盘流程说明。

### Added
- Local snapshot checks for A-share post-close and sentiment reviews: same-source 1/5/20-day sector flows, five-session limit-up ecology, threshold sensitivity, and one-price-board participation context.
- Separate stock-day and distinct-stock counts for limit-up history.
- Offline fixtures, data-contract documentation, and regression tests.

### Changed
- Scope the review branch to A-share post-close or sentiment requests without changing other markets, daily-flow streak classification, decision compilation, or position permissions.
- Check source, date, session clock, unit, taxonomy, and coverage consistency; retain missing or inconsistent data as explicit gaps rather than zero values or valid classifications.
- Document capture-once/local-compute handling, previous-plan reconciliation, and next-session verification.
