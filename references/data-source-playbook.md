# A/H/美股数据源与检索 Playbook（真实可用、可验证）

> 原则：**只用本机实测可跑的数据源**。每个数据点都要能复现来源与时间戳；取数失败一律写「缺口」，绝不补数字。
> 本 skill 跨 Agent 通用（Hermes / Claude Code / Codex）。Hermes 侧优先用 Longbridge MCP（145工具）→ CLI → SDK 三层降级；A 股遇到 LongBridge/AkShare/WindClaw 缺口时，用 **a-stock-data 直连公开源桥接**（腾讯/东财/巨潮）补证；其它环境保留 **AkShare（Python）** 与 **LongBridge CLI** 直连。
> **鉴权铁律**：任何数据源 token/鉴权过期不得静默跳过——必须立即打开浏览器/login 让 用户 续期（详见 SKILL.md §数据源鉴权续期）。

## 0. 通用步骤

1. 先确认市场、代码、公司名、币种、交易时段（优先用 `scripts/security_resolver.py`，兼容 `scripts/market_router.py`）。
2. **鉴权预检**：取数前先跑数据源的验证命令（`longbridge check` / `hermes mcp test longbridge` / `hermes auth status xai` 等），token 过期就触发续期，不要跳过（见 SKILL.md 操作铁律）。
3. 用 `scripts/evidence_run.py <标的>` 先生成 EvidenceArchive 草稿；之后再补 Grok/web/公告/财报等人工判断。
4. 记录每个关键数据的来源和时间戳；行情与财报分开（行情看实时/收盘，财报看最新报告期）。
5. 先做自上而下预检（新闻/政策/宏观/微观/账户座位），再做个股交易结构。
6. 工具取数失败 → 写「影响判断的关键缺口」，不脑补。**若是鉴权过期导致 → 先触发续期，不要立刻降级为缺口。**
7. 资讯覆盖不足 → 先生成 `multi_source_search.py` plan，明确 provider 和查询外发范围；确认不是敏感查询后才加 `--allow-external-search`。候选必须回抓原文才能入 Evidence Ledger。
8. 多标的/跨市场研究 → 用 `scripts/intelligence_coverage.py` 编译每个 `target × dimension` 的 capability/coverage/DataGap；按 high→medium→low criticality round-robin 补抓，禁止把 unsupported/error 写成 0 或“无事件”。
9. 跨 agent 环境通过 PATH 或环境变量解析：LongBridge 用 `LONGBRIDGE_BIN`，AkShare Python 用 `AKSHARE_PYTHON`，Hermes 用 `HERMES_BIN`；缺失时明确报错。

## 1. 本机已验证数据源能力矩阵

| 数据源 | 形态 | 覆盖 | 验证命令 | 备注 |
|---|---|---|---|---|
| **AkShare** | Python 包（运行时验证） | A/港/美行情K线、涨停/炸板/龙虎榜、资金流、南向、美债利率 | `python3 -c "import akshare,sys;print(akshare.__version__)"` | eastmoney `push2*` 推送域名走本机代理会断连，需绕代理（见 §2 注意） |
| **LongBridge MCP** 🆕 | Hermes MCP（OAuth，运行时验证） | A/港/美全维度：选股筛选、财报/估值/行业对比、期权链、组合分析、新闻/公告、市场温度、DCA、预警 | `hermes mcp test longbridge`；并确认当前会话是否有 MCP 工具 | **首选数据层**，145 工具；OAuth token 自动管理，但可用性必须运行时验证 |
| **LongBridge CLI** | `~/.local/bin/longbridge`（运行时验证） | A/港/美实时行情、K线、盘前盘后、组合/持仓；美股财报、估值、filing、Form 4、股东、显式机构 13F | `longbridge check` / `longbridge auth status` | 设备流授权（`auth login`），token 存 `~/.longbridge/openapi/tokens/`，自动复用；**永不输出 token**；美股公司证据由 `us_company_evidence.py` 做只读归一化 |
| **LongBridge script wrapper** 🆕 | `scripts/longbridge_query.py`（SDK 优先，SDK 缺失/超时自动 CLI fallback） | 行情/K线；SDK 可用时另含盘口/交易时段 | `python3 scripts/longbridge_query.py quote TSLA.US --json` | 供本 skill 脚本稳定取数；自动从 `~/.config/longbridge/.env` 映射 `LONGBRIDGE_* → LONGPORT_*`，但 SDK 连接失败时改走 PATH / `LONGBRIDGE_BIN` 中的 `longbridge` |
| **SEC EDGAR 官方 API** | `scripts/us_company_evidence.py`（stdlib only、按需） | `company_tickers.json` exact ticker→CIK、submissions、accession/原始 URL、修订申报、XBRL companyfacts | `python3 scripts/us_company_evidence.py AAPL --json`；identity 缺失应返回 `identity_missing` | 免费、无商业 key；需 `SEC_EDGAR_IDENTITY` 联系身份，1 req/s、6h 私有缓存；与 LongBridge 同 accession 不算第二来源 |
| **a-stock-data bridge** 🆕 | `scripts/a_stock_data_bridge.py`（零第三方依赖直连公开源） | A 股腾讯行情/PE/PB/市值/涨跌停、东财板块归属/分钟资金流、巨潮公告动态 orgId | `python3 scripts/a_stock_data_bridge.py health --json` | 来源 `simonlin1212/a-stock-data` v3.2.2；默认中国源直连不走代理；东财只用于独有数据且限流，不能单独提高动作等级 |
| **CBOE delayed**（`scripts/options_gamma.py`） | 内部脚本 | 美股期权/Gamma：Put Wall/Call Wall/Gamma Flip/GEX | `python3 scripts/options_gamma.py AVGO --json` | 免 key，约15分钟延迟；yfinance+BS 仅兜底 |
| **Hermes Grok（xai-oauth）** | `hermes` CLI | 全网实时新闻/X 社媒/叙事热度/交叉验证 | `python3 scripts/live_intel_run.py <标的> --health` | 仅用已登录 Grok；不可用时切 web 搜索并标注 |
| **WindClaw / Wind** | Hermes MCP + `scripts/windclaw_bridge.py` | A 股实时表现、个股/板块、Wind 金融语料、公告/研报/新闻搜索 | `hermes mcp test windclaw-web`; `hermes mcp test windclaw-quote`; `python3 scripts/windclaw_bridge.py health --json` | A 股备用与交叉验证源；session 只放 `.env`/runtime 文件，不写进报告 |
| **IMA 个人交易法 KB** | OpenAPI（`ima-skill`） | 方法论原文（华源/Serenity/游资/威科夫/利弗莫尔/因子/杠铃） | KB `个人交易法`，75 条 | 方法论参考，非行情；本目录 `references/*` 已是其蒸馏版 |
| **Web 搜索/抓取** | 各 Agent 已装 skill + `scripts/multi_source_search.py` | 突发新闻、公告、研报、IR、多索引候选 | `web_search`/`web_extract`；失败或覆盖不足时 `python3 scripts/multi_source_search.py '<query>' --profile news --allow-external-search --json` | 搜索结果只作发现；原文回抓后重定级 |

> 不可用/未接入的源（如 IBKR 只读账户、Choice）：报告写「unavailable」，给一般仓位上限，不声称已抓取。WindClaw 只有健康检查通过且实际调用成功时才可写「已抓取」。

## 2. a-stock-data 直连公开源桥接（A 股补强层）

来源：`simonlin1212/a-stock-data` v3.2.2。只吸收其公开源机制，不把它注册成第二个 skill；本 skill 的桥接脚本是 `scripts/a_stock_data_bridge.py`。

```bash
# 健康检查：腾讯行情 + 东财概念 + 巨潮 orgId map
python3 scripts/a_stock_data_bridge.py health --json

# 快速行情/估值/交易结构快照（腾讯财经；字段43=振幅，46=PB）
python3 scripts/a_stock_data_bridge.py quote 600519.SH 300750.SZ --json

# 个股行业/概念/地域混合板块归属（东财 slist，替代失效百度 PAE）
python3 scripts/a_stock_data_bridge.py concept 600519.SH --json

# 巨潮公告（动态 orgId，避免 601xxx 股票公告查空）
python3 scripts/a_stock_data_bridge.py announcements 601318.SH --limit 10 --json

# 东财分钟级资金流；盘中更有意义，空值可能是非交易时段/风控/网络缺口
python3 scripts/a_stock_data_bridge.py fund-flow 000858.SZ --limit 20 --json
```

纪律：
- 默认直连中国公开源，不走本机代理；确需代理才加 `--use-proxy`。
- Eastmoney 请求串行限流，批量任务把 `EM_MIN_INTERVAL=1.5` 或更高；禁止并发刷东财。
- 腾讯字段口径固定：`43=amplitude_pct`，`46=pb`；报告里不要把 43 写成 PB。
- 巨潮公告必须记录 `cninfo_orgid_fallback`，fallback=true 时把 orgId 作为待确认缺口。
- 该层只补证，不替代 LongBridge/Mira/Decision Compiler。

## 2b. AkShare 实测用法（A 股广覆盖兜底）

**代理注意**：本机设了 `HTTP(S)_PROXY=http://127.0.0.1:8118`，eastmoney 行情/推送域名经代理常 `RemoteDisconnected`。取数时绕开代理并重试：

```bash
# 绕代理跑 akshare（eastmoney 为国内直连，无需代理）
env no_proxy='*' http_proxy='' https_proxy='' HTTP_PROXY='' HTTPS_PROXY='' python3 - <<'PY'
import akshare as ak, warnings; warnings.filterwarnings('ignore')
# A股：日线（前复权）、个股资金流、涨停池、炸板池、龙虎榜
print(ak.stock_zh_a_hist(symbol='300846', period='daily', start_date='20260501', end_date='20260605', adjust='qfq').tail())
print(ak.stock_zt_pool_em(date='20260604').head())        # 涨停池（实测可用）
print(ak.stock_zt_pool_zbgc_em(date='20260604').head())   # 炸板池（实测可用）
print(ak.stock_lhb_detail_em(start_date='20260601', end_date='20260605').head())  # 龙虎榜（实测可用）
print(ak.stock_hsgt_fund_flow_summary_em())               # 南向/北向资金汇总（实测可用）
PY
```

常用函数速查（失败即标缺口，单标的重试≤2 次，不要刷爆 eastmoney）：

| 用途 | 函数 |
|---|---|
| A 股实时快照 | `stock_zh_a_spot_em()`（push 域名，易被代理挡，绕代理重试） |
| A 股日线/周线 | `stock_zh_a_hist(symbol, period, start_date, end_date, adjust='qfq')` |
| A 股个股资金流 | `stock_individual_fund_flow(stock, market='sz'|'sh')` |
| 涨停/炸板池 | `stock_zt_pool_em(date)` / `stock_zt_pool_zbgc_em(date)` |
| 龙虎榜明细 | `stock_lhb_detail_em(start_date, end_date)` |
| 港股快照/日线 | `stock_hk_spot_em()` / `stock_hk_hist(symbol, period, start_date, end_date, adjust)` |
| 南向资金汇总 | `stock_hsgt_fund_flow_summary_em()` |
| 美股快照/日线 | `stock_us_spot_em()` / `stock_us_hist(symbol='105.TSLA', ...)` |
| 美债收益率（宏观） | `bond_zh_us_rate(start_date)` |

## 2c. AkShare ETF / A 股期权 / 可转债 实测用法（2026-07-19）

同 §2b 绕代理方式；以下函数专供 `references/etf-selection-rotation.md` 与 `references/a-share-derivatives-ipo.md` 使用。

```bash
env no_proxy='*' http_proxy='' https_proxy='' HTTP_PROXY='' HTTPS_PROXY='' python3 - <<'PY'
import akshare as ak, warnings; warnings.filterwarnings('ignore')
print(ak.fund_etf_spot_em().head())                                   # ETF 实时行情+溢价折价+资金流（1549 只全市场）
print(ak.fund_etf_fund_info_em(fund="510050", start_date="20260601", end_date="20260701").tail())  # ETF 净值历史（不含费率）
print(ak.option_finance_board(symbol="华夏上证50ETF期权", end_month="2607").head())  # 期权链（行情+行权价）
print(ak.option_risk_indicator_sse(date="20260718").head())           # 逐合约希腊字母（官方风险指标，762行）
print(ak.option_daily_stats_sse(date="20260718"))                     # 按标的聚合 P/C 比+总OI（5个标的，信息量最高）
print(ak.bond_zh_cov().head())                                        # 可转债全市场快照（债现价/转股溢价率/转股价值）
PY
```

| 用途 | 函数 | 备注 |
|---|---|---|
| ETF 实时行情+溢价折价+资金流 | `fund_etf_spot_em()` | 已测通；关键列 `最新价`/`IOPV实时估值`/`基金折价率`/`成交额`/`换手率`/`主力净流入-净额` |
| ETF 净值历史 | `fund_etf_fund_info_em(fund, start_date, end_date)` | 已测通；不含费率/跟踪误差，需 Grok/web_search 补 |
| A 股期权链 | `option_finance_board(symbol, end_month)` | 已测通，24 档；`symbol` 需中文全称如「华夏上证50ETF期权」 |
| A 股期权逐合约希腊字母 | `option_risk_indicator_sse(date)` | 已测通，交易所官方风险指标 |
| A 股期权按标的聚合统计 | `option_daily_stats_sse(date)` | 已测通，含认沽认购比+总持仓+成交量 |
| 可转债全市场快照 | `bond_zh_cov()` | 已测通，1035 只；`债现价`/`转股溢价率`/`转股价值`/`正股价`/`转股价`/`信用评级`/打新字段 |

**已弃用**：`option_current_em()` 实测报 `TypeError`（不接受 `symbol` 参数）与 `ConnectionError`（重试后仍连接中断），改用上述三个函数替代，不写入常规取数路径。

## 2d. 利率 / FX / 加密 免 key 公开源（2026-07-19，`references/rates-fx-crypto-overlay.md` 专用）

中美利率日常判断仍以 AkShare `bond_zh_us_rate(start_date)` 为首选（单次调用双曲线+利差）。以下为其补充/交叉核对源：

```bash
# 美债全曲线（仅美国，短端补查）
curl -s "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv"

# BTC/ETH 现价+24h+市值（CoinGecko 免 key，注意速率限制）
curl -s "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"

# 美元指数 DXY（首选）
curl -s -A "Mozilla/5.0" "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=1d"

# 美元指数交叉核对（FRED 广义贸易加权，口径不同于 ICE DXY，不能直接比较数值）
curl -s "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS"
```

| 数据 | 源 | 备注 |
|---|---|---|
| 中美利率+利差+GDP | AkShare `bond_zh_us_rate` | **首选**，日常判断用这个 |
| 美债全曲线（短端补查） | Treasury.gov CSV | 无需 key；只在需要 1mo/3mo 等极短久期时查 |
| BTC/ETH 现价 | CoinGecko `simple/price` | 无需 key；免费额度有速率限制 |
| DXY | Yahoo Finance chart API `DX-Y.NYB` | **首选**，`regularMarketPrice` 字段 |
| DXY 交叉核对 | FRED `DTWEXBGS` | 广义贸易加权，仅作趋势核对 |
| ~~DXY~~ | ~~stooq CSV~~ | **实测失败**：返回 Anubis 反爬 JS 挑战，不可用，不写入取数路径，标 `DataGap` |

纪律：利率/FX 读数只作 `macro`/`risk_regime` 的 overlay 输入，不对国债/汇率本身做独立标的买卖建议；加密杠杆清算无实时数据源，缺失时标 `data_gap`，不得假设「当前杠杆水平正常」。

## 3. LongBridge CLI 实测用法（行情/组合/持仓主力）

```bash
longbridge check                             # 验证 token 有效性 + API 连通性（首选自检命令）
longbridge auth status                       # 确认授权状态
longbridge quote TSLA.US NVDA.US --format json   # 美股实时行情
longbridge quote 700.HK --format json            # 港股（CODE.MARKET，HK 用纯数字代码）
longbridge quote 600519.SH --format json         # A股（.SH/.SZ）
longbridge kline 600487.SH --period day --count 120 --format json  # K线
longbridge portfolio --format json               # 组合
longbridge positions --format json               # 持仓（账户上下文，只读用于决策，不下单）
longbridge profit-analysis --format json         # 组合损益
longbridge financial-report 600487.SH --latest --format json  # 最新财报
longbridge valuation 600487.SH --format json     # 估值（PE/PB/股息率）
longbridge capital 600487.SH --flow --format json  # 资金流向
longbridge market-temp CN --format json          # 市场温度（CN/HK/US）
```

若 PATH 找不到 `longbridge`，设置 `LONGBRIDGE_BIN="${HOME}/.local/bin/longbridge"` 后再运行。

- 符号格式：`TSLA.US` / `700.HK` / `600519.SH` / `300846.SZ`。`--format json` 便于解析。
- LongBridge 优先作为**实时行情/交易时段/盘前盘后/可用时期权链**源；A 股短线结构（涨停/连板/龙虎榜）仍以 AkShare 为主。

**两条独立的授权/凭证路径（互不冲突，按需用）：**

1. **CLI 路径（agent 当前取行情走这条）**：`longbridge auth login` 设备流授权（打印 `open.longbridge.cn/oauth2/device/...` 链接，浏览器点确认），token 自动存 `~/.longbridge/openapi/tokens/<client_id>`，所有命令自动复用。**首选设备流**，不要用 `--auth-code <CODE>`（code 10 分钟单次失效，极脆）。token 约 90 天有效，失效后重跑设备流一键续。
2. **SDK/env 耐用凭证库（durable）**：`~/.config/longbridge/.env`（chmod 600，**在 skill 目录与任何 git 之外**），含 `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN`。ACCESS_TOKEN 有效期按长桥返回结果为准；长桥安全设计无真正永久 token，必须运行时检查而不是相信文档日期。供 longport SDK（Python/Node，未装时按需 `pip install longport`）`set -a; . ~/.config/longbridge/.env; set +a` 读环境变量使用。`scripts/research_run.py` 只从此文件检测**凭证名是否存在**（names-only，零值回显）。

- 凭证只从环境变量/CLI 自管读，**绝不硬编码进 .py、绝不回显 token**；报告只写「Longbridge 可用/不可用」。
- **永久禁区**：不真实下单、不自动交易、不把下单代码写进主链路；持仓/组合仅作只读决策上下文。

## 3a. LongBridge MCP（Hermes 首选数据层）🆕

MCP 是本 skill 在 Hermes 端的**首选数据入口**，覆盖 CLI/SDK 无法提供的选股、财报、估值、行业对比、组合分析等能力。145 个工具按功能域分类：

| 功能域 | 核心工具 | 典型用法 |
|---|---|---|
| **选股筛选** | `screener_search`、`screener_strategy`、`industry_rank` | 按市值/PE/行业/技术指标筛选；平台预设策略 |
| **财报** | `financial_report_latest`、`financial_statement`、`consensus`、`forecast_eps` | 最新财报摘要、三表、一致预期、EPS 预测 |
| **估值** | `valuation`、`valuation_comparison`、`valuation_rank`、`industry_valuation` | 单票估值、同行对比、行业分布 |
| **期权** | `option_chain_expiry_date_list`、`option_chain_info_by_date`、`option_quote` | 到期日列表、特定到期日链、期权行情 |
| **组合** | `stock_positions`、`profit_analysis`、`profit_analysis_detail` | 持仓列表、组合损益、单票损益明细 |
| **资金** | `capital_flow`、`short_positions`、`broker_holding` | 资金流向、做空仓位、港股经纪商持仓 |
| **情绪** | `market_temperature`、`top_movers`、`anomaly`、`institution_rating` | 市场温度、异动股、机构评级 |
| **公司** | `company`、`business_segments`、`shareholder_top`、`executive` | 公司概况、业务分部、主要股东、高管 |
| **新闻** | `news`、`news_search`、`topic_search` | 个股新闻、关键词搜索、社区讨论 |

```bash
# 验证 MCP 可用性（需重启 Hermes 会话后）
hermes mcp test longbridge
```

- MCP OAuth 自动管理 token，无需手动续期。
- 降级顺序：MCP → CLI → SDK。每层降级在报告中标注 `longbridge_tier`，并注明缺失了哪些数据维度。
- 下单类工具（`submit_order`/`cancel_order`等）**仅作只读参考**，不通过 MCP 触发真实交易。
- **已知陷阱**：并行发起多个 MCP 调用可能把单点 schema/限流问题放大成临时的服务端级不可达状态；一旦出现，应切换数据路径，而不是用更多并发 MCP 调用加剧故障。

## 3b. `longbridge_query.py` 脚本包装器 🆕

```bash
# 实时行情（含盘前盘后）
python3 scripts/longbridge_query.py quote TSLA.US BABA.US --json
# K线
python3 scripts/longbridge_query.py candle TSLA.US --period day --count 5 --json
# 交易时段
python3 scripts/longbridge_query.py session --json
# 盘口深度
python3 scripts/longbridge_query.py depth TSLA.US --count 5 --json
```

- `longbridge_query.py` 默认先尝试 longport SDK；SDK 未安装、初始化失败或行情请求超时，会自动降级到 LongBridge CLI 的 `quote` / `kline` JSON 输出，避免关系图/事件日志因 SDK 单点故障变成空图。
- SDK 自动从 `~/.config/longbridge/.env` 读凭证并做 `LONGBRIDGE_* → LONGPORT_*` 映射。
- `session` / `depth` 仍需要 SDK；SDK 不可用时要标注缺口。其余维度（财报/估值/选股/期权链等）MCP 不可用时需切 web 搜索或标注缺口。
- Kline 字段契约（SDK/CLI 归一化后一致）：`time`, `open`, `high`, `low`, `close`, `volume`。

### Relationship Graph / Event Reaction Memory 冒烟自检

改动 LongBridge 数据路径后，关系图/事件反应记忆相关脚本用以下命令验证：

```bash
python3 scripts/longbridge_query.py candle SPY.US --period day --count 5 --json
python3 scripts/relationship_graph.py build --json
python3 scripts/relationship_graph.py show --pair NVDA.US,MU.US --json
python3 scripts/validate_skill.py
python3 scripts/validate_skill.py --scenario-only
```

健康关系图冒烟标准：

```json
{
  "market_beta_count": 11,
  "pair_count": 110,
  "data_gaps_count": 0,
  "spy_beta_120d": 1.0,
  "all_corr_in_range": true
}
```

陷阱：
- `longport` SDK 初始化可能向 stdout 打印行情权限表；`longbridge_query.py --json` 必须在初始化阶段抑制该 banner，保证 stdout 首字符就是 `[` 或 `{`。若 JSON 消费端再次出现 `JSONDecodeError: line 1 column 1`，先验证包装器输出纯 JSON，不要在每个下游各写一套脆弱的字符串截取。
- 空关系图伴随 `SPY.US: benchmark returns unavailable` 不是有效降级输出；先恢复行情/K线路径，再信任异常收益/残差计算。
- 不要无限重试同一失败访问层；确认失败后立即切换路径。
- 不要把瞬时环境问题固化为永久负面规则；只记录 fallback/修复模式本身。

### A 股取数字段速查

每个 A 股标的建议采集：
- 行情：最新价、涨跌幅、最高/最低、换手率、成交量。
- K 线指标：20/60/120 日均线、5/10/20/60 日涨跌幅、RSI14、ATR14、20/60 日高低点。
- 最新财报：营收同比、净利同比、EPS、BPS、ROE、净利率。
- 估值：PE、PB、股息率、市值；有历史区间时对比自身区间。
- 资金流：日内起止/最高/最低流向；单位存疑时只用方向不用绝对值。
- 市场温度：CN/HK/US 温度及其估值/情绪分量。

## 3c. 美股公司证据适配器（LongBridge-first → SEC → AkShare）

```bash
# 默认：LongBridge 五个只读端点；仅在缺口且 identity 有效时调用 SEC
python3 scripts/us_company_evidence.py AAPL --json

# 只验证 LongBridge 归一化，不触发 SEC
python3 scripts/us_company_evidence.py AAPL --no-sec --json

# 完整 evidence archive；SEC identity 未配置时必须干净返回 identity_missing
python3 scripts/evidence_run.py AAPL --market US --no-store --source-tier cli
```

规范化保留 `provider/provider_family/source_family/source_kind/form_type/accession_number/issuer_cik/filed_at/report_period/document_url/underlying_fact_key/retrieval_paths/data_gaps`。LongBridge CLI 在 JSON 后追加升级提示时允许解析，但任意未知尾随输出 fail-closed；财报单位与未知期间不得猜测；股东混期/匿名行不聚合、不声称 Top holders；`investors <CIK>` 只在用户明确指定机构 CIK 时调用。

SEC 只允许 `https://data.sec.gov` / `https://www.sec.gov`，需要 `SEC_EDGAR_IDENTITY`，缺失时零网络、零磁盘触碰。LongBridge filing 为空时，适配器用官方 `company_tickers.json` 做唯一 exact ticker→CIK 解析；0 命中/歧义不猜公司。商业聚合 API 与 EdgarTools 都不进入默认运行依赖。

## 4. 实时情报 / 宏观 / 期权（脚本入口）

```bash
python3 scripts/live_intel_run.py <标的> --plan-only   # 生成情报检索计划
python3 scripts/live_intel_run.py <标的> --health       # 校验 Hermes Grok 是否就绪
python3 scripts/options_gamma.py <美股代码> --json       # CBOE 期权墙/GEX（聚合45天，中线）
python3 scripts/options_gamma.py <美股代码> --near --json # 近月/0DTE 单到期日（日内 dealer 对冲，常与聚合相反）
python3 scripts/options_gamma.py <美股代码> --expiry YYYY-MM-DD --json  # 复现软件单到期日 Gamma 图
```

> 用户给单到期日 Gamma 截图时必须用 `--near`/`--expiry` 复现，不要用聚合默认值——聚合常把近月负 gamma 洗成假性正 gamma，决策相反。详见 `references/options-gamma-structure.md`。

- 宏观雷达（美债名义/实际利率、美元、黄金、SPX、通胀、流动性）：用 AkShare `bond_zh_us_rate` + Grok/web_search 组合，**本地已无 `src/data_feed` 引擎**，不要再引用。
- 杀杠杆雷达（VIX、黄金与指数同跌、AAPL、长端利率、Skew、指数 GEX）：默认跑 `python3 scripts/risk_regime_snapshot.py <标的> --json`；脚本会自动抓 Yahoo chart + `options_gamma.py`，并把 SPY GEX 的同口径历史快照写到 repo 外缓存 `~/.cache/hermes/trading-research/risk_regime/history/`。**没有 GEX 前值时，只能写结构变脆 proxy，不能硬写“出逃”**。
- 杠杆拥挤 / 离散度回归雷达（COR1M、VIXEQ−VIX 溢价）：跑 `python3 scripts/dispersion_crowding.py --json`，判断杠杆是否堆到极致、相关性是否正在回归 1。数据源 = **CBOE delayed JSON**（`.../quotes/_COR1M.json` + `.../charts/historical/_COR1M.json`，免费无 key，与 options_gamma 同源）。**⚠️ 勿用 Yahoo 拉 `^VIXEQ`/`^COR1M`——Yahoo 对这两个指数 404（实测，曾导致整次会话空转）**。门槛与机制见 `references/leverage-crowding-dispersion-playbook.md`。
- AI 供应链雷达（Serenity）：用 Grok + SEC/IR/PRNewswire/GlobeNewswire 检索，**本地已无 `src/research/serenity` 引擎**，按 `references/serenity-method.md` 手动走判断顺序。

## 5. 三市场数据要点（逻辑不同，不可生搬）

- **A 股**：必查 涨跌幅/成交额/换手/量比/20·60日位置/涨停跌停/炸板率/连板高度/龙虎榜/板块梯队/公告/财报。制度：涨跌停、T+1、盘后固定价格交易、收盘集合竞价、连板、龙虎榜、ST/退市强烈影响执行；**先按交易所+日期刷新交易规则**，再给短线/执行结论。来源：AkShare（主）+ LongBridge + WindClaw（备用交叉验证）+ 巨潮/交易所公告/同花顺/东方财富。2026-07-06 起沪市交易规则已有变化，见 `a-share-short-term-layer.md` §关键纪律；分析深市/北交所前回抓对应交易所原文。**短线层补充**：筹码分布、主力资金流向、板块涨停梯队完整性、炸板率（>40%=极弱）。
- **港股**：必查 成交额与流动性/南向资金/折溢价/ADR·美股映射/港交所披露易/公司 IR。制度：无涨跌停但流动性断层明显，小票提高滑点假设。来源：AkShare（hk_* + 南向汇总）+ LongBridge 行情 + 披露易。**估值二象性补充**：AH 溢价（Longbridge MCP `ah_premium`）、港股通纳入/剔除风险、配股/供股稀释历史。
- **美股**：必查 盘前盘后/成交额/10-K·10-Q/earnings release/call transcript/guidance/SEC filings/insider/13F/capex/订单/库存/竞争格局/期权结构。来源：SEC EDGAR + 公司 IR + LongBridge/AkShare 行情 + CBOE 期权（options_gamma.py）+ Grok/web。**瓶颈评分卡补充**：客户集中度（10-K Item 1）、产能利用率/资本开支指引（MD&A）、管理层讨论中的瓶颈/扩产描述、S-3/ATM 注册（稀释风险）、专利引用/标准参与（架构耦合度）。

## 6. 搜索问题模板

- 这家公司现在被市场买的核心叙事是什么？
- 最新财报有没有验证这个叙事？哪些指标反证？
- 当前价格已经隐含了多高的终局利润？
- 资金是在确认还是派发？
- 若今天买入，什么事实出现必须承认错了？（falsifier）
- 下一次更新数据是什么时候：财报、公告、行业会议、政策、交易日收盘？（review clock）
