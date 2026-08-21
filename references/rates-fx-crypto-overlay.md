# Rates / FX / Crypto Overlay

## 定位（先读，决定这份文件管什么、不管什么）

两档完全不同的处理方式，禁止混用：

1. **债券利率 / FX 只作宏观 overlay**——收益率曲线、信用利差、美元指数（DXY）只接入 `macro-dashboard-four-pillar.md` 与 `risk_regime`，**不对国债/汇率本身做独立标的层面的买卖建议**。这不是本 skill 的能力范围（缺流动性、久期、央行政策路径的专业定价能力），只用作判断权益/信用/杠杆环境的宏观变量。
2. **加密只支持 BTC/ETH 主流币**，且仍是 Tier 0/1 查询与风险框架为主——不做山寨币、不做链上数据深挖、不做代币经济学分析。BTC/ETH 走与股票同一套 Decision Compiler 管线，但杠杆清算风险需要单独约束（见下）。

## 数据源（已实测，2026-07-19）

| 数据 | 源 | 命令 | 实测结果 |
|---|---|---|---|
| 中美国债收益率（2/5/10/30年）+ 10y-2y 利差 + GDP | AkShare（既有桥接） | `ak.bond_zh_us_rate(start_date="YYYYMMDD")` | **首选**。已测通，单次调用同时返回中美两条曲线+利差，覆盖 `macro-dashboard-four-pillar.md` 所需的利率维度 |
| 美债全曲线（1mo-30yr，仅美国） | Treasury.gov 公开 CSV | `curl "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/<year>/all?type=daily_treasury_yield_curve&field_tdr_date_value=<year>&page&_format=csv"` | 已测通，无需 key；只在需要 1 个月/3 个月等短端极短久期数据时补查（`bond_zh_us_rate` 无短端），日常判断以 AkShare 为准 |
| BTC/ETH 现价 + 24h 涨跌 + 市值 | CoinGecko 免 key API | `curl "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"` | 已测通，无需 key；免费额度有速率限制，高频轮询前先查 CoinGecko 当前限额政策 |
| 美元指数 DXY | Yahoo Finance chart API | `curl -A "Mozilla/5.0" "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=1d"` | **首选**，已测通，`regularMarketPrice` 字段可用，接近实时 |
| 美元指数 DXY（交叉核对/降级） | FRED `DTWEXBGS`（美联储广义美元指数） | `curl "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTWEXBGS"` | 已测通，但发布口径不同于 ICE DXY（广义贸易加权 vs 传统六币种），只作趋势交叉核对，不能与 Yahoo 数值直接对比 |
| ~~stooq DXY CSV~~ | stooq.com | — | **实测失败**：返回 JS 工作量证明反爬挑战（Anubis-style），无法用 curl 直接取数。标记 `DataGap`，不写入常规取数路径；如未来需要需换无头浏览器方案，不在本次范围内实现 |

## 风险框架约束

- **利率/FX 变量的裁决上限**：单独的利率/DXY 读数不构成任何标的的加仓理由——只能收紧或维持，不能因为「利差走阔利好某板块」就单独提高 action level。必须与该标的自身的 `fundamentals`/`endogenous_structure` 证据同时成立才能参与正常裁决（复用 `decision-compiler.md` 行 184 的既有规则：`macro + fundamentals + endogenous_structure` 同时给出交叉验证时，才不超过上游基本面与风控允许等级）。
- **加密杠杆清算风险**：BTC/ETH 永续合约资金费率极端、未平仓合约激增、交易所强平公告等信号，走既有 `forced_liquidation`／`liquidity_squeeze` 模块（tighten-only，属于 Cap & Tighten-Only Registry 的市场风险型集合，触发时强制 `holding_directive=EXIT`），不新建加密专属清算等级。本 skill 无实时永续合约资金费率/强平数据源，该维度目前只能凭用户提供的截图/新闻披露作为证据输入，缺失时明确标 `data_gap`，不得假设"当前杠杆水平正常"。
- **波动率**：BTC/ETH 历史波动率显著高于大盘股，同等仓位乘数计算需按 `references/decision-compiler.md` 既有仓位公式代入更高波动率输入，不单独定义新公式。

## Decision Compiler 映射

不新增模块：

| 信号 | module | sub_framework | 封顶/收紧行为 |
|---|---|---|---|
| 收益率曲线/利差/DXY 宏观读数 | `macro` | `rates_fx_overlay` | 只作为 overlay 输入 `macro-dashboard-four-pillar.md`，不单独提升任何标的 action level |
| BTC/ETH Tier 0/1 现价查询 | `market_data` | — | 沿用既有 Tier 0/1 直答格式，无需完整 Decision Compiler 管线 |
| BTC/ETH 结构性风险判断（Tier 2） | `risk_regime` | `crypto_overlay` | 高波动率环境下仓位乘数按既有公式自动收紧，不新增独立乘数表 |
| 杠杆清算/强平信号 | `forced_liquidation` / `liquidity_squeeze` | — | 沿用既有市场风险型 hard veto 集合；触发强制 `EXIT`，无缓冲 |

## 边界

- 不覆盖山寨币、DeFi 协议、链上数据、代币经济学——超出主流币 Tier 0/1 查询范畴即回复「不在本 skill 覆盖范围」。
- 不对国债/外汇本身做买卖时点建议；只输出「当前宏观环境对权益仓位的收紧/维持含义」。
- CoinGecko/Yahoo/FRED 均为免 key 公开接口，无 SLA 保证；连续失败三次应整体标 `data_gap` 并降级为「宏观环境未知」，不得用陈旧缓存值冒充实时数据。
