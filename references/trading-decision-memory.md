# Trading Decision Memory Protocol（长期交易决策记忆）

## 目标

本协议记录的是 **AI 在每次投研报告中给出的判断质量**，不是 真实交易流水。

每次报告都要可追踪：
- 当时为什么给出买入 / 卖出 / 减仓 / 观望 / 回避判断。
- 当时用了哪些证据、因子、假设和风险约束。
- 后续市场是否验证了判断。
- 哪些因子有效，哪些因子误导，哪些条件下应更保守或更积极。

长期目标：让 trading-research 从“会分析”进化为“会记忆、会复盘、会克制、会修正”。

---

## YantrikDB 设计迁移原则

本协议吸收 yantrikdb-server 的记忆架构，但不强依赖 YantrikDB 运行时。完整设计迁移笔记（typed memory / append-only / claims-first / admission control 门槛）见 `references/yantrikdb-memory-transfer.md`。

### 1. Memory is not a log

普通日志只记录发生了什么；交易记忆必须记录 **决策因果链**：

`输入上下文 → 证据账本 → 假设账本 → 冲突裁决 → 仓位上限 → 行动等级 → 后续验证 → 因子归因 → 规则修正`

### 2. Typed memory

对应 YantrikDB 的三类记忆：

| 类型 | 在交易系统中的含义 | 示例 |
|---|---|---|
| episodic | 单次分析决策与结果验证 | 2026-06-06 对 TSLA 给出 L2 建仓，理由是 E1/E3/E30 共振 |
| semantic | 稳定状态/因子/标的画像 | TSLA 近 36 次中追高回撤标签偏多 |
| procedural | 可复用规则与约束 | 低数据覆盖 + 宏观逆风时，仓位倍率不得高于 0.3 |

### 3. Append-only mutation log

原始决策不能被后来的结果覆盖。后续验证、纠错、复盘、权重调整都作为新事件追加。

原因：
- 保留当时认知状态，避免事后诸葛亮。
- 能审计“当时到底怎么想的”。
- 能区分“判断错”与“执行后行情变化”。

### 4. Claims, not vague notes

长期记忆的最小单位不是一句感想，而是结构化 claim：

```json
{
  "claim_id": "auto",
  "decision_id": "DM-20260606-TSLA-abc1",
  "subject": "TSLA",
  "relation": "factor_supported_decision",
  "object": "options_gamma_repair",
  "polarity": "positive",
  "regime": "stress_building",
  "evidence_ids": ["E1", "E3"],
  "confidence": 0.72,
  "valid_from": "2026-06-06T12:00:00+08:00",
  "valid_to": null
}
```

验证失败时，追加反向 claim，而不是修改旧 claim：

```json
{
  "relation": "factor_invalidated_after_review",
  "object": "options_gamma_repair",
  "failure_tags": ["fake_breakout", "entry_too_early"],
  "evidence_ids": ["R1", "R2"]
}
```

### 5. Think loop 需要门槛

YantrikDB 的 Phase 3E 说明：小样本下过早 consolidation 会把相似但不同的实体误合并。交易系统同理：不能因为 1-2 次失败就改策略。

默认复盘门槛：
- window = 最近 36 条已验证决策。
- min_samples = 至少 12 条已验证决策。
- cooldown = 360 分钟。
- pattern_min_count = 同类失败至少 3 次。
- pattern_min_share = 同类失败占样本至少 20%。
- max_adjustment_delta = 单次权重调整最大 15%。

### 6. Temporal decay but no destructive forgetting

历史决策不能删除或被覆盖，但旧样本对下一次判断的影响应自然衰减。

本 skill 的实现：
- 原始统计保留 `win_rate` / `avg_return_pct`。
- 同时输出 `decayed_win_rate` / `decayed_avg_return_pct`，默认 30 天半衰期。
- preflight 降权优先参考衰减加权统计；旧样本仍可审计，但不会永久支配新决策。

### 7. Triggers as review obligations

YantrikDB 的 trigger 概念在交易系统中映射为：
- `pending_decision_review`：旧 AI 决策到达 review clock。
- `pattern_recommendation`：滚动复盘发现明确失败模式或因子失效。
- `defensive_state`：近期连续失败，下一轮分析默认禁止 Level 3。

这些 trigger 是下一次分析前的约束，不是事后日志。

---

## 每次分析前：Memory Preflight

在 Evidence 前或 Decision 前必须跑：

```bash
python3 scripts/trading_memory.py preflight \
  --symbol <SYMBOL> \
  --market <A|HK|US> \
  --direction <long|short|watch|reduce|avoid> \
  --json
```

如果标的未解析完成，可以先跳过 symbol 精确项，但必须在报告中写明记忆检索缺口。

Preflight 返回：
- 最近同标的表现。
- 同方向表现。
- 常见失败标签。
- 因子降权建议。
- 数据覆盖门槛调整。
- 是否进入 defensive state。
- 待验证的旧决策。
- 本次建议扣分/倍率。

### Preflight 对决策的强制影响

| 记忆信号 | 决策影响 |
|---|---|
| 同标的近期持续拖累 | 候选分扣 0.3-1.0；进攻权重降级 |
| 同方向近期低胜率 | direction_multiplier 降到 0.6-0.85 |
| 低覆盖失败偏多 | data_quality_multiplier 上限 0.7；关键缺口时最高观察 |
| 快速亏损偏多 | entry_timing_score 扣分；追价过滤更严格 |
| 假突破偏多 | 必须等待二次确认/回踩确认 |
| 新闻逆风/资金流逆风偏多 | 社媒/新闻线索不得单独提高仓位 |
| 过早止盈偏多 | 提高分批/追踪止盈优先级，减少一次性离场 |
| 观望错过机会偏多 | 检查保守过滤条件，但不能直接放宽数据门槛 |
| 连续失败或近期胜率过低 | attack_state = defensive，禁止 Level 3 |

---

## 第一层：Decision Memory（每次分析决策记忆）

每次投研报告输出后，必须记录一条 decision memory。

命令：

```bash
python3 scripts/trading_memory.py record-decision --payload decision.json --json
```

最低字段：

```json
{
  "decision_id": "可省略，脚本自动生成",
  "analysis_time": "ISO-8601",
  "agent": "Hermes|Claude|Codex",
  "skill_version": "v10.2",
  "strategy_version": "trading-research-memory-v1",
  "symbol": "TSLA",
  "market": "US",
  "longbridge_symbol": "TSLA.US",
  "direction": "long|short|watch|reduce|avoid",
  "action_level": "L0|L1|L2|L3|L4|L5",
  "action_label": "观察|试错|建仓|加仓|减仓/止盈|回避",
  "price_at_decision": 248.5,
  "position_cap_pct": 22.0,
  "risk_level": "low|medium|high|extreme",
  "attack_state": "aggressive|neutral|defensive",
  "manual_aggressive_profile": "none|user_specified",
  "scores": {
    "total": 3.2,
    "data_coverage": 0.86,
    "professional_score": 0.72,
    "market_quality": 0.64,
    "entry_timing": 0.58,
    "fund_flow": 0.61,
    "execution_risk": 0.35
  },
  "confluence_count": 4,
  "candidate_source": "user_query|screen|watchlist|scanner|manual",
  "reasons": ["站回 Put Wall", "财报仍验证收入增速"],
  "factors": {
    "atr_pct": 4.8,
    "trend_strength": 0.62,
    "sentiment_score": 0.57,
    "positioning_change": "neutral_to_bullish",
    "news_impact": 0.2,
    "smart_money_change": 0.1,
    "model_score": 0.68,
    "estimated_ev": 0.11,
    "estimated_win_rate": 0.57,
    "estimated_rr": 1.9,
    "position_scale": 0.73,
    "attribution_summary": "技术和财报支持，宏观压仓位"
  },
  "evidence_ids": ["E1", "E3", "E7", "E30"],
  "hypothesis_ids": ["H1"],
  "conflict_ids": ["C1"],
  "data_gaps": ["期权数据延迟 15 分钟"],
  "falsifier": "跌破 Put Wall 且两个交易日未收回",
  "review_clock": "2026-06-13T16:00:00+08:00",
  "report_path": null,
  "notes": "非下单；记录 AI 决策质量"
}
```

### 校准必填字段（否则决策无法被自动验证）

`record-decision` 不会拒绝缺字段的决策（绝不丢掉真实决策），但缺了下面两个，这笔决策就进不了校准、也无法被自动复盘，`record-decision` 输出会标 `completeness=partial`、`completeness_missing` 列出缺什么：

- **`factors.estimated_win_rate`（0–1）**：这笔判断你自己估的胜率。没有它，校准记分卡（loop B）没法把"嘴上胜率"和"实际结果"配对，Brier 永远算不出来。
- **`price_at_decision`**：决策当时的参考价（顶层或 `factors` 内均可）。没有它，`record_due_results.py` 到期时算不出收益、无法自动判 success/failure，只能丢给人工。

任何 L1 及以上可执行决策都必须带齐这两个字段，做到 `completeness=full`。L0 纯观望可只带 `estimated_win_rate`（观望对错由 `record_due_results` 留 neutral、人工判定）。

### 多周期预测登记（v2.56）

Decision Memory 继续记录“最终判断”，Prediction Ledger 记录“可结算概率”。二者复用同一个 SQLite，但一条 decision 可关联多条不同 horizon 的 prediction，禁止把多个 horizon 塞进一个 decision result：

```bash
python3 scripts/prediction_ledger.py register --payload prediction.json --json
python3 scripts/record_due_results.py --json
python3 scripts/calibration_scorecard.py --source skill --json
```

- 登记合同以 `multi-horizon-prediction-contract.md` 为准；缺时钟、结算规则、3 EID、共识/价格贴现、3 条 premortem 或 `no_trade_if` 时不落表。
- 到期行情不可得时保持 open，绝不生成 outcome；结算是 append-only。
- scorecard 逐 horizon / calibration bucket 输出，小样本写 `insufficient_n`，跨 horizon 聚合固定禁用。
- 账本、结算与校准均 advisory；不能直接改 soft weight、Compiler cap、动作等级或仓位。

### 记录纪律

- L0 观察也要记录；观望是否正确本身就是决策质量。
- 若数据覆盖不足导致 L0，`valid_for_review=false` 可由脚本或 agent 标注，但仍保留事件。
- 不记录 是否真实买卖、实际仓位、账户盈亏。
- 如果后来发现报告中数据有误，用 `record-result` 或新 decision 修正；不要覆盖旧 decision。

### 外部来源溯源 admission（v2.39）

外部原文、方法论蒸馏或言行交叉验证先过 `scripts/provenance_guard.py`；契约见 `references/source-grounded-research-provenance.md`。

- `verified`：只有 `memory_link.accepted_claim_ids` 可作为稳定关联；仍必须有正常 canonical EIDs，并保留原有 `evidence_ids/hypothesis_ids/conflict_ids`，provenance 本身不授予写入资格。
- `partial`：只记作非 material watch/research note，固定 `materiality_eligible=false`、`valid_for_review=false`，不进入 calibration 或 procedural memory consolidation；可留 accepted stable IDs 供人工刷新，但不得形成 durable claim。
- `blocked`：`accepted_claim_ids` 必须为空；最多附加 DataGap、`rejected_claim_ids`、`rejection_sha256` 与 bundle hash，不得复制其 analysis/framework ID 列表为已接受事实。
- 只存 stable IDs 与 `provenance_bundle_sha256`；raw source、长引文和许可受限正文留在独立 artifact，不复制进 memory DB。
- 安装/校验 KOL 卡不得自动写 Decision、Hypothesis Registry 或 Event Memory。Hypothesis 只能来自未来显式 frozen experiment；Event Memory 必须有真实 event payload，文章 publication header、图表/dashboard row 均不满足。
- 没有显式 `factors.estimated_win_rate` 与二元 validated outcome 的成对样本，不得计算 Brier；KOL 自报收益永不成为 probability 或 outcome。
- 后续证伪仍走 append-only：追加反向 claim 或 correction event，不覆盖原 bundle/decision。
- 该接入复用现有 `payload_json`，不新增数据库表、第二套记忆或交易执行路径。

---

## 第二层：Result Validation（结果验证与离场记忆）

到达 review clock、触发 falsifier、止损/止盈条件、或用户再次询问同标的时，必须验证旧决策。`review_clock` 必须是带时区的 ISO 8601/RFC3339（例如 `2026-06-13T16:00:00+08:00`），禁止写 `after US close`、`HK close` 等自然语言。`record-decision` 会把合法值归一化为 UTC；`valid_for_review=true` 且缺失/不可解析/无时区时拒绝写入。仅 `valid_for_review=false` 的即时校准或非复盘记录可省略。

### 到期自动验证（默认路径）

到了 review_clock 还没结果的决策，由 `scripts/record_due_results.py` 自动收口：只读拉 LongBridge 现价、对比决策时记录的 `price_at_decision`、按方向算收益、判 success/failure（小于 deadband 记 neutral；watch/avoid 一律 neutral 留人工），再走同一套 append-only 写回；同一入口还会结算到期 Prediction Ledger 条目。它**只读行情、绝不下单**，缺价格或行情的对象只会保持 pending/列入人工清单，绝不臆测；历史非 ISO 时间会进入 `invalid_review_clocks`，不得静默跳过。SourceDocument 的 `published_at/retrieved_at/as_of` 或文章标题日期不能替代 decision `review_clock` / prediction `due_at`。

```bash
python3 scripts/record_due_results.py --json          # 验证所有到期决策
python3 scripts/record_due_results.py --dry-run --json # 只看会验证哪些，不写
```

每日 cron 在守护检查后跑一次它，把"决策→结果"这一环自动补齐——这正是校准记分卡有数据可算的前提。

### 手动验证（提前离场 / 改判）

止损、止盈、falsifier 触发、或方向改判这类**提前离场**仍走手动 record-result（带准确的 exit_type 与 failure_tags），不要等到期：

```bash
python3 scripts/trading_memory.py record-result \
  --decision-id <DECISION_ID> \
  --payload result.json \
  --json
```

最低字段：

```json
{
  "validation_time": "ISO-8601",
  "price_at_decision": 100.0,
  "price_at_validation": 108.0,
  "return_pct": 8.0,
  "max_favorable_excursion_pct": 12.0,
  "max_adverse_excursion_pct": -3.2,
  "stop_hit": false,
  "target_hit": true,
  "falsifier_hit": false,
  "expired_early": false,
  "exit_type": "partial_take_profit|trailing_take_profit|stop_loss|timeout_exit|weak_exit|active_reduce|risk_release_exit|thesis_invalidated|watch_correct|watch_missed|avoid_correct|avoid_missed",
  "outcome": "success|failure|mixed|neutral",
  "failure_tags": [],
  "effective_factors": ["earnings_validation", "gamma_repair"],
  "ineffective_factors": ["social_sentiment"],
  "factor_notes": "新闻热度贡献小，真正有效的是财报验证 + 期权结构修复",
  "review_evidence_ids": ["R1", "R2"],
  "notes": "验证 AI 判断，不代表真实交易"
}
```

失败不能只写“错了”。必须打标签。

### Failure Tag Taxonomy

| 标签 | 含义 |
|---|---|
| quick_loss | 快速亏损 |
| chase_reversal | 追高后回撤 |
| fake_breakout | 假突破 |
| flow_against | 资金流逆风 |
| news_against | 新闻/公告逆风 |
| smart_money_against | 聪明钱逆风 |
| model_signal_against | 模型信号逆风 |
| low_coverage_loss | 低数据覆盖亏损 |
| execution_risk_high | 执行风险过高 |
| entry_too_early | 入场过早 |
| direction_invalidated | 方向判断失效 |
| over_optimistic | 过度乐观 |
| over_conservative | 过度保守 |
| take_profit_too_early | 止盈过早 |
| risk_warning_insufficient | 风险提示不足 |
| timeout_failure | 超时失败 |
| watch_missed_opportunity | 观望错过机会 |
| avoid_missed_opportunity | 回避错过机会 |
| symbol_drag | 标的持续拖累 |
| regime_misread | regime 误判 |
| data_gap_mispriced | 数据缺口被低估 |

---

## 第三层：Rolling Review（滚动复盘记忆）

默认命令：

```bash
python3 scripts/trading_memory.py review \
  --window 36 \
  --min-samples 12 \
  --cooldown-minutes 360 \
  --json
```

统计内容：
- 最近总收益表现、原始胜率、30 天半衰期衰减胜率与衰减平均收益。
- 买入/建仓/加仓建议成功率（`action_success_rates.buy_build_add`）。
- 卖出/减仓/做空建议成功率（`action_success_rates.sell_reduce_short`）。
- 观望/回避准确率（`action_success_rates.watch_avoid`）。
- 多空方向表现（`long_short_performance`）。
- 拖累最大的交易标的（`biggest_drag_symbols`）。
- 失败最多的 exit_type。
- 最常见 failure_tags。
- 最常见 data_gaps。
- 表现最稳定的 factors（`stable_factors`）。
- 失效频率最高的 factors（`degrading_factors`）。

System A 侧模拟仓的反事实归因（missed_signals/noise_trades/early_exit/late_exit/overtrading 五分解）与行为偏差诊断词汇表见 `references/paper-portfolio-analysis-playbook.md`；本层只引用其标签喂 `learning_digest`，不重复定义口径。

### 复盘动作建议

| 模式 | 动作 |
|---|---|
| low_coverage_loss 偏多 | 提高数据覆盖门槛；缺关键数据时最高 L0-L1 |
| quick_loss 偏多 | 加强追价过滤，要求入场缓冲 |
| timeout_failure 偏多 | 弱方向更早减仓/退出 |
| symbol_drag 集中 | 下调该标的候选权重和进攻权重 |
| long/short 某侧持续失败 | 降低该方向 multiplier |
| flow_against / smart_money_against 频繁 | 降低资金流/聪明钱相关因子可信度 |
| take_profit_too_early 偏多 | 优化分批止盈和持仓规则 |
| watch_missed_opportunity 偏多 | 复查过度保守过滤条件，但不得取消证据门槛 |

### 防过拟合规则

系统调整必须同时满足：
1. 有效样本 ≥ min_samples。
2. 距离上次 review ≥ cooldown。
3. 同类模式 count ≥ pattern_min_count。
4. 同类模式 share ≥ pattern_min_share。
5. 调整只影响下一轮决策倍率，不重写历史记录。
6. 单次权重调整幅度不得超过 max_adjustment_delta。

---

## 第四层：Feedback Into Next Decision（反哺下一次分析）

每次报告的仓位上限计算器新增一项：

`× 长期记忆倍率（聚合字段名 memory_multiplier）= symbol_multiplier × direction_multiplier × data_gap_multiplier × factor_multiplier × defensive_state_multiplier`

默认取值：1.0（`memory_multiplier = 1.0` 表示无历史调整）。只有 `preflight` 返回明确建议时才调整。

### 必须写入报告的字段

每份投研都必须完成 decision memory preflight/record；但主回复只写一行状态，完整表格进文件或附录：

```markdown
Decision Memory：preflight={{STATUS}}；memory_multiplier={{X}}；decision_id={{DM_ID}}；write_status={{WRITE_STATUS}}；completeness={{full|partial}}
```

如未成功写入，需要明确写：`memory_write_status=failed` 与原因；不能假装已记录。
`completeness=partial` 时同样要如实写出缺了哪个字段（`estimated_win_rate` / `price_at_decision`），并说明为什么这笔决策这次进不了校准——不能粉饰成 full。

---

## CLI 快速工作流

### 1. 分析前读取记忆

```bash
python3 scripts/trading_memory.py preflight --symbol TSLA --market US --direction long --json
```

### 2. 报告后记录决策

```bash
python3 scripts/trading_memory.py record-decision --payload /tmp/decision.json --json
```

### 3. 到期查看待验证决策

```bash
python3 scripts/trading_memory.py pending --json
```

### 4. 记录验证结果

```bash
python3 scripts/trading_memory.py record-result --decision-id DM-... --payload /tmp/result.json --json
```

### 5. 滚动复盘

```bash
python3 scripts/trading_memory.py review --window 36 --min-samples 12 --cooldown-minutes 360 --json
```

### 6. 认知循环 / 触发器

```bash
python3 scripts/trading_memory.py think --window 36 --min-samples 12 --cooldown-minutes 360 --json
```

`think` 输出 pending reviews、rolling review、pattern triggers 和 stats。它遵守同样的样本门槛与冷却时间，不因为一两次失败修改策略。

### 7. 看系统状态

```bash
python3 scripts/trading_memory.py stats --json
```

---

## 存储位置

默认 SQLite 路径：

`~/.cache/hermes/trading-research/memory/trading_memory.sqlite`

可用环境变量覆盖：

`TRADING_MEMORY_DB=/path/to/trading_memory.sqlite`

原则：运行态数据不进入 skill 目录，避免污染 skill 仓库与 symlink 源。


## Paper Calibration Bucket

System A paper-trading outcomes can be translated into an isolated calibration bucket via `scripts/paper_outcome_calibration_feed.py`. Boundary:

- Read-only: reads `~/.hermes/longbridge-paper-trading/journal/paper_outcomes.jsonl`, `paper_orders.jsonl`, plus read-only proposal/decision packet JSON for explicit prediction fields; never modifies System A.
- Separate bucket: samples are stored in `calibration_samples_paper` with `source='paper'`, not mixed into the skill decision/result chain.
- No invented probabilities: outcomes without an explicit predicted-probability field are skipped as `no_prediction_field`; open predicted trades are reported as `open_not_closed_yet` until a closed outcome exists.
- Reference only: `scripts/calibration_scorecard.py --source paper` emits Brier/reliability reads with `materiality_eligible=false`; paper samples never trigger sizing, position caps, or `calibration_materiality`.
- Skill bucket remains authoritative for method materiality: use `--source skill` for self-calibration gates.


### Pending Paper Predictions

`paper_outcome_calibration_feed.py` also records explicit predicted open proposals into `calibration_pending_paper_predictions`. These rows are not calibration samples; they are an audit queue showing which paper predictions have probabilities but are still waiting for a closed outcome. `calibration_scorecard.py --source paper` reports `paper_pending_predictions` so a zero-sample paper bucket can distinguish “no predictions exist” from “predictions exist but are not closed yet”.
