# Source Map · 方法论资料映射（v2.55 更新）

本 skill 的方法论来自多个来源。**原文单一事实来源**按方法论分列如下。

## v2.55 多周期 / 信号融合 / GEX 冲突来源

### Multi-Horizon Prediction Contract
- **吸收**：预测必须按 horizon 分账；校准分桶；禁止跨周期合成胜率。对齐既有 E01 语义边界。
- **拒绝**：任何“综合胜率提升”承诺。
- **映射**：`references/multi-horizon-prediction-contract.md`、scenario `multi_horizon_shared_score_cannot_lift`。

### Signal Fusion & Risk-Budget Agreement
- **公开锚点（机制级，非溢价承诺）**：Asness (1997) / Asness–Moskowitz–Pedersen (2013) *Value and Momentum Everywhere*（条件交叉，非平均）；Newfound Research / Corey Hoffstein 公开博客中的等风险混合与 tilt vs overlay 纪律（访问日 2026-08-15 研究包）。
- **吸收**：禁止相关信号算术平均抬仓；一致才放大风险预算投影；冲突收缩；tilt/overlay 分列。
- **拒绝**：历史因子溢价、Return Stacking 产品配置、回测 IR 承诺。
- **映射**：`references/signal-fusion-risk-budget.md`、既有 `portfolio_risk_budget`。

### Dealer-gamma multi-vendor vs Cboe
- **公开锚点**：SqueezeMetrics GEX 白皮书（2016）；SpotGamma 支持文档；Cboe *Volatility Insights: Evaluating the Market Impact of SPX 0DTE Options*（2023-09-08）及后续 0DTE decoded 文；Muravyev–Pearson–Pollet JFE 2025（借券费）。
- **吸收**：Conflict Ledger、禁止平均 flip、名义成交≠净 gamma、skew 前控借券费。
- **拒绝**：任一家日度 GEX 数字硬编码为买卖信号。
- **映射**：`references/options-gamma-structure.md` §多 vendor Conflict Ledger。

## v2.53 美股监管证据来源

### SEC EDGAR 官方 API 与 fair-access 边界

- **官方 API 文档**：SEC, *EDGAR Application Programming Interfaces*，<https://www.sec.gov/search-filings/edgar-application-programming-interfaces>；用于 submissions 与 XBRL companyfacts 契约。
- **官方访问规则**：SEC, *Accessing EDGAR Data*，<https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data>；用于可联系 User-Agent、限速、缓存与自动访问纪律。访问日期 2026-08-11。
- **本机运行时观察**：LongBridge CLI 当前可返回 `financial-report`、`valuation`、`filing`、`insider-trades`、`shareholder`，显式机构 CIK 可查 `investors`；该观察必须运行时复核，不冻结为供应商永久承诺。
- **吸收**：LongBridge-first 只读采集；LongBridge filing 为空时用 SEC 官方 `company_tickers.json` 做 exact ticker→CIK；再以 submissions/companyfacts 免费按需兜底；accession/CIK/原始 URL/XBRL 归一化；同 accession 传输路径去重；identity missing 与网络失败 fail-closed；私有 TTL 缓存。
- **拒绝**：不新增商业 API、EdgarTools 运行依赖、匿名 SEC 请求、provider ID 冒充 accession、多传输路径虚增 EID、目标公司 holder lookup 与机构 13F portfolio 混淆、任何订单能力。
- **映射**：`scripts/us_company_evidence.py`、`scripts/fundamental_snapshot.py`、`scripts/evidence_run.py`、`scripts/test_us_company_evidence.py`、`data-contracts.md`、`source-reliability-policy.md`、`runtime-fallbacks.md`、`data-source-playbook.md`、`market-source-matrix.md`、`scenario-regression-tests.md`。

## v2.52 港股 board lot 制度来源

### HKEX · Streamlined Board Lot Framework

- **官方公告**：HKEX, *HKEX to Move Forward with Streamlined Board Lot Framework*，发布/更新 2026-06-30，<https://www.hkex.com.hk/News/Market-Communications/2026/260630news?sc_lang=en>。
- **当前版本与生效边界**：Phase 1 自 2026-07-02 生效，覆盖新的 board-lot value floor/ceiling guidance，并要求涉及每手股数变更、合股或拆股的现有发行人遵守完整框架；首个每手金额六个月评估期从 2026-07 开始。Phase 2 随 USM 于 2026-11-16 启动，但发行人是在完成各自 USM 过渡后六个月内采用标准化每手单位，不得误写成所有标的于启动日同步切换。odd-lot 自动撮合仍只是最早 2027 Q3、且取决于监管批准与市场准备的探索项。
- **吸收**：港股执行前核验 point-in-time board-lot unit/value、适用范围、公司行动/USM 迁移状态和碎股盘口；未知或过期映射到既有 `data_quality` / `liquidity`，只收紧、不产生精确数量。
- **拒绝**：不把规则更新当作利好，不提高 action level/position cap，不假设未来 odd-lot 自动撮合已经上线，不新增平行动作体系或订单路径。

## v2.48 OKX 适配来源

### OKX API V5 与 Unified Tokenized Stocks

- **官方 API 文档**：<https://www.okx.com/docs-v5/en/>，访问日期 2026-07-27。用于 instruments、ticker、books、candles、Demo header、`expTime`、Order precheck、Cancel All After、Private WS account/positions/orders/fills 的接口契约。
- **官方产品公告**：<https://www.okx.com/en-us/help/okx-to-list-unified-tokenized-stocks-for-spot-trading>，发布 2026-07-15、访问 2026-07-27。公告明确 X 前缀、USDT 报价、24/7、非底层公司所有权/投票权、地区资格差异，并列出 `XMU/USDT` 与 `XSKHY/USDT`。
- **公共 API 历史 E2E 观察**：2026-07-27 以 `python3 scripts/okx_public_snapshot.py <instId> --site eea` 读取 `XMU-USDT`、`XSKHY-USDT`，两者当时均返回 `SPOT`、`instCategory=3`、`state=live`，ticker/books/candles 正常。该观察不是冻结行情产物，只证明当时公共产品存在，不证明当前价格、账户资格或投资适合性；发布验证必须重新运行命令。
- **吸收**：产品身份先于评分；public/read-only snapshot；last-good + stale；REST baseline + WS 持续同步；item-level 回报检查；账实对账；`pause_required/pause_effective` 分离。
- **拒绝**：不把 tokenized stock 当底层股票所有权；不把自然语言名称当 `instId`；不连接暴露过的凭据；不在本 Skill 实现订单写入、策略 Bot 或实盘执行。
- **映射**：`okx-research-execution-supervision.md`、`scripts/okx_public_snapshot.py`、`scripts/entry_score.py`、`scripts/okx_execution_supervisor.py`、`scripts/okx_monitor_dashboard.py`、`scripts/research_run.py`、`data-contracts.md`、`decision-compiler.md`、`templates/universal-equity-report.md`、`templates/routing-evals.jsonl`、`templates/scenario-regression-evals.jsonl`。

### OKX Agent Trade Kit

- **仓库**：<https://github.com/okx/agent-trade-kit>。
- **固定提交**：`ed431ba12e7eec4e6ef8222109a1f3bcda95599c`；2026-07-27 只读审计。
- **许可证**：MIT；本 Skill 未复制 TypeScript、配置、凭据、日志或交易实现。
- **源码边界**：固定提交同时包含 market/account 读取与 spot/swap/futures/options/bot 等写工具；工具元数据用 `isWrite` 区分读写，并提供 `--read-only`。因此不能整包暴露给对话 Agent。
- **吸收**：write deny-by-default、read-only 进程、模块级过滤、本地审计与 item-level error 语义。
- **拒绝**：不安装为本 Skill 的运行依赖，不让研究 Agent 持有交易权限，不采用其工具调用直接替代 Decision Compiler。

## v2.42 KOL Method Card 来源

本节的 2026-07-17 三张 legacy 卡只登记 sealed Grok public-source ledger 中带作者、日期和原始 URL 的 accepted metadata。当前执行环境对 X 原帖的独立直开没有获得可存档正文，且未复制订阅正文，因此这些 legacy 行故意不含 QuoteAnchor/local capture，机器状态保持 `partial`；以下摘要不是逐字引文，也不把 discovery ledger 当作原文证明。2026-08-02 追加的 `KMC-BALDER-20260731-FINAL` 另有 purpose-bound、hash-bound 私有捕获 attestation，固定 L0/零仓位；它不反向提升 legacy 卡。未来刷新必须重新抓取原始页面、记录 hash，再按 `research_provenance.v1` 提升或降级。

### @citrini / Citrini Research

- `KOL-SRC-C3`（2026-07-14）：https://x.com/citrini/status/2076891040343072975 — portfolio-as-position、drawdown 的 thesis/expectations/size 与 next-dollar discipline；仅作可回抓的方法元数据。
- `KOL-SRC-C9`（2026-06-22）：https://x.com/citrini/status/2069090305870115131 — 方向可能正确但 timing 失败的公开复盘元数据。
- `KOL-SRC-C7`（2026-06-28）：https://substack.com/@citrini/note/c-284380995 — 含自报曲线结果，只登记 promotion gap，不能提高 reliability。
- `KOL-SRC-C8`（2026-06-08）：https://www.citriniresearch.com/p/state-of-the-themes-june-2026 — subscriber-only gap；未推断付费主题篮、权重或持仓。

### @Balder13946731 / Balder

- `KOL-SRC-B1`（2026-07-16）：https://x.com/Balder13946731/status/2077755188291756213 — SPX spot/gamma flip/call wall/put wall/VRP/range 的日度结构元数据；只能刷新或收紧。
- `KOL-SRC-B6`（2026-07-16）：https://x.com/Balder13946731/status/2077815613112897882 — 财报 expected move 与 IV-crush/side-trade 风险元数据。
- `KOL-SRC-B5`（2026-07-13）：https://x.com/Balder13946731/status/2076675543593087321 — nightly verdict/book grade 为自报绩效，只登记 promotion gap；公开仓位公式、完整 09:40 实施链仍 unknown。

### @Franktradinglog / Frank Trading

- `KOL-SRC-F8`（2026-07-01）：https://x.com/Franktradinglog/status/2072428625685786711 — compute monetization 叙事与半导体 earnings-gap invalidation 元数据。
- `KOL-SRC-F9`（2026-06-21）：https://x.com/Franktradinglog/status/2068843747182625171 — passive-flow 风险期渐进加仓元数据，不是全仓指令。
- `KOL-SRC-F1`（2026-07-13）：https://franktrading.substack.com/p/07132026 — 只把 public-visible 部分标为 `partial`；付费 outlook、仓位表和社区记录均为 subscriber gap。
- `KOL-SRC-F4`（2026-07-15）：https://x.com/Franktradinglog/status/2077397261919855064 — option outcome 为自报绩效，只登记 promotion gap。

三张卡统一映射至 `references/source-grounded-research-provenance.md`、`scripts/kol_method_card.py` 与现有 `x_frontline/research_readiness`；不移植精确交易，不产生新动作等级、仓位来源、Memory 或订单路径。

## v2.41 / v2.40 working-tree 元数据回填

- v2.41：仅记录升级前已验证工作树已把 `research_provenance.v1` 接入 report/validator/memory handoff；历史形成与发布日期未知。
- v2.40：仅记录升级前已验证工作树已有 AnySearch joint discovery；它是 `discovery_only`，历史形成与发布日期未知。
- 两条均于 2026-07-17 回填，不据此声称曾存在独立 release artifact。

## v2.39 新增来源

### lyra81604/zhengxi-views

- **仓库**：https://github.com/lyra81604/zhengxi-views
- **固定版本**：commit `44e0c48657e93c5bf8340fcd2d0f5bac3a4b17d1`；tree `cb3f50b5d1a4b723dcfa4feb2e028047fe8c4fff`；`git archive HEAD` SHA-256 `e2008c730284e760e7db1fa2cb296e8dbf052e402ce30f60f991ba0b2349b306`；141 个 tracked files；无 tag；审计工作树 clean。
- **许可证/版权**：根 LICENSE 对代码/自写文档为 MIT，但 `upstream/LICENSE:25-28` 明确 `upstream/references/corpus/`、`upstream/references/fund_data/` 等第三方内容版权不随 MIT 转移；部分基金经理手记另含禁止复制/派发/发布限制。未迁移 corpus、基金数据、人物专属观点、长篇原文或精确评分示例。
- **源码审计**：读取 `SKILL.md`、README、method/scorecard、corpus index、搜索/索引/评分/抓取脚本、evals、license 与样例语料。上游可复用增量是语料→方法→应用分层、先查再说、原话/推演/待核实三分法、公开表态与行为对照；但引用、反方、不杜撰和记忆主要是 prompt/文档软约束，缺少 claim-level schema、validator、可执行 test runner、Conflict Ledger 与长期决策记忆。
- **红队结论**：`search_corpus.py` 能返回片段和来源，但不能校验最终回答；`score_fund.py` 只输出机械指标，关键维度仍需人工评分，存在伪精确风险；基金抓取含 TLS `verify=False`/明文 HTTP 和 skill 目录缓存模式；7 天文件缓存不是 Memory。上游业务代码、依赖与凭据路径不作为本 skill 运行依赖。
- **吸收**：新建 `research_provenance.v1`：SourceDocument→QuoteAnchor→FrameworkClaim→AnalysisClaim→BehaviorCrossCheck；加入内容 hash、行锚点、短引版权门、反证/falsifier、主体/时间/任期对齐和禁止行为因果；输出 `verified|partial|blocked` 与只收紧 `research_readiness` signal，复用现有 Decision Memory stable ID/hash 关联。
- **拒绝**：不新增第二套 Evidence/Compiler/Memory/评分器；不迁入固定权重 100 分、人物风格、抓取器、邮件/cron、基金缓存或任何交易执行路径。
- **映射**：`source-grounded-research-provenance.md`、`data-contracts.md`、`source-reliability-policy.md`、`trading-decision-memory.md`、`scripts/provenance_guard.py`、`scripts/test_provenance_guard.py`。

## v2.38 新增来源

### Open-Dev-Society/OpenStock
- **仓库**：https://github.com/Open-Dev-Society/OpenStock
- **固定版本**：commit `4597c9a668118844b588f95eddb9342eed31c41d`；tree `2b8605c9d36497637e788efb1afe9ca17d70f46f`；`git archive HEAD` SHA-256 `ad6cdac1dabfa166782e66f57bebd9f953edbf6573850f15e0624d4b620844ad`；main 分支，无 tag。
- **许可证**：AGPL-3.0。只做 clean-room 方法抽象；未复制 TypeScript、Prompt、UI、Schema 或 tests。
- **Clean-room marker**：`methodology_only_no_code_copied=true`；该标记必须保留在 OpenStock 自身来源段，不能由其他项目的同名声明代替。
- **源码审计**：逐文件读取 Finnhub actions、Adanos normalize/compare、AI provider fallback、watchlist/alert models+server actions、Inngest schedules、TradingView symbol mapping、auth/middleware、API docs、market support、tests、package manifests/lockfile。实际运行 `npm ci --ignore-scripts`、test/lint/tsc 与官方 npm audit。
- **生态/Issue 复核**：2026-07-12 GitHub API 显示 13,774 stars、1,822 forks、29 open issues、无 releases，main 未保护。Issue #83/#86 明确指出 free-tier 非美股/实时覆盖限制并请求 pluggable provider；PR #82 复现缺失 market cap 被渲染为 `NaN`；PR #81 的自动 paper-trading/买卖功能仍未合入 main，不能当当前能力，也因执行边界明确拒绝。
- **安全结论（Critical）**：固定提交的 `scripts/resolve_srv.js:37-51` 硬编码 MongoDB 用户名/密码、组装 URI 并可写入 `mongo_uri.txt`；`database/mongoose.ts:50` 可把完整 `MONGODB_URI` 打入日志。凭据值不记录、不复述，统一视为已失陷：`upstream_credential_compromised=true`。本 skill 禁止执行这些路径、复制其配置或将该仓库作为可直接安装依赖；上游维护者应立即轮换账户并清理历史。
- **其他高风险**：Server Actions 信任客户端 `userId/alertId` 且删除/切换缺 owner filter；middleware 仅看 cookie presence；可配置 base URL 会把 API key/Bearer 发往环境指定 host；邮件模板和 LLM HTML 未统一 sanitize；Kit secret 进入 query string；Docker 缺 `.dockerignore`、使用 `npm install`/未 pin digest并暴露 Mongo；`next.config.ts` 忽略 lint/type 构建错误。
- **质量结论**：79 tests pass、4 integration tests skipped；lint 36 errors/44 warnings；tsc 11 errors（含 Inngest API signature drift）；npm audit 65 vulnerabilities（3 critical/15 high/46 moderate/1 low），直接高风险依赖含 Better Auth、Inngest、Mongoose、Next、Nodemailer。README 的生产化/个性化邮件声称与当前 runtime、unused imports、typecheck 状态存在漂移。
- **吸收**：market×dimension capability 显式化；区分 capability/coverage/evidence；多标的按 criticality round-robin 的公平补抓；跨来源 metric 保留量纲/窗口/样本并只把 dispersion 用作 conflict/verification priority；alert condition/expiry/cooldown/rearm/one-shot 抽象为只触发重研的 `ResearchWatchTrigger`。
- **拒绝**：AGPL 代码/Prompt、Next/UI/Better Auth/Mongo/Inngest/Nodemailer、自动 cron/邮件、client-passed userId CRUD、无 owner filter 的 alert delete/update、`NEXT_PUBLIC_` 行情 key、静态 ticker 映射真值、不可比的 bullish/buzz 简单平均、缺失价格写 0、通用新闻伪装 symbol coverage、AI 摘要作证据。
- **映射**：`intelligence-coverage-and-watch-triggers.md`、`data-contracts.md`、`market-source-matrix.md`、`attention-rumor-triage.md`、`scripts/intelligence_coverage.py`、`scripts/research_watch_trigger.py`。

## v2.37 新增来源

### ClawHub `@gpyangyoujun/multi-search-engine` v2.1.3
- **页面**：https://clawhub.ai/gpyangyoujun/skills/multi-search-engine
- **精确归档**：`/api/v1/download?slug=multi-search-engine&ownerHandle=gpyangyoujun&version=2.1.3`；2026-07-12 下载 SHA-256 `c0624c09d52931dd5cb9526d2519ec09a4bc117ce83b36936be79631ddfe1536`。
- **审计**：ClawHub security audit 为 3 个 medium finding；核心问题是“无外部传输”表述失实、按语言自动跨 provider 路由、缺少敏感查询外发警告。65/65 VirusTotal clean，静态扫描无恶意模式。
- **吸收**：语言/市场/意图路由、多搜索索引、操作符、限速、聚合与 retry 思路；实现为 stateless plan-first helper、source health、去重和独立 provider family。
- **拒绝**：16 引擎盲扫、自动 cookie 获取/刷新、绕过 403/429、错误隐私承诺、WolframAlpha 股票事实源。归档 `metadata.json` 仍写 2.1.0，和 `_meta.json` 2.1.3 存在版本漂移，故只按精确归档审计。
- **映射**：`multi-source-search-layer.md`、`scripts/multi_source_search.py`、`data-contracts.md`、`runtime-fallbacks.md`。

### ClawHub `@udiedrichsen/stock-analysis` v6.2.0
- **页面**：https://clawhub.ai/udiedrichsen/skills/stock-analysis
- **精确归档**：`/api/v1/download?slug=stock-analysis&ownerHandle=udiedrichsen&version=6.2.0`；2026-07-12 下载 SHA-256 `0a967cef6bca56306ba2d6b8e74d927ae188dc2f9d9ec5c25d245ed73c137eea`。
- **审计**：完整读取 Hot Scanner、Rumor Scanner、Dividend、Watchlist、Portfolio、8-dimension analyzer、tests 和 architecture。ClawHub audit 有 19 findings；高风险集中在要求用户复制 X `AUTH_TOKEN`/`CT0`、读取整份 `.env` 并传给 Bird CLI 子进程，以及声明 Yahoo scope 却静默扩展外部通信。归档 frontmatter v6.2.0 但正文标题仍 v6.1，存在文档漂移。
- **吸收**：多车道热点发现、传闻类别、attention/credibility 分轴、分红 yield/payout/growth/continuity 产品结构；分红进一步补 FCF、资产负债表、周期与 yield-trap 门。
- **拒绝**：独立 8 维 BUY/HOLD/SELL、keyword-only rumor impact score、本地 portfolio/watchlist JSON、自动 cron、Bird/X cookie、全 `.env` 传播、静态 ticker/geopolitical map、固定 0–100 dividend safety score。未复制外部代码，只迁移可审计机制。
- **映射**：`attention-rumor-triage.md`、`dividend-quality-framework.md`、`multi-factor-evidence-synthesis.md`、`decision-compiler.md`。

## v2.35 新增来源

### Balder（@Balder13946731）公开 close-to-open / 09:40 / SPX range 卡片
- **公开来源**：
  - 2026-07-06 Pre-Open Read / 9:40 sell card：https://x.com/Balder13946731/status/2074128645925597568
  - 2026-07-08 09:30–09:40 continuity gap 自述：https://x.com/Balder13946731/status/2074849560197738546
  - 2026-07-06 SPX range outlook：https://x.com/Balder13946731/status/2074208136706756807
  - 2026-07-08 Pareto/no-look-ahead 图：https://x.com/Balder13946731/status/2074832331090104696
  - 2026-07-09 10:00 ET、6,000-path gamma-adjusted Monte Carlo range/touch card：https://x.com/Balder13946731/status/2075220699774636417
  - 2026-07-09 live projection / rest-of-day range card：https://x.com/Balder13946731/status/2075267285359657044
- **图片复核**：公开图片逐项 OCR/视觉审计，确认 pre-open index/basket 卡、固定 9:40 退出文案、SPX probability cone/walls/VRP/GEX 字段；Pareto 图与 Monte Carlo 卡均缺完整参数、成本、未删样本和独立 OOS 校准，不能证明可交易优势。
- **Superfollows 状态**：`https://x.com/Balder13946731/superfollows` 在当前未登录环境未暴露订阅正文；记录 `subscriber_only_gap/paywall_gap`，禁止推断隐藏内容。
- **蒸馏内容**：只吸收“close→pre-open→09:40 continuity→新决策”的流程缺口、字段设计与可证伪检查；自报命中率/概率区间不作为 verified alpha。
- **映射文件**：`short-cycle-market-structure-overlay.md`、`data-contracts.md`、`decision-compiler.md`、`scripts/short_cycle_structure.py`。

### Balder（@balder714059）2026-07-31 Substack 单篇方法卡
- **公开来源与身份边界**：`https://baldertrader.substack.com/p/balders-position-2026-07-31`；本卡只覆盖用户提供的 2026-07-31 单篇邮件及其中一张语义图片。捕获的 profile handle 为 `@balder714059`，它未与既有 X 卡 `@Balder13946731` 完成身份关联；两张卡必须保持独立，禁止按同一作者或长期历史合并。
- **保存边界**：Skill 只保存来源 metadata、逐项 hash、review-index、短 QuoteAnchor 与蒸馏结论；原邮件、原图、个人数据和本地私有路径均不进入仓库。私有 capture 只允许通过 purpose-bound、hash-bound 的 KOL attestation 做 partial/watch-only 复核，不能取得 report authority。
- **准入结果**：`accepted_claim_ids=[]`、`canonical_eids=[]`，整体固定 `partial`、L0、position multiplier 0、`no_order_execution=true`；只允许 tighten、刷新来源或人工复核，不得提高 action/reliability/仓位，也不得写入 material Memory。
- **吸收**：信号状态与跟踪状态分离、同单位可证伪、缺新证据时不升级、生命周期缺口显式化，以及可复现预测所需的 point-in-time / benchmark / denominator 检查。
- **拒绝迁移**：87bp、36、31.8、37.9、38 等单篇数值；图片 dashboard 与自报收益；隐藏 universe、公式、阈值、仓位、加减仓、止盈、时间止损或订阅规则；NVDA/存储/HBM/DRAM、SpaceX/TSLA 等来源特定 taxonomy；以及任何新模块、score、提醒、cron 或下单路径。
- **映射文件**：`templates/kol-method-cards-public-ledger.json`、`source-grounded-research-provenance.md`、`data-contracts.md`、`trading-decision-memory.md`、`scenario-regression-tests.md`。
- **公共 ledger 兼容边界**：报告必须显式选择一个已安装的 `kol_method_card_id`。只有 `KMC-BALDER-20260731-FINAL` 绑定本次 claim coverage；选择任一 legacy 卡只保留其原有非晋级 L0 行为，不得继承新卡的 QuoteAnchor、AnalysisClaim 或 freshness 断言。缺失或未知 card ID 均拒绝。

### SEC DERA · 0DTE limit-order execution quality
- **来源**：*Hope at a Reasonable Price: Limit Orders in 0DTE Options*（2025-03），https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf
- **蒸馏内容**：执行质量与方向 alpha 分离；near-money 0DTE fill probability、aggressiveness、non-fill 与 price impact 进入 option instrument gate。
- **约束**：不得把成交概率统计当方向预测；不得因此鼓励 market order 或放宽 cost hurdle。

### Cboe Research · dealer gamma / 0DTE market impact
- **来源**：
  - *Gamma Squeezes*：https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf
  - *Evaluating the Market Impact of SPX 0-DTE Options*：https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options/
- **蒸馏内容**：gamma 是条件型库存/对冲结构，必须叠加到期、moneyness、IV、时间和可比口径；0DTE aggregate hedging flow 常较平衡，不能把墙位图当单因果预测。
- **约束**：positive gamma/墙位触碰不能提高 upstream cap；delayed/stale gamma 只能收紧或登记 data gap。

### X 公开 practitioner scan（方法发现，不作事实锚）
- **样本**：SpotGamma、SqueezeMetrics、Tier1Alpha、MenthorQ、VolSignals、Kris Sidial、GammaEdge 等公开帖子；代表链接见 v2.35 研究留档。
- **吸收**：zero gamma / put-call walls / opening-range / dealer exposure 的检查字段，以及“先验→触发→失效”的表达方式。
- **拒绝迁移**：付费阈值、无样本自报战绩、单图命中、把 dealer positioning 当确定方向、固定参数照抄。

## v2.33 新增来源

### HKUDS/Vibe-Trading · AI 量化研究 agent
- **来源**：https://github.com/HKUDS/Vibe-Trading；本次复核 commit `9387c605cae45f5b05136c31bf1e18d60c63ee22`（2026-07-05）；License MIT。
- **蒸馏内容**：严格因子验证门（同宇宙随机对照零假设、Harvey-Liu-Zhu 多重检验校正、OOS train/test 分割、`confirmed_alive/train_only/reversed_strict/noise` 四态分类税则）、持久化假设注册表数据契约（`hypothesis_id/status/falsifiers/evidence_refs` 生命周期）、反事实归因分解词汇表（missed_signals/noise_trades/early_exit/late_exit/overtrading）与行为偏差诊断词汇（处置效应/追涨/锚定/过度交易）、回测失败诊断分类法、数据源防封序原则（永不封 IP 的公开源优先）。
- **映射文件**：`factor-validation-strict-gate.md`、`hypothesis-lifecycle.md`、`scripts/hypothesis_registry.py`、`paper-portfolio-analysis-playbook.md`（反事实归因/行为偏差两节）、`decision-compiler.md`、`open-source-quant-research-patterns.md`、`runtime-fallbacks.md`。
- **拒绝迁移**：29 个 preset YAML 的 swarm 多智能体编排（已由 `counter-consensus-framework.md` + Conflict Ledger + 大师圆桌覆盖，引入会制造第二套裁决路径）；456 因子 zoo 代码与 `pip install vibe-trading-ai`（不引运行时依赖，qlib158 衍生代码还有 Apache-2.0 NOTICE 义务）；`live/mandate/order_guard`、trading connectors、channels（涉及真实下单/消息通道，与 `no_order_execution` 冲突）；goal policy 执行拒绝正则、`scheduled_research`（与既有纪律重复）；`memory/persistent`、可编辑工作流（与 `adaptive-self-optimization.md` 重复）。System A 的反事实影子账本实现归 Codex（alpha 方案 Phase 2 A3/A4/A6），本 skill 只定义词汇表与数据契约。

## v2.30 新增来源

### Balder（@Balder13946731）Substack + X 近三日长帖
- **来源**：Substack《The Memory Trade on Nvidia Time》（2026-07-02，公开全文，已 WebFetch 原文）+ X 2026-07-02 SPX read / 2026-07-01 云分化 / 2026-06-29 M7 相对 IV 帖（贡献者提供的 `x_articles_last_20_days_20260702.md` 汇总）
- **蒸馏内容**：三时钟/三峰分离/二阶导数/七路标 checklist、类比先验双向断裂清单；VRP 门（implied vs realized c2c + Garman-Klass、四象限）、财报季相对 IV 横截面 positioning clue；locked_buyer 云厂 vs 现货算力商分化
- **映射文件**：`cycle-position-three-clocks.md`、`options-gamma-structure.md`（VRP 节）、`second-order-supply-shock-mapping.md`（暴露分层交叉）、`x-frontline-intelligence.md`（kol_model_signal）
- **拒绝迁移**：MU/NVDA 2026-07 数值与 analog 减点区间（只作带日期实例，禁止照抄）；Balder Fable 系统自报点位命中（记 `kol_model_signal`，幸存者偏差不可排除）；6/30 第一性原理帖（已被 v2.29 Participant Flow 覆盖，不重复）

### Frank（@Franktradinglog）X 近 20 天长帖
- **来源**：2026-07-01 Meta 算力长帖 / NeoCloud 分层帖 / MU 缺口纪律帖、2026-07-02 执行帖、2026-06-17 Fed 反应函数帖、2026-06-21 月末被动流帖、2026-06-24 回撤自检帖、2026-06-13 注意力时代帖（同上汇总文件）
- **蒸馏内容**：叙事-事实一致性检验、主题内暴露分层（theme_beta_proxy/take-or-pay/spot/locked_buyer）、资产代际分化、认知与执行分离、leader_gap_integrity；Fed reaction_function 两行读数、known_flow_calendar；回撤自检永久红线
- **映射文件**：`second-order-supply-shock-mapping.md`、`macro-dashboard-four-pillar.md`（§8）、`trading-laws.md`（永久红线）、`cycle-position-three-clocks.md`（注意力时代权重说明）
- **拒绝迁移**：「叙事>内在价值」不改变 falsifier 纪律（只吸收为短线 sentiment_clock 权重上升）；具体票名单按当期事实重画

### Citrini（@citrini）X 近 20 天长帖
- **来源**：2026-07-01 DRAM/Jevons 帖、2026-06-22 Getty/Shutterstock 复盘帖（同上汇总文件）
- **蒸馏内容**：Jevons 效率反噬检查（做多涨价受益方 = 做空被逼出来的创新，thesis_half_life 必答）；论点-交易转化门（eventually right ≠ tradeable，validity/path/timing/carry 分离）
- **映射文件**：`cycle-position-three-clocks.md`
- **拒绝迁移**：具体对冲腿标的（按当期重找）

## v2.29 新增来源

### Participant Flow & Motivation Mapping（第一性原理层）
- **来源**：个人交易哲学（可替换为自有方法论） + 市场微观结构理论（Kyle 1985、Hasbrouck 1991、Avellaneda-Stoikov 2008）+ Auction Market Theory（Steidlmayer 1985）+ 全网订单流与参与者流实践
- **核心洞见**：价格没有公式，只有买卖双方的成交。每只股票由一群结构相对固定的参与者构成；他们的动机随信息输入此消彼长。先画参与者图谱再走任何方法论。
- **映射文件**：`participant-flow-motivation.md`
- **约束**：参与者图谱只能基于可验证数据（13F/CCASS/short interest/cap flow），不可虚构。参与者流是第一性原理层（Step 0），不单独决定动作等级。

## IMA 个人知识库 「个人交易法」

创建者 Maintainer，75 条内容。
**本地原文路径：`${HOME}/Documents/trading-research-notes/source-archive`**（53 个文件，含全部 Agent 提示词 md + txt 正文；2026-06-05 从已删除的 `Downloads/trading` 归档至此）。
> 注：IMA OpenAPI 只能枚举结构，**无法读取正文**。所以正文研究只能走本地 `收集/` 目录。

| 资料类别 | 映射模块 |
|---|---|
| 华源证券投资方法论、华源叙事框架 Agent | 华源叙事 / 财报验证（`huayuan` 权重） |
| 美股 Serenity 大神投研 Agent | Serenity 供应链瓶颈（`serenity-method.md`） |
| 股票大作手回忆录（利弗莫尔） | 趋势与关键点（`livermore` 权重） |
| 量价分析：威科夫的盘口解读方法 | 量价结构（`wyckoff` 权重） |
| 28 个游资心法、退学炒股、炒股养家、小群哥、Asking | A 股情绪周期、主线、仓位（`youzi_emotion` 权重） |
| 因子投资方案与实践 | 因子 / 组合排序（`factor` 权重） |
| 《历史会押韵吗》叙事估值与杠铃策略 | 叙事估值 + 杠铃（`serenity-method.md` 交叉） |
| 投资是泊松过程 | 事件强度 λ、证据临界密度（`poisson-method.md`） |

## v2.5 新增来源

### serenity-skill 公开仓库
- **来源**：https://github.com/muxuuu/serenity-skill（MIT 许可证）
- **蒸馏内容**：9 步深度研究流程、瓶颈评分卡（8因素+8惩罚）、证据三级分级+7大红旗、研究伙伴协议、论文模板
- **映射文件**：`serenity-method.md`（升级）、`bottleneck-scorecard.md`、`evidence-ladder.md`
- **脚本**：`scripts/serenity_scorecard.py`

### HH 洪总 · 逆共识交易
- **来源**：`~/Downloads/交易/逆共识交易/HH深圳春季分享会.pdf`（2025.3）+ 三个月前分享会内容
- **蒸馏内容**：大周期嵌套、边际定价、模式转变识别、逆共识四步法、关键宏观指标
- **映射文件**：`counter-consensus-framework.md`

### 刘备（星球干货系列）
- **来源**：`~/Downloads/交易/新生代打板/新生代作手/Y-013 刘备和朋友们/`（干货 + 答疑 + 日常 + 可转债）
- **蒸馏内容**：预期三分法（算/问/蒙）、逻辑>估值>业绩排序、第二逻辑识别、古典价值 vs DCF、四层流动性、全天候作战
- **映射文件**：`expected-returns-framework.md`、`liquidity-valuation-duality.md`

### 新生代作手（A股短线体系）
- **来源**：`~/Downloads/交易/新生代打板/新生代作手/新生代作手/206.新生代/课程/`（14 节视频课）+ 杰尼系统课
- **蒸馏内容**：右侧买点体系、仓位管理金字塔、止盈规则、层次思维（主线→支线→补涨）、筹码分布分析、复盘体系
- **映射文件**：`a-share-short-term-layer.md`

## v2.10 新增来源

### Hermes 已登录 Grok / xAI OAuth
- **来源**：本机 Hermes `xai-oauth` 实测可用；`hermes chat -Q --provider xai-oauth -m grok-4.3`。
- **蒸馏内容**：Grok 全网/X 只作实时发现层，不作事实锚点；必须回抓原始 URL、标时间戳、按来源重新定级。
- **映射文件**：`grok-web-research-layer.md`、`x-frontline-intelligence.md`、`source-reliability-policy.md`、`data-contracts.md`。

### GitHub 高星开源交易研究项目
- **来源**：GitHub API / README 抽样：OpenBB、Freqtrade、Qlib、RD-Agent、Lean、vectorbt、FinRL/FinRL-X、FinGPT、backtrader、PyPortfolioOpt、pyfolio、backtesting.py。
- **蒸馏内容**：数据源注册与 source health、research/backtest/live 语义分离、walk-forward/no-lookahead、成本/滑点/容量、risk budget、tearsheet、benchmark/eval 驱动自优化。
- **映射文件**：`open-source-quant-research-patterns.md`、`decision-compiler.md`、`data-contracts.md`、`scenario-regression-tests.md`。
- **拒绝迁移**：自动交易机器人、broker write API、具体策略参数、大型依赖、GPL 代码片段、仅因 star 增长产生的升级。

### VeighNa/vn.py · 多接口事件驱动量化交易平台
- **来源**：https://github.com/vnpy/vnpy；本次复核 commit `1b78494979deb4c4996f6b864f234d9839f2f239`（2026-06-29）；License MIT。辅助复核 `vnpy_paperaccount` commit `fcfe2b5`、`vnpy_ib` commit `6ec7115`、`vnpy_xtp` commit `e5a386e`、`vnpy_tora` commit `c0ae44a`。
- **蒸馏内容**：`BaseGateway` 统一接口、`Exchange`/`vt_symbol` 合约标准化、MainEngine/App 插件边界、网关能力声明、PaperAccount 本地撮合仿真与 broker paper account 的边界区分；LongBridge 可作为 vn.py-style gateway/datafeed 的上游 API，但 adapter 先只读/格式转换，执行权仍归 LongBridge System A 安全门。
- **映射文件**：`open-source-quant-research-patterns.md`。
- **拒绝迁移**：不引入 GUI/PySide/C++ gateway/券商 SDK；不新增 Hermes 下单路径；不把本地撮合仿真当 LongBridge Demo/Paper 账户证明；不替代 LongBridge 主数据源或 `paper_account_gate`。

### 666ghj/MiroFish · 群体智能情景沙盘
- **来源**：https://github.com/666ghj/MiroFish；本次复核 commit `96096ea0ff42b1a30cbc41a1560b8c91090f9968`（2026-05-25）；License AGPL-3.0。
- **蒸馏内容**：ontology-first 实体/关系建模、知识图谱视角、群体模拟作为情景先验、simulation_trace/action log 可审计输出、分章节报告必须基于检索观察的纪律。
- **映射文件**：`mirofish-swarm-simulation-patterns.md`、`open-source-quant-research-patterns.md`、`decision-compiler.md`、`data-contracts.md`、`scenario-regression-tests.md`。
- **拒绝迁移**：不复制 AGPL-3.0 代码/Prompt；不引入 Zep Cloud、OASIS/camel、Flask、前端、运行器、IPC、后台线程或 cron；不把模拟 Agent 采访当真实证据；不让 modeled_scenario 提高 action level、position cap 或 position_multiplier。

### 每日自检 / 自进化 / 自优化机制
- **来源**：本 skill 的 v2.10 维护协议 + `skill-quality-gate` 外部项目→skill 升级模式。
- **蒸馏内容**：Materiality Gate、Conflict-Fusion Review、Validation Gate；无必要时输出 `no_necessary_upgrade`。
- **映射文件**：`adaptive-self-optimization.md`、`scripts/self_optimization_check.py`

## v2.14 新增来源

### Polymarket / Prediction-market 开源数据项目
- **来源**：Hermes Grok/xAI 搜索 + GitHub/Web 原始仓库复核（2026-06-13）。重点复核：
  - https://github.com/Jon-Becker/prediction-market-analysis
  - https://github.com/SII-WANGZJ/Polymarket_data
  - https://github.com/warproxxx/poly_data
  - https://github.com/Polymarket/real-time-data-client
  - https://github.com/nevuamarkets/poly-websockets
  - https://github.com/Polymarket/py-sdk
  - https://github.com/Polymarket/polymarket-subgraph
  - https://github.com/PaulieB14/polymarket-subgraph-analytics
  - https://github.com/pmxt-dev/pmxt
  - https://github.com/NYTEMODEONLY/polyterm
- **蒸馏内容**：预测市场只读事件概率先验、统一 YES 视角、流动性/价差/深度质量门、resolution-risk 检查、WebSocket/subgraph/历史数据集作为可选增强而非默认依赖。
- **映射文件**：`polymarket-signal-layer.md`、`data-contracts.md`、`decision-compiler.md`、`scripts/polymarket_signal.py`。
- **拒绝迁移**：钱包/私钥/下单/撤单/做市/copy trading/hosted custody、默认下载 100GB+ 数据集、直接迁移 GPL 代码、把 Polymarket 概率替代 LongBridge/公告/财报/Decision Compiler。

## v2.17 新增来源

### Cboe / SEC · Multi-listed equity options extended trading hours
- **来源**：SEC approval order / Federal Register 2026-10951（SR-CBOE-2025-079，2026-06-02）：https://www.federalregister.gov/documents/2026/06/02/2026-10951/self-regulatory-organizations-cboe-exchange-inc-order-approving-a-proposed-rule-change-as-modified；Cboe rule filings page：https://www.cboe.com/us/options/regulation/rule_filings/
- **蒸馏内容**：部分美股单名期权将存在 GTH 7:30–9:25 ET 与 Curb 16:00–16:15 ET；期权/Gamma 报告必须标注 option session、数据时间戳、OPRA/OCC/实施清单可用性，避免把延长时段稀薄成交当作 RTH 墙位确认。
- **映射文件**：`options-gamma-structure.md`。
- **拒绝迁移**：不改 Decision Compiler、不新增交易执行能力、不把延长时段信号单独提高仓位；只作为 session/freshness/data-gap 约束。

## v2.18 新增来源

### 上交所 · 上海证券交易所交易规则（2026年修订）
- **来源**：上交所规则通知（上证发〔2026〕41号，2026-04-24，2026-07-06 施行）：https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml；上交所发布说明：https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260424_10816474.shtml
- **蒸馏内容**：A 股短线/执行判断加入交易规则 freshness gate；沪市盘后固定价格交易扩至全部 A 股和 ETF、基金收盘阶段改收盘集合竞价、沪市主板风险警示股票涨跌幅限制由 5% 调至 10% 时，只更新时段/撮合/涨跌停/滑点假设，不提高动作等级或仓位上限。
- **映射文件**：`a-share-short-term-layer.md`、`data-source-playbook.md`。
- **拒绝迁移**：不把沪市规则直接套用到深市/北交所；不新增执行能力、不改 Decision Compiler、不触碰真实/模拟下单。

## v2.22 新增来源

### 公开 X 截图 / Balder AI Capex 分化讨论
- **来源**：用户提供的公开 X 截图（Balder @Balder13946731，2026-06-18 会话中解读），并用 LongBridge 行情横截面做一次方向性验证。
- **蒸馏内容**：AI/半导体不能作为单一交易处理；必须拆 `capex_payer_future_monetizer`（花钱方/远期变现）与 `capex_receiver_current_cashflow`（收钱方/当前现金流），再结合折现率、好/坏鸽派、仓位结构解释 hyperscalers vs 供应链分化。
- **映射文件**：`capex-cashflow-duration-rotation.md`、`liquidity-valuation-duality.md`、`macro-dashboard-four-pillar.md`、`endogenous-market-structure-playbook.md`、`decision-compiler.md`。
- **拒绝迁移**：不把截图/KOL 观点作为事实锚点；不新增动作等级；不因为“当前收钱”单独提高仓位；不把具体 ticker 分类永久化，每次必须用最新财报/订单/现金流复核。

## v2.23 新增来源

### simonlin1212/a-stock-data · A 股全栈公开源工具包
- **来源**：https://github.com/simonlin1212/a-stock-data；本次复核 v3.2.2 commit `9379ab90d0219312b5f4845cd8c97502f40b0806`（2026-06-03）；License Apache-2.0。
- **蒸馏内容**：A 股公开源优先级（通达信/腾讯优先，东财只用于独有数据）、东财统一限流防封、腾讯财经字段校准（43=振幅，46=PB）、东财 slist 概念板块替代失效百度 PAE、巨潮公告 orgId 动态映射、失效源/风控缺口标注。
- **映射文件**：`a-stock-data-source-layer.md`、`data-source-playbook.md`、`market-source-matrix.md`、`data-contracts.md`。
- **脚本**：`scripts/a_stock_data_bridge.py`。
- **拒绝迁移**：不注册第二个 A 股 skill；不复制 2000+ 行上下文；不引入真实/模拟下单；不默认启用 iwencai key、PDF 批量下载或东财并发抓取；不把桥接层数据直接提高动作等级。

## v2.25 新增来源

### 景气度投资分析框架提示词 v1.0 · 中观景气度投资研究员方法论
- **来源**：贡献者提供的《景气度投资分析框架提示词 v1.0》文档（2026-06-25 会话摄入）；案例跨市场（纳指 1999、立讯 2018-2019、光模块 2023-2025、美伊冲突 2026）。
- **蒸馏内容**：信息有效性过滤器（只看影响未来两年盈利预测的信息）、周期长度判断=胜负手（≥2 年才重仓，判不出长度→全篇降级）、戴维斯双击双段收益（只有板块性 beat 触发估值第二段）、成长/消费/周期（纯周期 vs 周期成长接力结构）型归类、空间→壁垒→确定性→估值四维度双门槛、业绩超预期路径推演、左右侧买点（行情级别>左右侧）、卖出风控三件套、回撤归因三分法、方法适用性自检。
- **映射文件**：`prosperity-davis-double-framework.md`、`decision-compiler.md`、`method-rotation-matrix.md`、`serenity-method.md`、`expected-returns-framework.md`。
- **拒绝迁移**：A 股数值锚（科技 10-40 倍/白酒批价/制造业 40 倍卖出纪律）禁止直接套用港股美股；「不做 DCF/宏观降权」只在本方法内部生效，不否定 `liquidity-valuation-duality`、不覆盖 `risk_regime`；不新增动作等级；不给具体仓位比例；不真实下单。

### 最强公募选股 skill（fund-stock-scanner 五层框架）
- **来源**：贡献者提供的《最强公募选股skill.md》文档（2026-06-25 会话摄入），产业中观→产业链纵深→供需瓶颈→量价齐升→远期价值定价五层 + 极致集中。
- **蒸馏内容**：只取两点真增量——「远期价值定价透支判断」（当前 PE 对应未来 1-2 年业绩是否透支）与「公募极致集中输出审美」（核心 2-4 + 观察 2-4、≤8 只、敢于排序、新题材优先、纠错信号必备）。
- **映射文件**：`prosperity-davis-double-framework.md`（输出纪律段）。
- **拒绝迁移**：五层框架的产业链/瓶颈四层已被 `serenity-method.md` 完整覆盖，不重复；公募执行机制（`search_finance_reports`/`query_finance_data`/gather-make-report 并行/`.alphaclaw` 路径）属另一套系统，**不迁移**——本 skill 取数仍走 LongBridge/Grok/Decision Compiler 主链。

## v2.44 新增来源

### 美债收益率曲线 / DXY / 加密现价 公开免 key 数据源（rates-fx-crypto-overlay）
- **来源**：Treasury.gov 官方 CSV `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/<year>/all?type=daily_treasury_yield_curve`；CoinGecko `simple/price` API；Yahoo Finance chart API `DX-Y.NYB`；FRED `DTWEXBGS` CSV。均于 2026-07-19 实测 curl 确认可用（无需 key）。stooq DXY CSV 实测返回 Anubis 反爬 JS 挑战，判定不可用，未采纳。
- **蒸馏内容**：利率/FX 只作宏观 overlay（不对国债/汇率本身做买卖建议）；DXY 首选 Yahoo Finance chart API，FRED `DTWEXBGS` 作口径不同的交叉核对（广义贸易加权 vs ICE 六币种，不能直接比较数值）；中美利率读数已有 AkShare `bond_zh_us_rate` 更优先（单次调用双曲线+利差），Treasury.gov 只用于短端极短久期补查。
- **映射文件**：`rates-fx-crypto-overlay.md`、`data-source-playbook.md`、`macro-dashboard-four-pillar.md`。
- **拒绝迁移**：不对国债/汇率做独立标的买卖建议；不新建加密专属清算等级（复用既有 `forced_liquidation`/`liquidity_squeeze`）；stooq 路径不写入常规取数流程，标记为 DataGap。

### AkShare A 股期权/可转债/ETF 新函数（a-share-derivatives-ipo、etf-selection-rotation）
- **来源**：AkShare（既有桥接库）`option_finance_board`、`option_risk_indicator_sse`、`option_daily_stats_sse`、`bond_zh_cov`、`fund_etf_spot_em`、`fund_etf_fund_info_em`。均于 2026-07-19 实测（`option_current_em` 实测报 `TypeError`/`ConnectionError`，未采纳，改用前述三个函数替代）。
- **蒸馏内容**：A 股期权只能拿到逐合约行情/官方希腊字母/按标的聚合 P/C 比与总 OI，无法重建逐档 dealer gamma 敞口——明确禁止套用美股 Put Wall/Call Wall/Gamma Flip 表述；可转债双低框架直接用 `债现价`+`转股溢价率`两列排序，不新建评分公式；ETF 场内实时报价含溢价折价率/资金流，但费率/跟踪误差/AUM/持仓集中度仍是数据缺口，永远走 Grok/web_search 补。
- **映射文件**：`a-share-derivatives-ipo.md`、`etf-selection-rotation.md`、`data-source-playbook.md`。
- **脚本**：复用既有 AkShare 桥接（`a_stock_data_bridge.py` 同款绕代理模式，无需新建脚本）。
- **拒绝迁移**：不新建 Decision Compiler 模块（复用 `endogenous_structure`/`fundamentals`，用 `sub_framework` 区分）；A 股期权信号最高 L0/L1 且永不 hard veto，不得赋予美股 `gamma` 模块的 Put Wall hard veto 权限；强赎条款判断只做接近度提示，逐券条款以公司公告为准。

## v2.45 新增来源

### 面基播客 E159 · 恽雷@南方基金《港股的特殊之处与生存之道》
- **来源**：面基播客 E159（嘉宾恽雷@南方基金，2026-05-25）。证据材料四项：本地文字稿（原路径 `${HOME}/Documents/trading-research-notes/examples/hk-offshore-notes.md` 已于 2026-07-21 失效——文件被移入废纸篓，同日抢救归档至 `${HOME}/Documents/trading-research-notes/source-archive`；SHA-256 `331dd05384e60d2800eb184e8198d3052972ce8e7e8b05b5fa0c1cac6567ef9b`，32136 字节；`partial`，仅前~30分钟）、贡献者提供的全集精简摘要（2026-07-20 会话摄入）、小宇宙 https://www.xiaoyuzhoufm.com/episode/6a13b560e59ebca9363afd1d 、老钱日日谈公众号转载的恽雷一季度基金报告节选《港股市场的特殊之处和生存之道》 https://mp.weixin.qq.com/s/QgKtQdHvkM5ZpJYGg3mLjQ 。级别 `framework_inference`（非逐字引文）。
- **蒸馏内容**：港股离岸身份第一性检查（可选性/增强互补性/平准力量缺失→低估值不构成买入理由，必须有收敛契机）、资金阵营三分（外资长线/保险南下/对冲基金）、流动性横截面分层（高股息端/高景气端/中间地带）、收益资产 vs 波动率资产分类（恒科 ETF 行为学）、因子排序与动量崩溃纪律（动量>价值>股息率>低波动，回撤 10-20% 强制复核）、生存三件套建仓顺序（打底→入池→右侧进→再平衡）、388.HK 择时锚+海外流动性二次验证、IPO 抽水与次新蜜月期供给检查、贝塔各向同性/异性 ETF-主动路由、充分定价识别与幸存者偏差提醒。
- **映射文件**：`hk-offshore-market-playbook.md`（唯一新文件，tighten-only overlay）；交叉引用（不改写）`participant-flow-motivation.md`、`liquidity-valuation-duality.md`、`etf-selection-rotation.md`、`endogenous-market-structure-playbook.md`、`dividend-quality-framework.md`、`trading-laws.md`、`method-rotation-matrix.md`、`decision-compiler.md`。
- **拒绝迁移**：控回撤三件套不进 `trading-laws.md`（避免与既有单笔止损/破 5 日线/Frank 回撤自检重复）；不改 `liquidity-valuation-duality.md`（不同作者的蒸馏文件不混写，只交叉链接）；不新增数据脚本（388.HK 择时锚复用现有 `scripts/longbridge_query.py` 手动取数，YAGNI，可选 Phase 2 默认不做）；幸存者偏差/回测后视镜论述只并入 playbook 一句方法论提醒，不建独立框架。数值锚（成交额 1/10、股息率 11-12%、恒科 ±40-50%、2021 分水岭）按 v2.30 先例标注：机制可迁移，实例数值以 2026-05 播客陈述为准，使用时须按当期事实重填。

## 使用原则

跨市场只迁移底层逻辑，不迁移制度细节：
- A 股涨跌停/连板/龙虎榜不得套到美股/港股
- 美股期权/Gamma/SEC/guidance 不得套到 A 股短线
- Serenity 产业链瓶颈框架跨市场通用，但具体信号来源按市场切换
- 杠铃与泊松互为印证：见 `poisson-method.md` §7
- 古典价值/DCF 切换取决于流动性环境，不是市场偏好
- Grok/开源项目只提高发现与验证质量，不替代主数据源、不自动提高动作等级
- Polymarket/预测市场只提高事件概率先验和验证优先级；没有原始市场 URL、时间戳、流动性/价差/规则检查时只能作线索；即使完整也不能单独提高仓位
- MiroFish/群体模拟只提供 modeled_scenario 与 evidence_collection_plan；模拟观察必须先回到真实来源验证，不能作为 verified_fact、trading_confirmation 或 position_sizing 输入
- 每日自优化只在 materiality gate 通过且验证通过时更新；非必要不升级
- Capex 收钱方/花钱方只是一种现金流久期拆分；必须重跑行情、利率、财报/订单证据，不能把截图中的具体 ticker 分类当永久事实
- a-stock-data 只作为 A 股公开源直连补强；腾讯/东财/巨潮输出必须进入 Evidence Ledger 并交叉验证，不能绕过 LongBridge/Mira/Decision Compiler。
- 景气度·戴维斯双击的方法论（周期长度判断、双段收益、双门槛、回撤归因）跨 A/H/US 通用，但 A 股数值锚（10-40 倍/批价/40 倍/公募集中审美）属制度细节，禁止套用港股美股；景气诊断复用 Serenity，不另起炉灶；结论编译进 fundamentals，不新增动作等级、不单独提高仓位。
- 美股期权 Gamma 框架（逐档 dealer 敞口重建）不得套到 A 股期权——A 股只能算方向性情绪信号（P/C 比、总 OI），禁止声称 Gamma Flip/Put Wall/Call Wall 价位。
- 利率/FX 读数（收益率曲线、利差、DXY）只作宏观 overlay 输入 `risk_regime`/`macro`，不对国债/汇率本身做独立标的买卖建议；加密杠杆清算复用既有 `forced_liquidation`/`liquidity_squeeze`，不新建加密专属清算等级。
