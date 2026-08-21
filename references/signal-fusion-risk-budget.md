# Signal Fusion & Risk-Budget Agreement · 信号融合与风险预算一致门

## 适用与失效

**适用**：≥2 个独立研究信号（因子、主题、结构、宏观 overlay、KOL 线索、期权体制）被要求“综合成一个仓位/方向”时。

**失效**：
- 把相关信号简单相加或平均成更高 conviction → 禁止；记 `calculation_quality`。
- 用知名度/粉丝数抬权重 → 禁止；KOL 仍受 `x_frontline` 封顶。
- 信号冲突时仍放大风险预算 → 必须收缩或 `no_trade`。

机制蒸馏自公开可追溯框架（Asness–Moskowitz–Pedersen 价值×动量条件交叉；Newfound/Hoffstein 等风险贡献与“一致才放大”），**只移植纪律，不移植历史溢价或产品配置**。不新增动作等级。

## 1. 禁止的融合

| 禁止做法 | 原因 | 映射 |
|---|---|---|
| 分数算术平均 / 加权平均抬动作 | 忽略相关性与适用域 | `calculation_quality` |
| 异质 horizon 合成一个方向分 | 违反多周期合同 | `conflict_ledger` |
| 相关线索当独立 EID 灌水 | 重复计算 | `data_quality:source_coverage` |
| 冲突作者观点取中庸加分 | 掩盖失效域 | `conflict_ledger` |

## 2. 条件交叉（AQR-style，非平均）

对可配对的二维信号（例：价值×动量、叙事时钟×价格门、正股 thesis×期权体制）：

1. 先各自在**本域适用条件**下评分，保留缺失为 `unknown`（不得写 0）。
2. 做条件交叉表：记录“信号 A 在 B 的子集是否仍成立”，而不是输出单一均值。
3. 若某一维缺失、过期或与另一维逻辑互斥 → `unresolved_conflict=true`，entry 封顶 BLOCK/WATCH。
4. 历史因子溢价、论文样本均值**不得**写成现行期望收益。

## 3. 一致才放大风险预算（Hoffstein-style）

在组合层（已有 `portfolio_risk_budget`）增加解释性规则：

```text
if signals_agree(domain_set) and no_hard_veto and data_fresh:
    risk_budget_scale ∈ (1.0, scale_cap]   # 仅允许在既有账户容量内解释“可略增研究风险权重”
else if signals_conflict or any_unknown:
    risk_budget_scale ≤ 1.0                # 收缩或维持；冲突时优先 <1.0
else:
    risk_budget_scale = null               # 不发布
```

硬约束：
- `scale_cap` 不得突破 Decision Compiler Cap Registry 与账户门；本门**不能**提高 `entry_permission` 或 action level。
- 等风险/波动目标只作研究投影；缺协方差窗口、融资成本或账户只读数据 → `unknown`，不脑补。
- tilt（组合内倾斜）与 overlay（叠加暴露）必须分列披露；禁止把 overlay 回测 IR 当承诺。

## 4. 融合输出合同

```yaml
fusion_id:
as_of:
horizons_involved: []      # 必须可映射到 multi-horizon-prediction-contract
members:
  - signal_id:
    module:
    reliability:
    freshness:
    applicable_regime:
agreement_state: agree|conflict|partial|unknown
correlation_notes:         # 已知共线/重复计算
risk_budget_scale:         # number|null
cannot_raise_action: true
no_trade_if: []
```

## 5. Compiler 映射

| 条件 | module | 效果 |
|---|---|---|
| 简单平均抬升倍率或动作 | `calculation_quality` | 否决该融合；回退更保守 upstream |
| 冲突仍 `risk_budget_scale>1` | `portfolio_risk_budget` | 强制 scale≤1 或 BLOCK |
| 融合缺 freshness/适用域 | `data_quality` | 最高 L0/WATCH |
| KOL/社媒进融合 | `x_frontline` | 仍受既有封顶；不得因融合解封 |

## 6. 拒绝移植

- AQR/Newfound 回测收益、产品“Return Stacking”配置、任何保证溢价。
- 将名人观点平均成买卖信号。
- 在数据不足时发布融合总分。
