# MiroFish Swarm Simulation Patterns · 群体智能情景沙盘

## 目的

将 MiroFish 的「ontology-first 图谱建模 + 多 Agent 群体模拟 + 报告证据日志」方法，降维吸收到 `trading-research` 的复杂事件研究中，用于生成**情景先验**和**观察优先级**，而不是生成事实证据或交易动作。

本文件只迁移方法论，不迁移代码、依赖、服务、Prompt 原文或运行时。

## Source anchor

- Source: https://github.com/666ghj/MiroFish
- Reviewed commit: `96096ea0ff42b1a30cbc41a1560b8c91090f9968` (2026-05-25)
- Repository description: A Simple and Universal Swarm Intelligence Engine, Predicting Anything.
- License: AGPL-3.0
- Adoption mode: methodology reference only; no code copied; no dependency introduced.

## 适用场景

仅在问题不是单一价格/财报事实，而是复杂系统反应时启用：

- 政策、监管、诉讼、地缘事件可能如何扩散；
- 消费者、散户、媒体、监管、管理层等多方如何反应；
- 供应链/产业链冲击如何沿实体关系传播；
- 题材股、概念板块、AI/半导体链条的叙事传播路径；
- 需要把「谁影响谁」「什么变量驱动什么反应」先结构化，再决定要抓哪些真实证据。

不适用于：单纯行情、财报快照、期权墙位、账户仓位、真实订单、已可由 LongBridge/公告/交易所材料直接验证的问题。

## MiroFish 可迁移机制

| MiroFish 机制 | trading-research 吸收方式 | 决策用途 |
|---|---|---|
| Ontology-first | 先定义实体、关系、事件变量，再抓证据 | 降低复杂事件漏项 |
| Knowledge graph / entity-edge view | 把政策、公司、供应链、人群、平台、资产映射成图 | 帮助构建 Evidence/Hypothesis Ledger |
| Swarm simulation | 把群体反应当情景沙盘，而不是事实 | 形成 scenario_prior / watch_priority |
| ReportAgent 分章节检索 | 正式报告每章必须有证据/缺口/冲突 | 增强 Mira Quality Gates |
| InsightForge 式子问题拆解 | 把复杂问题拆成多个可验证子问题 | 指导 Grok/Web/LongBridge/公告抓取 |
| PanoramaSearch 式全貌视图 | 区分当前有效事实、历史事实、过期事实 | 防止拿旧叙事当新事实 |
| Action log / simulation_trace | 保留每轮假设、动作、输出和限制 | 保证模拟可审计 |

## 严格不迁移内容

- 不复制 MiroFish 代码、类、函数、Prompt 原文或前后端实现。
- 不引入 Flask、React/Vite、Zep Cloud、camel-oasis、camel-ai、OASIS runner 等依赖。
- 不接入 MiroFish 的 `simulation_runner`、IPC、长期进程、后台线程或 Web API。
- 不把模拟 Agent 采访当作真实投资者、消费者、媒体或监管方证据。
- 不把模拟结果写入 `verified_fact`、`financial_validation`、`trading_confirmation`。
- 不用模拟结果提高 `action_level`、`position_multiplier`、`position_cap`。
- 不新增 cron 自动模拟，不让自优化闭环根据模拟输出自动改 skill。
- 不替代 LongBridge、公告/财报、交易所/监管、公司原始材料、Mira Quality Gates 或 Decision Compiler。

## 输出契约：ModeledScenarioSignal

MiroFish 模式的任何输出都必须降级为 `modeled_scenario`。它是模型生成的情景观察，不是事实。

```yaml
modeled_scenario_signal:
  source_project: "MiroFish"
  source_license: "AGPL-3.0"
  adoption_mode: "methodology_only_no_code_copied"
  scenario_question: "本次要推演的复杂事件/市场反应"
  ontology:
    entity_types: []
    edge_types: []
    scenario_variables: []
  simulation_trace:
    assumptions: []
    agent_groups: []
    rounds_observed: null
    synthetic_observations: []
    action_log_summary: []
  evidence_status:
    claim_type: "assumption"
    evidence_category: "modeled_scenario"
    verification_status: "modeled"
    readiness_impact: "monitoring_only"
  allowed_use:
    - "hypothesis_ledger"
    - "scenario_prior"
    - "watch_priority"
    - "risk_watchlist"
    - "evidence_collection_plan"
  forbidden_use:
    - "verified_fact"
    - "financial_validation"
    - "trading_confirmation"
    - "position_sizing"
    - "order_execution"
  module_signal:
    module: "modeled_scenario"
    max_action_level: "L0"
    position_multiplier: 0.0
    hard_veto: false
    reason: "synthetic modeled scenario; requires real-world evidence before any action"
```

## Decision Compiler 映射

`modeled_scenario` 与 `prediction_market_prior`、`grok_web`、`x_frontline` 同属发现/先验层，但比它们更弱：

- `modeled_scenario` 最高用途是 `scenario_prior` 或 `watch_priority`；
- 默认 `max_action_level=L0`；
- 默认 `position_multiplier=0.0`；
- 只有当后续被 LongBridge、公告/财报、原始新闻、交易所/监管材料、真实行情/期权/资金数据独立验证后，才可以把对应事实转入其它模块；
- 转入其它模块时，必须保留原始 EID 与 cross-check，不得把模拟文本本身当 EID。

## 工作流

1. **Define scenario**：用一句话定义要推演的事件变量，避免“预测万物”。
2. **Build ontology**：列出实体类型、关系类型、变量、时间窗口。
3. **Separate facts vs assumptions**：已有真实事实进 Evidence Ledger；未知反应进 Hypothesis Ledger。
4. **Draft modeled scenario**：只产出可能路径、关键参与者、风险分支。
5. **Generate evidence collection plan**：把每条模拟观察翻译成需要抓取的真实证据。
6. **Compile as L0**：进入 Decision Compiler 时只作为 `modeled_scenario`，不得提高动作等级。
7. **Upgrade only after verification**：真实证据补齐后，才由对应模块重新裁决。

## 质量门

启用本层时必须写明：

- `scenario_question` 是否具体；
- 参与实体是否覆盖关键利益相关方；
- 哪些是事实，哪些是模型假设；
- 哪些结论需要真实数据验证；
- 模拟是否可能受 prompt framing、agent profile、样本缺失影响；
- 输出是否仍保持 `position_multiplier=0.0`；
- 是否有任何句子把模拟结果写成事实，如有必须降级或删除。

## 典型错误

1. **把模拟当事实**：Agent 说了什么不等于市场/消费者真实这么想。
2. **把热闹叙事当交易信号**：群体反应强只能提高观察优先级，不能买入。
3. **复制 AGPL 代码**：只允许方法论总结，不允许代码/Prompt 原文迁移。
4. **替代真实数据源**：模拟不能替代 LongBridge、公告、财报、交易所/监管材料。
5. **隐性提高仓位**：如果模拟让报告语气更乐观，但 `position_multiplier` 没有保持 0，就是冲突。
6. **自动化过度**：不得接 cron 自动模拟或自改 skill；需要用户明确要求才做一次性研究。

## 最小输出示例

```markdown
Modeled Scenario Layer:
- scenario_question: 某监管事件是否会引发 AI 供应链风险偏好下降？
- ontology: regulator / hyperscaler / supplier / investor / media；关系为 guidance、order、sentiment、fund_flow。
- modeled_observation: 投资者风险偏好可能先压缩高估值远期现金流标的，再传导到供应链 beta。
- evidence_collection_plan: 回抓监管原文、公司订单/指引、LongBridge 行情横截面、期权 IV/Gamma、X frontline 线索。
- compiler_mapping: modeled_scenario → max_action_level=L0, position_multiplier=0.0。
```

## 与现有模块的关系

- 与 `Mira Quality Gates`：本层默认 `readiness_impact=monitoring_only`，不能成为 actionable 结论。
- 与 `Decision Compiler`：只作为 L0 先验；真实动作必须由其它模块重新裁决。
- 与 `Open-Source Quant Research Patterns`：同属外部开源项目方法论吸收，但本层不是量化回测，不进入 Quant Robustness Gate。
- 与 `Grok/X`：Grok/X 可以用于发现真实线索；MiroFish-style 模拟只能提出待验证问题。
- 与 `System A`：不得把 modeled scenario 直接推给模拟仓执行；只能作为人工/研究层 watchlist 输入。
