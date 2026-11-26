# Factor Deployment Playbook · 因子消费与部署纪律

## 1. 定位与总边界

本 playbook 只规定因子研究产物如何被盘前、盘中和交易建议流程消费。它不是新交易系统，不新增 module、动作等级或百分制总分；Decision Compiler 仍是唯一动作权威。

部署前置门：横截面因子宇宙至少 30 只，建议 50+ 以覆盖 null 与 warm-up 损耗；8 只票 watchlist 按设计不产出 rank，`ranking_unavailable` 是正确行为，不是故障。

- 因子状态以 `hypothesis_registry` 为准，只有 `confirmed_alive` 可进入排序 vote。
- `premarket_screen.py` 只输出 watch priority，固定 `position_multiplier=0.0`。
- 因子证据统一使用 `quant_robustness`；风险旗标只生成 tighten-only `suggested_module_signals`。
- 任一候选仍需刷新事实、经过对应市场流程并重新编译；排序不能复活 L0、提高 action level 或扩大 position cap。
- 不跨 horizon 合成胜率、方向分或仓位理由。

## 2. US 盘前线：ranker 的一个 vote 成分

US 线使用：

```text
premarket_screen.py --market US
  → factor_cross_section_vote
  → Overnight Ensemble Ranker
  → US Close-to-Open Execution Overlay
  → Decision Compiler
```

### 2.1 成分纪律

- 只有 registry 中 `confirmed_alive` 的因子进入 vote；`train_only`、`noise`、`reversed_strict` 和未登记因子只展示，不计票。
- `confirmed_alive` 的统计前提是 h 日收益按 `stride=h` 的固定 phase 非重叠截面计算，且 `effective_sections>=min_dates`；旧 run 缺 `effective_sections` 时先重跑，不沿用旧显著性。
- `n_factors_scanned` 只披露因子×horizon 的真实检验数；`alpha_t=3.5` 是固定门，不是随本次族规模调整的多重检验校正。
- 单因子票为 `sign(zscore × direction)`，跨因子求整数 vote；纪律与 ranker 的动量/相对强度成分相同。
- null 不补 0；单票 null 因子数 `>=3` 时只保留 watch 行，不进入排序头部。
- 默认有效横截面至少 30 只；即使面板文件都存在，只要历史足够、能形成有效因子值的标的少于 `min_cross_section`，输出 `insufficient_cross_section_ranking_unavailable` 并清空全部 rank。
- `|vote| < conviction_floor` 为 `no_edge`，不进入 head set；floor 是方差过滤器，不是已验证盈利门槛。
- 因子成分不得主导；权重进入 `calibration_scorecard.py` 的 regime/方向/结果分桶，用事后样本自调。样本不足时保持低先验，不能手工抬权。
- ranker 与 screen 自身均为排序层，`position_multiplier=0.0`；头部候选仍逐一经过下游 overlay 与 Compiler。
- 收盘竞价流只有 15:50 ET 后才可能完整；次日 pre-open read 最晚观察到 09:40 ET。时间窗只属于本 US 小节。

## 3. A 股盘前线：四步编排

A 股流程独立于 US 排序与执行制度，不借用其他市场的时间窗或字段语义。

### 步骤 1：市场情绪与风险底图

运行 `a_share_sentiment_cycle.py` 取得上一交易日的情绪阶段、涨跌停生态、板块梯队与资金流状态；并运行 `risk_regime_snapshot.py` 取得风险环境。缺失或冲突必须进入 `data_gaps`，不能填 0。

### 步骤 2：目标交易日新鲜度门

运行 `data_freshness_guard.py`，对齐目标交易日、面板 as-of 与各证据时间：

- `blocked`：禁止排序，输出数据质量 tighten-only 信号。
- `degraded`：只允许 watch，不得解释为入场许可。
- `ok`：只代表数据时点可用，不代表可交易。

### 步骤 3：逐票补证

对候选使用 `a_stock_data_bridge.py quote/fund-flow` 与 `fundamental_snapshot.py --market A` 补齐行情、资金流和基本面证据。少量逐票调用不得改成 LongBridge 批量历史取数；空结果必须保留 provider gap。

### 步骤 4：生成 watch priority

运行 `premarket_screen.py --sentiment-file <path>`，输出因子得分、整数 vote、风险旗标和 `watch_priority_rank`。结果进入 Tier 2 多标的速判模板：

- “明日/下一步”列只能写观察优先级、刷新条件与触发条件。
- 不回答“明天买什么”，不把 rank 解释为买入顺序。
- 逐票行动必须回到 Tier 1/2 证据链并由 Decision Compiler 裁决。
- screen 固定 `position_multiplier=0.0`，风险信号只能收紧。
- 默认 `--min-cross-section 30`；研究小样本不得靠降低该门伪装成可部署排序。

### 3.1 Phase A 统计与回测局限

- A 股涨跌停探测统一按 9.5% 阈值，暂未识别 300/688 的 20% 或 ST 的 5%；`limit_move_days` 是 `(symbol, epoch)` 次数。
- 分位分箱对并列因子值按 symbol 字典序切开，低基数 spread 可能由并列切分驱动。
- 印花税 `sell` 仅作用于卖出侧 turnover；`both` 才作用于双边总 turnover。
- walk-forward 必须有至少 3 个 OOS `test.periods`，否则 `missing_walk_forward` 且仅 `hypothesis_only`。
- 默认集合面板抓 800 自然日；最低交易行数须满足 `window ≥ min_dates×h + warmup + h`，运行时还叠加 30% 排除率门。低于修正后的 `required_rows` 返回 `panel_window_too_short_for_factor`，不得解释为因子无效。
- `range_pos_252` 已从 `--factors all` 摘出；仅在显式长窗（至少约 1300 自然日）研究时请求，不能出现在默认推荐配置中。
- `ranking_status` 保留单值兼容；诊断必须同时读取 `ranking_blockers[]`，避免无 confirmed、截面不足与 freshness 阻断并发时被单值掩盖。

## 4. 盘中辅助：独立预测、五路融合、三类触发器

### 4.1 独立 horizon

盘中结论必须登记为 `horizon_id=intraday` 的独立预测，并完整填写 `multi-horizon-prediction-contract.md` 的全部字段，包括 identity、`as_of`、`due_at`、reference price、resolution、drivers、entry_triggers、invalidation、scenarios、risk_factors、`no_trade_if`、calibration bucket、consensus 与至少三条 premortem。不得把盘中因子读数转移到 overnight、swing 或长期桶，也不得合成跨周期胜率。

### 4.2 五路 fusion

盘中只按 `signal-fusion-risk-budget.md` 汇总以下五路：

1. 行情：价格、成交与市场结构。
2. 资金流：可核验的逐票/板块资金状态。
3. 情绪：A 股情绪周期或 US 已冻结上游状态。
4. 技术：独立、可复算的技术条件。
5. 因子：registry 为 `confirmed_alive` 且与当前 horizon 对齐的读数。

输出必须使用既有合同的 `agreement_state`，不得做算术平均、加权平均或单一方向总分：

- `agree`：只说明条件一致，仍不得抬高上游动作或倍率。
- `partial`：`risk_budget_scale<=1`，减少或等待。
- `conflict`：`no_trade` 或人工复核；禁止用高因子分抵消情绪/资金反证。
- `unknown`：数据缺口，输出 watch 或 BLOCK。

US 盘中只消费已冻结的上游结论与现有 short-cycle 软组件；本阶段不改 overlay 代码。因子软权重若要进入该层，必须先走 EOD 至少 12 个样本的 advisory 校准通道。

### 4.3 ResearchWatchTrigger 三模板

所有候选先过 `scripts/research_watch_trigger.py`；触发后只允许刷新事实并重新编译。

```yaml
schema_version: research_watch_trigger.v1
trigger_id: rwt-price-cross-example
symbol: 600000.SH
trigger_type: price_cross
condition: cross_up
threshold: 10.50
reference_value: 10.20
created_at: "2026-08-21T01:00:00Z"
expires_at: "2026-08-22T07:00:00Z"
cooldown_seconds: 3600
rearm_rule: recross
one_shot: false
evidence_refs: [EID-PRICE-PLAN]
on_trigger: rerun_research
no_order_execution: true
```

```yaml
schema_version: research_watch_trigger.v1
trigger_id: rwt-source-stale-example
symbol: 600000.SH
trigger_type: source_stale
condition: becomes_stale
threshold: 0
reference_value: 0
created_at: "2026-08-21T01:00:00Z"
expires_at: "2026-08-22T07:00:00Z"
cooldown_seconds: 1800
rearm_rule: after_cooldown
one_shot: false
evidence_refs: [EID-FLOW-SOURCE]
on_trigger: refresh_source
no_order_execution: true
```

```yaml
schema_version: research_watch_trigger.v1
trigger_id: rwt-gap-recovered-example
symbol: 600000.SH
trigger_type: data_gap_recovered
condition: recovers
threshold: 0
reference_value: 0
created_at: "2026-08-21T01:00:00Z"
expires_at: "2026-08-22T07:00:00Z"
cooldown_seconds: 3600
rearm_rule: manual
one_shot: true
evidence_refs: [EID-DATA-GAP]
on_trigger: request_manual_review
no_order_execution: true
```

触发器不得创建订单、cron、邮件或外部 alert；不得直接改变 action level、entry permission 或 position cap。

## 5. 交易建议三件套

| 要素 | 宿主字段 | 纪律 |
|---|---|---|
| 买入区间/卖出区间 | 决策链 `plan.entry_triggers / invalidation / no_trade_if / review_clock`；Tier 2“关键价位”列；每个价位带 `calculation_ref` | 区间是 compiled action 之下的计划属性；无触发、无失效条件就不交易 |
| 预期收益空间 | `scenarios.upside/base/downside`、`magnitude_definition`、`expected_return_def`、`potential_loss_def`、`payoff_ratio_def`；可结算项登记到 `prediction_ledger.py` | 缺成本模型不报可交易 EV；未过两道门的回测数字最高 `research_hypothesis` |
| 置信度 | `confidence`、严格 `0<p<1` 或 `unknown` 的 `probability`、`readiness_level`；事后由 `calibration_scorecard.py` 分桶 | 置信度不是仓位公式；不输出跨 horizon 综合胜率 |

## 6. Worked example：A 股单票从筛选到结算

以下只演示合同，不构成真实股票建议，所有数值均为示例字段。

### 6.1 筛选

`premarket_screen.py` 对 `600000` 输出：

```json
{
  "symbol": "600000",
  "vote": 2,
  "watch_priority_rank": 1,
  "edge_status": "edge",
  "risk_flags": ["sentiment_phase=repair"]
}
```

含义仅为“先研究”，倍率仍为 0。随后补齐行情、资金流、基本面、风险环境与反证。

### 6.2 Tier 1 编译

构造既有 module signals 后调用 Decision Compiler。假设编译结果为 `L1/WATCH`、`final_position_multiplier=0.0`，则任何因子高票都不能把它改成入场；输出继续等待预声明触发。

### 6.3 三件套输出

```yaml
plan:
  entry_triggers:
    - "收盘确认站上 10 日高点；calculation_ref=max(close[-10:])"
  invalidation:
    - "收盘跌破前低；calculation_ref=min(low[-5:])"
  no_trade_if:
    - "资金流证据过期或情绪状态转为退潮"
  review_clock: "next_trading_day_close"
scenarios:
  upside: "+6% within 10 trading days"
  base: "+1% to +3% within 10 trading days"
  downside: "-4% invalidation"
magnitude_definition: "close-to-close return from frozen reference_price"
expected_return_def: "probability-weighted scenario return after explicit costs; unknown until probabilities are calibrated"
potential_loss_def: "reference_price to invalidation including estimated costs"
payoff_ratio_def: "expected_return_def / potential_loss_def"
confidence: "L1 evidence strength"
probability: "unknown"
readiness_level: "working_view"
```

由于示例编译结果仍是 WATCH，区间只作为后续计划，不产生交易许可。

### 6.4 次日结算

若已形成可结算预测，先用 `prediction_ledger.py register` 冻结 `reference_price`、正阈值 resolution、`due_at=<next_trading_day_close>` 与 `horizon_id`。次日到期后按顺序执行：

```bash
python3 scripts/prediction_ledger.py register --payload prediction.json --json
python3 scripts/record_due_results.py --json
python3 scripts/calibration_scorecard.py --source skill --json
```

行情缺失、身份不一致或无法取得 point-in-time 报价时保持 open/unresolved，禁止拿当前价回填。不得因单次命中提高因子权重，也不得改写原预测。

## 7. 生命周期与检查清单

```text
factor run → factor verdict → hypothesis registry
  → premarket screen → evidence refresh → Decision Compiler
  → prediction register（如可结算）→ due result → calibration
```

交付前确认：

- registry 只有 `confirmed_alive` 因子进入 vote。
- 无 confirmed 时明确 `no_confirmed_factors_ranking_unavailable`。
- null 未补 0，`>=3` null 的标的只留 watch 行。
- 无 `entry_score_100` 或其他百分制合成分。
- 所有 suggested signals 都是 tighten-only，且 `position_multiplier=0.0`。
- 每个市场与每个 horizon 独立处理，最终动作只来自 Decision Compiler。
