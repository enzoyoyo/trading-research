# YantrikDB → Trading Research Memory Transfer

## 调研对象

- Repo: https://github.com/yantrikos/yantrikdb-server
- Local clone: `/tmp/trading-research-memory-upgrade/repo`
- Inspected commit: `564e4dc`
- Scale snapshot from pygount: 438 files, Rust 140 files / 30,954 code lines, Python 105 files / 14,882 code lines, Markdown 73 files.

## 可迁移的核心设计

### 1. Memory is not a log

YantrikDB 的核心区别不是“把东西存起来”，而是把记忆做成认知系统：记录、召回、遗忘、纠错、合并、冲突检测、触发器。

迁移到交易系统时，交易记忆也不能只是盈亏流水。它必须记录：

`输入上下文 → 证据账本 → 假设账本 → 冲突裁决 → 仓位上限 → 行动等级 → 后续验证 → 因子归因 → 规则修正`

### 2. Typed memory

YantrikDB 区分 episodic / semantic / procedural。交易系统映射如下：

| YantrikDB 类型 | 交易记忆含义 | 本 skill 实现 |
|---|---|---|
| episodic | 单次 AI 投研判断和后续验证 | `decisions` + `results` |
| semantic | 标的、方向、因子在一段时间内的稳定画像 | `review_stats` / `symbol_memory` / `direction_memory` |
| procedural | 可复用的交易约束与降权规则 | `last_adjustments` / `review.recommendations` |

### 3. Append-only mutation log

YantrikDB 的 forget/correct 通过 tombstone / correction 保留历史。交易记忆同理：原始 decision 不能被后续行情覆盖。

本 skill 采用：

- `memory_events`：append-only 事件流。
- `decisions`：冻结当时 AI 判断。
- `results`：追加后续验证。
- `reviews`：追加滚动复盘。
- `claims`：追加因子有效/失效 claim。

不要 silent edit 原始判断；需要纠错时追加 result/review/correction 事件。

### 4. Claims-first，而不是模糊笔记

RFC 006 的关键思想：冲突检测的基本单位是 claim，不是笼统 edge 或 prose。

交易系统中的 claim 示例：

```json
{
  "subject": "TSLA",
  "relation": "factor_supported_decision",
  "object": "gamma_repair",
  "polarity": "positive",
  "regime": "stress_building",
  "evidence_ids": ["E1", "E3"],
  "confidence": 0.72
}
```

验证失败时追加反向 claim：

```json
{
  "relation": "factor_invalidated_after_review",
  "object": "gamma_repair",
  "polarity": "negative",
  "failure_tags": ["fake_breakout", "entry_too_early"]
}
```

这样系统长期能知道“哪个因子在哪种 regime 下失效”，而不是只知道“某次判断错了”。

### 5. Think loop with admission control

YantrikDB 的 `think()` 做 consolidation / conflict scan / pattern mining / triggers，但 repo 文档也警告小样本过早 consolidation 会伤害质量。交易系统必须有 admission control：

- `window=36`
- `min_samples=12`
- `cooldown_minutes=360`
- `pattern_min_count=3`
- `pattern_min_share=0.20`
- `max_adjustment_delta=0.15`

未满足门槛时，只记录、不改权重。

### 6. Temporal decay，不等于删除历史

YantrikDB 的记忆会随时间衰减。交易系统不应该删除旧决策，但下一次分析时应降低过旧验证的影响。

本 skill 实现方式：

- review 统计保留原始 `win_rate` / `avg_return_pct`。
- 同时输出 `decayed_win_rate` / `decayed_avg_return_pct`，默认 30 天半衰期。
- preflight 的同标的/同方向降权优先参考衰减加权统计。

### 7. Triggers and proactive review

YantrikDB 会 surface pending triggers。交易系统映射为：

- 到期旧决策：`pending --due-only`。
- 复盘后模式建议：`review.recommendations`。
- 认知循环：`think` 命令统一输出 pending reviews、rolling review、pattern triggers、stats。

下一次同标的分析前，如果存在 due review，应先验证旧判断，再让新判断吃到 memory feedback。

### 8. Local-first, no external dependency

YantrikDB 可以用 MCP / HTTP / 集群，但本 skill 不依赖外部 YantrikDB 运行时。原因：

- 交易分析需要马上可用，不能要求额外服务。
- 本阶段核心是结构化审计与反馈回路，不是向量检索。
- SQLite + JSON 能满足本地、可备份、可验证、无密钥的需求。

未来如果 YantrikDB MCP 成为稳定依赖，可把 `claims` / `decisions` 同步为外部 cognitive memory，但当前默认只用本地 SQLite。

## 不采纳内容

- 不接入真实券商下单。
- 不记录 是否真实交易、真实仓位、账户盈亏。
- 不用一两次失败自动重写策略。
- 不把所有历史硬塞进 prompt；只通过 preflight 召回相关摘要。
- 不把运行态 SQLite 写进 skill 目录。
