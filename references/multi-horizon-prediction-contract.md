# Multi-Horizon Prediction Contract · 多周期预测合同

## 适用与失效

**适用**：任何把方向、幅度、波动或交易收益写成“预测”的请求——盘中、隔夜、短线、波段、中长期、主题年，或同一回复里混写多个 horizon。

**失效 / 降级**：
- 未声明 `horizon_id` → 最高 `research_hypothesis` / L0，不得进 material Decision Memory。
- 用单一合成分数合并异质 horizon → `conflict_ledger` / `calculation_quality` 收紧，禁止抬动作。
- 用较长 horizon 的正确性为较短 horizon 的执行开脱（或相反）→ 记 Conflict，分别重算。

本文件只定义**可复现字段与隔离纪律**，不新增方向模型、动作等级或仓位上限。既有 E01 scenario（`balder_prediction_E01_multi_horizon_undefined`）与本门一致。

## 1. 强制分账的 horizon 桶

| horizon_id | 典型窗口 | 主要证据层 | 不得直接继承 |
|---|---|---|---|
| `intraday` | 开盘–收盘内 | 短周期结构、近月 gamma、盘口 | overnight vote、主题年叙事 |
| `overnight_cto` | 收盘→次日开盘/09:40 | close-to-open overlay、pre_open_read | 波段基本面、财报季度观点 |
| `swing_days` | 2–20 个交易日 | 事件反应、动量/拥挤、earnings blackout | 单日 GEX 墙位 |
| `position_months` | 数周–数月 | 景气/三时钟、估值、供给冲击 | 09:40 continuity |
| `theme_years` | 多季度–数年 | 叙事机制、类比先验（带 breaks） | 隔夜排序、盘中 flow |

同一标的允许并存多条预测，但**每条必须自带完整合同**；禁止输出一个跨桶“综合胜率”或加权平均方向分。

## 2. 每条预测最小字段

```yaml
prediction_id:
symbol:                    # 含 venue / 证券身份
horizon_id:                # 上表枚举之一；未知则 fail-closed
direction:                 # up|down|neutral|undefined
magnitude_definition:      # 绝对收益 / alpha / touch / range 等，必须可观测
probability:               # 0–1 或 unknown；未知不得写 0
confidence:                # 证据强度，不是胜率承诺
as_of:                     # 数据截止 RFC3339+tz
due_at:                    # 到期结算时点 RFC3339+tz，必须晚于 as_of
drivers: []                # 关键驱动 EID 列表
entry_triggers: []
invalidation: []
scenarios:
  upside: {}
  base: {}
  downside: {}
expected_return_def:
  potential_loss_def:
  payoff_ratio_def:        # 定义即可；缺成本模型不得报可交易 EV
risk_factors: []
no_trade_if: []            # 不满足则明确输出「不交易」
calibration_bucket:        # symbol×horizon×regime×direction×threshold，禁止不同难度混桶
reference_price:           # 事前冻结；缺失时不可自动结算
longbridge_symbol:         # 先过身份解析后冻结；返回身份不一致不得结算
resolution:
  metric: directional_return
  threshold_pct:           # 必须 >0，事前冻结；不得到期后改阈值
consensus:
  consensus_view:
  price_discounts:
  variant_view:
premortem:                 # 至少 3 条，每条含 failure + 可观察 indicator
  - failure:
    indicator:
```

缺失 `horizon_id`、`as_of`、`due_at`、已核验 `longbridge_symbol`、`invalidation`、`no_trade_if`、正阈值可结算 `resolution`、共识/价格贴现检查或 3 条 premortem 任一关键项 → 不得登记为可校准预测，也不得给出可执行建议。`as_of` 不得晚于登记时钟（允许 5 分钟时钟偏差），`due_at` 必须晚于登记时钟；`probability` 必须严格位于 0 与 1 之间。无真实概率就写 `unknown` 并停在研究假设，禁止用 0/1 伪装确定性。

## 3. 信号与校准隔离

- 校准、Brier、命中率、期望值必须按 `horizon_id`（及可选 `regime`）分桶；禁止跨桶合并后宣称“模型胜率”。
- 事后解释帖、收盘后信息解释盘中决策 → 只能进复盘，不得进该 horizon 的命中样本（与财报预测门一致）。
- Overlay（overnight、short-cycle、三时钟、KOL）只能收紧本桶或提高 watch priority，不得用外桶分数复活本桶 L0。
- 校准输出必须逐 `horizon_id` 展示 `n/status/Brier/calibration_gap`；样本少于门槛写 `insufficient_n`。跨 horizon 聚合字段固定为 `null`，不得以总样本量掩盖分桶饥饿。

## 3a. 可执行登记与结算

预测账本复用 Decision Memory 的同一 SQLite，不新建第二个记忆系统：

```bash
python3 scripts/prediction_ledger.py register --payload prediction.json --json
python3 scripts/record_due_results.py --json
python3 scripts/calibration_scorecard.py --source skill --json
```

- `register` 只接受 3 个独立 EID、严格时钟、共识/贴现/差异观点、至少 3 条 premortem 与可观察结算规则；否则 `blocked` 且不落表。
- 到期结算只读行情。返回标的身份必须与已冻结 `longbridge_symbol` 严格一致；行情缺失/异常/身份不一致时保持 `open` 并显式输出，禁止猜 outcome。超过各 horizon 的结算迟延上限且无 point-in-time 报价时转 `unresolved`、排除出校准，禁止拿当前价回填旧 horizon。
- 结果 append-only 写入 `prediction_outcomes`；不得覆盖事前 payload。Decision Compiler 输出与仓位上限不因账本登记或结算自动改变。

## 4. Compiler 映射（tighten-only）

| 条件 | module | 效果 |
|---|---|---|
| 多 horizon 未拆分或共享一个分数 | `research_readiness` / `conflict_ledger` | 最高 L0/WATCH；倍率 0 |
| 短 horizon 执行依赖长 horizon “终局正确” | `quant_robustness` | 封顶 research_hypothesis |
| 预测缺 `no_trade_if` 却给仓位建议 | `data_quality` | 阻断 entry |

不新增 Compiler module。`no_order_execution` 不变。

## 5. 与用户“提高胜率”话术的边界

Skill 只承诺可统计指标：分桶命中率、概率校准、盈亏比定义、最大回撤、样本数、lookahead 检查状态。  
**禁止**承诺、暗示或硬编码“必然提高胜率”。
