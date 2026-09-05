# 事件侧击：扣除共同市场与已知传导后的价格偏离

入口：`scripts/event_dislocation.py`；测试：`scripts/test_event_dislocation.py`。这是“事件发生后，关联公司是否相对冻结统计模型反应不同”的实算模块，不是估值模型。最高 `hypothesis_only`，固定 `no_order_execution=true`、`compiler_isolated=true`。不使用 `event_reaction_journal.py` 缺 beta 时的 1.0 fallback，不写订单、仓位许可、目标价或主观胜率。

## 输入与调用

```bash
python3 scripts/event_dislocation.py --input event-input.json --output event-result.json
python3 scripts/event_dislocation.py --self-test
```

Python 为 `analyze(payload)`，输出 `event_dislocation.v1`。CLI 0 表示有研究结果（可能 discovery_only），2 表示 blocked，绝不表示允许交易。

```json
{
  "as_of": "2026-09-05T03:30:00Z",
  "retrospective_only": true,
  "event": {
    "event_id": "an_explicit_event",
    "issuer": "ISSUER.US",
    "published_at": "2026-08-19T10:45:00Z",
    "known_at": "2026-09-05T03:30:00Z",
    "source_ref": "original_publication_or_evidence_reference"
  },
  "benchmark": "SPY.US",
  "candidate": {
    "symbol": "TARGET.US",
    "relationship": {
      "kind": "co_development",
      "issuer": "ISSUER.US",
      "target": "TARGET.US",
      "source_ref": "reviewed_relationship_evidence",
      "known_at": "2026-09-05T03:30:00Z",
      "description": "Explain the documented economic relationship."
    }
  },
  "rule": {
    "rule_id": "research_rule_created_today",
    "declared_at": "2026-09-05T03:30:00Z",
    "lookback": 120,
    "min_abs_residual_z": 2,
    "max_event_sessions": 20,
    "max_data_age_hours": 72
  },
  "series": {
    "SPY.US": {
      "source_ref": "original_daily_data_receipt",
      "retrieved_at": "2026-09-05T03:00:00Z",
      "price_basis": "explicit_adjustment_basis",
      "rows": []
    }
  }
}
```

这是**字段示意，不是可运行真实价格输入**。`series` 需同时有 benchmark、issuer、target 三组完整日线。每行 `date`（YYYY-MM-DD）、正数 `close`、带时区 `close_at`。三个标的必须不同，所有日期和对应收盘时间严格一致；不静默取交集、填零或前向填充。重复日期、缺一方日期、未来行、未来取得数据、非正数与不一致复权口径均阻断。

本版本只做美国现金股票同一日线日历。`close_at` 为实际/明确来源的交易时段收盘时间，不能直接拿供应商 bar 的 session 午夜时间冒充。若由交易所日历推导，附 `close_at_basis`、`close_at_source_ref`，输出会保留。DST 和节前早收盘必须处理。可额外给 `session_calendar{source_ref,expected_dates}`，检查三组同时缺一天；未给时明确 `calendar:common_missing_sessions_not_independently_checked`，不能宣称核过交易日历。

`price_basis` 有 raw/unadjusted/none 时，需上游已核验的 `corporate_action_review_ref` 才能超过 discovery_only；数值仍会计算。一个 reference 不代表本模块独立完成企业行动审计，原始拆股、分红、配股可能造成假异常。数据按本次实际取得时刻记账，不能把今天拉的历史日线说成当年已经冻结的原始版本。

## 冻结模型与反应分解

基线是最后一个 `close_at <= published_at` 的共同收盘。训练使用截至该基线的最后 `lookback` 个对数收益，需要 lookback+1 个价格。事件后价格永不进入本次 beta 拟合；事件盘前发生会包含当日收盘反应，盘后发生则从后一个收盘计算。时刻恰好等于 close 依照上述明确的 `<=` 合同划界，实际发布/竞价先后不清时应上游降级。

1. issuer 日收益对 benchmark 做带截距 OLS，取得 issuer 市场残差。
2. target 日收益对 benchmark 做带截距 OLS，取得 target 市场残差。
3. target 市场残差对 issuer 市场残差回归（FWL），还原 `target = alpha + beta_benchmark × benchmark + beta_issuer × issuer + residual`。

返回训练样本数、三变量系数、原始与 partial correlation、单期 residual sigma；benchmark 零方差、issuer 被 benchmark 完全/近乎解释时直接 singular blocked。绝不退回 beta=1，也不跳过 benchmark 消除共同走势。

事件后 h 个完整收盘的累计对数收益分解：

- `issuer_benchmark_abnormal_logreturn` = issuer 累计收益 − h×issuer截距 − issuer市场beta×市场累计收益。
- `target_benchmark_abnormal_logreturn` = target 累计收益 − h×target单市场截距 − target单市场beta×市场累计收益。
- `spillover_predicted_logresponse` = partial issuer beta × issuer 异常反应。
- `extra_residual_logreturn` = target 异常反应 − 上式传导。
- `estimated_dislocation_pct` = `100 × expm1(extra_residual_logreturn)`，以百分数表示，不是可赚收益率。

负数为相对模型 laggard，正数为 rich；这只是模型残差方向，不能翻译为低估/高估或“必然补涨/回归”。企业重估可能让旧关系永久失效；相关性与商业关系都不构成经济因果识别。

## 偏离尺度、事前规则与降级

训练的日残差按**与当前事件年龄 h 相等**的长度，从训练末尾向前划完整、非重叠块；舍弃最旧的不完整余量。计算这些块累计残差的样本均值/标准差，再给当前 residual 的 descriptive z。至少 3 个块且方差非零才计算 z。不用单期 sigma×sqrt(h) 假装残差独立；块仍可能序列相关，非重叠不等于独立，z 不转换正态尾概率。

仅 |z| 达明确 `min_abs_residual_z`、仍在 `max_event_sessions` 内、数据依明确 freshness 上限为 fresh、关系证据完整、企业行动审计要求满足且无已发现 regime break，才标签 `relative_dislocation_candidate`。这也仍是研究假设。模型因新机制永久改变时，旧 z 没有均衡回归含义。

可给 `regime_break{detected:true,source_ref,...}` 直接阻断应用但保留计算；可选 `rule.max_coefficient_shift` 在训练前后半窗分别回归，任一 beta 绝对变动超阈值即诊断阻断。阈值需事前声明；此检查不是稳定性证明，未做独立 regime 验证时输出缺口。fresh/stale 都保留公式结果与时间，不把 stale 清空成零。

运行时汇总区分研究截点（request `as_of` / component `input_as_of`）与来源时钟（例如 output `freshness.last_close_at`）。更新研究截点不能刷新旧行情：汇总纳入来源年龄和组件自己报告的 stale / unknown_policy；缺少来源时钟或有效时效规则、未来/非法时钟均不能记 fresh，保留 `freshness_data_gaps` 与已有统计。`oldest_source_age_hours` 单独展示最旧来源年龄。回顾研究 `retrospective_only` 与数据时效独立，截至本次研究截点足够新的数据不会仅因回顾性而变 stale。

关系 `kind` 支持 commercial_partnership / co_development / supplier_customer / ownership / competitor / shared_product_exposure；必须有 source_ref、description、known_at，issuer/target 若给出必须正确。缺失、未知 kind 或错标只许 `statistical_residual_only / discovery_only`，仍可查看统计；不把随意关系枚举当已验证事实。

任何 `event.known_at`、`relationship.known_at` 或 `rule.declared_at` 晚于 publication 均强制 `retrospective_only=true` 并说明原因。今日新规则回看旧事件不能倒填成事前策略。所有 known_at 不得晚于 as_of，事件 known_at 亦不得早于公开 publication。

`reversion_probability`、`reversion_time`、`price_target`、`expected_return`、`net_edge` 始终 null。这些估计器尚未实现，并非只缺本轮数据；补齐输入也不会启用计算。输出 `forward_estimation_capability.status=not_implemented`，顶层 `data_gaps` 固定包含 `forward_estimates:estimator_not_implemented`。需要另行设计、实现并验证事件条件前瞻估计与成本模型。这里没有合格的相似事件样本与前瞻路径校准；平静期训练残差只能描述尺度，不能拿来估“事件发生后多久回归/几成会补涨”。缺完整前瞻 payoff 与成本合同也不能算 net edge。可交给独立 conditional path 研究，但不能假装本模块已经打通事件概率。

## 输入验证与交易日历

日线需要明确复权口径、企业行动处理、交易所时区与交易日；若用当前获取的数据回看旧事件，必须保持 retrospective_only。训练期、事件后窗口与已知时间分别记录，不能事后挑选更有利的模型作为已验证信号。

时间规范化可参考 [ICE/NYSE 交易日与早收盘日历](https://ir.theice.com/press/news-details/2024/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar/default.aspx)及 [Nasdaq 交易时间](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)。按日历推导的 scheduled close 必须标记为推导值，不能冒充供应商报价时间。
