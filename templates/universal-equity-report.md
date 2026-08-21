# {{SYMBOL}} {{COMPANY}} 简洁投研报告

> 目标：主回复一屏先给决策，证据够具体但不堆审计表。完整 Evidence Ledger / Conflict Ledger / Decision Memory 可写入文件或附录，不默认塞进聊天回复。
>
> 本文件是主回复格式的唯一权威（SKILL.md 步骤 9 只指向这里，不重复格式细节）。`scripts/validate_report.py` 的 `REQUIRED_ANY`/`REQUIRED_PHRASES` 是 Tier 2 完整报告的硬门槛；改这里的字段名/标签，必须同步改那边，反之亦然——两处任一方漂移，生成的报告会在校验时被拒。Tier 0/Tier 1 格式不经过 `validate_report.py`（它只针对 Tier 2 完整报告契约），但仍须保留 `no_order_execution` 与数据时间戳。

## 输出纪律

- 先给结论，不铺垫。
- 每个判断最多 1 句解释，能用数字就用数字。
- 不重复同一原因；同一证据只出现一次。
- 主回复默认 7 个区块：核心结论、持仓/标的速览表、关键依据、行动线、数据缺口、验证结果、质量门。
- 涉及真实持仓、组合风险、多标的对比、次日应对线时，核心数据与决策**必须表格化**；不要把每只票写成长段列表。
- 表格列数控制在 5-8 列；每格不超过 1 句，优先用“动作等级 / 关键价位 / 风险点 / 下一步”这类短标签。
- 表格之后只补 3-5 条解释，不逐行重复表格内容。
- 只有用户要求“完整报告/审计报告/复盘文件”时，才展开完整证据账本、冲突账本、仓位计算器、长期决策记忆。
- 不捏造：没有抓到的数据写“缺口”，不要补数字。
- **闭环状态如实披露**：假设注册表（`hypothesis_registry.py`）、校准桶（`calibration_scorecard.py`）等长期闭环为空、样本不足或长期未更新时，质量门/验证结果里必须写明 `data_starved` 及根因（如「注册表 0 条，本次已登记」「校准 pending>0 paired=0，等待 System A 结果回填」），不得写 `none` 掩盖。

## Tier 0 直答格式

适用于单纯行情/持仓/盈亏/交易日/单指标当前值这类无需研究管线的问题。不铺垫，不展开质量门八件套。

```markdown
the user，{{ONE_LINE_FACT}}。

数据时间：{{DATA_TIMESTAMP}}；来源：{{DATA_SOURCE}}
```

取数失败或数据陈旧时改用：

```markdown
用户，{{SYMBOL}} 数据缺口：{{DATA_GAP_REASON}}（{{DATA_SOURCE}} {{FAILURE_MODE}}）。
```

## Tier 1 速判格式

适用于单标的能不能买/卖/加仓/止盈止损这类简单问题，用户未要求深度报告。跳过完整证据账本，但保留 Decision Compiler 裁决、falsifier 和最小质量门。

```markdown
the user，{{SYMBOL}} {{ACTION_LEVEL}} {{ACTION_LABEL}}：{{ONE_SENTENCE_REASON}}。

依据：[{{E1}}] {{EVIDENCE_1}}；[{{E2}}] {{EVIDENCE_2}}；[{{E3}}] {{EVIDENCE_3}}

falsifier：{{FALSIFIER}}

质量门：readiness_level={{READINESS_LEVEL}}；stale_after={{STALE_AFTER}}；review clock={{REVIEW_CLOCK}}

非下单指令，不构成投资建议；我没有操作券商。
```

## Tier 2 · 默认主回复模板

```markdown
用户，已完成。核心结论：

{{SYMBOL}}：{{ACTION_LEVEL}} {{ACTION_LABEL}}；{{NEW_MONEY_ACTION}}；若已持有，{{HOLDING_ACTION}}。

原因很简单：{{ONE_SENTENCE_REASON}}。

关键依据：
- [{{E1}}] {{EVIDENCE_1}}
- [{{E2}}] {{EVIDENCE_2}}
- [{{E3}}] {{EVIDENCE_3}}
- [{{E4}}] {{EVIDENCE_4}}

行动线：
- 空仓：{{EMPTY_POSITION_PLAN}}
- 低吸：{{PULLBACK_PLAN}}
- 已持有：{{HOLDING_PLAN}}
- 转强复核：{{RECHECK_UPSIDE}}
- falsifier / 证伪：{{FALSIFIER}}

数据缺口：
- {{DATA_GAP_1}}
- {{DATA_GAP_2}}

验证结果：
- {{VALIDATION_1}}
- {{VALIDATION_2}}
- {{DECISION_MEMORY_STATUS}}
- write_status: {{WRITE_STATUS}}；completeness: {{COMPLETENESS}}

质量门：
- readiness_level: {{READINESS_LEVEL}}
- intelligence coverage: {{COVERAGE_RATIO}}；critical gaps={{CRITICAL_GAP_COUNT}}
- stale_after: {{STALE_AFTER}}
- must_refresh_if: {{MUST_REFRESH_IF}}
- research watch triggers: {{RESEARCH_WATCH_TRIGGERS_OR_NONE}}
- review clock: {{REVIEW_CLOCK}}

非下单指令，不构成投资建议；我没有操作券商，也不代表你的真实持仓。
```

## Tier 2 · 美股监管证据可选区块

涉及 10-K/10-Q/8-K/Form 4/13F、SEC 原文或 LongBridge filing index 时，插入下列短表；主回复不展开整份 filing 列表。

```markdown
监管证据：

| Form / accession | filed / report period | 原始文档 | retrieval paths | source family |
|---|---|---|---|---|
| {{FORM_TYPE}} / {{ACCESSION_NUMBER}} | {{FILED_AT}} / {{REPORT_PERIOD}} | {{DOCUMENT_URL_OR_GAP}} | {{RETRIEVAL_PATHS}} | {{SOURCE_FAMILY}} |

来源覆盖：independent_source_count={{INDEPENDENT_SOURCE_COUNT}}；unknown_source_family={{UNKNOWN_SOURCE_FAMILY}}；compiler signal={{SOURCE_COVERAGE_SIGNAL_OR_NONE}}

边界：LongBridge 与 SEC 直连命中同一 accession 只算一个 `sec_edgar` 底层来源；缺 accession/CIK/原始 URL/报告期必须列入 DataGap，不能用 provider ID 或 filing 数量补票。
```

## Tier 2 · OKX / Unified Tokenized Stocks 可选区块

涉及 OKX public/read-only 数据、tokenized stock 或外部策略监督时，插入下列区块。只用 API 当前返回的产品身份；自然语言公司名不能替代 `instId`。

```markdown
产品身份：

| 维度 | 当前值 | 状态 |
|---|---|---|
| venue / channel / mode | OKX / {{CHANNEL}} / {{MODE}} | {{IDENTITY_STATUS}} |
| instrument | {{INST_ID_OR_TOKEN_CONTRACT}} | {{INSTRUMENT_STATE}} |
| 产品类别 | {{INST_TYPE_CATEGORY_OR_CHAIN}} | {{PRODUCT_CLASS_STATUS}} |
| 底层映射 | {{UNDERLYING_MAPPING}} | verified={{MAPPING_VERIFIED}} |
| 地区/账户资格 | {{REGION_ACCOUNT_ELIGIBILITY}} | {{ELIGIBILITY_STATUS}} |
| Supervisor 凭据范围 / 外部执行许可 | {{CREDENTIAL_SCOPE}} / {{EXECUTION_PERMISSION}} | {{PERMISSION_BOUNDARY_STATUS}} |
| 数据时间 | {{DATA_TIME_ET}} ET | {{MARKET_DATA_STATUS}} |

当前入场评分：{{ENTRY_SCORE_OR_NOT_PUBLISHED}} / 100（{{ENTRY_SCORE_STATUS}}）

| 因子 | 0–5 | 贡献分 | 关键理由 | EID / freshness |
|---|---:|---:|---|---|
| 技术面（20%） | {{TECHNICAL_SCORE}} | {{TECHNICAL_POINTS}} | {{TECHNICAL_REASON}} | {{TECHNICAL_EID_FRESHNESS}} |
| 资金流（20%） | {{CAPITAL_FLOW_SCORE}} | {{CAPITAL_FLOW_POINTS}} | {{CAPITAL_FLOW_REASON}} | {{CAPITAL_FLOW_EID_FRESHNESS}} |
| 情绪（20%） | {{SENTIMENT_SCORE}} | {{SENTIMENT_POINTS}} | {{SENTIMENT_REASON}} | {{SENTIMENT_EID_FRESHNESS}} |
| 基本面（25%） | {{FUNDAMENTALS_SCORE}} | {{FUNDAMENTALS_POINTS}} | {{FUNDAMENTALS_REASON}} | {{FUNDAMENTALS_EID_FRESHNESS}} |
| 宏观（15%） | {{MACRO_SCORE}} | {{MACRO_POINTS}} | {{MACRO_REASON}} | {{MACRO_EID_FRESHNESS}} |

冲突与置信度：{{ENTRY_CONFLICTS_CONFIDENCE}}

评分编译边界：compiler_effect={{ENTRY_SCORE_COMPILER_EFFECT}}；完整无阻断时不得生成正向 ModuleSignal。

只读监督：

| 状态 | 当前值 | freshness / 对账 | 影响 |
|---|---|---|---|
| 行情 | {{MARKET_STATUS}} | {{MARKET_FRESHNESS}} | {{MARKET_IMPACT}} |
| 策略心跳 | {{STRATEGY_STATUS}} | {{HEARTBEAT_AGE}} | {{STRATEGY_IMPACT}} |
| 持仓 | {{POSITION_STATUS}} | {{POSITION_RECONCILIATION}} | {{POSITION_IMPACT}} |
| 订单/成交 | {{ORDER_FILL_STATUS}} | {{ORDER_RECONCILIATION}} | {{ORDER_IMPACT}} |
| 连接/REST baseline | {{CONNECTION_STATUS}} | {{REST_BASELINE_STATUS}} | {{CONNECTION_IMPACT}} |
| 暂停 | required={{PAUSE_REQUIRED}} | effective={{PAUSE_EFFECTIVE}} | {{PAUSE_REASON}} |

Decision Compiler：{{COMPILED_ACTION}}；entry_permission={{ENTRY_PERMISSION}}；holding_directive={{HOLDING_DIRECTIVE}}；hard gates={{HARD_GATES}}

边界：last-good 值若 stale 必须标 stale，不显示 0；`pause_required` 不等于已暂停；`entry_score` 不替代 Decision Compiler。非下单指令，我没有操作 OKX 或钱包。
```

## Tier 2 · 组合 / 真实持仓分析模板

适用于用户要求“分析我的持仓 / 长桥持仓 / 明日持仓应对 / 组合风险”时。主回复优先用表格，不用逐票长段落。

```markdown
核心结论：{{PORTFOLIO_ONE_LINE_CONCLUSION}}

组合风险仪表盘：

| 指标 | 当前读数 | 判断 | 下一步 |
|---|---:|---|---|
| 净资产 | {{NET_ASSET}} | {{NET_ASSET_JUDGEMENT}} | {{NET_ASSET_ACTION}} |
| 持仓市值 / 净资产 | {{GROSS_TO_NET}} | {{LEVERAGE_JUDGEMENT}} | {{LEVERAGE_ACTION}} |
| 现金 | {{CASH}} | {{CASH_JUDGEMENT}} | {{CASH_ACTION}} |
| 最大单仓 | {{MAX_POSITION_WEIGHT}} | {{CONCENTRATION_JUDGEMENT}} | {{CONCENTRATION_ACTION}} |
| risk_regime | {{RISK_REGIME}} | {{REGIME_JUDGEMENT}} | {{REGIME_ACTION}} |

真实持仓与动作：

| 标的 | 仓位 | 浮盈亏 | 今日/近5日 | 关键风险位 | 动作等级 | 决策 |
|---|---:|---:|---:|---|---|---|
| {{SYMBOL}} | {{WEIGHT}} | {{PNL}} | {{MOMENTUM}} | {{KEY_LEVEL}} | {{ACTION_LEVEL}} | {{DECISION}} |

行动线（优先处理顺序）：
1. {{PRIORITY_1}}
2. {{PRIORITY_2}}
3. {{PRIORITY_3}}

关键依据：
- [{{E1}}] {{EVIDENCE_1}}
- [{{E2}}] {{EVIDENCE_2}}
- [{{E3}}] {{EVIDENCE_3}}

falsifier：{{PORTFOLIO_FALSIFIER}}

数据缺口：{{DATA_GAPS_SHORT}}

验证结果：{{VALIDATION_SHORT}}；write_status: {{WRITE_STATUS}}；completeness: {{COMPLETENESS}}

质量门：readiness_level={{READINESS_LEVEL}}；coverage={{COVERAGE_RATIO}}；critical gaps={{CRITICAL_GAP_COUNT}}；stale_after={{STALE_AFTER}}；must_refresh_if={{MUST_REFRESH_IF}}；research watch triggers={{RESEARCH_WATCH_TRIGGERS_OR_NONE}}；review clock={{REVIEW_CLOCK}}

非下单指令，不构成投资建议；我没有操作券商。
```

## Tier 2 · 多标的速判模板

```markdown
核心结论（多标的速判）：{{BASKET_ONE_LINE_CONCLUSION}}

| 标的 | 当前状态 | 动作等级 | 关键价位 | 风险点 | 明日/下一步 |
|---|---|---|---|---|---|
| {{A}} | {{STATE}} | {{L}} | {{KEY_LEVELS}} | {{RISK}} | {{NEXT_ACTION}} |

行动线：见上表「明日/下一步」列。

关键依据（只展开最重要的 3-5 条）：
- [{{E1}}] {{EVIDENCE_1}}
- [{{E2}}] {{EVIDENCE_2}}
- [{{E3}}] {{EVIDENCE_3}}

falsifier：{{BASKET_FALSIFIER_SHORT}}

数据缺口 / 分歧：{{CONFLICT_OR_GAP_SHORT}}

验证结果：{{VALIDATION_SHORT}}；write_status: {{WRITE_STATUS}}；completeness: {{COMPLETENESS}}

质量门：readiness_level={{READINESS_LEVEL}}；coverage={{COVERAGE_RATIO}}；critical gaps={{CRITICAL_GAP_COUNT}}；stale_after={{STALE_AFTER}}；must_refresh_if={{MUST_REFRESH_IF}}；research watch triggers={{RESEARCH_WATCH_TRIGGERS_OR_NONE}}；review clock={{REVIEW_CLOCK}}

非下单指令，不构成投资建议；我没有操作券商。
```

## 附录触发条件

以下情况才展开完整表格：
- 用户要求“完整报告 / 给我文件 / 审计过程 / 可复盘”。
- 行动等级 L2+ 且冲突很多，需要展示 Conflict Ledger。
- 涉及复杂派生计算，需要展示 Calculation Ledger。
- 涉及多数据源失败，需要展示 Data Gap Ledger。

附录必须包含但不默认展开：
- 执行记录 / commands_run
- Evidence Ledger
- Conflict Ledger
- Decision Compiler payload/result
- Position Cap calculation
- Decision Memory write/read status
