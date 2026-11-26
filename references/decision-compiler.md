# Decision Compiler · 决策编译器

## 目的

把各模块输出从“意见”编译成统一动作，避免方法论之间互相打架。

每个模块只输出结构化约束；L1+ 与 System A open proposal 必须使用 `decision_request.v2`，完整 schema 见 `references/data-contracts.md`：

```yaml
decision_context:
  query_tier: "T0|T1|T2"
  intent: "research|open|hold|reduce|exit"
  has_position: false
  as_of: "RFC3339"
  required_modules: [risk_regime, portfolio_risk_budget, data_quality]
module_signal:
  module: "registered module"
  max_action_level: "L0|L1|L2|L3|L4|L5" # legacy display
  entry_permission: "WATCH|TEST|BUILD|ADD|BLOCK"
  holding_directive: "HOLD|REDUCE|EXIT"
  position_multiplier: 1.0
  hard_veto: false
  evidence_refs: ["EID-..."]
  observed_at: "RFC3339"
  stale_after: "RFC3339"
  reason: ""
  repair_signal: ""
  sub_framework: "optional"
```

最终动作由 `scripts/decision_compiler.py` 统一裁决。运行时强制 module 注册表；未知 module 在 legacy/v2 都拒绝。v2 还强制完整且类型正确的 `query_tier/intent/has_position/as_of`、required modules、重复键、EID 与 freshness；strict `evidence_refs` 必须是非空字符串 id 列表（示例 `EID-...`），禁止 parser invalid-integer sentinel、非字符串元素、空白项，以及“至少一个 truthy 项即可”的 presence-only 通过；L0 也能通过 `WATCH/BLOCK`、hard veto、holding directive 或 multiplier 改变结果，所以不得豁免 EID/freshness。`position_multiplier` 必须能无损进入有限、非负浮点域；bool、NaN/inf、负数、IEEE/字符串/raw-JSON 负零、负下溢、不可转换值及整数转浮点 overflow 均属于合同错误，必须整包 fail-closed，不能静默 clamp 成已接受的 `0.0` 或回退到中性 `1.0`。CLI 在 JSON parser boundary 使用有界 `parse_int`：普通整数保持 native `int`，词法 `-0` 保留 sign bit，超长整数变成不含原值的 JSON-safe invalid sentinel，从而在 Python 3.9/3.11 都进入同一 `strict_failed/legacy_failed` 编译 envelope，而不是依赖运行时 `int_max_str_digits` 返回 generic load error。freshness 以 Compiler 调用时可信 runtime `now` 为权威，并同时对照 `decision_context.as_of`；payload 不能自行回拨或前推时钟。strict v2 的 `intent=open` 内置资本承诺 baseline 固定为 `risk_regime + portfolio_risk_budget + data_quality`，payload 只能增加 required modules，不能缩减这三项；其它 intent 不形成 entry authority。旧 payload 仅输出 `contract_status=legacy_unverified`，不得作为 System A 执行凭证。

## 合成规则

1. **动作分轴**：`entry_permission` 只裁决新开/加仓；`holding_directive` 只裁决已有持仓，优先级 `EXIT > REDUCE > HOLD`。L4/L5 不再参与 L0-L3 数值排序。strict v2 只有 `intent=open` 能形成 entry authority；`research/hold/reduce/exit` 即使 baseline 豁免且收到正向 L1-L3 signal，也强制 `entry_permission=BLOCK`、`final_position_multiplier=0.0`，但仍可按 holding signal 输出 REDUCE/EXIT。
2. **Hard veto 优先并分类型**（v2.36，用户 2026-07-10 拍板）：任一模块 `hard_veto=true` 都令新仓 `BLOCK`、`final_position_multiplier=0.0`。有持仓时按下表裁决；两类同时出现时市场风险型优先。

   | veto 类型 | module 集合 | `has_position=true` 后果 |
   |---|---|---|
   | 市场风险型 | `risk_regime`、`forced_liquidation`、`liquidity_squeeze`、`gamma`、`account`、`brokerage_portfolio_margin`、`portfolio_risk_budget`，以及未列入认知型集合的其他注册模块 | 强制 `holding_directive=EXIT` |
   | 认知型 | `data_quality`、`calculation_quality`、`ingestion_permission`、`conflict_ledger`、`research_readiness` | 不机械卖出；holding 仍取信号自身 L4/L5 推导的最大值，并输出 `epistemic_veto=true`、`manual_review_required=true` 交回人工复核 |
3. **新仓最保守权限优先**：WATCH/TEST/BUILD/ADD 取最低权限；任一 BLOCK、REDUCE、EXIT 都禁止新 entry。
4. **仓位乘数按 module 聚合**：每条信号先应用 Cap Registry 硬钳制；同一 module 内多个 overlay 再取最小 `position_multiplier`，跨 module 连乘：`final_cap = base_cap × Π_module(min(position_multiplier))`。被同 module 最小值替代的信号必须在 `cap_applied` 保留 `superseded_by_min=true` 审计项。中性值是 1.0，0.0 表示不允许新仓。若最终乘数为 0，则 entry permission 退回 WATCH/BLOCK，不得输出“L1 但零仓位”。
5. **未裁决冲突默认 BLOCK/WATCH**：Conflict Ledger 存在 unresolved 项时，不得升到 TEST。
6. **合同先于评分**：未知 module、缺 required module、重复 module、任意 strict signal（含 L0）缺 EID/时间或 stale/future 均 fail-closed。
7. **数据缺口 cap 优先于评分**：关键行情/财报/Gamma/账户缺口触发对应最高动作限制。
8. **时间框架分离**：短线执行、波段持有、中线观点必须分开编译。
9. **Mira readiness 只降级不加分**：`readiness_level` 进入 `research_readiness` 模块，只能限制动作上限，不能自动提高 L 级。注意：`research_ready` 是 readiness_level 取值，`research_readiness` 是 module 名，禁止混用。
10. **派生计算缺口优先**：影响动作的 `derived_calculation` 若无 formula / calculation_ref，按 `calculation_quality` 缺口降级。
11. **Grok 全网只发现不加分**：`grok_web` 未回抓原始 URL/时间戳时最高 L0；回抓成功后按原始来源进入对应模块，Grok 本身不提高动作。
12. **量化稳健性只降级不加分**：`quant_robustness` 通过只能允许量化证据参与排序/风险上限；缺 walk-forward、成本、容量、回撤、no-lookahead 时必须降级。
13. **预测市场只作先验不加仓**：`prediction_market_prior` 是 `market_pricing`，只能提高 watch priority / scenario prior；即使原始市场 URL、时间戳、流动性、价差、规则风险都通过，也不得单独提高动作等级或 position cap。
14. **执行窗口只降维不加分**：`execution_window` 只能在上游动作等级内裁决“何时执行/何时结束”，不得复活 L0 标的、不得提高 action level、不得提高 position cap。
15. **群体模拟只作 modeled scenario**：MiroFish-style swarm simulation / synthetic agent reactions 是模型情景，不是事实；`modeled_scenario` 只能形成 hypothesis / scenario_prior / watch_priority / evidence_collection_plan，默认 L0、`position_multiplier=0.0`。
16. **短周期结构层只收紧且分 instrument**：09:40 continuity pass 只允许重新编译，不代表自动续持；延迟/过期 SPX gamma 不能放宽；坏 option BBO 只进入 option branch 的 `data_quality`，不得污染 underlying branch；EOD 权重建议不得修改原多因子或 hard gates。
17. **搜索/热点/传闻只发现不加分**：搜索标题、摘要、provider 数量与 attention 只提高 evidence_collection/watch priority；回抓原始来源后才进入真实模块。传闻 credibility 未过门时最高 L0/L1 watch，不能提高 position cap。
18. **分红质量不另建评分器**：`dividend_quality` 作为 `fundamentals` 的 sub_framework；EPS/FCF/周期/资产负债表缺口只会降级，固定 safety score 不能覆盖 Compiler。
19. **KOL Method Card 只作同一证据链的可移交结构**：`kol_method_cards` 复用 `research_provenance.v1` 与 `x_frontline` / `research_readiness`，不新增评分器或动作模块。公开来源不完整最高 L1，受限/订阅来源若被用于生命周期结论则 L0；自报收益不提高 reliability ceiling；卡片只能收紧、刷新来源或请求人工复核，不能抬 action level / position cap、复活 L0 或触发订单。
20. **OKX venue adapter 只提供证据和收紧约束**：`entry_score.v1`、公开行情和 `okx_execution_supervision.v1` 不新增 module。完整健康的 EntryScore/Supervision 不生成正向 ModuleSignal；只有数据缺口、身份异常、feed/heartbeat/连接/REST baseline/订单/对账/item-level failure、流动性或账户红线时，才映射到现有 `data_quality/conflict_ledger/account/liquidity/execution_window` 阻断信号。认知型缺口和地区资格不得机械卖出现有持仓，账户 hard redline 仍按市场风险型 veto 处理。


## Cap & Tighten-Only Registry · 封顶值唯一规范表

本表是封顶值与 tighten-only 不变量的唯一权威；`SKILL.md` 和其它 references 只引用本表，不重复维护数值。

| 项 | 值 | 性质 |
|---|---:|---|
| `political_disclosure` vote 封顶 | 0.10 | 有界 vote 成分，calibration 自调，必带 `disclosure_lag_days` |
| `x_frontline` / KOL 封顶 | 0.15 | 仅线索，不独立提仓位 |
| `kol_method_cards` handoff | `position_multiplier=0.0` | 只允许 tighten / refresh source / manual review；不得抬级、加仓、复活 L0 或下单 |
| ETF/基金 `participant_flow` confidence 加成 | +0.1 | 唯一可加分项；仅当 `净流入 / 基金规模` 落在自身近 N 期高分位且披露 N/阈值时生效；名义流量或 AUM 缺失/过期则不加分、退回中性并记 `data_gaps: flow_not_scale_normalized`；不进 action level / position cap / sizing |
| `modeled_scenario` / `analog_prior` | `position_multiplier=0.0` | 只进 hypothesis/scenario_prior/watch_priority |
| `overnight_ensemble_ranker` 自身 | `position_multiplier=0.0` | 只排序/提 watch priority |
| 因子结论缺 `random_ic_mean`/`alpha_t` 或缺 `n_factors_scanned`（v2.33） | `readiness_level` 封顶 `research_hypothesis`，`position_multiplier=0.0` | 未过同宇宙随机对照零假设或未声明扫描规模 |
| `train_only`/`noise`/`reversed_strict`（v2.33 四态分类） | `position_multiplier=0.0` | 只进研究假设账本，不得作为加分信号 |
| `hk_deep_value_no_catalyst`（港股深度价值无收敛契机，v2.45） | `entry_permission` 封顶 `WATCH`（观察池，不得右侧买入）；`position_multiplier` 不单独设值，仅继承既有按 module 聚合规则（本条为门槛型收紧，不发明数值） | 右侧买入纪律的编译形态；来源 `hk-offshore-market-playbook.md` 第 1、6 节 |
| `hk_momentum_drawdown_review`（港股动量持仓自高点回撤 10-20% 区间，v2.45） | 强制触发止盈/止损复核，复核前不得维持或提高原动作等级 | 只收紧复核纪律，不替代 `trading-laws.md` 永久回撤红线；来源 `hk-offshore-market-playbook.md` 第 5 节 |
| `counter_consensus_thesis`（逆共识命题） | `position_multiplier<=0.3`；`falsifier` 或 `time_stop` 任一缺失时 `entry_permission` 封顶 `WATCH` | 编译进 `endogenous_structure`；把人性判断限制为有仓位上限、有证伪条件、有时间止损的有界输入 |
| Tighten-only 不变量 | 所有 vX 新增门只收紧可交易集，永不放宽动作等级/仓位上限 | 全局 |

本表在 `scripts/decision_compiler.py` 的 `MODULE_POSITION_MULTIPLIER_CAP` / `_hk_overlay_entry_doc_ref` / `apply_caps()` 中运行时强制：模块乘数封顶硬钳制、`counter_consensus_thesis` 缺 `falsifier/time_stop` 的 WATCH 门、零乘数模块伪造 TEST/BUILD/ADD 直接 fail-closed（而非静默钳制）、`required_modules` 不得由 payload 自我缩小（内置 baseline 按 `decision_context.intent` 推导，见脚本注释）。`hk_deep_value_no_catalyst` / `hk_momentum_drawdown_review`（第 76-77 行）在代码里按 `module_signal` 上的同名布尔字段生效，不绑定特定 `module` 名——与 `hk-offshore-market-playbook.md` 第 136 行「对应现有模块」的表述一致，由携带该发现的模块（`fundamentals`/`endogenous_structure` 等）在信号上直接携带该字段。

## Mira Quality Gate → Compiler 映射（v2.7）

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| `readiness_level=draft/not_actionable/needs_refresh` | `research_readiness` | L0 |
| `readiness_level=working_view/watch_only` | `research_readiness` | L1 |
| `readiness_level=actionable_with_caveats` | `research_readiness` | 可 L2/L3；结论必须携带 caveat 注记，caveat 未解决不得升 L4+ |
| `knowability_status=irreducible_uncertainty` | `research_readiness` | L0 |
| `evidence_category=stale/contradicted/unknown` 支撑核心结论 | `data_quality` | L0/L1 |
| material `derived_calculation` 缺 formula / calculation_ref | `calculation_quality` | L0/L1 |
| `license_scope=paid_restricted/vendor_restricted` 且无可公开复核来源 | `ingestion_permission` | 最高 working_view，不支持公开 durable conclusion |

## v2.10 新模块映射

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| Grok 全网结果只有摘要、无原始 URL/时间戳 | `grok_web` | L0 |
| Grok 发现原始官方/公司/行情来源并完成交叉验证 | 转入 `filing/fundamentals/market_data` | 按原始来源裁决 |
| 回测缺 walk-forward / 成本 / 容量 / 回撤 / no-lookahead 任一关键项 | `quant_robustness` | L0/L1 |
| 回测通过多周期、多市场、含成本和 drawdown 检查 | `quant_robustness` | 可辅助排序；不自动加分 |
| 真实组合缺账户只读数据或保证金/币种/集中度未知 | `portfolio_risk_budget/account` | 不给具体数量 |

## v2.14 预测市场映射

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| Polymarket/预测市场只有截图、社媒转述、无原始市场 URL 或无时间戳 | `prediction_market_prior` | L0 |
| 原始市场 URL 可用但价差/深度/成交/OI/规则风险缺失 | `prediction_market_prior` | L0 |
| 流动性薄、spread 宽、resolution rule 模糊或争议 | `prediction_market_prior` | L0/watch |
| 市场深、spread 可接受、规则清楚、时间戳新鲜并有独立来源交叉验证 | `prediction_market_prior` | 最高 L1 watch/scenario_prior；`position_multiplier=0.0` |

预测市场概率不是事实验证：它不能替代 LongBridge、财报、公告、交易所/监管材料、公司原始材料或 Mira readiness；只能提示“市场正在如何定价”。

## v2.28 MiroFish 群体模拟映射

`modeled_scenario` 详见 `references/mirofish-swarm-simulation-patterns.md`。它吸收 MiroFish 的 ontology-first / swarm simulation / simulation_trace 研究纪律，但不引入 AGPL-3.0 代码、Zep/OASIS/camel 依赖或运行器。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|---|
| 只有模拟 Agent 行为、采访回答、合成未来路径，未回到真实来源验证 | `modeled_scenario` | L0；`position_multiplier=0.0` |
| 情景沙盘提出了可验证风险分支，但真实行情/公告/财报/监管/期权证据未抓取 | `modeled_scenario` | L0；只生成 evidence_collection_plan |
| 模拟观察已被 LongBridge、公告/财报、交易所/监管、真实新闻、行情/期权数据独立验证 | 转入对应真实证据模块 | 按原始证据裁决；模拟文本本身不加分 |
| 模拟结果与真实证据冲突 | `conflict_ledger` + 对应真实证据模块 | 未裁决前 L0 |

MiroFish-style modeled scenario 比 prediction-market prior 更弱：它不是市场出价，也不是外部事实，只是合成情景。它可以改变"下一步查什么"和"先观察谁"，不能改变"能不能买、买多少"。

## v2.29 参与者流映射

`participant_flow` 详见 `references/participant-flow-motivation.md`。它是第一性原理层（Step 0），在任何其他方法论之前执行。它不单独决定动作等级，但为其他模块提供参与者语境。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|---|
| 参与者图谱清晰、边际买卖双方稳定、动机变化可识别 | `participant_flow` | 无直接限制；其他模块按正常流程裁决 |
| 参与者图谱清晰、且主导流方向与决策方向一致；若输入为 ETF/基金流，须以 `净流入 / 基金规模` 表达并落在自身近 N 期高分位（披露 N/阈值） | `participant_flow` | 可小幅提升 confidence（`confidence += 0.1`），不提高 action level；ETF/基金仅有名义流量或 AUM 缺失/过期时不加分、退回中性并记 `data_gaps: flow_not_scale_normalized` |
| 参与者图谱清晰、但主导流方向与决策方向相反 | `participant_flow` | 降 confidence（`confidence -= 0.2`）；逆主导流交易必须有明确动机变化催化剂 |
| 参与者结构剧烈变化（如机构集体减持、short squeeze、lockup 到期）但方向未明 | `participant_flow` | 最高 L1 watch；等待结构稳定 |
| 关键参与者数据不可得（持股结构/做空比例/资金流缺项） | `participant_flow` | 标注 `participant_gap`；降 confidence；不单纯因缺参与者数据直接否决 |
| 边际买卖双方逻辑出现未定价的催化剂变化 | `participant_flow` | 标记 `motivation_change_detected` 进入 hypothesis_ledger；最高 watch priority，不单独提高 action level |

## v2.21 US close-to-open 执行窗口映射

`execution_window` 是美股专用 overlay，详见 `references/us-close-to-open-execution-overlay.md`。它只处理“收盘前 10 分钟 + 次日开盘 10 分钟”的执行降维，不改变研究结论。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| 非 US 标的、A股、港股 | `execution_window` | L0（回到对应市场框架） |
| 上游 Decision Compiler 为 L0 | `execution_window` | L0 |
| 无隔夜催化/setup、关键报价/流动性/成本缺失 | `execution_window` | L0/L1 |
| binary event 未拆情景，或 near-expiry 负 gamma 贴近 put wall | `execution_window` | L0/L1 |
| `pre_open_read=weak`、候选篮子明显回吐、或 UVXY/波动率确认风险扩散（v2.27） | `execution_window` | L0/L1；开盘退出/不延长 |
| `pre_open_read=strong`（SPY/QQQ/篮子健康度/赢家守住/流动性均确认，v2.27） | `execution_window` | 只允许观察到 09:40；不提高动作等级/仓位，09:40 后必须新决策 |
| US 标的、上游允许 L2/L3、流动性/成本/隔夜命题均通过 | `execution_window` | 不超过上游动作等级；仓位 cap 不上调 |

输出必须标注：`entry_window=15:50-16:00 ET`、`pre_open_read=strong|neutral|weak`（SPY/QQQ、basket green_count/avg、winners_holding、UVXY/vol_confirmation、known_flow、spread/liquidity）、`exit_window=09:30-09:40 ET next session`、`exit_rule=09:40 后若继续持有必须重新跑 intraday/swing Decision Compiler`。

## v2.35 Short-Cycle Market Structure Overlay 映射

`references/short-cycle-market-structure-overlay.md` / `scripts/short_cycle_structure.py` 不新增 module 或动作等级。先以原多因子结果得到不可变 upstream cap，再把 overlay 的两条输出分开编译：

- equity/underlying：原 upstream modules + `underlying_module_signals`
- option instrument：原 upstream modules + `option_module_signals`

禁止把两条数组合并；否则坏期权合约会错误否决 equity thesis。

| 输入状态 | 既有 module_signal | 默认动作上限 |
|---|---|---|
| `pre_open.status=weak` | `execution_window` | L0；开盘结束 close-to-open trade |
| 09:40 continuity fail | `execution_window` | L0；退出/减仓 |
| 09:40 continuity pass | `execution_window` | 不超过 upstream；必须新跑 intraday/swing Compiler |
| continuity mixed 或缺≥2组件 | `execution_window` / `data_quality` | L0/L1；不延长 |
| confirmed-live 负 gamma + 现货低于 put wall/flip（long） | `gamma` | L0/L1；低于 put wall 可 hard veto |
| delayed/stale/invalid gamma | `gamma` / `data_quality` | 只收紧；不得据此解除限制 |
| option BBO stale/invalid、size 不足、spread 超阈值、cost edge≤0/unknown | `data_quality:option_execution`（option branch only） | option L0；underlying unchanged |
| option 仅适合 non-marketable limit | `data_quality:option_execution`（option branch only） | option L1；接受 non-fill |
| EOD 样本未过 minimum | 无 current-day module effect | 不调权 |
| EOD 样本过门且 Brier 可算 | advisory soft-weight suggestion | `applied=false`；单次绝对变化≤0.15；原多因子/hard gate 不变 |

`checkpoint_verdict=recompile_intraday` 是流程权限，不是动作升级。overlay 的 action cap 不得高于 upstream；overlay `position_multiplier` 是 `[0,1]` 的相对修正值，必须与不可变 upstream signal 一起送入最终 Compiler，使最终倍率不超过 upstream。

## v2.22 Capex cashflow duration 映射

`capex_cashflow_duration_rotation` 详见 `references/capex-cashflow-duration-rotation.md`。它不是新动作模块，只把 AI/半导体内部的现金流久期分化映射到既有模块。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| 只有截图/KOL 观点，无行情横截面、利率变量、现金流/订单交叉验证 | `endogenous_structure` / `x_frontline` | L0/watch |
| 有 payer vs receiver 行情分化，但缺财报/订单/共识修正证据 | `endogenous_structure` | 最高 L1 watch；不加仓 |
| 有行情 + 利率 + 现金流/订单/共识修正交叉验证 | `macro` + `fundamentals` + `endogenous_structure` | 不超过上游基本面与风控允许等级；仓位 cap 不上调 |
| 与 `active_deleveraging`、Gamma hard veto、账户风险或基本面 falsifier 冲突 | 对应高优先级模块 | 回到更保守上限 |

## v2.24 隔夜集成投票排序映射

`overnight_ensemble_ranker` 详见 `references/overnight-ensemble-ranker.md`。它不是新动作模块，是美股专用的横截面**发现/排序先验**，纪律等同 `grok_web`（规则 9）/`prediction_market_prior`（规则 11）/`execution_window`（规则 12）：只改 watch priority 和候选顺序，自身 `position_multiplier=0.0`。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| 仅排序输出、头部候选未过 Close-to-Open overlay + Compiler 复核 | `execution_window`（排序先验） | L0；只提高 watch priority |
| `predicted_overnight_return` 缺 walk-forward/成本/no-lookahead | `quant_robustness` | L0/L1；预测数字只作 hypothesis，不进排序权重 |
| KOL 成分超过 Cap & Tighten-Only Registry 的 `x_frontline` 封顶或未过身份/交叉/时间验证 | `x_frontline` | 不提高仓位；仅提高观察优先级 |
| `|vote| < conviction_floor`（未过信念阈值，v2.26） | `execution_window`（排序先验） | L0；no_edge，不进 head set，最高 watch |
| `political_disclosure` 国会/政治披露成分（v2.26·C） | `x_frontline` | 进 vote 但封顶值以 Cap & Tighten-Only Registry 为准；由 `calibration_scorecard.py` 按 disclosure_present 分桶自调；不独立提高仓位/动作等级；必标 `disclosure_lag_days`（STOCK Act ≤45 天） |
| 持仓窗口内有财报/二元事件（earnings_blackout，v2.26） | `event_proximity`/`execution_window` | 移出可交易集，无论票数多高 |
| 非 US / A股 / 港股 | `execution_window` | L0，回到对应市场框架 |
| 头部候选已过 Close-to-Open overlay 且上游允许 L1/L2 | 转入对应上游模块裁决 | 不超过上游动作等级；排序层自身仓位乘数 =0.0 |
| risk_regime=active_deleveraging/forced_liquidation 或 gamma hard_veto | 对应高优先级模块 | 排序结果作废，回到更保守上限 |

排序层永远在 Redline Cap 之下：能改变"先看谁"，不能改变"能不能买、买多少"。`conviction_floor`/`earnings_blackout`（v2.26）只会**进一步收紧**可交易集；`political_disclosure`（v2.26·C）是封顶 0.10、权重校准自调的有界 vote 成分，可小幅改变排序但永不放宽动作等级或仓位上限；`pre_open_read`（v2.27）属于次日退出管理，只允许把 winners 持有到 09:40 或更早退出，不新增 alpha。

## v2.25 景气度·戴维斯双击映射

`prosperity_davis_double` 详见 `references/prosperity-davis-double-framework.md`。它不是新动作模块，是一套选股方法，结论编译进 `fundamentals`（景气趋势/业绩超预期）。其「不做 DCF」「宏观降权」只在方法内部生效，不否定 `liquidity-valuation-duality`，更不覆盖 `risk_regime`。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| 周期长度 <1 年或判不出长度 | `fundamentals` | 全篇降级；最高 L1 watch |
| 双门槛只满足空间大、无短期业绩（主题投资） | `fundamentals` | 交易性仓位，最高 L1；转 `early_stage_quality` 评分 |
| 双门槛只满足业绩好、空间小 | `fundamentals` | 赔率不足，不进重仓候选 |
| 估值已透支未来 1-2 年业绩 | `fundamentals` | 只观察，不推荐当下买入 |
| 方法适用性自检：风险偏好极低（红利占优） | `fundamentals` | 降为绝对收益视角 |
| 方法适用性自检：风险偏好极高（炒主题，类 2015） | `fundamentals` | 提示主题风格占优，本方法跑输但不亏钱 |
| 双门槛齐 + 周期≥2 年 + 板块性 beat + 估值未透支 | `fundamentals` | 进核心推荐；不超过 risk_regime/Gamma/账户允许等级 |
| 与 active_deleveraging、Gamma hard veto、账户风险或基本面 falsifier 冲突 | 对应高优先级模块 | 回到更保守上限 |

A 股数值锚（科技 10-40 倍、白酒批价、制造业 40 倍卖出纪律、公募 2-4 核心集中审美）禁止直接套用港股/美股，必须按当地事实重填后才进入裁决。

## v2.30 三时钟 / 二阶供给冲击 / VRP / KOL 轮巡映射

来源：`references/cycle-position-three-clocks.md`、`references/second-order-supply-shock-mapping.md`、`references/options-gamma-structure.md`（VRP 门）、`references/x-frontline-intelligence.md`（KOL 轮巡）、`references/macro-dashboard-four-pillar.md`（§8 反应函数/已知流日历）。全部编译进既有 module，不新增动作等级、不新增仓位来源。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| `signpost_count>=2` / `second_derivative=rolled_over` / `earnings_reaction_quality=fade_after_pop\|no_pop` | `fundamentals` | 收紧一档；`signpost_count>=4` 视同周期顶确认进 L4 讨论；路标只收紧不加分 |
| `analog_prior`（"X 是 Y 的某年某月"模板映射） | 按 `modeled_scenario` 同档 | L0；只作 scenario_prior/watch priority，`position_multiplier=0.0`；缺双向 analogy_breaks 清单 = 不合格引用；类比价格区间只作 hypothesis |
| 涨价论点 `jevons_check` 答不出 `thesis_half_life` | `fundamentals` | 只按「进行中周期」交易，禁止按「新常态」估值 |
| `thesis_trade_gate` 缺 timing_catalyst | `fundamentals` | watchlist_only；论点正确 ≠ 交易成立 |
| 正 gamma + `VRP<0`（range_expansion_warning） | `gamma` | pin 可信度下调；区间均值回归策略降杠杆，不满仓做 pin |
| 负 gamma + `VRP<0` | `gamma` | 最危险象限，风险优先，新多头收紧至 L0/L1 |
| `narrative_fact_consistency=inconsistent`（头条与主体可观察行为矛盾） | `endogenous_structure` | 只降级该叙事的证据等级，不反向作为加仓理由（误伤 ≠ 立即回补） |
| 二阶供给冲击 Step 1-4 未做完（缺交叉验证） | `endogenous_structure`/`fundamentals` | 本层结论最高 L0/L1 |
| `leader_gap_integrity=broken_unrecovered`（领头羊跌破财报缺口且次日未收回） | `endogenous_structure` | 同主题多头 de-risk 进 L4 讨论；硬收紧触发，不自动生成做空信号 |
| `kol_model_signal`（KOL 自报战绩/点位命中） | `x_frontline` | 只作线索登记 decision memory，由 `calibration_scorecard.py` 事后实测；不提高 reliability ceiling |
| `subscriber_only_gap`（订阅墙只见标题/预览） | `data_quality` | 登记缺口，禁止脑补内容 |
| `kol_method_card` 公开材料不完整、缺发布日期/原始 URL/作者/抓取时间，或 lifecycle / direct-vs-inference 映射不完整 | `research_readiness` / `data_quality` | `partial`，最高 L1；仅允许补证、刷新来源或人工复核 |
| `kol_method_card` 使用 blocked / subscriber-only 材料支撑生命周期结论 | `research_readiness` / `data_quality` | `blocked`，最高 L0、`position_multiplier=0.0`；不得固化为已接受结论 |
| `known_flow_events` 与结构破位冲突 | `endogenous_structure` | 破位纪律优先；已知机械流不豁免破位 |

### v2.42 KOL Method Card 生命周期映射

固定字段覆盖 `public_claim → falsifiable_thesis → universe_selection → entry → add_reduce → stop_invalidation → take_profit → exit → sizing_risk → post_trade_calibration`。每一阶段必须保留 canonical claim type、`direct_assertion | framework_inference | self_reported_performance | not_found_publicly`、来源文档 ID、支持/反对 EID、证据状态与 falsifier/unknown。`self_reported_performance` 只能是 `opinion + unverified`；未观察到公开规则必须写 `not_found_publicly`，不得反推订阅内容。Method Card 的 portable parts 只进入研究 handoff；最终裁决仍由原 Evidence / Mira / Decision Compiler / Decision Memory 主链完成。

## v2.33 严格因子验证门映射

`quant_robustness` 的前置补强，详见 `references/factor-validation-strict-gate.md`。它不是新动作模块，是 Quant Robustness Gate 的零假设检验前置层：任何因子/信号的 IC 类证据必须先过同宇宙随机对照，再谈 walk-forward/成本/容量/回撤/no-lookahead。

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| 因子结论缺 `random_ic_mean`/`alpha_t` 或缺 `n_factors_scanned` | `quant_robustness` | `readiness_level` 封顶 `research_hypothesis`；`position_multiplier=0.0` |
| 单因子扫描声明 `n_factors_scanned` 但 t<3.5（Harvey-Liu-Zhu 门槛）且无随机对照 | `quant_robustness` | L0/L1；不构成 alpha 证据 |
| 因子状态 = `train_only`（train 通过、test/OOS 失败） | `quant_robustness` | L0/L1；过拟合证据，不得作为加分信号 |
| 因子状态 = `noise`（与随机对照统计不可区分） | `quant_robustness` | L0/L1；不得作为排序或仓位输入 |
| 因子状态 = `reversed_strict`（显著负 `alpha_t`） | `quant_robustness` | L0/L1；先查数据错误，再考虑反向信号解释 |
| 因子状态 = `confirmed_alive`（全样本 + OOS 均跑赢随机对照） | `quant_robustness` | 可作为 `module_signal` 输入参与排序/风险上限；仍须过既有 walk-forward/成本/容量/回撤/no-lookahead 检查 |

只收紧：本门槛不豁免、不替代既有 Quant Robustness Gate 的检查项，只在其基础上叠加零假设检验要求；`confirmed_alive` 不自动提高 action level 或 position cap。

`train_only`/`noise`/`reversed_strict`/待验证的因子结论应同步登记进假设注册表（`scripts/hypothesis_registry.py`，详见 `references/hypothesis-lifecycle.md`），避免报告一次后失踪；注册表本身是台账，不是 `module_signal` 输入，不改变本节任何封顶值。

## v2.48 OKX 适配映射

| 输入状态 | 既有 module_signal | 默认动作影响 |
|---|---|---|
| 五因子、EID 或 freshness 缺失 | `data_quality` + `research_readiness` | 新仓 BLOCK/L0；总分不发布 |
| material evidence conflict / 账实 drift | `conflict_ledger` | 认知型 hard veto；新仓 BLOCK，已有持仓 HOLD + manual review |
| market/account/orders stale 或 WS 恢复后 REST baseline 未完成 | `data_quality` | 认知型 hard veto；last good 只展示，不解锁新仓 |
| 策略 heartbeat 丢失、stopped/faulted | `data_quality:execution_supervision` | 新仓 BLOCK；`pause_required=true`，不得声称 pause 已生效 |
| spread/深度不合策略门 | `liquidity` | 只收紧 action/position cap；严重时市场风险型 veto |
| account/portfolio hard redline | `account` | 市场风险型 hard veto；已有持仓可进入 EXIT 语义 |
| public-only 且无账户状态 | `data_quality` | 可做市场研究；不给账户级数量或 execution permission |
| `entry_score.v1` 完整高分 | 无 ModuleSignal | 只发布解释分；`compiler_effect=none`，不得形成动作或仓位许可 |

`pause_required` 是监督状态，不是订单动作。只有独立 Supervisor/Execution Engine 实际停住后才可标 `pause_effective=true`；本 Skill 不执行暂停、撤单或下单。

## v2.55 多周期预测 / 信号融合 / GEX 冲突映射

来源：`references/multi-horizon-prediction-contract.md`、`references/signal-fusion-risk-budget.md`、`references/options-gamma-structure.md`（多 vendor Conflict Ledger）。全部映射既有 module，不新增动作等级。

| 输入状态 | 既有 module_signal | 默认动作影响 |
|---|---|---|
| 异质 horizon 共用一个方向/胜率分，或缺 `horizon_id` | `conflict_ledger` / `research_readiness` | 认知型 veto 或 L0/WATCH；倍率 0 |
| 相关信号算术平均试图抬倍率或动作 | `calculation_quality` | 否决融合；回退更保守 upstream |
| 信号冲突仍 `risk_budget_scale>1` | `portfolio_risk_budget` | 强制 scale≤1 或 BLOCK entry |
| 多 vendor GEX flip/wall 冲突后取平均价 | `gamma`（tighten-only） | 取更保守上界；不得抬 upstream |
| 用名义 0DTE 成交额代替净 gamma | `calculation_quality` / `gamma` | 降级为 data gap / 观察 |
| IV skew 未控制借券费即作方向 | `quant_robustness` | 方向权重 0 |

## 模块优先级

从高到低：

1. 风控红线 / 账户合法性 / no_order_execution
2. forced_liquidation / active_deleveraging / liquidity squeeze
3. Gamma hard veto（跌破 Put Wall、负 gamma、墙位失效）
4. 数据缺口、证券/产品身份解析失败、状态陈旧与账实未对账
5. 宏观四象限与内生市场结构
6. 官方/财报/市场数据事实
7. 产业链/X-Ray/瓶颈评分卡
8. 量化稳健性 / 组合风险预算
9. 执行窗口约束（US close-to-open 等，只降级不加分）
10. Grok 全网/X 一线线索、预测市场先验、modeled_scenario 与社媒叙事
11. 技术形态与情绪短线信号

## Redline Cap

| 触发条件 | 新多头动作 | 已持有动作 |
|---|---|---|
| 证券身份无法唯一解析 | L0 | 不给账户级动作 |
| OKX `instId`/Wallet token identity 未核验或地区资格未知 | L0 | 保留研究观察；不给账户级动作 |
| OKX 关键 feed stale、策略无心跳、REST baseline 未完成、订单 unknown、账实 drift 或批量 item 部分失败 | L0/BLOCK | 认知型缺口不机械卖出；若同时触发 account 市场风险红线则按更高优先级 EXIT |
| Unified Tokenized Stock 短历史、24/7 wrapper 偏离、价差/深度不合门 | 最高 L0/L1 | 只收紧；不得拿底层股票高分覆盖 wrapper 风险 |
| forced_liquidation | L0 | L4/L5 |
| active_deleveraging | 最高 L1 | 降低仓位/保护 |
| 跌破 Put Wall 且未收回 | L0 | 降仓/等待修复 |
| Gamma Flip 下方且 near expiry 负 gamma | 最高 L1 | 降仓/严控 |
| 基本面核心 falsifier 触发 | L0/L5 | L4/L5 |
| 重大司法/退市/停牌/ST 风险 | L0/L5 | L4/L5 |
| 账户保证金/币种/集中度风险未知 | 不给具体数量 | 仅给情景分支 |
| X/Grok 未验证线索 | 不提高仓位 | 仅提高观察优先级 |
| 估值锚触达但作者明确技术触发未满足（如 RSI14 仍高于阈值） | L0/L1 watchlist | 不加仓；等待触发或新证据 |
| Grok 全网缺原始 URL/时间戳 | L0 | 仅作为待验证线索 |
| Polymarket/预测市场缺原始市场 URL、时间戳、价差/深度/流动性或 resolution rule 检查 | L0 | 仅作为待验证线索 |
| Polymarket/预测市场完整且高流动性 | 最高 L1 watch/scenario_prior；`position_multiplier=0.0` | 不加仓；等待官方/基本面/行情证据 |
| MiroFish-style 群体模拟 / Agent 采访 / synthetic future path 未经真实证据验证 | L0；`position_multiplier=0.0` | 仅作为 hypothesis_ledger / scenario_prior / evidence_collection_plan |
| 回测/因子存在 lookahead bias 或无成本/容量约束 | L0 | 不作为加仓依据 |
| 因子/信号缺同宇宙随机对照（`random_ic_mean`/`alpha_t`）或缺 `n_factors_scanned` | L0/L1；`readiness_level` 封顶 `research_hypothesis` | 不作为加仓依据 |
| 因子状态为 `train_only`/`noise`/`reversed_strict` | L0/L1 | 只进研究假设账本，不得作为加分信号 |
| 美股 close-to-open overlay 被错误用于 A股/港股/非 US 标的 | L0 | 回到对应市场框架，不输出隔夜执行窗口 |
| close-to-open 成本后 edge 不足、流动性/报价缺口、或 09:40 后未重新裁决仍继续持有 | L0/L1 | 停止把隔夜单延长为波段单 |
| Capex 收钱方/花钱方观点只有截图/KOL、无行情横截面/利率/现金流证据 | L0/L1 watch | 仅提高研究优先级，不加仓 |
| Capex receiver 当前收钱但估值拥挤、订单/EPS 无上修或系统风险覆盖 | 按更高优先级模块降级 | 不把"收钱方"机械等同买点 |
| 参与者流：参与者结构剧烈变化（机构集体减持、lockup 到期、short squeeze）但方向未明 | 最高 L1 watch | 等待结构稳定再判断 |
| 参与者流：逆主导流交易且无明确动机变化催化剂 | 降 confidence（`confidence -= 0.2`），不改 action level | 不因逆流直接否决，但需要更硬催化剂 |
| 参与者流：关键参与者数据不可得（持股/做空/资金流缺项） | 标注 `participant_gap`, 降 confidence | 不单纯因缺参与者数据直接否决 |
| 景气度周期长度 <2 年或判不出 | 全篇降级，最高 L1 watch | 降低 conviction，不重仓 |
| 景气度双门槛缺一项（空间大无业绩 / 业绩好无空间） | 空间大无业绩→最高 L1 交易性仓位；业绩好无空间→不重仓 | 不进核心重仓池 |
| 景气度结论把 A 股数值锚（10-40 倍/批价/40 倍）直接套到港股/美股 | L0 | 按当地事实重填后再裁决 |
| `analog_prior` 类比被用作目标价或仓位依据 | L0 | 改写为 hypothesis / scenario_prior 后再裁决 |
| 正 gamma 但 `VRP<0` 时满仓做区间均值回归 | 最高 L1 | 降杠杆；pin 可信度下调 |
| `leader_gap_integrity=broken_unrecovered` | L0/L1 | 同主题多头进 L4 de-risk 讨论 |
| 二阶供给冲击结论缺叙事-事实/资产代际/技术性解释交叉验证 | L0/L1 | 只提高观察优先级 |

## 输出格式

```markdown
Decision Compiler:
- 短线执行：L0/L1/L2...
- 波段持有：...
- 中线观点：...
- final_position_cap：__%
- hard_veto：true/false，原因：...
- 主要降级模块：risk_regime / gamma / data_gap / x_frontline / ...
- 修复信号：...
```

## 方法 → 落点 module 映射契约

`method_router.py` 的方法名是研究路由镜头，不是 Compiler module。下表把
16 个 `METHOD_LABELS` 钉到现有 `REGISTERED_MODULES`；第一项是主落点，后续项
只在方法确实产出对应类型事实时作为次级落点。映射本身不生成正向
`module_signal`，也不提高动作等级或仓位上限。

<!-- method-module-map:start -->
| 方法 key | 落点 module（主 → 次） | 边界 |
|---|---|---|
| `huayuan` | `fundamentals`, `filing` | 叙事先回到财报/公告事实 |
| `serenity` | `fundamentals`, `endogenous_structure` | 供需瓶颈为基本面主落点，结构分化为次级 |
| `youzi_emotion` | `participant_flow`, `endogenous_structure` | 情绪与边际资金优先，不把热度当事实 |
| `wyckoff` | `market_data`, `endogenous_structure` | 量价事实与结构解释分列 |
| `livermore` | `market_data`, `endogenous_structure` | 趋势/关键点不越过上游风控 |
| `factor` | `quant_robustness` | 因子只经严格门参与排序/收紧 |
| `poisson` | `event_proximity`, `research_readiness` | 事件时钟与等待纪律，不独立抬仓 |
| `options_gamma` | `gamma`, `data_quality` | Gamma 结构与数据新鲜度分列 |
| `supply_chain_xray` | `fundamentals`, `endogenous_structure` | 公司级供应链事实为主，横截面结构为次 |
| `early_stage_quality` | `fundamentals`, `research_readiness` | 早期主题缺口只会降低可行动性 |
| `endogenous_microstructure` | `endogenous_structure`, `participant_flow`, `dispersion_crowding` | 方法名不等于 module 名；按事实类型分流 |
| `macro_policy_news_account` | `macro`, `filing`, `x_frontline`, `account` | 宏观为主；官方、线索、账户事实不得混账 |
| `counter_consensus` | `endogenous_structure` | 使用 `counter_consensus_thesis` 子框架与 Cap 行 |
| `expected_returns` | `fundamentals`, `research_readiness` | 预期来源可靠性不能替代事实验证 |
| `bottleneck_scorecard` | `research_readiness`, `fundamentals` | 评分只排研究优先级，底层事实另入基本面 |
| `a_share_short_term` | `participant_flow`, `endogenous_structure`, `market_data`, `a_share_raw_source` | A 股情绪/资金为主，制度与行情来源单列 |
<!-- method-module-map:end -->

Overlay 文件每个只能声明一个主落点；多模块输出保留为次级证据分流。下表是
`validate_skill.py` 使用的显式 allowlist，避免用文件名或全文关键词猜测范围。
`adjudication_fallback` 表示原文件未指定唯一主落点，按本轮任务 2 #1 的
既有挂载清单落到 `endogenous_structure`；若文件文本与裁决清单都无法确定，
必须写 `needs_review`，不得猜测别的 module。

<!-- overlay-module-map:start -->
| Overlay reference | 主落点 module | 判定依据 |
|---|---|---|
| `attention-rumor-triage.md` | `endogenous_structure` | `adjudication_fallback` |
| `a-share-derivatives-ipo.md` | `endogenous_structure` | `file_explicit` |
| `capex-cashflow-duration-rotation.md` | `endogenous_structure` | `adjudication_fallback` |
| `cycle-position-three-clocks.md` | `endogenous_structure` | `adjudication_fallback` |
| `earnings-call-interpretation.md` | `fundamentals` | `file_explicit` |
| `etf-selection-rotation.md` | `endogenous_structure` | `adjudication_fallback` |
| `hk-offshore-market-playbook.md` | `endogenous_structure` | `adjudication_fallback` |
| `prosperity-davis-double-framework.md` | `fundamentals` | `file_explicit` |
| `rates-fx-crypto-overlay.md` | `endogenous_structure` | `adjudication_fallback` |
| `second-order-supply-shock-mapping.md` | `endogenous_structure` | `adjudication_fallback` |
| `semis-index-divergence-overlay.md` | `endogenous_structure` | `file_explicit` |
| `short-cycle-market-structure-overlay.md` | `execution_window` | `file_explicit` |
| `us-close-to-open-execution-overlay.md` | `execution_window` | `file_explicit` |
<!-- overlay-module-map:end -->
