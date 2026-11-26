# Open-Source Quant Research Patterns · 高星开源交易研究项目迁移矩阵

## 目的

参考 GitHub 高 star 开源交易/量化研究项目时，只迁移**可复用的研究机制**，不把 `trading-research` 改造成交易机器人、回测框架或自动下单系统。

本 skill 的定位仍是：研究、证据整合、风控边界、仓位上限与决策约束；默认 `no_order_execution`。

## 参考项目族与可迁移机制

| 项目族 | 代表项目 | 可迁移机制 | 不迁移内容 |
|---|---|---|---|
| 数据平台 / Agent 数据层 | OpenBB | 连接一次、多处消费；数据源注册表；MCP/REST/Python 多入口；来源健康检查 | 不把 OpenBB 作为唯一主数据源，不替代 LongBridge 主链路 |
| AI 量化研发闭环 | Qlib, RD-Agent | 因子/模型/模板的假设→实验→评估→迭代；benchmark/eval 驱动；自动 R&D 需要成本与回归测试 | 不自动生成可交易策略，不把模型收益当真实可执行收益 |
| 策略开发与自适应模型 | Freqtrade, FreqAI | backtest / hyperopt / dry-run / protections / adaptive retraining 的生命周期纪律 | 不做加密货币自动交易，不迁移 GPL 代码，不启用下单 |
| 生产级事件驱动引擎 | QuantConnect Lean | research/backtest/live 的语义分离；事件驱动；broker/data/portfolio/risk 模块边界 | 不接管券商执行，不把 live parity 当成 Hermes 目标 |
| 多接口事件驱动交易平台 | VeighNa/vn.py | `BaseGateway` 统一交易接口契约、Exchange/vt_symbol 标准化、MainEngine/App 插件边界、PaperAccount 本地撮合仿真、网关能力声明；审计细节见 `references/vnpy-audit-notes.md` | 不引入 GUI/C++ gateway/券商依赖；不把本地仿真当经纪商 paper account；不替代 LongBridge 数据/模拟仓安全门 |
| 向量化与稳健性测试 | vectorbt, backtesting.py, backtrader | 大规模参数扫描、walk-forward、交易成本、滑点、drawdown/trade analytics | 不用一次回测证明交易结论；不迁移具体策略参数 |
| 强化学习与训练-测试-交易链 | FinRL, FinRL-X | train-test-trade 分层、no-lookahead、paper/live risk controls、多 benchmark | 不把 RL 输出直接转成买卖建议 |
| 组合风险与归因 | PyPortfolioOpt, pyfolio | risk budget、Black-Litterman/HRP、回撤、夏普、暴露、tearsheet、组合层约束 | 不把优化器建议当现实可成交仓位 |
| 金融 LLM / 情绪数据 | FinGPT | 金融数据持续更新、情绪/新闻/关系抽取、个人风险偏好可定制 | 不用 LLM 情绪替代公告/财报/行情 |
| 群体智能 / 情景沙盘 | MiroFish | ontology-first 实体/关系建模、swarm simulation、simulation_trace、分章节报告证据纪律 | 不复制 AGPL-3.0 代码，不引入 Zep/OASIS/camel/Flask/前端依赖，不把模拟结果当事实或仓位依据 |
| 因子验证纪律 / 假设账本 / AI 研究 agent | Vibe-Trading | 严格因子验证（strict bench、同宇宙随机对照、多重检验校正、四态分类）、run card 工件；详见 `references/factor-validation-strict-gate.md` | 不迁 456 因子 zoo 代码、不 `pip install`，不接 connectors/channels/swarm runtime，不引入运行时依赖 |

## 迁移后的 `trading-research` 机制

### 1. Quant Robustness Gate

任何“回测/因子/模型/量化策略”结论进入交易建议前，必须回答：

| 检查项 | 红线 |
|---|---|
| 数据切分 | 没有 train/validation/test 或 walk-forward → 不支持行动 |
| 未来函数 | 存在 lookahead bias / survivor bias / restated financials 未处理 → L0 |
| 成本 | 未包含交易成本、滑点、冲击成本、借券/融资成本 → 仓位上限降级 |
| 稳健性 | 只在单市场、单周期、单参数有效 → 只能作假设 |
| 容量 | 成交额/换手/流动性不支持目标仓位 → position cap 下调 |
| 风控 | 无最大回撤、止损、风险预算、复盘时钟 → 不给动作 |
| 复现 | 无代码/公式/数据口径/calculation_ref → 进入 calculation gap |

以上检查项通过之后，任何 IC 类因子证据还须先过同宇宙随机对照零假设，详见 `references/factor-validation-strict-gate.md`；裸 IC 通过本表不构成 alpha 证据。

### 2. Portfolio Risk Budget Gate

真实持仓或组合分析必须输出：
- 单仓集中度；
- 主题/行业/因子暴露；
- 最大回撤或左尾来源；
- 现金/保证金/币种风险；
- `position_cap` 与 `risk_budget_used`；
- 缺账户只读数据时，不给具体买卖数量。

### 2b. Gateway / Paper-Simulation Boundary Gate

从 VeighNa/vn.py 这类多接口交易框架吸收机制时，先做能力归类，避免把“本地仿真”“券商模拟仓”“真实账户”混成一套：

```yaml
gateway_capability:
  framework: "VeighNa/vn.py|other"
  market_scope: ["CN_A", "HK", "US", "futures", "options"]
  broker_gateway: "IB|XTP|TORA|CTP|unknown"
  account_mode: "live|broker_paper|local_matching_sim|backtest|unknown"
  data_source: "broker_realtime|datafeed|csv|local_db|unknown"
  order_path: "disabled|local_sim_only|paper_broker|live_broker"
  hermes_allowed_use: "methodology_only|research_data|backtest_review|paper_process_review"
```

硬边界：
- `local_matching_sim`（如 vn.py PaperAccount）只证明撮合流程可跑，**不证明券商 paper token 安全**；不能通过它绕过 LongBridge `paper_account_gate`。
- `broker_paper` 必须由对应券商账户字段/通道证明；没有明确 paper channel，一律按 live/unknown 处理。
- A/H/US 是否可用由 gateway + 数据源共同决定：Exchange 枚举覆盖不等于账户、行情、历史数据、卖空/融资、时区和交易规则都可用。
- 任何 vn.py / gateway / paperaccount 机制只能进入 `quant_robustness`、`data_contract`、`paper_process_review` 或 `backtest_review`；不得新增 Hermes 下单路径。

允许的融合路径（LongBridge → vn.py 语义层）：
- LongBridge 可作为 vn.py-style gateway/datafeed 的上游 API：把 LongBridge quote/kline/positions/orders 映射为 `TickData`、`BarData`、`AccountData`、`PositionData`、`OrderData` 这类统一事件对象。
- 第一阶段只做 `read_only_datafeed` + `proposal_adapter`：用于三市场行情、回测、信号与 proposal 校验，不发单。
- 第二阶段若用于 Demo/Paper 执行，仍必须复用现有 LongBridge System A：`paper_account_gate=pass`、`account_channel=lb_papertrading`、isolated token、limit-only、journal-first、executor guard；adapter 只做格式转换，不拥有最终下单权。
- 禁止把 LongBridge MCP 默认账户输出当作 Demo 账户权威；订单 eligibility 仍以 isolated CLI gate / decision packet / executor journal 为准。

### 2c. External Repository Credential Quarantine Gate

任何 GitHub/开源项目进入源码审计、依赖安装或方法迁移前，先执行：

1. 固定 commit/tree/archive hash 与许可证。
2. 对 **Git tracked files** 静态检查硬编码账号、密码、连接串、token、把 secret 写文件/URL/日志的路径。
3. 若发现真实或疑似有效凭据：标记 `upstream_credential_compromised=true`；凭据值不得进入聊天、报告、测试 fixture 或 skill；禁止执行相关脚本、连库、验证凭据、复制配置或把项目作为运行时依赖。通过测试、star 数或 README 声称不能抵消该 veto。
4. 只有凭据门通过后才允许 `--ignore-scripts` 安装依赖；仍须使用官方 registry audit，并记录 lint/type/build 是否被配置绕过。
5. 同步审计 IDOR/owner filter、runtime validation、external base URL allowlist、日志脱敏、HTML/邮件 sanitizer、Docker build context 与自动 cron/邮件/交易副作用。
6. 只把通用机制 clean-room 重写；在 `source-map.md` 保存固定版本、风险、吸收/拒绝边界。上游泄露只建议维护者轮换和清理历史，不公开复述秘密。

### 3. Research Experiment Ledger

当用户要求“量化/因子/模型/回测/筛选器”时，输出或写入：

```yaml
research_experiment:
  hypothesis: "要验证的市场命题"
  data_scope: "market/date/universe/source"
  feature_set: []
  train_validation_test: "walk_forward|time_split|unavailable"
  costs_included: "yes|no|partial"
  robustness_checks: []
  failure_modes: []
  decision_use: "hypothesis_only|ranking_support|risk_cap_support|not_usable"
```

### 4. Backtest Claim Cap

| 回测质量 | 最高用途 |
|---|---|
| 无回测，只是观点 | Hypothesis Ledger |
| 单次回测，无成本/无 walk-forward | 观察/研究，不提高仓位 |
| 多周期、多市场、含成本、含 drawdown | 可作为辅助排序或风险上限输入 |
| 通过 paper/live 小样本验证 | 仍需结合实时 regime，不自动 L2/L3 |

## 与现有链路的融合规则

- `Decision Compiler` 仍是唯一动作裁决器。
- `Mira Quality Gates` 负责 readiness、calculation_ref、stale_after；量化输出必须过 Mira。
- `LongBridge` 仍是行情/财报/估值/账户主链路；开源项目只提供方法论，不替代数据源。
- `Grok Web Research Layer` 只负责发现新线索，不替代 backtest/evidence。
- 主回复仍保持简洁；完整实验账本默认写文件/附录。

## 禁止的伪优化

- 因为某项目 star 多就盲目增加依赖。
- 用“AI/自适应/强化学习”包装没有验证的模型输出。
- 只看收益率，不看回撤、成本、容量和制度约束。
- 把 crypto bot 的短线参数套到 A/H/美股。
- 为了“自进化”每天改模板，造成 skill 漂移和冲突。
- 把 MiroFish-style synthetic agent reactions 当成真实市场/消费者/监管证据，或让 modeled_scenario 提高 action level / position_multiplier。
