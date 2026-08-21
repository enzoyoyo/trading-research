# trading-research

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

**v2.59 新增**：可复现因子研究/分位回测与盘前筛选、A 股情绪周期、财报期权定位/隐含波动分布和历史反应工具；全部保持 research-only。

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
| `scripts/options_positioning_snapshot.py` / `earnings_move_history.py` / `earnings_implied_distribution.py` | 财报期权定位、历史反应与隐含分布 |
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

### 第三方方法论说明

部分 playbook 记录了对上游项目（含 Apache-2.0 / AGPL-3.0）的 **clean-room 方法论**吸收，并标明 `methodology_only_no_code_copied`，拒绝迁移代码/Prompt。本仓库整体为 MIT。

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

### Third-party methodology

Some playbooks document clean-room methodology inspired by upstream projects (including Apache-2.0 and AGPL-3.0 sources), marked `methodology_only_no_code_copied`. This repo is MIT overall.

### License

MIT — see `LICENSE`.
