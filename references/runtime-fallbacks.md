# Runtime Fallbacks

## Purpose

When LongBridge-backed research needs portfolio-aware answers but runtime tools are missing, schema responses drift, or option-chain quotes are partial/delayed, keep the answer auditable, conservative, and never mixed with executable-fill claims. This reference is the single consolidated fallback doc — it replaces the previously separate `longbridge-query-fallback.md`, `cboe-delayed-chain-fallback.md`, `longbridge-runtime-fallbacks/SKILL.md`, and `options-market-data-resilience/SKILL.md`.

## 1. 降级阶梯（general fallback ladder）

1. Check whether the LongBridge MCP tools exist in the current Hermes session.
2. If MCP is unavailable, use CLI or SDK and label `mcp_unavailable_runtime`.
3. If portfolio/account calls fail, keep public market research separate from account-level advice.
4. If schema fields changed, preserve raw response snippets in private notes/logs and map only fields that are verified.

## 2. LongBridge 通道序（MCP → CLI → SDK → public）

1. **MCP**: use current Hermes MCP tools when available in the session.
2. **CLI**: use `longbridge` CLI for quotes, portfolio, positions, and session status.
3. **SDK/script**: use `scripts/longbridge_query.py` for quote/K-line/depth-style fallbacks.
4. **Public/web source**: only for non-account public facts; label source and timestamp.

Rules:
- Do not skip auth failure silently. Trigger the login/renewal flow or mark `auth_gap`.
- `scripts/longbridge_query.py` may retry **only** read-only `quote`/`kline` once when the CLI returns the observed `401003 token expired`: the retry removes inherited `LONGBRIDGE_*`/`LONGPORT_*` credentials so an already-valid CLI OAuth session can take over. It must not start login, retry unrelated failures, call account/order commands, or hide a second failure; if OAuth also fails, stop and record `auth_gap`.
- Do not mix account data from one channel with market data from another without labels.
- Do not invent missing fields. Missing broker/account context means no exact quantity recommendation.
- If option chains fail but underlying quote works, preserve the partial data and route option structure via §3 below.

Verification checklist:
- Run the selected source command and capture exit code/output summary.
- Confirm `longbridge_tier=mcp|cli|sdk|public` is labeled.
- Confirm every price/position/portfolio figure has a timestamp or `timestamp_gap`.
- Confirm failures are written as `DataGap` and reflected in Decision Compiler caps.

## 2a. 美股公司证据 fallback（v2.53）

自动证据管线固定为：

1. `scripts/us_company_evidence.py` 先调用 LongBridge CLI 的只读 allowlist：`financial-report`、`valuation`、`filing`、`insider-trades`、`shareholder`；显式提供机构 CIK 时才调用 `investors`，因为它表示该机构的 13F portfolio，不是目标公司的 holder lookup。
2. 缺财报或 filing 元数据时，才进入免费 SEC 链：若没有 issuer CIK，先用官方 `company_tickers.json` 做 exact ticker→CIK；随后分别请求 `submissions` 与 `companyfacts`，一个端点失败不得拖死另一个。必须先把 `SEC_EDGAR_IDENTITY` 配成真实姓名与真实联系邮箱；缺失时在 ticker map、网络和磁盘缓存之前返回 `identity_missing`。适配器只校验格式，无法证明邮箱归属，因此操作者不得照抄文档或测试里的占位地址。
3. LongBridge 核心财报/估值 payload 均为空且 SEC 未补齐时，`fundamental_snapshot.py` 才继续 AkShare US 兜底。一个空壳成功响应不能阻断下一层。
4. 任一层失败都保留已成功维度并登记精确 DataGap；403/404/429/5xx、schema drift、空 payload、超时不能改写为“无 filing/无内部人交易/无股东”。

SEC 默认 1 req/s、单响应上限 8 MiB、最多一次 5xx 重试；磁盘缓存 TTL 6 小时，目录/文件权限 700/600。LongBridge 与 SEC 命中同一 accession 只合并 retrieval paths，不增加独立来源数。EdgarTools、FMP、Financial Datasets、Alpha Vantage、Finnhub、Twelve Data 均不在默认链；未来只有明确覆盖缺口并重新过质量门时才可增加。

## 3. 期权链 fallback（CBOE delayed chain + source-quality classification）

**Boundary**: CBOE delayed chains are **reference-only** unless the broker confirms executable bid/ask. They can support structure selection, not fill claims. Never say "options data unavailable" if a safe delayed public fallback can still answer structure selection — but never present fallback prices as executable fills.

Classify source quality:
- Broker live quote: usable for actionable levels.
- Broker delayed quote: usable with timestamp and caution.
- Public delayed chain (CBOE): usable for research structure only.
- No bid/ask/Greeks: watchlist only, no precise contract recommendation.

Keep the primary broker source for whatever still works even when the option quote path fails: underlying quote, expiry list, option volume/call-put skew, finance calendar/catalysts, company/valuation context.

Suggested CBOE chain fields: underlying symbol and quote timestamp; expiry; strike; call/put; bid/ask/last if available; open interest and volume; implied volatility and Greeks when present; spread width and liquidity notes.

Contract filter defaults:
- Directional long: prefer 30–45 DTE and delta around 0.35–0.55 when available.
- Event/high-IV: prefer defined-risk vertical spreads over naked premium buys (earnings/high-IV event: same rule).
- Lottery/speculation: cap premium risk explicitly and label low confidence.
- Avoid stale, wide-spread, low-OI, or missing-Greeks contracts for precise recommendations.

Decision impact:
- Full broker quote + liquid chain: normal Decision Compiler path.
- Public delayed fallback: allow structure recommendation, but no exact fill claim.
- Missing bid/ask/Greeks: downgrade to directional thesis + watchlist; avoid precise strike/price claims.

Verification checklist:
- Confirm the primary broker/API option path status and timestamp before falling back.
- Label the data as delayed/reference-only; compare expiry availability with broker/source when possible.
- Confirm bid/ask, open interest, volume, IV/Greeks, and spread width are present before naming a specific contract.
- Reject exact fill language unless broker live quote confirms bid/ask.
- Ensure fallback data does not raise action level or position size; if fields are missing, verify the recommendation is downgraded to thesis/watchlist.
- If a script or notebook was used to filter contracts, rerun it and preserve the command/output summary in the report or notes.

Recommended language:
> LongBridge option quote access was unavailable, so I used CBOE delayed chain data for structure selection. Treat prices as reference, not live executable quotes.

Avoid: "This is the best fill." / "Options data is unavailable."

Pitfalls:
- Do not turn a permission/setup failure into a permanent claim that the tool is broken.
- Do not mix delayed public chain prices with broker live quotes without labeling source boundaries.
- Do not recommend short-dated naked calls into high IV unless the user explicitly wants lottery risk and the premium loss is capped.
- Do not let fallback data raise action level or position size; it can only preserve or downgrade confidence.

## 4. 账户门（account gate）

Portfolio/position sizing requires current account/position data. Missing account context means no exact buy/sell quantity. Unknown account channel must be treated as non-paper/real-risk for execution safety.

Portfolio fields to compute from `portfolio --format json`:
- `net_asset = overview.total_asset`
- `gross_exposure = overview.market_cap`
- `gross_net_ratio = gross_exposure / net_asset`
- `cash_net_pct = overview.total_cash / net_asset`
- `single_position_weight = holding.market_value_usd / net_asset`

If `gross_net_ratio >= 1.5`, `cash_net_pct <= -0.5`, or any single position is `>= 50%` of net asset, the portfolio gate below comes before ticker alpha.

Use this workflow when producing portfolio-aware "买什么/分析持仓" answers, especially when LongBridge MCP calls partially fail (mover/hotspot-style endpoints), or the account has negative cash, low/negative buy power, financing, or concentrated positions:

1. **Account first**: read balance, positions, fund positions, market status, quotes.
2. **Capacity gate**: compute net assets, cash, gross exposure / net assets, single-position weights, and buy power.
3. **Candidate max-qty check**: for top candidates, call max purchase quantity with realistic current/limit prices. If all return 0 and buy power is <= 0, new buys are L0 hard veto.
4. **Hotspot fallback**: if top-mover feeds fail validation or repeat identical errors, stop retrying and use rank_list / industry_rank / anomaly / quotes.
5. **Decision compiler framing**: separate account-now action from conditional "if cash is freed" buy list.
6. **Write decision memory** when producing an actionable portfolio decision.

Output pattern for "买什么" — start with one direct sentence, e.g. `现在账户内不新增买入；如果释放现金，优先 X，其次 Y。` — then compact tables: portfolio risk dashboard, existing holdings and actions, hotspot read, conditional buy ranking. Do not bury the hard veto under a long stock-picking discussion.

Hard gates:
| Condition | Default action |
|---|---|
| `buy_power <= 0` and candidate max purchase quantity is 0 | New buy = L0 hard veto |
| Gross exposure / net assets >= 1.5 | Do not add high-beta exposure unless hedge |
| Any single position >= 50% net assets | Address concentration before adding same-direction risk |
| Negative cash plus financing pressure | Repair cash / reduce exposure before new buys |
| Hotspot data partial | Label data gap; do not invent movers |

Fallback wording:
- Good: `top_movers_gap: mover feed unavailable/partial; fallback used rank_list + industry_rank + anomaly`.
- Bad: "LongBridge is unavailable" when balance/quotes/rank/anomaly calls still work.

## 5. 输出契约（output contract）

Every fallback answer should include:
- data tier used;
- unavailable source and reason;
- fields that were verified;
- fields withheld because of missing account/schema context;
- Decision Compiler impact.

## 6. 源排序原则（fallback ordering by IP-ban risk, v2.33）

When a fallback chain touches more than one external source (LongBridge, CBOE, or the A-Stock sources in `references/a-stock-data-source-layer.md`), order candidates by ascending IP-ban risk, not by raw data quality alone:

1. Sources that never rate-limit/ban for normal single-symbol research queries go first.
2. Rate-limited or key-required sources go after, and only when the free-tier source above them is unavailable or has a confirmed coverage gap.
3. If 用户 or the task explicitly names a specific local/offline source, use it as specified — do not silently fall back to a network source instead, even if the network source would answer faster. Report the explicit-source failure instead of substituting silently.

This is an ordering principle only; it does not change which sources are wired into any bridge script, and it does not touch `references/a-stock-data-source-layer.md`'s existing tencent/eastmoney/juchao/AkShare chain or `scripts/a_stock_data_bridge.py`.

Candidate-only registration (YAGNI — no bridge implementation): `mootdx`（通达信 TCP 协议）and `baostock` are noted here as known alternative A-share sources for future reference. Neither is wired into any script today, because the existing tencent/eastmoney/juchao/AkShare chain shows no coverage gap that would justify adding them. Do not implement a bridge for either until a real coverage gap is observed and recorded.

## 7. 资讯检索 fallback（v2.37）

主搜索/新闻工具失败或结果覆盖不足时：

1. `web_search` 找候选，`web_extract` 回抓原文。
2. LongBridge `news/news_search/filings` 与 A 股 WindClaw/官方源补金融语料。
3. 仍有缺口时，用 `scripts/multi_source_search.py` 按市场/语言/profile 选择 4–5 个无 key provider，输出 source health、候选、去重与 DataGap。
4. 搜索候选回抓原文后，才按原始来源重新定级；只有标题/摘要时最高 C、monitoring-only。
5. 403/429 不读取或刷新 cookie，不通过代理池规避；立即标 `blocked` 并切源。查询含秘密、内部主机名或未公开材料时 fail-closed，不出网。

热点/传闻额外走 `attention-rumor-triage.md`：attention 与 credibility 分轴；多引擎命中不等于多事实源。分红问题走 `dividend-quality-framework.md`，不使用固定 0–100 safety score 替代现金流与周期检查。

## 8. OKX 状态与对账 fallback（v2.48）

完整契约见 `okx-research-execution-supervision.md`。这里仅定义运行时降级：

1. Public API 失败：保留 last good market value，标 `stale/unavailable`；不得回填 `0`，不得继续发布当前入场分。
2. Private WS 断线：立即标 account/positions/orders stale；重连后先做 REST 全量 baseline，再恢复 fresh。
3. REST 成功但 WS 未恢复：允许只读对账，不恢复持续执行许可。
4. WS 恢复但 REST baseline 失败：仍是 `pending_reconciliation`，新开仓阻断。
5. 订单提交回报不确定：记 `unknown`；禁止按成功或失败重试，先按 client order id 查单/成交。
6. 批量调用必须逐 item 检查 `sCode/sMsg`；transport `code=0` 不能覆盖部分失败。
7. 手工 App 交易造成 drift：交易所状态是外部事实，本地 intent 是审计轨迹；暂停新仓并人工/程序化对账。
8. public-only 模式可继续市场研究，但不得给账户级数量、仓位或 execution permission。

所有降级只收紧，不得因切到 delayed/cached/public source 提高 action level 或 position cap。
