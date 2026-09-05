# US Strategy Campaign · 先分任务，再统一裁决

## 解决的具体问题

发现候选、证明命题、估计概率、约束风险、下单和复盘是不同职责。不能把 KOL 线索、排序器的零仓位输出全部塞进同一个开仓约束数组，也不能用量化综合分绕开主 Compiler 自签 BUILD。`scripts/strategy_orchestrator.py` 只安排研究路径和汇总原 Compiler 结果，不生成另一套动作等级、概率或敞口。

每个候选先固定 `symbol × strategy_id × horizon_id × instrument`。同票短期反弹与中期下行分别研究；组合与账户风险始终共用。研究完成后仍按 `scripts/decision_compiler.py` 编译，输入信号完整保留，不能由调用者将真实 veto 标为不适用后删除。

## 六条美股研究路径

| strategy_id | 周期 | 要证明的交易条件 | 核心失效 | 用途 |
|---|---|---|---|---|
| trend_following | swing_days / position_months | 价格趋势、相对强弱、盈利或现金流趋势支持持续持有 | 趋势破位、基本面反证、时间止损 | 进攻主线候选 |
| momentum_breakout | swing_days | 有量突破后能守住，行业扩散与催化一致 | 假突破、领头羊缺口失守、拥挤 | 进攻候选 |
| oversold_reclaim | intraday / swing_days | 超卖后实际收复关键位，卖压减弱，有明确小风险失效点 | 仅便宜无收复、再创新低、流动性恶化 | 防守期间继续找条件性反弹 |
| event_followthrough | swing_days | 公告/财报事实与价格反应一致，催化仍有时效 | 利好兑现、预期差消失、事件窗口错配 | 事件后确认候选 |
| defensive_relative_strength | swing_days / position_months | 弱市仍保持相对强势、现金流与流动性可核实 | 防御拥挤、趋势失守、组合暴露超限 | 防守期间替代品比较 |
| overnight_cto | overnight_cto | 当日催化/流动性/成本成立，能执行次日退出时钟 | 二元财报未拆、价差差、09:40 未重新编译 | 专门隔夜分账 |

这些是研究路径，**不是已证明有效的新量化策略**。新提炼方法默认 `train_only`；System A 只执行自身已通过验证、且当次通过主 Compiler 的策略。禁止为让系统交易而把上述名字直接加入 active roster。

## 进攻、防守与重新进入

- 进攻：新鲜独立证据、可解释正向命题、触发已发生、成本/账户/风险门通过，Compiler 才可能给 TEST/BUILD/ADD；按 Compiler 的实际倍率使用既有风险预算，不能重新用分数换算仓位。
- 防守：global veto 时仍扫描六条研究路径，但不发新仓。给出具体下一观察条件、数据来源与下次检查时间。风险尚可但正在积累时，优先检查相对强势、事件后确认、超卖收复；它们不豁免系统性风险。
- 风控退出后：旧信号和旧信封失效。必须新证据、新时间戳、新决策，检查原失效因素是否修复；不能把旧 alpha 留在系统里让下一轮立刻买回。
- 修复条件满足只触发 `rerun_research`，不会自动解除 veto。真实持仓的机械减仓/退出由独立 executor 管理。
- 不做“为了找机会必须买一笔”。扫描有结果但没有可交易命题，输出 `no_edge`；数据拿不到，输出 `data_gap`，二者不得混用。

## 信号归属

1. `discovery_candidates` 保存 KOL、排序、模型情景、公开市场预测线索及其原始引用，不是 `module_signals`。
2. 将线索回到公告/财报/行情等实际来源证明后，用真实来源模块重新构建证据；不得改名沿用线索的权限。
3. `decision_request` 是完整、不可静默过滤的 strict v2 请求。强制 risk_regime、portfolio_risk_budget、data_quality；选定策略需要的证据缺失必须显式降级。
4. 期权合约执行问题仅阻断期权分支；正股使用自己独立的请求。scope 不明先阻断并补证，不能猜测为不适用。
5. 每次落盘 `blocker_class`：data_gap / contract_gap / market_risk / portfolio_or_permission / unresolved_conflict / no_edge_or_non_open_intent / ready。它是流程诊断，不改变 Compiler 结果；最后一类非ready原因也可能是退出/观察意图，不能统称市场没有机会。

## 概率与模拟盘反馈

- 排序分不是概率，置信度不是胜率。未知概率保持 `null`；不可用 `0.42 + score × 0.22` 或类似固定公式填满校准桶。
- 概率质量与交易收益分账：前者事前冻结问题、周期、阈值与概率后到期结算；后者按完整交易生命周期和实际成交计算成本后盈亏、R、回撤。
- 无交易也可学习，但必须事前登记完整候选集、排除原因、冻结参考价/期限/可执行性与成本口径；事后挑赢家不算漏单机会成本，未成交影子收益不算纸面净值。
- challenger 只出研究优先级、数据修复、测试或方法改进候选；paper / counterfactual 不直接修改主 Skill 的仓位硬顶，也不因一周赚钱而晋级。
- 对已经形成的系统性亏损优先诊断策略/体制/成本/执行差异。旧 OOS 回测不能自动覆盖较新的真实纸面衰减证据；是否恢复新仓必须留下更新证据和回归验收。

## 开源机制采用边界

采用 LEAN 的 alpha/portfolio/risk/execution 职责分离和 insight 过期纪律，特别注意风险退出后旧 insight 造成自动重入的问题：[官方 Alpha 文档](https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/alpha/key-concepts)。

采用 Freqtrade 的前视偏差检测与递归指标稳定性检测思路，作为已有 Quant Robustness Gate 的验收补充，不引入其交易引擎：[lookahead](https://docs.freqtrade.io/en/latest/lookahead-analysis/)、[recursive](https://docs.freqtrade.io/en/stable/recursive-analysis/)。Qlib 的版本化策略/滚动任务和在线表现追踪用于研究记录设计，不把 retrain 自动等同上线：[Online Serving](https://qlib.readthedocs.io/en/latest/component/online.html)。

以上链接用于方法约束参考；本仓库不引入这些项目的交易引擎或绩效结论。
