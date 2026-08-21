---
name: trading-research
description: Use when the user asks for A/H/US stock, OKX public/read-only or tokenized-stock research, sector, macro, quant/backtest, options/Gamma, ETF, earnings-call, rates/FX/crypto overlay, A-share derivatives, Grok/X/web live signals, portfolio risk, execution supervision, or skill maintenance that must turn evidence into risk-bounded decisions without order execution.
version: v2.59
---

# trading-research

## 目标
- 把「单票叙事、宏观结构、内生拥挤、Gamma/VRP、参与者流、账户约束」编译成统一动作等级和风险边界；研究、复盘、校准、自优化全部走可复现的结构化流程。
- 覆盖 A/H/美股个股、行业、指数、组合、ETF、期权/Gamma、A 股衍生品、OKX public/read-only 与 Unified Tokenized Stocks、宏观/政策/利率/FX/加密 overlay、事件驱动、财报电话会、Grok/X/web live signals、模拟仓复盘与决策记忆。
- 默认 `no_order_execution`：只输出研究结论、风险边界、仓位上限、复盘时钟；不执行真实交易。

## Query Tier Router
| Tier | 触发判据 | 执行路径 |
|---|---|---|
| Tier 0 直答 | 单纯行情、持仓、盈亏、交易日/交易时段、单指标当前值 | 鉴权/数据源预检 → 取数 → 一行结论 + 数据时间戳 |
| Tier 1 速判 | 单标的能不能买/卖/加仓/止盈止损，未要求深度报告 | Decision Memory preflight → risk_regime 快照 → 关键数据 → Decision Compiler |
| Tier 2 完整研究 | 多标的/组合/宏观/产业链/期权/Grok-X/预测市场/财报/OKX 产品与执行监督/skill 维护 | 完整 Evidence → Mira Quality Gate → Decision Compiler → 简洁主回复 + 附录账本 |

任何 tier 都保留 `no_order_execution`、数据时间戳、缺口标注、刷新条件；Tier 0 只省略研究管线，不省略安全边界。三个 tier 的主回复格式见 `templates/universal-equity-report.md`。

## 固定流程
0. **Participant Flow 第一性检查**：谁是边际买卖双方、信念多强、什么改变他们逻辑；`references/participant-flow-motivation.md`。
1. **Cognitive Fork**：新兴主题先拆投资命题；`references/theme-cognitive-fork.md`。
2. **Resolve**：识别叙事链路、时间线、资产联动、市场成熟度。
2a. **Venue/Product Identity Gate**：涉及 OKX 时，先按 `references/okx-research-execution-supervision.md` 区分 CEX / Unified Tokenized Stocks / Wallet / DEX / Agent Trade Kit，核验 `instId` 或 chain/token identity、产品状态和地区资格；未知即 fail-closed。
3. **Decision Memory Preflight**：读同标的/同方向历史约束（`references/trading-decision-memory.md`）。因子/信号类结论先查 `scripts/hypothesis_registry.py search/list` 避免重复造已否证假设；产出新的因子/信号类结论（`open`/`train_only`/`noise`/`reversed_strict`/`confirmed_alive`）时必须 `hypothesis_registry.py create`（新结论）或 `update`（改判已登记的）登记，不得报告一次后失踪；`references/hypothesis-lifecycle.md`。
4. **Event Reaction Memory**：事件驱动/共动先验用 `scripts/event_reaction_journal.py` + `scripts/relationship_graph.py` 登记，不自动改仓位。
5. **Evidence + Research Chain**：Tier 2 强制按 `宏观→行业→公司→情绪→资金结构→机会` 逐层输出 `state/as_of/evidence_ids/data_gaps/implication`；缺层、过期或冲突只允许降级，不得由后层分数越级抬仓。L1+ 结论至少 3 个独立 EID（与 `references/evidence-ladder.md` 晋级硬规则、`scripts/validate_report.py` 门槛一致），优先 LongBridge/公告/财报/交易所/监管/可复现行情数据。美股公司证据按底层 `source_family` 计独立性：LongBridge 与 SEC 直连命中同一 accession 仍只有一个 SEC 事实源；未知底层来源不计独立票。
6. **Source Provenance Guard**：外部原文、方法论蒸馏、直接引文或言行对照必须生成 `research_provenance.v1` 并跑 `scripts/provenance_guard.py`；`partial` 最高 L1，`blocked` 最高 L0 且不得写入已接受 claim；KOL 方法蒸馏用同一 bundle 的 `kol_method_cards`。
7. **Mira Quality Gate**：正式或可行动结论必须有 `readiness_level`、`stale_after`、`must_refresh_if`、claim-level posture；`references/mira-quality-gates.md`。
8. **Decision Chain + Compiler**：先走 `机会→评分→概率→风险→计划`。强制填写 `consensus_view/price_discounts/variant_view`、至少 3 条带观察指标的 premortem、默认 `risk_posture=neutral`、可结算概率合同与触发/失效/`no_trade_if`；未知概率不得伪造。最终动作由 `references/decision-compiler.md` + `scripts/decision_compiler.py` 裁决。`scripts/entry_score.py` 只提供可解释展示分，`scripts/okx_execution_supervisor.py` 只提供现有 modules 的监督约束，两者都不能替代 Compiler。US 短周期请求先编译不可变的上游方向/动作上限，再由 short-cycle overlay 生成只收紧的执行窗约束；underlying 与 option branch 分别重编译。L1+ 可执行结论必须 record-decision；可结算概率另用 `scripts/prediction_ledger.py` 登记，主回复写 `write_status` 与 `completeness`，缺状态行会被 `scripts/validate_report.py` 拒绝。用户明确授权 OKX Demo 时，只能输出 `okx_demo_research_bundle.v1` 给独立 `~/.hermes/okx-demo-trading`；外部引擎必须重放 SHA-256 固定的 EntryScore/Decision 编译器，本 Skill 仍不调用交易接口。
9. **Report Style**：主回复默认简洁决策版，结论先行；完整账本进文件/附录。格式唯一权威是 `templates/universal-equity-report.md`（Tier 0 直答 / Tier 1 速判 / Tier 2 单票·组合·多标的模板、表格纪律、闭环状态披露、附录触发条件），本文件不再重复格式细节。

## 能力地图

按域分组；每行「用途 → 文件」。references/ 与 scripts/ 前缀省略。

### 数据与行情
- 公开配置加载与安全状态检查（仅环境变量/示例配置，不回显密钥）→ `config_loader.py`
- LongBridge 主数据层（行情/财报/估值/监管申报/Form 4/股东/显式机构 13F/组合/期权/新闻）与免费 SEC EDGAR 缺口兜底 → `data-source-playbook.md`；`longbridge_query.py`、`us_company_evidence.py`、`security_resolver.py`（标的解析）、`market_router.py`（市场路由）、`fundamental_snapshot.py`
- A 股公开源补强（腾讯/东财/巨潮）与备用交叉验证 → `a-stock-data-source-layer.md`、`windclaw-a-share-bridge.md`、`a-share-short-term-layer.md`；`a_stock_data_bridge.py`、`windclaw_bridge.py`
- A 股情绪周期专项（涨跌停生态/连板梯队/板块扩散/情绪阶段/资金流状态；仅 A 股短线按需加载）→ `a-share-sentiment-cycle.md`；`a_share_sentiment_cycle.py`
- 通用数据新鲜度与交易日护栏（as-of 对齐、周末降级、缺失降级）→ `data_freshness_guard.py`
- 市场结构与风险快照（Gamma/VRP、拥挤/离散度、去杠杆、risk_regime）→ `options-gamma-structure.md`、`leverage-crowding-dispersion-playbook.md`、`deleveraging-liquidity-squeeze-playbook.md`；`options_gamma.py`、`dispersion_crowding.py`、`market_structure.py`、`risk_regime_snapshot.py`
- MCP/CLI/SDK/公开源降级链、期权链 delayed fallback、账户门 → `runtime-fallbacks.md`
- OKX CEX/Wallet/DEX/Agent Trade Kit 边界、Unified Tokenized Stocks 身份核验、公共盘口/K线快照 → `okx-research-execution-supervision.md`；`okx_public_snapshot.py`

### 情报与检索
- 每次调用刷新 72h 新闻/政策/宏观/结构；Grok 失败自动切多源，敏感查询 fail-closed → `live-intelligence-rotation.md`；`live_intel_run.py`
- 无 key 多源发现、健康检查、去重与来源矩阵；豆包搜索 Global 版作带 key 备用兜底 → `multi-source-search-layer.md`、`market-source-matrix.md`；`multi_source_search.py`
- Grok/X 只作发现和一线线索，不作事实锚点 → `grok-web-research-layer.md`、`x-frontline-intelligence.md`
- 热点/异动/传闻分离 attention 与 credibility → `attention-rumor-triage.md`
- 预测市场概率只作 market_pricing 先验 → `polymarket-signal-layer.md`；`polymarket_signal.py`
- 证据覆盖公平补抓与 `must_refresh_if` 结构化 → `intelligence-coverage-and-watch-triggers.md`；`intelligence_coverage.py`、`research_watch_trigger.py`
- 外部帖子/Substack/X 长文转循证研报；公众号文章推断关联 A 股 → `market-post-research.md`、`wechat-article-stock-inference.md`
- 财报电话会 / 业绩指引结构化解读 → `earnings-call-interpretation.md`

### 证据·溯源·质量门
- EvidenceItem/ModuleSignal/claim_type 主枚举与来源可靠性 → `data-contracts.md`、`source-reliability-policy.md`、`evidence-ladder.md`、`intelligent-research-contract.md`、`unified-research-framework.md`
- 五因子 0–100 入场展示分（缺因子不发布、冲突/资格只生成阻断信号、完整高分不生成正向 ModuleSignal）→ `multi-factor-evidence-synthesis.md`；`entry_score.py`
- 外部原文/引文/言行对照分层溯源 → `source-grounded-research-provenance.md`；`provenance_guard.py`
- KOL 公开方法十阶段 handoff（缺字段 fail-closed，自报战绩不加可信度）→ `kol_method_card.py`；`templates/kol-method-cards-public-ledger.json`
- 正式报告、行动判断、旧结论复用、材料摄入质量门 → `mira-quality-gates.md`
- 证据采集执行器 → `evidence_run.py`

### 裁决与风控
- module_signal → 动作等级、仓位倍率、hard veto；Cap & Tighten-Only Registry 唯一权威 → `decision-compiler.md`、`decision-cascade.md`；`decision_compiler.py`
- 组合门先于个股 alpha（持仓/保证金/集中度）→ `portfolio-risk-gate.md`
- 永久红线、回撤自检、泊松/座位/等待纪律 → `trading-laws.md`、`poisson-method.md`
- 反共识对抗评审 → `counter-consensus-framework.md`
- 模拟仓请求先证明不是实盘 → `longbridge-paper-trading-gate.md`
- 本次主导方法选择，主回复只露 Top 5 → `method-rotation-matrix.md`；`method_router.py`
- Tier 2 研究管线入口 → `research_run.py`
- OKX 策略心跳、feed freshness、持仓/订单/成交对账、last-good stale 与暂停建议；Demo 可显式使用 ≤30 秒 REST polling read-only projection，live 仍强制 private WS → `okx-research-execution-supervision.md`；`okx_execution_supervisor.py`
- 本地只读监督面板（30 秒相对路径刷新、ET、移动端、无订单/参数控件）；独立 companion server 只增加急停与只读状态问答 → `okx-research-execution-supervision.md`；`okx_monitor_dashboard.py`

### 场景与方法框架
- 参与者流/主题分叉/早期质量/社媒-技术入场门 → `participant-flow-motivation.md`、`theme-cognitive-fork.md`、`early-stage-theme-quality-framework.md`、`social-technical-entry-gate.md`
- 供应链 X 光/瓶颈评分/Serenity → `supply-chain-xray-playbook.md`、`serenity-method.md`、`bottleneck-scorecard.md`；`serenity_scorecard.py`
- 五维评分/预期收益/流动性-估值对偶/领先指标 → `multi-factor-evidence-synthesis.md`、`expected-returns-framework.md`、`liquidity-valuation-duality.md`、`leading-indicators-framework.md`
- 宏观四象限/政策/内生拥挤/被动流/发行解禁 → `top-down-broker-decision-framework.md`、`macro-dashboard-four-pillar.md`、`endogenous-market-structure-playbook.md`
- Capex 久期/景气度戴维斯/三时钟/二阶供给冲击 → `capex-cashflow-duration-rotation.md`、`prosperity-davis-double-framework.md`、`cycle-position-three-clocks.md`、`second-order-supply-shock-mapping.md`
- 股息率、分红安全、收益陷阱 → `dividend-quality-framework.md`
- ETF 筛选/轮动与个股替代决策 → `etf-selection-rotation.md`
- 港股离岸市场生存框架（港股标的/恒科/南向/高股息港股/港股ETF）→ `hk-offshore-market-playbook.md`
- 债券利率/FX 宏观 overlay 与 BTC/ETH 主流币框架 → `rates-fx-crypto-overlay.md`
- A 股 ETF 期权/可转债/打新 → `a-share-derivatives-ipo.md`
- 美股隔夜执行窗与横截面排序（只降级/排序/提 watch priority）→ `us-close-to-open-execution-overlay.md`、`overnight-ensemble-ranker.md`
- 短周期结构层（09:40 连续性、SPX gamma、期权执行质量、EOD 复盘调权；只判当日可执行性）→ `short-cycle-market-structure-overlay.md`；`short_cycle_structure.py`、`short_cycle_signals.py`、`short_cycle_review.py`
- 第三方期权 flow 告警/sweep 截图 → `options-flow-sweep-gate.md`
- 财报期权方向/幅度/隐含波动/LLM 历史重放的 point-in-time 与校准门、v2.59 可复现计算口径 → `earnings-event-options-prediction-gate.md`；`options_positioning_snapshot.py`、`earnings_move_history.py`、`earnings_implied_distribution.py`（CBOE 定位/ATM straddle 与 SEC 8-K + LongBridge session-aligned 历史反应；只读、零方向权重、零仓位）
- 多周期预测分账（盘中/隔夜/波段/中长期不得共用一个分数；缺 horizon 则不交易）→ `multi-horizon-prediction-contract.md`
- 信号融合与风险预算一致门（禁止平均加分；条件交叉；冲突则收缩预算）→ `signal-fusion-risk-budget.md`
- 回测/因子/模型审查与 IC 类证据严格门 → `open-source-quant-research-patterns.md`、`factor-validation-strict-gate.md`
- 因子研究执行器（qfq 面板、逐截面预处理、IC 随机对照、分位回测、四态裁决、survivorship 天花板；只研究不裁决）→ `factor-research-engine.md`；`factor_panel.py`、`factor_engine.py`、`factor_backtest.py`、`factor_verdict.py`
- 盘前因子筛选与部署编排（confirmed_alive-only 整数 vote、freshness 前置、US/A 股/盘中分线；只产 watch priority 与 tighten-only 风险信号）→ `factor-deployment-playbook.md`；`premarket_screen.py`
- 群体模拟只进 hypothesis/scenario_prior → `mirofish-swarm-simulation-patterns.md`

### 记忆·校准·复盘
- 决策记录、独立多周期预测登记、到期结算、同标的历史、判断质量归因 → `trading-decision-memory.md`；`trading_memory.py`（CLI 入口；拆分件 `trading_memory_core.py`、`memory_schema.py`、`memory_store.py`、`memory_review.py`）、`prediction_ledger.py`、`record_due_results.py`
- 假设生命周期注册表 → `hypothesis-lifecycle.md`；`hypothesis_registry.py`
- 事件登记、共动先验、反应残差 → `event-reaction-memory.md`；`event_reaction_journal.py`、`relationship_graph.py`
- System A 宇宙健康只读审计 → `universe-governance.md`
- 双系统边界、模拟仓分析、DecisionEnvelope/交易生命周期 → `two-system-trading-architecture.md`、`paper-portfolio-analysis-playbook.md`；`paper_trade_lifecycle.py`
- OKX venue-neutral 适配只增加只读 Research/Supervisor 角色；Demo Execution Engine 位于独立 `~/.hermes/okx-demo-trading`，以版本化 bundle 单向接收研究结果，不属于本 Skill → `okx-research-execution-supervision.md`
- tokenized wrapper 与底层股票基差校验（按时间距离最近的参考价选择，映射未证实即 fail-closed，严格 tighten-only）→ `okx-research-execution-supervision.md` §7a
- Demo 五阶晋级 S0–S4 与阶段化敞口（证据判据、`min(阶段, policy 硬顶)`、drift 需分类 spec_gap/infrastructure_fault）→ `okx-research-execution-supervision.md` §7b
- Demo 单向学习回路与静默看门狗（`okx_demo_learning_packet.v1`、`materiality_eligible=false`、人工消费）→ `okx-research-execution-supervision.md` §7c
- Paper 校准独立分桶（只读、`materiality_eligible=false`）→ `paper_outcome_calibration_feed.py`、`calibration_scorecard.py`
- 历史外部记忆迁移契约 → `yantrikdb-memory-transfer.md`
- 每日公开日志（盘前预测-盘中执行-盘后归因）→ `daily-public-journal.md`；`daily_journal.py`（CLI；拆分件 `daily_journal_data.py`、`daily_journal_render.py`）
- 数据轻量化/归档清理 → `data-retention-policy.md`；`data_retention.py`

### 自优化与回归
- 每日自检、自进化、护栏候选、周学习摘要、闭环活性监控 → `adaptive-self-optimization.md`；`self_optimization_check.py`、`self_optimization_ledger.py`、`learning_digest.py`、`eval_candidate_generator.py`
- 场景/输出回归与发布验证 → `scenario-regression-tests.md`；`validate_skill.py`、`validate_scenarios.py`、`validate_report.py`、`output_quality_regression.py`、`release_validation_runner.py`
- 方法论来源追溯与外部仓审计笔记 → `source-map.md`、`vnpy-audit-notes.md`

## 决策约束
- 风控先于观点：`risk_regime`、forced liquidation、liquidity squeeze、账户合法性、Gamma hard veto 覆盖静态分数。
- 缺口不脑补：源缺失、视觉信号不可复核、派生计算无公式/`calculation_ref`，必须降级并写入 DataGap/Conflict Ledger。
- 来源能力与本次证据分开：`unsupported/auth_missing/rate_limited/error/stale/missing` 不能改写成 0、无事件或负面事实；多标的补抓按 criticality round-robin。
- 分层溯源：direct quote 需本地 hash+行锚点；framework inference 不得冒充原话；反证未检查或引用链失败时最高 L0，不得污染 Decision Memory。
- 所有封顶与收紧语义以 `decision-compiler.md` 的 Cap & Tighten-Only Registry 为唯一权威。各 overlay（overnight/close-to-open、短周期结构、三时钟七路标、VRP、二阶供给冲击、KOL 轮巡、`conviction_floor`、`earnings_blackout`、`political_disclosure` 带 `disclosure_lag_days`、多周期预测合同、信号融合/风险预算一致门、多 vendor GEX Conflict Ledger）只能收紧/排序/提 watch priority，不能复活 L0、不能提高 action level 或 position cap、不得反向加分；短周期 09:40 通过只代表 `recompile_intraday`，坏期权 BBO 只封 option branch，EOD 只给 overlay soft weights 提建议。异质 horizon 禁止合成单一胜率；相关信号禁止算术平均抬仓；GEX flip/wall 多源冲突禁止平均成一个价。
- `ResearchWatchTrigger` 只能 `rerun_research/refresh_source/request_manual_review`，候选必须过 `scripts/research_watch_trigger.py`；不得创建 cron、外部 alert、邮件或订单；触发后必须刷新事实并重新编译。
- X/Grok/KOL/社媒只作线索：必须身份识别、交叉验证、时间验证；不得单独提高仓位或 reliability ceiling。`kol_method_cards` 十阶段（public claim→falsifiable thesis→universe/selection→entry→add/reduce→stop/invalidation→take-profit→exit→sizing/risk→post_trade_calibration）必须齐全，自报 PnL 永不抬 reliability。
- 搜索引擎结果页/标题/摘要只作 discovery candidate，必须回抓原始 URL 后重新定级；外部检索不得发送秘密/内部主机名/未公开材料，不读 cookie 或 `.env`。带 key 备用搜索（豆包 Global）只从白名单搜索变量 `VOLC_DOUBAO_SEARCH_API_KEY` 取值、只在主层失败/被封/候选不足时触发；带 key 不提高 reliability ceiling、不提高动作等级、不放宽 position cap，`auth_missing/blocked/rate_limited` 一律写 DataGap 而不是改写成「无消息」。
- 热点/传闻分离 attention 与 credibility：热度只能提高观察优先级或触发拥挤风险，不能提高事实可信度或仓位。
- Polymarket/预测市场最高 watch/scenario prior，`position_multiplier=0.0`；MiroFish/群体模拟、`analog_prior`、modeled scenario 只进 hypothesis/scenario_prior/watch_priority/evidence_collection_plan，不得写 verified_fact。
- 量化/回测必须过 Quant Robustness Gate：无 walk-forward、成本、容量、回撤、no-lookahead 时最高研究假设。
- `participant_flow` 是第一性原理层，不单独决定动作等级；参与者结构剧烈但方向不清时降级等待。
- A 股公开源、WindClaw、Grok、Web、MCP 降级链只能补证，不替代 Decision Compiler。
- OKX `entry_score.v1` 只作解释/readiness；任一关键因子、EID、freshness 或身份缺失时总分必须为 `null`，高分不得覆盖产品资格、流动性、账户和对账 hard gate。
- OKX feed 失败保留 last-good 并标 stale，禁止显示 0；Demo 可在 `rest_polling_healthy=true`、间隔不超过 30 秒且 orders REST baseline 完成时运行并显示 warning；live 仍强制 private WS。连接恢复后必须先完成 REST baseline，订单 unknown/部分失败或账实 drift 时暂停新仓。
- OKX CEX/Wallet identity 字段严格互斥；positions/orders/fills 必须比较 canonical 全量行，不能只凭相同 ID 判 matched。demo/live 只有 fixed-weight EntryScore 五因子完整、公式一致、无冲突且 ceiling 为空时才可能允许新仓。
- OKX public/read_only 只能分析；`new_entries_allowed` 只可能出现在通过只读 credential scope、外部 paper/live permission 及全部安全门的 demo/live 快照。健康监督不发正向 ModuleSignal。
- `pause_required` 只是监督结论；只有独立 Demo runtime 创建 `PAUSE.json`/`STOP` 后才可写 `pause_effective`。研究/对话 Agent、`okx_public_snapshot.py`、`entry_score.py`、`okx_execution_supervisor.py` 均不得读交易凭据或产生订单副作用。

## AnySearch 联动发现层
每次需要出网的研究查询，必须将 AnySearch 与 LongBridge/交易所/公司IR/`web_search`/Grok/`multi_source_search.py` 同批联动运行，不能以任一主源成功为由跳过；纯本地计算、用户给定材料只读分析、Tier 0 取已有行情值除外。finance/social_media 等垂直意图先 `get_sub_domains` 发现子域再追加垂直检索。

```bash
ANYSEARCH="${ANYSEARCH_BIN:-anysearch}"   # install on PATH or set ANYSEARCH_BIN
$ANYSEARCH batch_search --queries '[{"query":"<公开查询>","max_results":5},{"query":"<公开查询>","domain":"finance","sub_domain":"<sub>","sub_domain_params":"<k=v>"}]'
$ANYSEARCH extract "<publisher-url>"   # 只对候选原文回抓
```

- 只允许公开命题；禁止发送未公开交易计划、内部材料、个人数据、cookie、`.env`、token、私网地址或本地路径。
- 全部返回均为 `discovery_only`：必须回抓出版方/交易所/监管/公司IR原文、生成正常 EvidenceItem 重新定级；原文不可得写 DataGap，最高 `monitoring_only`。
- 每个查询包记录 `provider=anysearch`、查询、domain、时间、结果数、错误状态与后续原文 URL；连续两次失败停止本轮并记独立 DataGap，不影响其他联动源。

## Multica 可选单向协作

普通 standalone 路径始终可独立完成，不得等待协作模型；只有任务明确选择 Multica 协作时才启用以下单向、可跳过的研究分工：

- `standalone_default=true`
- `fable_5=one_max_compact_context_framework_pass;tools=none;files=none;code=none;retry=none`
- `grok=web_x_source_ledger_only`
- `gemini=long_context_index_only_when_needed`
- `sol_ultra=sole_corrector_writer_validator`
- `downstream_return_to_fable=false`

Fable 5 的单次框架建议若失败或为空，Sol Ultra 直接依据当前树和可核验证据继续；不得重试、不得把实现/验证结果回传给 Fable。Grok/Gemini 输出只是索引或 source ledger，不能代替原始来源、Evidence/Mira/Compiler，也无写权限。

## 数据源鉴权续期
- 鉴权失败不得静默跳过；立即触发对应续期流程，超时标注 `auth_gap`；同一数据源连续 2 次失败后停止重试并报告阻塞原因。
- LongBridge CLI：`longbridge check` / `longbridge auth login`。LongBridge MCP：`hermes mcp test longbridge` / `hermes mcp login longbridge`。Hermes Grok/xAI：`hermes auth status xai` / `hermes auth login xai`。

## 验证与维护
```bash
python3 scripts/validate_skill.py --all
# 分项参考：
python3 scripts/record_due_results.py --self-test
python3 scripts/prediction_ledger.py --self-test
python3 scripts/options_positioning_snapshot.py --self-test
python3 scripts/earnings_move_history.py --self-test
python3 scripts/earnings_implied_distribution.py --self-test
python3 -m unittest scripts/test_options_positioning_snapshot.py scripts/test_earnings_move_history.py scripts/test_earnings_implied_distribution.py scripts/test_fundamental_snapshot.py -v
python3 scripts/provenance_guard.py --bundle templates/research-provenance-bundle-pass.json --pretty
python3 scripts/provenance_guard.py --bundle templates/kol-method-cards-public-ledger.json --pretty
python3 scripts/paper_outcome_calibration_feed.py --self-test
python3 scripts/calibration_scorecard.py --self-test
python3 scripts/decision_compiler.py --self-test
python3 -m unittest scripts/test_entry_score.py scripts/test_okx_public_snapshot.py scripts/test_okx_execution_supervisor.py scripts/test_okx_monitor_dashboard.py -v
python3 scripts/hypothesis_registry.py --self-test
python3 scripts/multi_source_search.py --self-test
python3 scripts/daily_journal.py --self-test
python3 scripts/data_retention.py --self-test
python3 scripts/data_freshness_guard.py --self-test
python3 scripts/a_share_sentiment_cycle.py --self-test
python3 -m unittest scripts/test_a_share_sentiment_cycle.py -v
python3 scripts/factor_panel.py --self-test
python3 scripts/factor_engine.py --self-test
python3 scripts/factor_backtest.py --self-test
python3 scripts/factor_verdict.py --self-test
python3 scripts/premarket_screen.py --self-test
python3 -m unittest discover -s scripts -p "test_factor*.py"
python3 scripts/output_quality_regression.py
python3 scripts/validate_skill.py
python3 scripts/validate_scenarios.py
python3 scripts/validate_report.py templates/report-contract-pass.md --provenance-bundle templates/research-provenance-bundle-pass.json
python3 scripts/self_optimization_check.py --json --skip-network
# 可选 public-only 实时核验（无凭据、无订单）：
python3 scripts/okx_public_snapshot.py XMU-USDT --site eea
python3 scripts/okx_public_snapshot.py XSKHY-USDT --site eea
```

维护纪律：不新增真实下单路径；不递归改 cron；paper bucket 只读且 `materiality_eligible=false`；封顶值只在 Decision Compiler Cap Registry 改；golden set 先人工确认再并入；SKILL.md 能力地图必须覆盖全部 references/scripts（`validate_skill.py` reachability 检查强制）。

Paper 校准闭环 pitfall：`paired_samples=0` 不等于「完成」。三种状态分开：1）无预测字段 = 数据链未打通；2）有预测未平仓 = 写 `calibration_pending_paper_predictions` 并在 scorecard 暴露 `paper_pending_predictions`；3）有预测已平仓 = 写 `calibration_samples_paper` 算 Brier。只有第 2 种可作为客观限制；不得为验收捏造预测概率或 outcome。

## 单一 Skill 边界
- `trading-research` 是统一投研主 skill；本 skill 无子 skill 目录，Claude Code 只注册顶层本文件。
- 新能力优先并入 `references/*`、`scripts/*`、`templates/*`，不得新增第二套动作等级或绕过主流程，也不得新建嵌套 `SKILL.md`。
- System A（LongBridge paper-trading 自动化）的运维 runbook 在 `~/.hermes/longbridge-paper-trading/docs/runbooks/`，不在本 skill 目录内维护。
