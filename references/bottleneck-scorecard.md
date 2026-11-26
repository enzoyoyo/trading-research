# Bottleneck Scorecard · 瓶颈评分卡

> 参考 serenity-skill 公开方法论，整合刘备估值维度。用于量化评估单个标的的研究优先级。

## 概述

对任一标的按 8 个正面因素 + 8 个惩罚因素打分（0-5），加权计算最终得分，输出 4 档研究优先级。这是 Serenity 框架中**最可直接提高胜率**的工具——把定性判断转为可对比的数字。

## 评分维度（8 正面因素，权重合计 100）

| # | 因素 | 权重 | 评分标准（0-5） |
|---|---|---|---|
| 1 | **demand_inflection**<br>需求拐点 | 15 | 0=需求下降；3=稳定增长；5=需求爆发、客户排队抢产能 |
| 2 | **chokepoint_severity**<br>瓶颈严重度 | 15 | 0=无瓶颈；3=偏紧，个别缺货；5=严重短缺，涨价>30%，客户预付款锁产能 |
| 3 | **evidence_quality**<br>证据质量 | 15 | 0=纯概念/传闻；3=研报+公司IR验证；5=SEC/HKEX/年报+客户合同+政府审批文件 |
| 4 | **supplier_concentration**<br>供应商集中度 | 12 | 0=充分竞争；3=3-5家供应商；5=独家或双寡头 |
| 5 | **expansion_difficulty**<br>扩产难度 | 12 | 0=扩产只需3个月；3=需12-18个月+新设备；5=需24月+特殊设备+材料纯度+客户认证 |
| 6 | **valuation_disconnect**<br>估值错配 | 11 | 0=估值已price in全部利好；3=部分低估；5=市场仍按旧业务分类定价，新业务未被认知 |
| 7 | **architecture_coupling**<br>架构耦合度 | 10 | 0=可轻易替换；3=与主流架构绑定但存在替代方案；5=系统架构核心，绕不开 |
| 8 | **catalyst_timing**<br>催化时点 | 10 | 0=无近期催化；3=3-6月内可能有订单/财报/政策催化；5=1-2月内高度确定事件 |

## 惩罚维度（8 项，每项扣分 = 评分 × 2）

| # | 惩罚因素 | 高扣分（4-5分）信号 |
|---|---|---|
| 1 | **dilution_financing**<br>稀释/融资风险 | 大额定增、可转债、ATM、S-3注册、持续烧钱 |
| 2 | **governance**<br>公司治理 | 关联交易、大股东质押高、管理层频繁变动、审计非标 |
| 3 | **geopolitics**<br>地缘政治 | 实体清单、出口管制、制裁、军事终端用户限制 |
| 4 | **liquidity**<br>流动性 | 日成交<1000万、微盘股、港股仙股、无港股通 |
| 5 | **hype_risk**<br>炒作风险 | 社交媒体爆炒、KOL带货、股价不反映基本面 |
| 6 | **accounting_quality**<br>会计质量 | 应收/存货增速>收入增速、经营现金流持续为负、毛利率异常 |
| 7 | **cyclicality**<br>周期性 | 强周期行业在周期顶部、产能过剩拐点临近 |
| 8 | **alternative_design_risk**<br>替代技术风险 | 新技术路线可能绕过现有方案、客户在测试替代供应商 |

## 评分公式

```
正面分 = Σ(因素评分 / 5 × 权重)
惩罚分 = Σ(惩罚评分 × 2)
最终分 = max(0, min(100, 正面分 - 惩罚分))
```

## 判定

| 最终分 | 研究优先级 | 行动 |
|---|---|---|
| ≥ 85 | **Top research priority** | 立即深度调研，优先级最高 |
| 70–84 | **High research priority** | 值得深入，但需补证据 |
| 55–69 | **Worth tracking** | 纳入观察，等催化或证据强化 |
| < 55 | **Early lead / Low priority** | 线索阶段，不投入深度研究 |

## 使用方式

### 手动评分（对话中）
直接按上表逐项打分，给出最终分和判定。适用于快速筛选。

### 脚本评分（批量/可复现）
```bash
# 生成模板
python3 scripts/serenity_scorecard.py --template > candidate.json
# 填好后评分
python3 scripts/serenity_scorecard.py candidate.json --format both
```
脚本位于 `scripts/serenity_scorecard.py`（从 serenity-skill 移植）。

## 关键使用规则

1. **先评层，后评票**：先用评分卡评估产业链层级的瓶颈价值，再对具体标的评分。层排名和票排名是两回事。
2. **证据质量是门槛**：evidence_quality < 3 的标的，即使其他维度高，也不应排到 Top priority。
3. **惩罚项是硬约束**：任一惩罚因素 ≥ 4 分 → 自动降一档研究优先级。两项 ≥ 4 分 → 最多 "Worth tracking"。
4. **定期复评**：每季度财报后重评，评分下降 ≥ 15 分 → 触发降级审查。

## 与现有框架的衔接

- 瓶颈紧张度（5 级，见 `serenity-method.md`）是 `chokepoint_severity` 的定性基础。
- `risk_regime`（见 SKILL.md）覆盖系统性风险，评分卡的惩罚项聚焦个股风险。
- 最终分需与 `decision-cascade.md` 的仓位规则联用：高评分 + active_deleveraging regime → 只观察不行动。

## 相关文件

| 文件 | 关系 |
|---|---|
| `serenity-method.md` | 瓶颈紧张度 5 级定性 → 本评分卡 chokepoint_severity 定量 |
| `evidence-ladder.md` | 评分卡的 evidence_quality 维度以此为准 |
| `expected-returns-framework.md` | 「第二逻辑」高评分标的，对应本卡 architecture_coupling + valuation_disconnect 双高 |
| `counter-consensus-framework.md` | 逆共识标的需通过本评分卡筛选 |
| `decision-cascade.md` | 最终分与仓位规则联用 |
| `scripts/serenity_scorecard.py` | CLI 评分脚本 |
