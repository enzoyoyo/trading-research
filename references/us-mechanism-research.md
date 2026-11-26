# 美股机制研究：发现、分布、表达、成交与归因

本模块把事件偏离、历史条件路径和期权表达接入同一版本化研究合同，保留输入、时间、范围与缺口，供独立复核。

## 实际计算入口

`scripts/us_mechanism_research.py --input request.json --output bundle.json` 实际调用三个计算器，保留各自输入、代码版本 hash、数据截点和缺口。也可由 `scripts/research_run.py <symbol> --mechanism-input request.json` 调用。没有输入时研究入口仍是计划，不得报告计算已执行。

输入为 `us_mechanism_request.v1`，必填 `research_id/as_of/scope_symbols/horizon_id/hypothesis_ids/invalidation/components`。components 可独立提交：

| 部分 | 实际计算 | 不得作出的跳跃 |
|---|---|---|
| event_dislocation | 原始事件及经济关系；事件前冻结 partial OLS，扣掉共同大盘反应后估计额外偏离，同期限非重叠残差尺度 | 相关性不是因果，z不是胜率，偏离不是低估；事件前瞻估计器尚未实现，回归概率/时间/目标价/预期收益/净edge均null，单独补数据不会自动产生估计 |
| conditional_paths | 状态分箱，终值、触达、修复高点、首次触达时间、双尾重叠、MAE/MFE、同截点基线、右截尾、逐步经验区间锥、滚动Brier/coverage/width | 价格状态样本不是事件相似样本；触达不是收盘；重复抽6000次不增加独立样本；当前缓存回放不是事前校准 |
| options_expression | 单腿/价差/蝶式，逐腿身份、自然买卖价、比例容量、明确费用，到期精确几何与隔日估值分开；个股期权与SPX蝶式联合压力比较 | 蝶式不是普适保险；指数不动而个股跌或IV下降时可能增加损失；BBO不是原子多腿成交；情景网格不等概率 |

各部分可独立完成或受阻，坏期权报价不抹掉有效的股票研究。scope 必须覆盖所有资产，组件输入不得晚于 bundle 截点。日线收盘/全天高低点路径不能回答盘中或 close-to-open，连 horizon=1 也不能偷换成次日开盘。各计算器缺口带来源前缀合并到 bundle 与 runtime summary，不能只在深层藏起来。

bundle 不合成胜率、不生成正向 ModuleSignal、不改 action/position。输入和四个计算器代码共同决定 contract_sha256，代码更换不得沿用旧合同。细节见 `event-dislocation.md`、`conditional-path-study.md`、`options-expression-lab.md`、`gamma-model-semantics.md`。

`scope_symbols` 是完整输入资产范围，包含发行方和基准；`attribution_symbols` 只包括实际成功计算的研究目标。基准或发行方不能仅因是输入便获得该研究的交易归因；受阻组件也不能扩大目标集合。目标子集随研究链接冻结，绑定端与反馈端都核验。反馈另保存实现hash及实际入/出场时间。`horizon_id` 当前只绑定命名期限桶，尚未将计算会话数和实际持仓窗口统一成可校准预测合同；不得把此描述性关联称为同期限预测验收。

## 研究问题与计算边界

1. **SPX 概率锥**：保留区间、收盘分位、触墙概率分别结算的机制。本地实现为经验路径区间；独立样本量与事前校准资格须分别报告；历史SPX Gamma条件数据缺失时显式列为缺口。
2. **SPX蝶式+个股单腿**：判断两个标的、期限、价格与IV联合分布下的组合PnL。看对单腿方向仍可能因IV/theta亏损。蝶身附近有限的局部稳定性不能推广到整个区间。支持American/European个股期权持仓；American隔日mark须显式为模型情景，不冒充未来观测报价。
3. **类似状态**：终值涨2%、路径触及涨2%、触及跌5%/10%、回到旧高点是不同事件；双尾可以同时发生，概率不必加总为1。报告只用实际独立样本；区间全宽与相对中点半宽分别计算。
4. **六步流程**：三个计算器进入同一版本化研究合同，再由原Compiler→Demo executor→fill reconciler→lifecycle→净费用反馈接力。现有股票shadow replay保留；缺历史逐腿报价时，不用股票K线编造期权回测收益。
5. **侧击**：先找有经济关系的受影响标的及市场已计入的预期，再核对相对模型偏离和竞争解释。证据成立、时间窗口合理、净成本后仍有空间，才进入原交易准入。区间预测是表达输入之一，不替代错价论证。

官方校验：[OIC Long Call Butterfly](https://www.optionseducation.org/strategies/all-strategies/long-call-butterfly)、[Cboe SPX contract specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications)。SPX根、AM/PM、最后交易/结算时间须明确并按当前交易所通知核验，不凭同一天到期推断相同产品。

## 进攻、防守、重评

风险预算决定当前可承担的风险，不能替研究系统得出“没有机会”。按 `us-strategy-campaign.md` 继续发现：已有统计优势且成本可承受的候选、有独立失效理由的减仓/对冲、暂被报价/证据/资金/准入门阻断的观察候选。每项写下一条可观察的重评条件。

未知概率不能转成零胜率，也不能为“必须找机会”凭空买入。防守期间可比较低相关或替换候选，但低相关必须过压力情景，不能仅靠普通相关系数或蝶式名称。

## 模拟盘反馈接线

以下描述独立 System A 执行器需要实现的对接契约；本 Skill 发布包不包含执行器、账户配置或定时任务。


System A `scripts/paper_mechanism_research.py` 在现有 mechanical cycle 的对账后学习段运行。只读 `research/mechanisms/requests/*.json`，计算后写不可变 `research/mechanisms/receipts/<contract_sha256>.json` 和 `reports/mechanism_research_latest.json`。无输入是no_inputs，不是没机会；同输入和代码保留首次实际记录时间；旧源保留计算并标stale，不伪刷新日期。当前步骤执行离线重算，不自动采集公告、行情或报价；日度Agent提供输入是runbook要求，自动刷新成功须另有真实回执。

时效读取计算器明确输出的最新价格时钟：事件的 `freshness.last_close_at`、条件路径的最新query bar、期权各合约 `quote_asof` 与持仓 `reference_mark_asof`。报告 `as_of`、下载时间和写盘时间不能代替源时钟；不遍历历史训练样本来误判当前研究全部过期。请求复核周期与期权自身报价有效期都须满足，缺时钟或无效周期明确列缺口；不删除有效的描述性计算。

候选如使用此研究，在Compiler前把 bundle.research_link 原样放进 proposal.metadata.mechanism_research_link，并明确 metadata.horizon_id。此处是提案Agent的显式生产职责，当前没有把每份研究自动转为提案的生产器；不能把消费者接线称为研究到成交已自动验收。链接不是执行许可。reconciler原样复制入口合同，feedback检查同hash/scope/horizon、实际研究记录时间早于entry，随后复用既有lifecycle与净费用函数。

`--feedback-input` 只做已链接交易的描述性归因。历史无链接保持legacy_unlinked，未结费用净PnL=null，相同生命周期集合不增加独立样本。即便获得已结净收益，也不自动证明因果、修改仓位/策略或充当概率校准。System B读取packet后按既有hypothesis/版本/样本规则提交可回滚改进。

对已有单入场、单全退出且费用待定的生命周期，reconciler现在可在有限查询预算内补取费用，只追加 `paper_trade_outcome_cost_receipt`。补录绑定原close哈希及两端成交/数量/币种/费用明细，经金额与时钟复验后供净费用函数消费，不改原close、不制造第二次退出。分批、多lot或仍缺资料保持pending；当前通过的是隔离费用补录测试，不是新策略真实费用已到账。研究链接须在初次绑定提案之前实际持久化，不能等成交后首次写入。

## 验证

`scripts/test_us_mechanism_research.py`：实际计算器组合、分支隔离、跨时间/资产/期限错误、输入与代码双hash、事后链接拒绝、费用缺口和生命周期去重。

模块测试：`scripts/test_event_dislocation.py`、`scripts/test_conditional_path_study.py`、`scripts/test_options_expression_lab.py`、`scripts/test_gamma_model_semantics.py`、`scripts/test_gamma_risk_semantics.py`。合成样本只用于测试，不进入生产请求、交易流水或真实收益报告。
