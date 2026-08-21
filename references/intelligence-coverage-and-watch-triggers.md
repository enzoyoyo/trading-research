# Intelligence Coverage Matrix & Research Watch Triggers

## 目的

把“来源能不能提供这类数据”“本次有没有拿到数据”“多个标的是否获得公平覆盖”“什么变化需要重新研究”拆成可审计状态，避免三类常见错误：

1. 把 `unsupported/auth_missing/rate_limited` 误写成公司没有事件或指标为零。
2. 多标的研究被热门股票或单一 provider 占满，其他标的在证据不足时仍给动作结论。
3. `must_refresh_if` 只写成人话，没有期限、冷却、重置与明确的重新研究动作。

本层只进入 `data_quality`、Evidence/DataGap/SourceHealth 和 review clock；不新增动作等级，不直接形成方向或仓位，不创建 cron、邮件、LongBridge alert 或订单。

## 触发条件

- 多标的、组合、watchlist、行业篮子或候选池研究。
- 同一标的需要 quote / filing / news / fundamentals / technicals / attention 等多维数据。
- 国际市场、跨交易所或免费/付费 provider 支持范围不一致。
- 主源失败后启用 delayed/cached/fallback 来源。
- 用户要求“持续盯”“突破后复核”“财报出来再判断”“数据恢复后提醒”。

## 三层状态必须分开

### 1. Capability state

来源对 `market × instrument × dimension` 的能力：

- `live`：支持实时或当前有效数据。
- `delayed`：明确延迟，必须记录 delay、`observed_at/stale_after` 与 `fallback_level`。
- `cached`：只有缓存值，必须记录 `observed_at/stale_after` 与 `fallback_level`。
- `unsupported`：来源本身不覆盖该市场或维度。
- `auth_missing`：能力可能存在，但当前没有可用授权。
- `rate_limited`：当前被 403/429/配额门限制。
- `error`：请求、解析或上游故障。
- `unknown`：尚未验证，不得按 supported 处理。

### 2. Coverage state

本次研究是否拿到可用数据：

- `covered_live`
- `covered_reference`：delayed/cached，或独立来源门槛需要 2 个但只有部分 provider family 为 live；只能作参考。
- `stale`
- `unsupported`
- `unavailable`
- `missing`

`unsupported/unavailable/missing/stale` 都是 DataGap，不是零值、负面基本面或“无新闻”。

### 3. Evidence posture

即使 coverage 通过，原始数据仍需按 Evidence 契约记录来源、时间、直接性、可靠性和 claim。Coverage 只证明“拿到了什么”，不证明“内容是真的”或“方向正确”。

## IntelligenceCoverageMatrix

```yaml
schema_version: intelligence_coverage.v1
as_of: "RFC3339"
targets: ["AAPL.US", "MSFT.US"]
requirements:
  - dimension: quote
    criticality: high
    min_independent_sources: 1
  - dimension: filing
    criticality: high
    min_independent_sources: 1
  - dimension: news
    criticality: medium
    min_independent_sources: 2
observations:
  - target: AAPL.US
    dimension: quote
    source: longbridge_quote
    provider_family: longbridge
    status: live
    observed_at: "RFC3339"
    stale_after: "RFC3339"
    data_present: true
    fallback_level: T1
```

编译输出必须包含：

- 每个 `target × dimension` 的 coverage state。
- 可用且真正独立的 provider family 数。
- fallback lineage。
- `coverage_ratio` 和 high/medium/low DataGap。
- 对既有 `data_quality` 的 tighten-only 建议。
- 公平采集队列 `fair_collection_queue`。
- `no_order_execution=true`。
- `coverage_id=ICOV-*`，以及可直接进入 strict `decision_request.v2` 的 `suggested_module_signal.evidence_refs/observed_at/stale_after`。Signal 默认最多存活 15 分钟，且不得晚于任一可用上游 observation 的最早 `stale_after`。
- OKX 研究必须把 `instrument_identity`、`market_data`、`account`、`positions`、`orders`、`fills`、`strategy_heartbeat` 和 `rest_reconciliation` 分成独立 dimension；一个 dimension live 不能替其他 dimension 解锁执行许可。
- feed 失败时 observation 保留 `last_good_value`，但 coverage state 必须为 `stale/unavailable`；last good 只供展示与对账，不能伪装当前值。

## 公平采集队列

借鉴 OpenStock 对 watchlist 新闻的分配意图，但不复制其固定“最多 6 条”实现：

1. 先在全局按 `criticality=high → medium → low` 分层，预算不足时低优先级不得抢占高优先级位置。
2. 每个 criticality 层内再按标的 round-robin；同一标的内部按 dimension 稳定排序。
3. 热度、已有新闻数量或市值不能让单一标的连续占满预算。
4. 已覆盖维度不进入缺口队列；同优先级 stale/unavailable/missing 按稳定顺序处理。
5. 队列只决定下一步抓什么，不决定买卖。

## CrossSourceAttentionMatrix

当 Reddit/X/新闻/预测市场/交易数据同时出现时，每个 source row 必须保留：

- `provider_family`
- `metric_name`、定义和量纲
- `sample_window`
- `sample_size` 或明确缺口
- `observed_at/stale_after`
- `attention_state`
- `credibility_state`
- `source_reliability_ceiling`

只有定义、量纲和采样窗可比时，才能报告 spread/dispersion。不得直接平均：

- mentions
- article count
- trade count
- buzz score
- bullish percentage
- prediction-market probability

来源分歧进入 Conflict Ledger 或提高 verification priority；共识最多提高 attention，不自动提高 credibility、action level 或 position cap。

## ResearchWatchTrigger

把 `must_refresh_if` 编译为通知候选，不执行外部动作：

```yaml
schema_version: research_watch_trigger.v1
trigger_id: "stable-id"
symbol: AAPL.US
trigger_type: price_cross | filing_event | event_window | source_stale | data_gap_recovered | conflict_resolved
condition: above | below | cross_up | cross_down | occurs | becomes_stale | recovers
threshold: 0
reference_value: 0
created_at: "RFC3339"
expires_at: "RFC3339"
cooldown_seconds: 3600
rearm_rule: manual | recross | after_cooldown | never
one_shot: true
evidence_refs: ["E1"]
on_trigger: rerun_research
no_order_execution: true
```

纪律：

- 结构化候选必须先通过 `scripts/research_watch_trigger.py`；该 validator 使用字段 allowlist、动作 allowlist、RFC3339 expiry、cooldown/rearm/evidence refs 和 `no_order_execution=true` fail-closed，输出 `research_watch_trigger_compilation.v1` 本地重研计划。
- 默认只是报告字段或本地计划，不自动创建 cron/邮件/平台 alert。
- `on_trigger` 只能是 `rerun_research`、`refresh_source` 或 `request_manual_review`。
- 触发后必须刷新 live facts 并重新走 Decision Compiler；不得沿用旧动作。
- 缺 `expires_at`、cooldown、rearm rule 或 evidence refs 的触发器只能写成非结构化观察项。
- 任何 `place_order/buy/sell/submit_order` 都是非法值。
- OKX 相关 trigger 仍只能使用现有 `source_stale/data_gap_recovered/conflict_resolved` 等研究触发类型；策略暂停、撤单和急停属于独立 Supervisor/Execution Engine，不能塞进 ResearchWatchTrigger。

## Data-quality 收紧映射

- 任一 high-criticality 维度 stale/unsupported/unavailable/missing：建议 `max_action_level=L0`、`position_multiplier=0.0`。
- 无 high gap，但有 medium gap 或独立来源不足：最高 `L1`，multiplier 不高于 `0.5`。
- 只有 delayed/cached reference coverage：最高 `L2`，multiplier 不高于 `0.75`。
- 全部 live 且独立性过门：本层保持中性，`cannot_raise_upstream=true`；`L3/1.0` 只是“不额外收紧”，不得提高上游 action 或 position cap。

## OpenStock 采纳矩阵

| OpenStock 机制 | 当前 skill | 处理 | 原因 |
|---|---|---|---|
| Finnhub/TradingView 市场能力说明 | 有静态 source matrix，缺本次运行 coverage compiler | 采纳并重构 | 能区分 unsupported 与 no-event |
| watchlist 新闻分配 | 多标的模板已有，缺公平抓取队列 | 采纳意图，独立实现 | 防单一热门票垄断研究预算 |
| Adanos 多源标准化/分歧 | attention/credibility 已有 | 适配字段 | 保留来源差异，拒绝不可比均值 |
| price alert condition/expiry/one-shot | 有 `must_refresh_if/review_clock` | 适配为 ResearchWatchTrigger | 只触发重研，不做外部副作用 |
| Gemini→MiniMax/Siray fallback | Hermes/live-intel 已覆盖 | 不新增 router | 只补 fallback provenance/DataGap |
| 静态 ticker→TradingView map | 已有 security resolver | 拒绝作为身份真值 | 静态映射可能过时或同名冲突 |
| Mongo watchlist/Better Auth/UI | 不属于研究链 | 拒绝 | 增维护与安全面，无研究增益 |
| Inngest 5 分钟扫描/邮件 | skill 不改 cron | 拒绝 | 副作用、扩展边界、依赖脆弱 |
| AI HTML 摘要/个性化文案 | 证据层要求 claim 可追踪 | 拒绝 | 生成文本不是事实证据 |
| 0 价格和通用新闻 fallback | DataGap 契约已有 | 明确反模式 | 缺失不能伪装成零或标的专属信息 |

## 许可证与安全边界

OpenStock 固定研究提交为 `4597c9a668118844b588f95eddb9342eed31c41d`，AGPL-3.0。trading-research 不复制其 TypeScript、Prompt、UI、Schema 或测试；这里只做清洁室方法论抽象和独立 Python 实现。完整来源、哈希、质量和供应链结论见 `source-map.md`。
