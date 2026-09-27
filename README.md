# trading-research

**v2.74** · [Changelog / 版本变更](CHANGELOG.md)

[English](#english) · [中文](#中文)

开源、**仅研究分析**的 Agent Skill：把多市场证据编译成可复现的风险边界与动作建议，**不提交实盘订单**。

An open-source, **research-only** agent skill that turns multi-market evidence into reproducible risk bounds and action guidance — **without placing live brokerage orders**.

---

## 中文

### 它是什么 / 解决什么问题

投研对话里最常见的痛点是：信息很多，但结论不可复盘、边界不清、容易把「故事」当成「可交易信号」，甚至误触实盘。

`trading-research` 把这些流程固化成可调用的 Skill + 脚本 + 质量门：

| 问题 | 本仓库怎么处理 |
|---|---|
| 单票叙事、宏观、拥挤度、期权结构各说各话 | 统一走证据链 → 质量门 → Decision Compiler，输出动作等级与仓位上限 |
| 结论写完就消失，下次重复踩坑 | 决策记忆、假设注册、预测账本、校准闭环 |
| 数据源过期或密钥缺失仍硬答 | 缺配置 / 缺证据 fail-closed；日志脱敏，不打印密钥 |
| 研究工具误变成下单机器人 | 默认 `no_order_execution`；开源安全层拒绝实盘开关 |

### v2.74 新增与改进

- **期权组合计算（多头蝶式）**：按对价计算的净支出必须大于 0 且小于 翼宽 × 乘数 × 组数，否则判为 blocked，不再输出"最大亏损 0"或"最大盈利 0"这类由不同步报价造成的结果；看涨、看跌蝶式同一标准。
- **过期原因**：合约已结算报 `contract:expired:<合约>`，已过最后交易时刻报 `contract:no_longer_trading:<合约>`，且先于报价规则判断；因合约过期导致的到期情景不再报 `scenario:not_future`。
- **范围**：铁蝶、断翼蝶、卖出蝶仍不支持，不能以 `kind=butterfly` 输入。对价成本、OCC 与条款对账、报价新鲜度、价差上限、盘口容量与默认不算概率均保持不变。

使用方法见 [期权组合计算](references/options-expression-lab.md)。

### v2.73 新增与改进

- **A 股盘后复盘**：新增本地快照审查命令，检查同源 1/5/20 日资金窗口、五个交易日涨停生态、阈值敏感性与一字板参与度。
- **统计与数据口径**：分别统计涨停股票日与去重股票数，检查日期、会话时钟、单位、分类及覆盖范围；数据不足保留缺口，不补零、不制造有效分类。
- **复盘流程**：补充一次采集、本地计算、前次计划逐项验证和次日验证条件的流程说明，并提供离线示例与回归测试。
- **市场隔离**：仅用于 A 股盘后/情绪复盘；不改变港股、美股、OKX、既有日级连续流入分类、决策编译器或仓位权限。

使用方法见 [A 股盘后复盘](references/a-share-post-close-review.md)。

### v2.71 功能与优化

| 能力 | 现在可以做什么 |
|---|---|
| 美股策略研究 | 按标的、策略、期限和工具分别研究趋势、突破、超卖收复、事件延续、防御相对强势与隔夜机会；防守期继续发现候选，重新入场需要新证据与重新裁决 |
| 事件与条件路径 | 用事件前冻结的模型衡量扣除大盘共动后的额外偏离；计算相似价格状态下的终值、触达、双尾风险、修复时间和经验区间，并保留样本与数据截点 |
| 期权与 Gamma | 比较单腿、价差、蝶式的自然买卖成本、到期收益与隔日估值，检查个股期权和 SPX 组合压力；Gamma Flip 按假设现价重算纳入的期权链，明确持仓方向假设与未知状态 |
| 因子研究与风控 | 支持两种随机对照和使用滞后状态的条件分层；按预先登记的方向验证因子，阻止样本外净 Sharpe 非正的结果获得排序资格；风险倍率按同模块取最严格值、跨模块相乘聚合 |
| 数据源与恢复 | 增加可选同花顺 Financial-API A 股只读适配；完善港美股日线备源、东财健康检查与冷却，以及 LongBridge 报价/K 线在特定令牌过期错误后的单次 OAuth 重试 |
| A 股盘口与市场结构 | 提供七类盘口/分钟信号计算与证据引用模板，明确 tick 和快照差分的区别；新增半导体与指数背离风险研究。盘口信号默认仅作待验证研究 |
| 模拟盘学习与校准 | 校验版本化学习包、失败候选与完整交易生命周期，在账本锁内去重；概率只接受事前冻结的合同及合格结果，披露有效样本数与可计算的弃权率，缺少完整候选集时标明缺口；区分方法复查和仓位调整资格 |

研究计算、概率估计与交易权限分别核验。事件偏离不直接给出因果结论或回归胜率；日线路径不等于收盘到次日开盘；期权情景网格不代表已知概率。Gamma 的持仓方向是模型假设，数据缺失时保留未知。

**倍率兼容说明**：同模块取最严格值可避免相关信号重复折减，因此同模块存在多条约束时，倍率可能高于旧版逐条相乘的结果；硬否决仍然优先。详见 [Decision Compiler](references/decision-compiler.md)。

**港美股双系统对接**：本仓库是 System B，提供研究合同、校准工具和学习包消费接口，可与独立的 System A 港美股模拟交易运行层对接。System A 的行情调度、模拟订单执行、成交对账和运行面板不包含在此 Skill 包中，安装本仓库也不会配置交易账户或定时任务。学习候选需要证据与验证，不会自行修改 Compiler、资金权限或正式评测真值。接口见 [双系统架构](references/two-system-trading-architecture.md)。

覆盖范围概览：A/H/美股、行业与宏观 overlay、ETF、期权/Gamma、OKX public/read-only 与 tokenized stock 研究、事件驱动、财报电话会、多源检索、模拟仓复盘辅助。

### 适用于谁

- 用 Claude Code / Cursor / Hermes 等 Agent，需要可复用「投研 Skill」的个人研究者
- 做多市场股票/宏观研究，希望输出带证据、缺口、失效条件的结构化结论
- 关注研究纪律与风控边界，而不是自动下单的量化/基本面爱好者
- 需要把私有投研流程脱敏后开源、协作或教学演示的维护者

不太适合：期望「一键实盘交易」「保证收益」「免配置全自动赚钱」的用户。

### 能做什么

- 按 Tier 0/1/2 路由：直答行情类问题 / 速判 / 完整研究报告
- 加载 `SKILL.md` + `references/` 方法卡，驱动 Agent 按固定流程研究
- 用 `scripts/` 做证据采集、裁决编译、溯源校验、模拟仓复盘、离线自检
- 通过环境变量与示例配置接入只读数据源（如 Longbridge、公开行情、可选搜索 API）
- 用 Mock/模板数据跑通契约测试，不依赖你的私人账户

### 目前不能做什么（重要边界）

- **不能**向券商或交易所提交实盘买卖/撤单
- **不能**在缺少密钥或配置时静默“假装成功”；应安全退出或标注数据缺口
- **不能**把 Grok/X/社交媒体线索直接当事实锚点（仅作发现层，需交叉验证）
- **不能**替代持牌投顾或合规审核；输出是研究辅助，不是投资建议承诺
- **不能**开箱即用访问你的私人持仓/订单流水（需你自行配置只读凭证，且本 Skill 仍不下单）
- **不能**保证收益或预测准确率；校准与假设生命周期用于约束过度自信，不是收益保证

### 需要调用哪些工具

按「运行时依赖」与「本仓库脚本」分开列。不是每次研究都要全开；按市场与问题选型。

#### A. 外部工具 / CLI / Agent 能力（需本机安装或 Agent 已具备）

| 工具 | 用途 | 是否必须 | 说明 |
|---|---|---|---|
| Python 3.10+ | 跑全部 `scripts/` | **必须** | 建议 `venv` |
| Agent 宿主（Claude Code / Cursor / Hermes 等） | 读取 `SKILL.md` 并编排流程 | 主用法必须 | 本仓库是 Skill，不是独立 GUI |
| `longbridge` CLI | A/H/美行情、财报/filing 等只读取数 | 强烈推荐（多市场主源） | OpenAPI 设备流登录；token 勿写入报告 |
| `longport` Python SDK（可选） | `longbridge_query.py` 优先路径 | 可选 | 未装则自动降级 CLI |
| `hermes` CLI | `live_intel_run.py` 调 Grok/web 实时情报 | 可选 | 需已登录 xAI / Hermes 侧 provider |
| LongBridge MCP（Hermes） | 选股/财报/估值/组合等更广工具面 | 可选（Hermes 首选） | 运行时 `hermes mcp test longbridge` |
| WindClaw / Wind MCP（Hermes） | A 股交叉验证、公告/研报检索 | 可选 | session 只放本机配置，不写入报告 |
| Agent 自带 `web_search` / `web_extract` | 新闻/公告/IR 发现与原文回抓 | 推荐 | 搜索结果只作发现，需回抓原文 |
| AnySearch（若环境已装） | 与多源检索同批联动 | 可选 | 见 `SKILL.md` 中的调用约定 |
| AkShare（Python 包） | A/港/美公开行情与结构数据 | 推荐（A 股兜底） | `pip install akshare`；经 `AKSHARE_PYTHON` |
| `yfinance`（可选） | CBOE 期权缺口时的 BS 兜底 | 可选 | 仅 `options_gamma.py` 降级路径 |
| PyYAML | 读 `config.yaml` | 推荐 | `pip install pyyaml` |

#### B. 本仓库脚本（Agent 或你手动调用）

| 脚本 | 调用场景 |
|---|---|
| `scripts/validate_skill.py --all` | 安装后完整性 / 离线自测 |
| `scripts/config_loader.py` | 检查配置与安全状态（不打印密钥） |
| `scripts/research_run.py` | Tier 2 研究规划入口 |
| `scripts/evidence_run.py` | 证据档案草稿（行情/结构/情报计划） |
| `scripts/live_intel_run.py` | 实时情报计划 / Hermes Grok 健康检查 |
| `scripts/multi_source_search.py` | 无 key 多源搜索；可选豆包 Global 兜底 |
| `scripts/longbridge_query.py` | LongBridge 行情/K 线封装 |
| `scripts/us_company_evidence.py` | 美股公司证据（LongBridge → SEC） |
| `scripts/a_stock_data_bridge.py` | A 股腾讯/东财/巨潮公开源桥接 |
| `scripts/okx_public_snapshot.py` | OKX 公共行情（无密钥） |
| `scripts/okx_execution_supervisor.py` | OKX 监督投影（分析-only，不下单） |
| `scripts/options_gamma.py` / `dispersion_crowding.py` | CBOE 期权结构 / 拥挤离散度 |
| `scripts/factor_panel.py` / `factor_engine.py` / `factor_backtest.py` / `factor_verdict.py` | 因子面板、IC、分位回测与严格裁决 |
| `scripts/premarket_screen.py` / `a_share_sentiment_cycle.py` / `data_freshness_guard.py` | 盘前筛选、A 股情绪周期与新鲜度护栏 |
| `scripts/a_share_post_close_review.py` | A 股盘后/情绪复盘的本地数据口径审查；示例数据为 fixture，不联网、不下单 |
| `scripts/options_positioning_snapshot.py` / `earnings_move_history.py` / `earnings_implied_distribution.py` | 财报期权定位、历史反应与隐含分布 |
| `scripts/strategy_orchestrator.py` | 美股策略/期限/工具分账、研究路径与原 Compiler 结果汇总 |
| `scripts/us_mechanism_research.py` | 实际调用事件偏离、条件路径和期权表达计算器，输出版本化研究合同 |
| `scripts/event_dislocation.py` / `conditional_path_study.py` / `options_expression_lab.py` | 事件前模型、历史价格条件路径与期权组合情景计算 |
| `scripts/financial_api_bridge.py` | 同花顺 A 股代码、报价、日线、估值、财报和历史交易日，只读补证 |
| `scripts/microstructure_signals.py` / `semis_divergence.py` | A 股盘口信号与半导体/指数背离研究 |
| `scripts/paper_learning_consumer.py` / `self_optimization_ledger.py` | 学习包校验、候选复查与防重复计证的消费回执 |
| `scripts/paper_outcome_calibration_feed.py` / `calibration_scorecard.py` | 独立模拟盘校准桶、事前概率核验、样本数与弃权率 |
| `scripts/polymarket_signal.py` | 预测市场概率（只作 pricing prior） |
| `scripts/decision_compiler.py` / `entry_score.py` / `validate_report.py` | 裁决、展示分、报告契约校验 |
| `scripts/hypothesis_registry.py` / `prediction_ledger.py` / `trading_memory.py` | 假设/预测/决策记忆 |
| 其余 `scripts/test_*.py` 与 `--self-test` | 回归与契约测试 |

更完整的能力地图见 `SKILL.md`「能力地图」；数据源细则见 `references/data-source-playbook.md`。

### 需要自行注册的 API / Key

**原则**：没配也能跑离线自测与大量公开源；配了只读凭证才能打通券商/交易所私有只读面。密钥放在 chmod 600 的 env 文件或 shell 导出里，**不要**写进仓库。

| 变量名 | 向谁申请 / 如何获得 | 用途 | 必需？ |
|---|---|---|---|
| `LONGPORT_APP_KEY` | [Longbridge OpenAPI](https://open.longbridge.com/) 创建应用 | SDK/OpenAPI 鉴权 | 要用 LongBridge SDK/脚本主路径时需要 |
| `LONGPORT_APP_SECRET` | 同上 | 同上 | 同上 |
| `LONGPORT_ACCESS_TOKEN` | 同上（或 CLI `longbridge auth login` 设备流） | 访问令牌 | 同上；**永不打印到报告** |
| `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` | 同上 | 自动映射到 `LONGPORT_*` | 与上三选一命名风格即可 |
| `VOLC_DOUBAO_SEARCH_API_KEY` | 火山引擎 / 豆包 Search Global | `multi_source_search` 带 key 备用搜索 | **可选**；仅主搜索失败/候选不足时触发 |
| `SEC_EDGAR_IDENTITY` | 自拟联系身份字符串（SEC Fair Access 要求） | 访问 `data.sec.gov` / `www.sec.gov` | 要用 SEC 官方 API 时需要（免费，无商业 key） |
| `FINANCIAL_API_KEY` | [同花顺 Financial-API](https://github.com/HiThink-Tech/Financial-API) 数据服务 | 可选 A 股只读适配器 | 可选；由环境或安全存储注入 |
| `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` | [OKX API](https://www.okx.com/) 创建只读/Demo 密钥 | read_only/demo 监督投影 | **可选**；纯 `public` 快照不需要 |
| `OKX_MODE` | 本地设置 | `public` / `read_only` / `demo` | 默认应按 `public`；本 Skill **拒绝实盘下单** |
| `HERMES_BIN` / `HERMES_GROK_MODEL` / `HERMES_GROK_PROVIDER` | 安装 Hermes + 登录 xAI/OAuth | 实时情报 | 可选 |
| `LONGBRIDGE_BIN` / `AKSHARE_PYTHON` | 本机路径 | 定位 CLI/解释器 | 可选（否则靠 `PATH`） |
| `TRADING_RESEARCH_SEARCH_ENV` 等路径变量 | 本地 | 指定 env/config/缓存目录 | 可选 |
| `WIND_SESSION_ID` / OpenClaw 相关 | Wind / WindClaw 侧会话 | `windclaw_bridge.py` | 可选；有 Wind 权限才有意义 |
| `TRADING_RESEARCH_ALLOW_LIVE` | — | 实盘开关 | **保持 unset/false**；开源层会拒绝实盘 |

说明：

- Google / Bing / DuckDuckGo / Brave / 百度 / 搜狗 / 360 等 HTML/RSS 搜索：**无需 key**（可能被风控或验证码拦住，属预期缺口）。
- CBOE delayed、OKX public、Polymarket public、腾讯/东财/巨潮公开页：**无需商业 API key**。
- Agent 宿主自身的模型 API（Anthropic/OpenAI 等）由宿主管理，**不属于**本 Skill 的 allow-list 搜索凭证。

### 数据源涉及哪些（按调用面）

| 数据源 | 典型调用入口 | 市场/内容 | Key？ |
|---|---|---|---|
| LongBridge OpenAPI / CLI / MCP | `longbridge_query.py`、`us_company_evidence.py`、Hermes MCP | A/H/美行情、财报、filing、期权链、新闻等 | 要（OpenAPI/OAuth） |
| SEC EDGAR 官方 | `us_company_evidence.py` → `data.sec.gov` / `www.sec.gov` | 美股申报、companyfacts、ticker→CIK | 仅需 `SEC_EDGAR_IDENTITY` |
| AkShare（多背后接东财等） | `evidence_run.py`、`a_share_sentiment_cycle.py`、`fundamental_snapshot.py` | A/港/美公开行情、涨停/龙虎榜、资金流、利率等 | 通常无商业 key |
| 同花顺 Financial-API | `financial_api_bridge.py`；A 股桥接显式 `--source financial_api` | A 股代码、报价、日线、估值、三张财报、历史交易日 | `FINANCIAL_API_KEY` |
| 腾讯财经公开接口 | `a_stock_data_bridge.py` | A 股行情/估值字段 | 无 |
| 东方财富公开接口 | `a_stock_data_bridge.py`（限流） | 板块归属、分钟资金流等 | 无（需限流） |
| 巨潮资讯 CNINFO | `a_stock_data_bridge.py` | A 股公告 orgId/列表 | 无 |
| CBOE delayed CDN | `options_gamma.py`、`dispersion_crowding.py` | 美股期权墙/GEX、COR1M/VIXEQ 等 | 无 |
| Yahoo Finance（yfinance） | `options_gamma.py` 兜底 | 现价/期权链降级 | 无 |
| OKX Public API v5 | `okx_public_snapshot.py` | Tokenized stock 公共盘口/K 线 | 无 |
| OKX Private（只读/Demo） | 外部快照喂给 `okx_execution_supervisor.py` | 账户/订单对账监督 | 要（只读/Demo） |
| Polymarket Gamma/CLOB/Data API | `polymarket_signal.py` | 预测市场概率 prior | 无（公共） |
| 多源网页搜索 | `multi_source_search.py` | Google News RSS、Bing RSS、DDG/Brave/百度/搜狗/360 HTML | 默认无；豆包要 key |
| 豆包 Search Global | `multi_source_search` engine `doubao` | 带 key 备用发现 | `VOLC_DOUBAO_SEARCH_API_KEY` |
| Hermes Grok（xai-oauth） | `live_intel_run.py` | 新闻/X 线索发现 | Hermes/xAI 登录态 |
| WindClaw / Wind | `windclaw_bridge.py` + MCP | A 股/公告/研报交叉验证 | Wind 权限/会话 |
| FRED 等宏观交叉源 | 玩法文档引用（AkShare/web 组合） | 利率/美元指数核对 | 视具体接口 |

不可用或未接入时：报告写 `unavailable` / `data_gap`，给一般仓位上限，**不得假装已抓取**。

同花顺适配器保留供应商的缺失时钟、分页范围与披露日期缺口；请求成功不等于实时行情或完整回测样本。凭据配置、命令和口径见 [Financial-API 数据源说明](references/financial-api-data-source.md)。

### 怎么用

#### 1. 安装

```bash
git clone https://github.com/enzoyoyo/trading-research.git
cd trading-research
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pyyaml          # 推荐，用于 YAML 配置
```

挂到 Agent Skills 目录（示例：Claude）：

```bash
mkdir -p "${HOME}/.claude/skills"
ln -sf "$(pwd)" "${HOME}/.claude/skills/trading-research"
```

Cursor / 其他 Agent：把本目录加入对应 skills 根目录，或在对话中 `@SKILL.md` / 指定本仓库路径。

#### 2. 配置（可选，按需）

```bash
mkdir -p "${HOME}/.config/trading-research"
cp config.example.yaml "${HOME}/.config/trading-research/config.yaml"
cp .env.example "${HOME}/.config/trading-research/search.env"
chmod 600 "${HOME}/.config/trading-research/search.env"
```

- 在 env 中填写你启用的只读数据源；**不要**把真实 `.env` 提交进 Git
- 保持 `safety.allow_live_trading: false`，不要设置 `TRADING_RESEARCH_ALLOW_LIVE=true`
- 完整键名见 `.env.example` 与 `config.example.yaml`

#### 3. 调用

**方式 A — 让 Agent 当投研助手（主用法）**

1. 确认 Skill 已安装且 Agent 能读到 `SKILL.md`
2. 用自然语言提问，例如：
   - 「帮我做一次 NVDA 的 Tier 2 研究，结论要有证据与失效条件」
   - 「当前风险环境如何？先给 risk_regime 快照再谈能不能加仓」
   - 「只做 OKX public 盘口研究，不要任何下单」
3. Agent 应按 Skill 固定流程：证据 → 质量门 → Compiler → 简洁主回复（格式见 `templates/universal-equity-report.md`）

**方式 B — 命令行自检 / 脚本**

```bash
# 离线完整性与自测（推荐首次安装后运行）
python3 scripts/validate_skill.py --all

# 配置安全状态（不打印密钥）
python3 scripts/config_loader.py

# 查看研究入口帮助
python3 scripts/research_run.py --help
```

#### 4. 验证

```bash
python3 scripts/validate_skill.py --all
python3 -m unittest scripts.test_oss_safety -v
```

### 安全摘要

- 默认研究-only；开源层拒绝实盘交易开关
- 密钥只走环境变量 / chmod 600 的 env 文件；日志应脱敏
- 详见 `SECURITY.md`

### 许可证

MIT — 见 `LICENSE`。

---

## English

### What it is / problems it solves

Research chats often produce long narratives without reproducible bounds: stale data still “answers,” social clues get treated as facts, and tooling accidentally drifts toward live trading.

`trading-research` packages a reusable skill + scripts + quality gates so an agent can:

| Pain | How this repo helps |
|---|---|
| Mixed narratives (stock story, macro, crowding, options) | One pipeline: evidence → quality gates → Decision Compiler → action level / size caps |
| One-off answers that never get reviewed | Decision memory, hypothesis registry, prediction ledger, calibration loops |
| Missing secrets / stale sources still sounding confident | Fail closed; redact secret-like log values |
| Research helpers turning into order bots | Default `no_order_execution`; OSS safety layer rejects live-trading flags |

### v2.74 additions and improvements

- **Option structures (long butterfly)**: the natural debit must be greater than 0 and less than wing × multiplier × units; otherwise the candidate is blocked instead of showing a "max loss 0" or "max profit 0" produced by out-of-sync quotes. Call and put flies share the rule.
- **Expiry reasons**: a settled contract reports `contract:expired:<id>` and a contract past its last trading time reports `contract:no_longer_trading:<id>`, both before quote rules; an expiry scenario that is past because the contract expired no longer reports `scenario:not_future`.
- **Scope**: iron, broken-wing, and short butterflies remain unsupported as `kind=butterfly`. Natural-price cost, OCC/terms reconciliation, quote freshness, spread cap, displayed capacity, and the no-probability default are unchanged.

See the [option structure lab guide](references/options-expression-lab.md).

### v2.73 additions and improvements

- **A-share post-close review**: adds a local snapshot checker for same-source 1/5/20-day sector flows, five-session limit-up ecology, threshold sensitivity, and one-price-board participation context.
- **Data consistency**: distinguishes stock-days from distinct stocks and checks dates, session clocks, units, taxonomies, and coverage. Missing or inconsistent inputs remain explicit gaps instead of zero values or valid classifications.
- **Review workflow**: documents capture-once/local-compute handling, previous-plan reconciliation, and next-session verification, with offline fixtures and regression tests.
- **Market isolation**: applies only to A-share post-close/sentiment reviews. HK, US, OKX, existing daily-flow streaks, decision compilation, and position permissions remain unchanged.

See the [A-share post-close review guide](references/a-share-post-close-review.md).

### v2.71 capabilities and improvements

| Capability | What is available |
|---|---|
| US strategy research | Separate trend, breakout, oversold reclaim, event follow-through, defensive relative strength, and overnight research by symbol, strategy, horizon, and instrument; keep discovering candidates in defensive regimes and require fresh evidence for re-entry |
| Events and conditional paths | Measure deviations after controlling for broad-market moves with a model frozen before the event; calculate terminal outcomes, barrier touches, both tails, recovery times, and empirical intervals for similar price states, retaining sample and cutoff information |
| Options and Gamma | Compare natural bid/ask costs, expiry payoffs, and next-day valuations for single legs, spreads, and butterflies; stress single-stock options together with SPX exposures; recalculate Gamma Flip across the included chain at hypothetical spot prices with explicit inventory assumptions and unknown states |
| Factor validation and risk | Compare two randomized nulls, condition on lagged states, respect registered factor direction, and block ranking eligibility when out-of-sample net Sharpe is nonpositive; aggregate the tightest multiplier within each module, then multiply across modules |
| Data access and recovery | Add an optional read-only Tonghuashun Financial-API adapter for A-shares; improve HK/US daily-bar fallbacks, Eastmoney health checks and cooldowns, and one OAuth retry for a specific expired-token error on LongBridge quote/kline reads |
| A-share microstructure and market structure | Compute seven order-book/minute signals with evidence references and distinct tick versus snapshot-difference fidelity; add semiconductor/index divergence research. Microstructure signals start as unvalidated research |
| Paper learning and calibration | Validate versioned learning packets, failure candidates, and completed trade lifecycles; deduplicate under the ledger lock; accept only frozen probability contracts and eligible outcomes for calibration, report evaluated samples and computable abstention rates, preserve gaps when the full candidate set is missing, and separate methodology review from sizing eligibility |

Research calculations, probability estimates, and trading authority have separate checks. Event deviations do not establish causality or reversion probabilities; daily paths are not close-to-open forecasts; option scenario grids do not imply known probabilities. Gamma inventory signs are modeling assumptions, and missing inputs remain unknown.

**Multiplier compatibility:** selecting the tightest value within a module avoids repeatedly discounting correlated signals. With multiple constraints in the same module, the resulting multiplier can be higher than under the previous per-signal multiplication. Hard vetoes still take precedence. See [Decision Compiler](references/decision-compiler.md).

**HK/US two-system integration:** this repository is System B. It supplies research contracts, calibration tools, and learning-packet consumption interfaces for an independent System A paper-trading runtime. System A's market scheduling, paper-order execution, fill reconciliation, and runtime dashboard are not included in this skill package. Installing this repository does not configure a trading account or scheduled jobs. Learning candidates require evidence and validation; they do not automatically alter the Compiler, capital permissions, or golden evaluation cases. See [two-system architecture](references/two-system-trading-architecture.md).

Scope overview: A/H/US equities, sector/macro overlays, ETFs, options/Gamma, OKX public/read-only and tokenized-stock research, event-driven flows, earnings calls, multi-source search, paper-trading review helpers.

### Who it is for

- Individuals using Claude Code / Cursor / Hermes (or similar) who want a reusable research skill
- Multi-market equity/macro researchers who need structured outputs with evidence, gaps, and invalidation conditions
- People who care about research discipline and risk bounds more than auto-execution
- Maintainers who want a sanitized, shareable research workflow for collaboration or teaching

Not a fit if you want guaranteed returns, one-click live trading, or a fully managed broker robot out of the box.

### What it can do

- Route Tier 0 / 1 / 2 queries (direct facts → quick decision → full research)
- Drive agents via `SKILL.md` + `references/` playbooks
- Run offline validation, provenance checks, compilers, and paper-review helpers under `scripts/`
- Connect optional read-only data sources through env / example config
- Exercise core contracts on mock/template data without your private accounts

### What it cannot do (current limits)

- **Cannot** submit live buy/sell/cancel orders to brokers or exchanges
- **Cannot** silently succeed when credentials or required evidence are missing
- **Cannot** treat Grok / X / social posts as hard facts (discovery only; must cross-check)
- **Cannot** replace licensed advice or compliance review; outputs are research aids, not performance promises
- **Cannot** see your private positions/orders unless you configure read-only access yourself (and this skill still will not place orders)
- **Cannot** guarantee PnL or forecast accuracy; calibration constrains overconfidence, it does not promise alpha

### Tools you need to invoke

Split by **runtime dependencies** vs **repo scripts**. You do not need every tool for every query.

#### A. External tools / CLIs / agent capabilities

| Tool | Role | Required? | Notes |
|---|---|---|---|
| Python 3.10+ | Run `scripts/` | **Yes** | Prefer a venv |
| Agent host (Claude Code / Cursor / Hermes, …) | Load `SKILL.md` and orchestrate | Yes for primary use | This repo is a skill, not a GUI |
| `longbridge` CLI | Read-only quotes / filings | Strongly recommended | Device-flow auth; never print tokens |
| `longport` Python SDK | Preferred path in `longbridge_query.py` | Optional | Falls back to CLI |
| `hermes` CLI | Live intel via Grok/web | Optional | Needs logged-in provider |
| LongBridge MCP (Hermes) | Broader research tools | Optional | `hermes mcp test longbridge` |
| WindClaw / Wind MCP | A-share cross-checks | Optional | Keep sessions local |
| Agent `web_search` / `web_extract` | Discovery + fetch originals | Recommended | Search hits are not evidence yet |
| AnySearch (if installed) | Parallel discovery | Optional | See `SKILL.md` |
| AkShare | Public A/HK/US market data | Recommended for A-shares | `pip install akshare` |
| `yfinance` | Options fallback only | Optional | Used by `options_gamma.py` degrade path |
| PyYAML | Load `config.yaml` | Recommended | `pip install pyyaml` |

#### B. In-repo scripts

| Script | When to call |
|---|---|
| `scripts/validate_skill.py --all` | Post-install integrity |
| `scripts/config_loader.py` | Config/safety status (no secret printing) |
| `scripts/research_run.py` | Tier 2 planning entry |
| `scripts/evidence_run.py` | Evidence archive draft |
| `scripts/live_intel_run.py` | Live-intel plan / Hermes health |
| `scripts/multi_source_search.py` | Keyless multi-engine search (+ optional Doubao) |
| `scripts/longbridge_query.py` | LongBridge quote/kline wrapper |
| `scripts/us_company_evidence.py` | US company evidence (LongBridge → SEC) |
| `scripts/a_stock_data_bridge.py` | Tencent / Eastmoney / CNINFO bridge |
| `scripts/okx_public_snapshot.py` | OKX public market snapshot |
| `scripts/okx_execution_supervisor.py` | Analysis-only OKX supervision |
| `scripts/options_gamma.py` / `dispersion_crowding.py` | CBOE structure / crowding |
| `scripts/factor_panel.py` / `factor_engine.py` / `factor_backtest.py` / `factor_verdict.py` | Factor panels, randomized controls, backtests, and validation |
| `scripts/premarket_screen.py` / `a_share_sentiment_cycle.py` / `data_freshness_guard.py` | Premarket screening, A-share sentiment, and freshness checks |
| `scripts/options_positioning_snapshot.py` / `earnings_move_history.py` / `earnings_implied_distribution.py` | Earnings positioning, historical reactions, and implied distributions |
| `scripts/strategy_orchestrator.py` | Strategy/horizon/instrument separation and original Compiler result aggregation |
| `scripts/us_mechanism_research.py` | Invoke event, path, and option calculators in a versioned research contract |
| `scripts/event_dislocation.py` / `conditional_path_study.py` / `options_expression_lab.py` | Pre-event models, historical conditional paths, and option scenarios |
| `scripts/financial_api_bridge.py` | Read-only A-share symbols, quotes, daily bars, valuation, financials, and historical trading dates |
| `scripts/microstructure_signals.py` / `semis_divergence.py` | A-share microstructure signals and semiconductor/index divergence |
| `scripts/paper_learning_consumer.py` / `self_optimization_ledger.py` | Learning-packet validation, candidate triage, and deduplicated consumption receipts |
| `scripts/paper_outcome_calibration_feed.py` / `calibration_scorecard.py` | Isolated paper calibration, frozen probability checks, sample counts, and abstention rates |
| `scripts/polymarket_signal.py` | Prediction-market pricing prior |
| `scripts/decision_compiler.py` / `entry_score.py` / `validate_report.py` | Decision, score, report contract |
| Memory/ledger helpers (`hypothesis_registry.py`, `prediction_ledger.py`, …) | Persistence of research judgments |

Full capability map: `SKILL.md`. Data-source details: `references/data-source-playbook.md`.

### APIs / keys you must register yourself

**Rule:** offline tests and many public sources work without keys. Register read-only credentials only for the surfaces you need. Store secrets in chmod-600 env files — **never** commit them.

| Env var | Where to get it | Used for | Required? |
|---|---|---|---|
| `LONGPORT_APP_KEY` | [Longbridge OpenAPI](https://open.longbridge.com/) | SDK/OpenAPI auth | If using LongBridge as primary |
| `LONGPORT_APP_SECRET` | same | same | same |
| `LONGPORT_ACCESS_TOKEN` | same / `longbridge auth login` | access token | same; **never print** |
| `LONGBRIDGE_APP_*` / `LONGBRIDGE_ACCESS_TOKEN` | same | auto-mapped to `LONGPORT_*` | alternate naming |
| `VOLC_DOUBAO_SEARCH_API_KEY` | Volcengine / Doubao Search Global | keyed search backup | Optional |
| `SEC_EDGAR_IDENTITY` | Your contact identity string (SEC Fair Access) | Official SEC HTTP access | Needed for SEC API (free; no commercial key) |
| `FINANCIAL_API_KEY` | [Tonghuashun Financial-API](https://github.com/HiThink-Tech/Financial-API) data service | Optional A-share read-only adapter | Optional; inject via environment or secret storage |
| `OKX_API_KEY` / `OKX_SECRET_KEY` / `OKX_PASSPHRASE` | OKX API (read-only/Demo) | non-public supervision inputs | Optional; public snapshot needs none |
| `OKX_MODE` | local | `public` / `read_only` / `demo` | Prefer `public`; **no live orders** |
| `HERMES_BIN` / `HERMES_GROK_*` | Hermes + xAI login | live intel | Optional |
| `LONGBRIDGE_BIN` / `AKSHARE_PYTHON` | local paths | locate CLI/interpreter | Optional |
| `WIND_SESSION_ID` / OpenClaw vars | Wind / WindClaw | `windclaw_bridge.py` | Optional |
| `TRADING_RESEARCH_ALLOW_LIVE` | — | live flag | **Keep unset/false** |

Notes:

- HTML/RSS engines (Google News, Bing, DDG, Brave, Baidu, Sogou, 360): **no key** (captchas/blocks are expected gaps).
- CBOE delayed, OKX public, Polymarket public, Tencent/Eastmoney/CNINFO public pages: **no commercial API key**.
- Model keys for the agent host (Anthropic/OpenAI/…) are managed by the host, not by this skill’s search allow-list.

### Data sources involved

| Source | Entry points | Markets / content | Key? |
|---|---|---|---|
| LongBridge OpenAPI / CLI / MCP | `longbridge_query.py`, `us_company_evidence.py`, Hermes MCP | A/H/US quotes, filings, options, news | Yes |
| SEC EDGAR | `us_company_evidence.py` | US filings / companyfacts / ticker map | Identity string only |
| AkShare | `evidence_run.py`, sentiment/fundamental helpers | Public A/HK/US + structure stats | Usually no commercial key |
| Tonghuashun Financial-API | `financial_api_bridge.py`; A-share bridge with explicit `--source financial_api` | A-share symbols, quotes, daily bars, valuation, financial statements, historical trading dates | `FINANCIAL_API_KEY` |
| Tencent Finance | `a_stock_data_bridge.py` | A-share quotes/valuation fields | No |
| Eastmoney | `a_stock_data_bridge.py` (rate-limited) | Sectors / minute flow | No |
| CNINFO | `a_stock_data_bridge.py` | A-share notices | No |
| CBOE delayed CDN | `options_gamma.py`, `dispersion_crowding.py` | Options walls / crowding indices | No |
| Yahoo Finance (`yfinance`) | `options_gamma.py` fallback | Degraded options/spot | No |
| OKX Public API v5 | `okx_public_snapshot.py` | Tokenized-stock public books/candles | No |
| OKX private read-only/Demo | feed into `okx_execution_supervisor.py` | Account/order supervision | Yes |
| Polymarket Gamma/CLOB/Data | `polymarket_signal.py` | Probability prior only | No (public) |
| Multi-engine web search | `multi_source_search.py` | News/discovery candidates | Mostly no; Doubao needs key |
| Doubao Search Global | keyed engine `doubao` | Backup discovery | `VOLC_DOUBAO_SEARCH_API_KEY` |
| Hermes Grok | `live_intel_run.py` | News/X discovery | Hermes/xAI session |
| WindClaw / Wind | `windclaw_bridge.py` + MCP | A-share cross-checks | Wind entitlement |
| Macro cross-checks (e.g. FRED via docs/AkShare/web) | playbooks + web | Rates / USD cross-checks | Depends on endpoint |

If a source is unavailable: emit `unavailable` / `data_gap` and keep conservative caps — **do not pretend it was fetched**.

The Financial-API adapter preserves missing vendor timestamps, page coverage, and disclosure-date gaps. A successful request does not establish real-time prices or a complete backtest sample. See [Financial-API setup and data semantics](references/financial-api-data-source.md).

### How to use

#### 1. Install

```bash
git clone https://github.com/enzoyoyo/trading-research.git
cd trading-research
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install pyyaml          # recommended for YAML config
```

Attach as an agent skill (Claude example):

```bash
mkdir -p "${HOME}/.claude/skills"
ln -sf "$(pwd)" "${HOME}/.claude/skills/trading-research"
```

For Cursor / other agents: put this folder on your skills path, or point the chat at `SKILL.md`.

#### 2. Configure (optional)

```bash
mkdir -p "${HOME}/.config/trading-research"
cp config.example.yaml "${HOME}/.config/trading-research/config.yaml"
cp .env.example "${HOME}/.config/trading-research/search.env"
chmod 600 "${HOME}/.config/trading-research/search.env"
```

- Fill only the read-only sources you need; **never** commit a real `.env`
- Keep `safety.allow_live_trading: false` and leave `TRADING_RESEARCH_ALLOW_LIVE` unset
- See `.env.example` and `config.example.yaml` for keys

#### 3. Invoke

**A — Agent research assistant (primary)**

Ask in natural language, for example:

- “Run a Tier 2 study on NVDA with evidence and invalidation conditions.”
- “Give a risk_regime snapshot before discussing whether to add risk.”
- “OKX public market research only — no orders.”

The agent should follow the skill pipeline and reply in the style of `templates/universal-equity-report.md`.

**B — CLI checks / scripts**

```bash
python3 scripts/validate_skill.py --all
python3 scripts/config_loader.py
python3 scripts/research_run.py --help
```

#### 4. Test

```bash
python3 scripts/validate_skill.py --all
python3 -m unittest scripts.test_oss_safety -v
```

### Safety summary

- Research-only by default; live-trading flags are rejected by the OSS safety layer
- Secrets via environment / chmod-600 env files only; logs should scrub values
- See `SECURITY.md`

### License

MIT — see `LICENSE`.
