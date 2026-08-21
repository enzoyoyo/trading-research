# Mira Quality Gates · 从 byteseek/Mira 吸收的无冲突研究质量覆盖层

## 迁移边界

本文件只迁移 Mira 的研究质量契约，不迁移 Mira Mode、人格、唤醒词、自更新脚本、完整 case 工作区或 Yahoo/SEC/BLS 数据源优先级。

在 `trading-research` 中：
- Longbridge / AkShare / CBOE / WindClaw / Hermes Grok 仍是主数据层。
- L0-L5 与 Decision Compiler 仍是唯一动作裁判。
- Mira 概念只作为证据、计算、时效、readiness 的前置 gate，不直接输出买卖指令。

## 触发条件

在以下场景运行本覆盖层：
- `standard` / `deep_dive` 正式投研报告。
- 用户问“能不能买/加/减/冲/抄底/仓位上限”。
- 结论依赖财报、估值、同比/环比/CAGR、peer ranking、Gamma/结构派生数值。
- 使用用户提供的 PDF、截图、研报、表格、券商/机构材料、portfolio export 或 vendor/API payload。
- 复用旧结论、历史记忆或旧报告给当前动作判断。

`quick_map` 可只输出摘要，但仍必须内部检查：证据是否当前有效、是否有不可知变量、是否需要刷新。

## 1. Depth Surface

| depth_mode | 使用场景 | 输出纪律 |
|---|---|---|
| `quick_map` | “看一下”、早期方向判断 | 少露字段，但不能省略证据边界、刷新条件、动作边界 |
| `standard` | 正式个股/行业/宏观研究 | 主回复保持简洁；Evidence Ledger、Decision Compiler、readiness 与 refresh 可放附录/文件 |
| `deep_dive` | 长期 thesis、复杂估值、财报/SEC/组合复盘 | 允许额外 artifact：calculation ledger、claim map、filing provenance、postmortem |

禁止因为用户要短答就跳过关键取数；也禁止因为模板完整就把 quick_map 变成臃肿报告。

## 2. Knowability Gate

在花更多研究预算前先判断：更多研究是否能改变结论。

| 字段 | 取值 |
|---|---|
| `information_value` | `low` / `medium` / `high` |
| `knowability_status` | `knowable` / `partially_knowable` / `unknowable_now` / `irreducible_uncertainty` |
| `depth_override_reason` | 为什么升级/降级研究深度 |

规则：
- `unknowable_now` 或 `irreducible_uncertainty` 是合法终态，不强行给方向。
- 输出应降级为 `watch_only` / `needs_refresh` / `no_action`，并给出可观察刷新条件。
- 不可知变量包括：未披露监管决定、未发布财报、无法复核的传闻、还没发生的宏观事件、缺失的期权/账户约束。

## 3. Evidence Posture（不替代 S/A/B/C/D）

S/A/B/C/D 管“来源可靠性”；Evidence Posture 管“这条 claim 能不能支撑当前结论”。两者并行，不能互相替代。

建议在 EvidenceItem 中增加：

```json
{
  "claim_type": "fact | reported_metric | guidance | forecast | assumption | opinion | market_pricing | derived_calculation | rumor_signal",
  "source_speaker": "company | management | regulator | exchange | market | sellside | social | agent",
  "verification_status": "verified | disclosed | claimed | estimated | modeled | unverified | contradicted",
  "evidence_category": "verified_fact | reported_fact | company_statement | management_guidance | market_pricing | modeled_scenario | assumption | inference | estimate | weak_signal | stale | contradicted | unknown",
  "freshness_status": "current | acceptable_for_period | preliminary | stale | unknown",
  "conflict_status": "none | unresolved | contradicted | not_checked",
  "treatment": "use_normally | attribute | sensitize | haircut | source_gap | monitor | exclude | open_item",
  "readiness_impact": "supports_durable_conclusion | supports_working_view | monitoring_only | blocks_actionability | not_material"
}
```

硬规则：
- `market_pricing` 只能说明市场如何定价，不能证明基本面为真。
- `company_statement` / `management_guidance` 是预期输入，不是已经兑现的事实。
- `assumption` / `inference` / `estimate` 必须有上游 EID 或公式。
- `weak_signal` / X/Grok / 社媒不得单独支撑 L1+ 或提高仓位。
- `stale` / `contradicted` / `unknown` 默认进入 DataGap 或 Conflict Ledger，不能支撑 durable conclusion。
- KOL Method Card 不是新的 Evidence Posture：它必须回链同一 `research_provenance.v1`。公开材料不完整最高 `partial` / L1；blocked 或 subscriber-only 内容若被用于生命周期结论则 `blocked` / L0；自报收益永不提高 reliability ceiling。

## 4. Calculation Ledger Trigger

只有 agent/脚本自己派生、且影响判断的数字，才强制进入 calculation ledger 或公式说明。

| 数字来源 | 处理 |
|---|---|
| 公司/交易所/监管/Longbridge 直接披露的指标 | `reported_metric`，无需 calculation ledger，但要标来源与期间 |
| agent 计算的同比/环比/CAGR、相对收益、peer rank、估值隐含、Gamma 解释、仓位倍率 | `derived_calculation`，必须有公式、输入来源、交叉校验或 `calculation_ref` |
| 无法复算但影响动作的数字 | `calculation_gap`，最高动作降级 |

派生计算不能因为“看起来合理”就进入仓位计算器。

## 5. Ingestion Boundary

用户材料、研报、截图、PDF、表格、portfolio export、vendor/API payload 先经过摄入判断，再进入证据。

最小字段：

```json
{
  "ingestion_route": "user_file | public_api | web_read | vendor_export | portfolio_export | manual_note",
  "license_scope": "public | user_provided | paid_restricted | vendor_restricted | unknown",
  "storage_scope": "transient | private | tracked_allowed",
  "redistribution_allowed": "yes | no | derived_only | unknown",
  "evidence_log_mapping": "which EID/claim uses this material",
  "calculation_ledger_required": "yes | no"
}
```

规则：
- 付费/受限/用户私有材料默认只能支持 private working view；公开报告只写短摘和影响，不复制长段原文。
- 账户、持仓、vendor 原始数据不进 skill 目录，不写入公开模板。
- 工具输出不是 evidence；必须转成 claim-level EID 后才能影响结论。

## 6. Research Readiness Gate

Readiness 只限制结论可用性，不替代 L0-L5。

| readiness_level | 含义 | 对动作的默认上限 |
|---|---|---|
| `draft` | 研究对象/来源计划明确但证据未完成 | L0 |
| `working_view` | 有方向性但关键 claim 仍弱/缺口未关 | 最高 L1 |
| `research_ready` | durable conclusion 可追溯，量化/冲突处理完成 | 可进入 Decision Compiler |
| `actionable_with_caveats` | 可作为动作语境，但仍有 caveat/invalidation | 可 L2/L3，但必须有 caveats |
| `watch_only` | 有价值但不足以行动 | L0/L1 |
| `research_hypothesis` | 研究假设类结论，未经充分证据/回测验证 | 最高 L1，仅供研究，不可行动 |
| `not_actionable` | 核心证据缺失、矛盾、过期或越界 | L0 |
| `needs_refresh` | 旧结论过期，需刷新后才可用 | L0 |

Decision Compiler 应把 `readiness_level` 作为 `data_quality` 或 `research_readiness` module_signal（注意：`research_ready` 是 readiness_level 取值，`research_readiness` 是 module 名）：
- `draft/not_actionable/needs_refresh` → `max_action_level=L0`
- `working_view/watch_only` → `max_action_level=L1`
- `research_ready/actionable_with_caveats` → 不自动提高动作，只允许其它模块正常裁决

## 7. Refresh Contract

每个正式结论必须给出：

```yaml
stale_after: "YYYY-MM-DD 或 事件窗口"
must_refresh_if:
  - "财报/业绩会/监管公告发布"
  - "关键价位/Gamma/Put Wall 失效"
  - "宏观四象限切换"
  - "核心 falsifier 或 disconfirming evidence 出现"
```

`review_clock` 是主动复盘节奏；`must_refresh_if` 是结论失效触发器。两者不能混用。

## 8. Report Interpretation / Filing / Earnings 触发

- 用户提供研报/卖方报告：先做 claim map，拆目标价、估值假设、关键变量、偏见/激励，再交叉验证。
- 美股 filing 关键问题：SEC/IR/Longbridge 交叉；记录 report period、filing date、section/tag/unit/frame。
- 财报事件：拆 actual disclosure、management explanation、guidance、market reaction、agent derived calculation；价格反应不能单独解释为 thesis change。

## 9. 输出最小检查

正式报告结束前检查：
- 核心判断是否能回链到 EID。
- EID 是否有 claim_type / evidence_category / freshness_status / readiness_impact。
- 影响动作的派生计算是否有 formula 或 calculation_ref。
- readiness_level 是否支持当前动作等级。
- stale_after / must_refresh_if 是否具体。
- X/Grok 或弱线索是否只作为 clue，未单独提高仓位。
