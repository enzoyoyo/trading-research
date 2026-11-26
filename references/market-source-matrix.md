# Market Source Matrix · 三市场数据源矩阵

> v2.23：Longbridge MCP 已成为三市场**首选数据层**（145 工具：行情/财报/估值/选股/期权链/组合/资金/情绪）。A 股新增 a-stock-data bridge 作为公开源直连补强（腾讯/东财/巨潮）。降级顺序：MCP → CLI/SDK → a-stock-data bridge / AkShare / WindClaw → web/官方原文。

## A 股

| 模块 | 首选 | 兜底 | 必填输出 |
|---|---|---|---|
| 证券身份 | `security_resolver.py` + 交易所后缀推断 | 搜索/交易所名录 | `600519.SH` / `300846.SZ` |
| 行情/K线 | LongBridge MCP `quote` / `candlesticks` | LongBridge CLI / `a_stock_data_bridge.py quote`（腾讯）/ AkShare `stock_zh_a_hist` / WindClaw | 价格、成交额、换手、PE/PB/市值、涨跌停、时间戳 |
| 情绪结构 | AkShare 涨停/炸板/龙虎榜/资金流 | `a_stock_data_bridge.py concept|fund-flow`（东财限流）/ WindClaw 市场/板块表现 + 东方财富/同花顺页面 | 涨跌停、炸板率、连板高度、龙虎榜、概念归属、资金流 |
| 公告/财报 | LongBridge MCP `financial_report_latest` 🆕 | `a_stock_data_bridge.py announcements`（巨潮动态 orgId）/ 巨潮/上交所/深交所/北交所 / WindClaw | 最新公告、报告期、问询/ST/减持、orgId 是否 fallback |
| 估值/行业 | LongBridge MCP `valuation` / `industry_rank` 🆕 | web 搜索 | PE/PB/PS 分位、行业排名 |
| 宏观政策 | Grok/web + 官方政策 | WindClaw reference/data + 财经媒体 | 政策顺逆风、流动性状态 |
| 新闻/传闻发现 | LongBridge `news/news_search` + `web_search` | `multi_source_search.py --market A --profile news|rumor` | provider、时间、原始 URL 候选、credibility gap |

## 港股

| 模块 | 首选 | 兜底 | 必填输出 |
|---|---|---|---|
| 身份 | `security_resolver.py` | 港交所/公司名搜索 | `00700.HK` |
| 行情/成交额 | LongBridge MCP `quote` | LongBridge CLI / AkShare `stock_hk_*` | 价格、成交额、流动性 |
| 公告/财报 | LongBridge MCP `financial_report_latest` 🆕 | HKEXnews/披露易 / 公司 IR | 最新公告、报告期 |
| 估值 | LongBridge MCP `valuation` / `industry_peers` 🆕 | web 搜索 | PE/PB 分位、同行对比 |
| 资金结构 | LongBridge MCP `broker_holding` / `short_positions` 🆕 | 南向/CCASS/沽空（可得则查） | 经纪商持仓、做空仓位、流动性风险 |
| 映射 | LongBridge MCP `ah_premium` 🆕 | ADR/AH 对照 / 搜索 | AH 溢价、跨市场情绪 |
| 新闻/传闻发现 | LongBridge `news/news_search` + `web_search` | `multi_source_search.py --market HK --profile news|rumor` | provider、时间、原始 URL 候选、credibility gap |

## 美股

| 模块 | 首选 | 兜底 | 必填输出 |
|---|---|---|---|
| 身份 | `security_resolver.py` | identity-gated SEC `company_tickers.json` exact ticker→CIK | `TSLA.US` / `BRK.B.US` + 唯一 issuer CIK；歧义不得猜测 |
| 行情/盘前盘后 | LongBridge MCP `quote` | LongBridge CLI / AkShare/Yahoo | 价格、成交额、时间戳 |
| 公告/财报/监管申报 | `us_company_evidence.py`：LongBridge CLI `financial-report/filing/insider-trades` → identity-gated SEC EDGAR | LongBridge MCP / 公司 IR / 财经媒体 | accession、CIK、form、filed_at、report period、原始 URL、修订状态、DataGap |
| 估值/一致预期 | `us_company_evidence.py` LongBridge CLI `valuation` + LongBridge MCP `consensus` | AkShare/web 搜索 / 手工计算 | PE/PB/PS、口径/时间、收入/利润一致预期 |
| 期权/Gamma | `options_gamma.py` CBOE delayed | LongBridge MCP `option_chain_*` 🆕 / yfinance+BS | Put Wall、Call Wall、Gamma Flip、GEX |
| 杀杠杆 Regime | `risk_regime_snapshot.py` | 财经媒体 + 手工截图对比 | VIX、黄金vs指数、AAPL相对表现、长端利率、Skew、指数GEX |
| 宏观四象限 | FRED/Treasury/Fed + `macro-dashboard-four-pillar.md` | 财经媒体 | liquidity / economy / inflation-rates / sentiment + composite |
| 产业链 | SEC/IR/新闻 + Serenity 框架 | Grok/web | capex、订单、交期、涨价、库存 |
| 内生结构 | X/Grok + 公司文件 + ETF/指数公告 + 新闻 | web_search/web_extract | narrative_stage、crowding、passive_flow、issuance_overhang、rotation_regime |
| 被动/发行/解禁 | SEC EDGAR 原始 filing + LongBridge filing index + 公司 IR | secondary/index provider news / 财经媒体 | IPO、lockup、secondary、convert、index inclusion；同 accession 只算一个 source family |
| 组合/损益 | LongBridge MCP `stock_positions` / `profit_analysis` 🆕 | 手工计算 / CLI `positions` | 持仓列表、组合损益、单票贡献 |
| 新闻/传闻发现 | LongBridge `news/news_search/filings` + `web_search` | `multi_source_search.py --market US --profile news|filing|rumor` | provider、时间、原始 URL 候选、credibility gap |
| 分红质量 | LongBridge `dividend/dividend_detail` + financial statements | 公司 IR/SEC/web | yield 口径、EPS/FCF payout、增长/削减、ex-date、收益陷阱 |

## 运行时 capability/coverage 门（v2.38）

静态矩阵只说明预期来源，不能替代本次运行检查。多标的或跨市场研究先用 `scripts/intelligence_coverage.py` 汇总每个 `target × dimension`：

- provider 标记 `live/delayed/cached/unsupported/auth_missing/rate_limited/error/unknown`。
- `unsupported/unavailable/stale/missing` 一律登记 DataGap，不能写成价格 0、无公告、无新闻或负面信号。
- delayed/cached 必带 `observed_at/stale_after/fallback_level`；不得与 live 混写；`observed_at > as_of` 或 `stale_after <= observed_at` 必须 fail-closed，防止未来数据或无效 freshness 窗口。
- 每个标的按 criticality round-robin 补抓，禁止热门票连续占满预算。
- 只有完成 security resolver 后的 canonical symbol 才进入 coverage matrix；静态 exchange suffix 映射不能替代身份解析。
