# 多因子证据综合框架（Multi-Factor Evidence Synthesis）

> 目标：把分散的 EID 证据系统化合成为统一评分，杜绝"我觉得该涨"式的苍蝇式决策。

## 五维评分矩阵（每个 0-5 分）

| 维度 | 权重 | A 股 | 港股 | 美股 | 评分依据 |
|---|---|---|---|---|---|
| 技术面 | 20% | K线位置/量价/均线/支撑压力/涨跌停 | K线/量价/均线/流动性槽 | K线/量价/均线/Gamma墙位置 | 处于历史分位的评估 |
| 资金面 | 20% | 资金流/龙虎榜/北向/机构持仓 | 南向/成交额/沽空比例/CCASS | 期权流/13F/insider/大宗交易 | 资金流入流出趋势 |
| 情绪面 | 20% | 涨停池/连板高度/炸板率/散户活跃度 | 新闻情绪/AH折溢价/南向意愿 | X社媒/Put-Call ratio/IV/恐惧贪婪 | 情绪水位高低 |
| 基本面 | 25% | 营收/利润/ROE/毛利率/现金流/估值/股息质量 | 营收/利润/ROE/股息率/FCF覆盖/估值/NTA | revenue/EPS/FCF/capex/margins/guidance/dividend coverage | 财务数字是否验证叙事；分红按 `dividend-quality-framework.md` 检查收益陷阱 |
| 宏观面 | 15% | 政策/监管/信贷/利率/产业周期 | 美元利率/联系汇率/内地政策/流动性 | Fed/liquidity/DXY/real yield/inflation | 宏观逆风还是顺风 |

## 综合评分与行动等级映射

| 综合评分 | 行动等级 | 含义 |
|---|---|---|
| 4.0-5.0 | L2-L3 建仓/加仓 | 多因子收敛共振，可执行 |
| 3.0-3.9 | L1 试错 | 有选择地介入，严格控制仓位 |
| 2.0-2.9 | L0 观察 | 等待更多证据或关键位确认 |
| 0.0-1.9 | L4-L5 减仓/回避 | 多因子背离，退出或远离 |

## 评分方法

每个维度取该维度下所有 EID 的方向加权平均：
- Bull EID 贡献正值（按 reliability × strength 加权）
- Bear EID 贡献负值（按 reliability × strength 加权）
- 最终归一化到 0-5 区间（0=极其看空，5=极其看多，2.5=中性）

## 交叉验证纪律

3 步法确保不偏倚：
1. **任意单一维度得分不超过 2.5 的极点差**（即最乐观 4.5 vs 最悲观 2.0 差距 > 2.5 时触发冲突裁决）
2. **基本面与情绪面不能同时超过 3.5 但结论是看空**（或相反）——这是 narrative-trap 或绝望底信号，需要特别说明
3. **最低维度自动成为最高风险来源**，必须在报告中显式标注它对最终结论的稀释作用

## Regime Override（杀杠杆优先于静态打分）

五维评分只能告诉你“这只票本身怎么样”，不能单独回答“系统现在允不允许你承担风险”。

当以下杀杠杆信号出现时，必须用 `risk_regime` 覆盖静态高分：

**A. 流动性挤兑层**（见 `deleveraging-liquidity-squeeze-playbook.md`）
- VIX 暴涨
- 黄金与指数同跌
- Apple / AAPL 暴跌
- 长端美债利率暴涨
- Skew 极度左偏
- 指数 GEX 出逃 / 变脆

**B. 离散度回归层**（见 `leverage-crowding-dispersion-playbook.md`，跑 `dispersion_crowding.py`）
- COR1M 地量（≤5% 分位）= 离散度极端、结构性埋雷 → 至少 `deleveraging_watch`
- COR1M 单日 ≥+15% / 5 日 ≥+30% = 相关性正在回归 1 → 按 `active_deleveraging`/`forced_liquidation`
- VIXEQ−VIX 溢价 ≥80% 分位 = 单股投机/杠杆过热 → 至少 `stress_building`
- 杠杆单票 ETF 左尾（跌停无对冲窗口）/ 监管硬性强制卖盘 → 单独升级左尾警戒

覆盖规则：
- `stress_building`：综合高分仍可做，但总仓先打 7 折。
- `deleveraging_watch`：综合分再高也默认禁止 L3 加仓。
- `active_deleveraging`：综合分只能支持“以后值得跟踪”，不能直接支持“现在重仓”。
- `forced_liquidation`：静态高分只保留为中线观察结论，短线动作降到观察/减仓/回避。

一句话：**先过 regime gate，再看 composite score。**

## `entry_score.v1` 展示层（v2.48）

需要回答“现在是否具备入场条件”时，可用 `scripts/entry_score.py` 把既有证据压缩为五组固定权重展示分：`technical=20%`、`capital_flow=20%`、`sentiment=20%`、`fundamentals=25%`、`macro=15%`。公式为 `Σ(score_0_5 × canonical_weight × 20)`。

边界：

- 五组只是既有 module/evidence 的报告投影，不新增 Compiler module、动作等级或独立 alpha 模型。
- 每组和 `product_identity` 必须有理由/身份字段、EID、`observed_at/stale_after`；任一缺失或过期，`entry_score_100=null`。
- material conflict 可保留展示分用于解释，但必须 `unresolved_conflict=true`、`entry_permission_ceiling=BLOCK`，并交给 `conflict_ledger` 阻断 entry。
- 完整且无冲突/资格 blocker 时，`entry_permission_ceiling=null`、`suggested_module_signals=[]`、`compiler_effect=none`；高分本身不产生正向 ModuleSignal。
- 分数区间只描述 readiness；最终动作、仓位和已有持仓处置仍由 `scripts/decision_compiler.py` 统一裁决。
- OKX tokenized stock 还必须额外通过产品身份、wrapper、账户资格、价差/深度和账实对账门；高分不能覆盖这些 hard gate。
