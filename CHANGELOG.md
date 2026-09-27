# Changelog

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
