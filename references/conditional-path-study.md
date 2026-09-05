# 条件历史路径：透明实现与真实证据边界

`scripts/conditional_path_study.py` 是独立实现的**显式状态分箱历史条件研究**，使用调用方提供的历史价格计算路径分布和滚动评分；不合成路径以增加独立样本、不产生 ModuleSignal、仓位或 Compiler 动作。

## 运行与两种证据状态

纯标准库，无网络、账户或订单调用。严格输入使用 `rows` 中的 `date/open/high/low/close`，与现有 `factor_panel.py` 缓存行结构一致；日线事件对齐沿用 `earnings_move_history.py` 的原则：明确起点，只使用下一交易日以后的路径。未直接复用该脚本的财报事件识别逻辑，避免混入不同问题。

```bash
python3 scripts/conditional_path_study.py --input /path/to/pit-input.json --horizon 10 --output /path/to/receipt.json
python3 scripts/conditional_path_study.py --input /path/to/US/SPY.json --retrospective-panel --rule /path/to/rule.json --horizon 10 --output /path/to/replay.json
python3 -m unittest discover -s scripts -p 'test_conditional_path_study.py' -v
```

- 严格输入：`symbol/as_of/provenance/sessions/rows/rule` 必填。`as_of` 为数据截点，`provenance.retrieved_at` 为取得快照的实际时间，两者不混用。每根 bar 有真实带时区 `close_at/available_at`；源、源引用、PIT 价格证据、交易日历源均必填。`sessions` 是独立来源的完整预期交易日序列，必须和行日期逐项一致；重复、缺 bar、混 symbol、非法 OHLC、未来可得数据会退出 2，输出 blocked。
- 严格 provenance 字段：`source/source_ref/retrieved_at/price_basis/pit_evidence_ref/calendar_source_ref`；`price_basis=point_in_time_consistent_ohlc` 必须有外部可审证据支撑。CLI 不认证字符串真实性，状态仍为 `pit_evidence_supplied_unverified`，不得据此宣布历史 PIT 已验证。
- 旧 `factor_panel_symbol.v1` 缓存可显式用 `--retrospective-panel` 分析。保留 source、fetched_at、adjust_basis、原 pit_caveats 和原始输入 hash。其日期只构成**观测 bar 序列**；00:00 UTC 是内部重放排序约定，绝非历史真实可得时间。结果为 `retrospective_only`，声明 `historical_pit_unverified`、日历未验证、复权修订风险。可以给真实 n、分布、频率、回放分数，但不可冒充事前校准。
- 同一快照内如果 OHLC 缺失或日序错乱，不填 0、不悄悄压缩；严格模式还会据独立 calendar 拒绝缺交易日。旧缓存模式无法查出原源已漏掉的交易日，这个限制直接进入 DataGap，时间单位降为观测日 bar。

完整严格输入示意（此处为契约结构说明，不是可作行情使用的数据）：

```json
{
  "symbol": "SPY", "as_of": "2026-09-04T21:00:00Z",
  "provenance": {
    "source": "provider", "source_ref": "original receipt reference",
    "retrieved_at": "2026-09-05T00:00:00Z",
    "price_basis": "point_in_time_consistent_ohlc",
    "pit_evidence_ref": "audited vintage data evidence",
    "calendar_source_ref": "exchange session calendar receipt"
  },
  "sessions": ["... every expected date ..."],
  "rows": [{"date":"2026-09-04", "open":1, "high":1, "low":1, "close":1,
            "close_at":"2026-09-04T20:00:00Z", "available_at":"2026-09-04T20:01:00Z"}],
  "rule": {"rule_id":"frozen-id", "declared_at":"2025-12-31T00:00:00Z",
    "declaration_ref":"external immutable declaration", "horizon":10, "lookback":20,
    "features":{"drawdown":[-0.05,-0.02]}, "up_thresholds":[0.02,0.05],
    "down_thresholds":[0.02,0.05], "oos_start":"2026-01-02", "min_train_n":20, "min_oos_n":30}
}
```

真实输入必须足够长、日期完整，不能照抄示意价格。`--rule` 文件只含上例 rule 对象。用旧历史新写的规则要填**真实当前 declared_at**，只能进行 retrospective replay，不许倒签预注册。

## 公式、抽样与时间

特征只用起点及以前 `lookback + 1` 根 bar：

- trailing_return = 当前 close / lookback 根以前 close − 1。
- drawdown = 当前 close / 窗口最大 high − 1。
- realized_vol = 窗口日 log return 的样本标准差 × √252；年化假设只适用于日线。

每个特征采用事先规定的边界；落在边界时进入上方 bin。所有声明特征的 bin 同时相同才匹配；没有通过全样本择参、距离缩放或择优样本。取最早匹配起点，随后跳过 horizon 根，令路径间距至少 horizon + 1，回放同样按该间距滚动。有效样本数明确给 `actual_n/complete_n/right_censored_n`；路径不重叠并不保证独立，持续市场状态仍可能相关。

起点是已可得的该日 close，未来从下一 bar 开始。终值收益 = 第 h 根 close / 起点 close − 1；MFE 为未来 high 相对起点收益最大值和 0 的较大者，MAE 为未来 low 相对起点收益最小值和 0 的较小者。三者只对完整 h 窗口统计，输出 mean/q05/q50/q95 和真实 n；分位数使用线性插值。

涨跌阈值为正的小数幅度。up:x 是某 future high ≥ origin close × (1+x)；down:x 是某 future low ≤ origin close × (1−x)。每个事件给首次触达 bar 数分布，**条件为在 h 内确实触达**，不是所有路径预期等待时间。回到当时近期高点的目标只由当时窗口最大 high 决定；若起点已在目标，时间为 0，不把它伪装成未来反弹。

终值上涨与途中触达上涨分开；同一路径可以先上涨后下跌，两尾触达不是互斥事件。输出每组 up/down 的联合频率；同一日 high/low 两边均触及时，日线无法判断先后，也不能代表实际成交。

## 右截尾、基线与校准

数据截点之后缺失的 future 不填 0。完整窗口给 descriptive historical_frequency 和 Wilson 95% 区间。未到期样本不进入终值分布或完整窗口命中率，但已发生触达仍进入全起点识别界限：下界 = 已知触达数 / 全部起点；上界 = (已知触达 + 截尾未决数) / 全部起点。不假设独立删失，不外推尾部，不把尚未触达当失败。完整窗口统计可能有日历/制度偏差，不能直接替代全样本界限。

unconditional_same_cutoff 使用同 symbol、同历史起始/结束截点、同 horizon/抽样间距，移除状态匹配条件。两种抽样起点未必完全相同，因为条件筛选会改变跳过窗口的位置；这是报告已披露的比较设计，而非配对因果估计。

滚动回放每个起点只选其当时已成熟 h 根的历史标签；每条预测保留 forecast_as_of、train_origins、train_last_label_date、训练 n、条件概率、无条件概率以及真正后验 outcome。过 `min_train_n` 才 issued；未到期 OOS 留 pending outcome，不结算失败。`min_oos_n` 是用户预先声明的**描述性打分样本门**，不是显著性、盈利性或校准的自动通过门。

到期预测逐触达事件计算 Brier，并给同截点无条件 Brier；终值 90% 经验区间给 coverage 和 mean width，另有无条件对照。coverage 不能脱离 width 评价。数据太少就明确 insufficient；即使样本门通过也始终 `calibration_accepted=false`、`ex_ante_calibrated_probability=false`。晋级必须有外部可审的事前规则凭证、历史 PIT 数据核验、独立未用于调参的样本外评估、事先约定的 score/coverage/width 标准及主 Skill 验收。回放得分本身不创造这些证据。

Wilson 为独立二项近似，仅作描述区间，不消除制度依赖、多阈值检验、样本选择或幸存者偏差。这里没有策略收益、成本、容量、交易风险预算验证，任何统计都不能越过 Compiler。

## 经验路径区间锥

`empirical_path_cone.steps` 按未来每个观测日 bar 给 `close_return/high_return/low_return` 的 `q025/q16/q25/q50/q75/q84/q975`，并保留该步真实 `n/censored_n`。每个点仅使用上述已选历史起点在数据截点前已经观察到的对应 bar；起点不是当前 bar，不增加模拟样本。短尾样本只贡献其已经成熟的步数，未到期步数不填 0、不插值、不复制终值。随着 horizon 增长，n 可能下降，逐步分布的样本组成变化必须在图中保留。

`close_price_projection/high_price_projection/low_price_projection` 按 `latest_observed_close × (1 + historical_return_quantile)` 平移到当前最近真实 spot。图例必须写“历史路径平移情景，非已校准未来价”；reference_spot 的时间与报告 as_of/末 bar 一起展示。high/low 是该单独 bar 的高低价分布，不是累计最高/最低路径，也不是 first-touch 概率。q025–q975 为逐点经验区间，不构成整条路径 95% 同时覆盖保证；末端 close 区间与中途触达阈值属于不同事件。

这个锥来自实际匹配样本 n，不通过重复抽样增加独立样本数；没有历史 Gamma conditioning，输出明确 `historical_gamma_conditioning=false`。回放校准资格保持 false；可复现分布不等于完成事前概率校准。
