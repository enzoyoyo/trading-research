# Multi-Source Search Layer · 多渠道资讯检索与抓取降级层

## 目标与边界

本层解决三个问题：
1. 每个外部研究命题都让既有索引与 AnySearch 同批发现，减少单一索引偏差。
2. 主搜索渠道失效、限流或覆盖不足时，仍能找到可回抓的原始页面候选。
3. 同一命题由多个搜索索引发现时，保留来源、时间、去重与健康状态。

搜索结果页是**发现层**，不是事实证据。标题、摘要、排名和“多个引擎都搜到”不能直接提高动作等级、仓位上限或来源等级；必须回抓原始 URL，再按 `source-reliability-policy.md` 重新定级。

永久边界：
- `no_order_execution=true`；不创建提醒、不写账户、不改 cron。
- 不读取浏览器 cookie、AUTH_TOKEN、CT0 或任何账户会话；不读 `.env` 里的券商/交易所/社媒/模型凭据。唯一例外是白名单内的搜索专用变量 `VOLC_DOUBAO_SEARCH_API_KEY`，来自独立的 `~/.config/trading-research/search.env`，且不注入 `os.environ`、不出现在任何输出里。
- 不持久化 cookie；遇到 403/429 不绕过、不换 cookie 刷站，记录 source health 后切源。
- 查询会发送给所选第三方搜索服务。自动隐私门覆盖常见密钥、私网地址、本地路径、邮箱、中国手机号、身份证/银行卡数字形态和“未公开/未披露/内部交易计划”等显式标记，但不可能识别所有姓名、上下文型隐私或隐晦内幕；即使自动门通过，操作者仍须确认 query 不含未公开计划、个人身份数据或受限材料。

## 入口

```bash
# 默认只生成路由与查询计划，不出网
python3 scripts/multi_source_search.py "NVDA latest earnings guidance" --profile news --json

# 明确允许把查询发送给计划中的外部搜索服务
python3 scripts/multi_source_search.py "NVDA latest earnings guidance" --profile news --allow-external-search --json

# 覆盖默认 72h 窗口；只接受 1–8760 小时
python3 scripts/multi_source_search.py "NVDA latest earnings guidance" --profile news --freshness-hours 24 --allow-external-search --json

# 中文/A股公告与资讯
python3 scripts/multi_source_search.py "贵州茅台 最新公告 减持" --market A --profile filing --allow-external-search --json

# 传闻发现（结果仍是 rumor_signal/discovery_only）
python3 scripts/multi_source_search.py "NVDA acquisition rumor analyst action" --market US --profile rumor --allow-external-search --json

# 仅使用明确指定的提供方
python3 scripts/multi_source_search.py "AAPL dividend coverage" --profile dividend --engines google_news,bing_web,duckduckgo --allow-external-search --json

# 检查带 key 备用提供方的配置状态（不出网、不打印密钥值）
python3 scripts/multi_source_search.py --check-credentials

# 关闭带 key 备用层；或调低/调高触发阈值
python3 scripts/multi_source_search.py "NVDA guidance" --profile news --no-fallback --allow-external-search --json
python3 scripts/multi_source_search.py "NVDA guidance" --profile news --min-candidates 5 --allow-external-search --json

# 离线契约自检
python3 scripts/multi_source_search.py --self-test
```

## AnySearch 联动增强（必做）

每个需要外部搜索的研究查询，必须同时运行本层选择的既有提供方与 AnySearch；这不是失败后的兜底。查询必须是公开命题。金融、公告、宏观或社媒意图先用 AnySearch 发现子域，再按返回的必填参数执行垂直检索；一般背景查询至少执行一条 AnySearch 通用检索。AnySearch 失败只写入独立 `source_health/data_gaps`，不阻断其余提供方。

```bash
ANYSEARCH="${ANYSEARCH_BIN:-anysearch}"
$ANYSEARCH get_sub_domains --domain finance
$ANYSEARCH search "NVDA latest earnings guidance" --domain finance --sub_domain finance.news --sdp type=stock,symbol=NVDA,cn_code= --max_results 5
```

将 AnySearch 结果标记为 `provider=anysearch`、`discovery_only=true`；记录 query、domain/sub_domain、时间、结果数和错误状态。它与其他引擎的共同命中只表示索引覆盖，不表示独立事实证实；全部候选仍须回抓原文。

## 自动接入 Live Intelligence

`live_intel_run.py` 先做 Hermes Grok health，再执行 Grok 请求；任一失败时默认自动调用本脚本的 `news` profile。敏感查询仍由本脚本阻断且不出网；全部 provider 失败时 `live_intel_run.py` 返回非零并保留 DataGap，不再把“主源失败”包装成成功。需要完全禁止外部搜索时使用 `live_intel_run.py --no-search-fallback`。

## 渠道阶梯

按“能力可靠性 + 隐私 + 被封风险”选择，而不是盲目同时抓 16 个引擎。

| 层 | 渠道 | 用法 |
|---|---|---|
| T1 工具原生 | `web_search` → `web_extract`；LongBridge `news/news_search/filings/top_movers/anomaly/rank_list` | 默认主路径；结构化、可观测 |
| T2 联动索引增强 | AnySearch 通用搜索；finance/social_media 等意图追加垂直搜索 | 每个外部研究命题必跑；只提供 discovery candidate，不替代主源 |
| T3 一线发现 | Hermes Grok/X；A股 WindClaw internet/document/reference | 只作线索，按既有身份/时间/交叉验证门 |
| T4 无 key 搜索补充 | `scripts/multi_source_search.py` 的 RSS/HTML 提供方 | 索引多样性补充，或其他渠道不可用时保持发现能力 |
| T4b 带 key 备用搜索 | `scripts/multi_source_search.py` 的 `doubao`（豆包搜索 Global 版） | 前面各层失败/被封/候选不足时自动兜底；消耗配额，默认不进主层 |
| T5 原文锚定 | SEC/CNINFO/HKEX/交易所/监管/公司 IR/财报原文 | 回抓候选后形成 S/B 级或相应原始证据 |
| 终止 | 所有渠道失败或原文不可得 | 写 DataGap；不得用标题补事实 |

## 提供方注册表

### 国际 / 美股优先
- `google_news`：Google News RSS，新闻发现；不是 Google 网页抓取。
- `bing_news`：Bing News RSS，新闻索引补充。
- `bing_web`：Bing Web RSS，通用网页与站内检索。
- `duckduckgo`：DuckDuckGo HTML，独立发现补充。
- `brave`：Brave HTML，独立索引补充。

### 中文 / A股 / 港股优先
- `google_news_zh`：中文地区 Google News RSS。
- `bing_news`、`bing_web`：中英文双语补源。
- `baidu`：中文网页候选。
- `sogou`：中文网页候选。
- `sogou_wechat`：微信公众号文章候选；链接可能是跳转页，必须再回抓。
- `so360`：中文索引补充。

### 备用带 key 提供方（fallback tier）
- `doubao`：火山引擎「豆包搜索 Global 版」，`POST https://open.feedcoopapi.com/search_api/global_search`，Bearer API Key 鉴权。

默认每次只选 4–5 个与市场/语言/意图相关的**无 key**引擎；并发上限 3。一次搜索每个引擎只发一个请求；网络超时/5xx 最多重试一次，403/429 不重试。带 key 提供方永远不进默认主层，只在 fallback 触发或 `--engines doubao` 显式指定时调用。

## 豆包搜索备用层

### 为什么单独一层
无 key 引擎会被限流（Brave 429）、解析失败（DuckDuckGo/百度 parse_empty）或区域性不可用。豆包提供一个**结构化 JSON、带发布时间、独立于 Google/Bing 索引**的补充来源。但它消耗付费配额（每账号每月 500 次免费，之后按量计费，5 QPS），所以默认不进主层。

### 触发条件
主层跑完后按以下任一条件触发（`fallback.reason` 会写明是哪一条）：

| reason | 判据 |
|---|---|
| `all_primary_providers_failed` | 主层没有任何 `status=ok` |
| `insufficient_primary_candidates` | 聚合后候选数 < `--min-candidates`（默认 3） |
| `majority_primary_providers_blocked` | 半数及以上主层引擎被 403/429 挡住 |
| `primary_coverage_sufficient` | 不触发，豆包不被调用，配额不消耗 |

`--no-fallback` 完全关闭该层；`--min-candidates 99` 可强制触发（用于验证链路）。

### 查询改写规则
豆包 Global 版**不支持多词搜索与 `site:` 等操作符**，且 Query 上限 100 字符。因此该引擎使用 `query_mode=raw`：收到的是用户原始 query，不是主层用的 `expanded_query`；超过 100 字符按文档截断。把 expanded_query 发给它会静默降低召回，这是刻意规避的。

### 凭据边界
- 变量名固定为 `VOLC_DOUBAO_SEARCH_API_KEY`，凭据文件默认 `~/.config/trading-research/search.env`（可用 `TRADING_RESEARCH_SEARCH_ENV` 覆盖）。
- 解析器只认白名单内的搜索变量名，其余键（券商、交易所、社媒、模型凭据）一律丢弃，**不注入 `os.environ`**。这一层永远没有能力读到 OKX/X/Telegram 凭据。
- 文件权限必须是 600。组或其他用户可读时 fail-closed，并在输出里给出 `chmod 600 <path>` 修复指引，不会「先用着」。
- key 只出现在该请求的 Authorization header；不写日志、不进输出、不进 candidate、不进 Decision Memory。
- `--check-credentials` 只报配置状态（`configured` 真假、来源、修复指引），不打印任何密钥值，也不出网。

### 失败语义
`unsupported/auth_missing/rate_limited/error` 绝不能改写成 0 条、无新闻或负面事实。

| 情况 | status | 说明 |
|---|---|---|
| 未配置 key | `skipped`，`error=auth_*` | 写 DataGap，impact 明确为 `backup_provider_unavailable_not_an_absence_of_news` |
| 权限过宽 | `skipped` | 带 `chmod 600` 修复指引 |
| 700901 / 401 / 403 | `blocked`，`config_error=true` | key 无效或无权限，重试无用 |
| 10410 / 10412 | `blocked`，`config_error=true` | 未开通套餐 / 配额耗尽 |
| 700429 / 429 | `blocked` | 5 QPS 限流；不重试、不绕过 |
| 10500 / 10501 | `fail`，`config_error=false` | provider 内部错误，可重试 |
| HTTP 200 但 `Result=null` 或 `ErrorCode≠0` | `fail`/`blocked` | 绝不当成功；HTTP 200 不等于有结果 |

### 时间戳
`DocumentInfo.PublishTime` 支持 epoch 秒/毫秒、`YYYY-MM-DD [HH:MM[:SS]]`，以及实测返回的带时区 ISO-8601（如 `2026-07-31T00:00:00+08:00`，文档未列出）。无法解析的值原样保留，freshness 记为 `unknown` 并写 `published_at_unknown` DataGap，**不伪造时间戳**。

### 证据地位不变
豆包结果与其他引擎完全同级：`discovery_only=true`、`verification_status=unverified`、`readiness_impact=monitoring_only`，`forbidden_use` 含 `verified_fact`/`position_sizing`/`order_execution`。带 key 不等于更可信，不提高 reliability ceiling、不提高动作等级、不放宽 position cap。跨层重复候选按 URL/标题正常合并，`provider_families` 保证 `independent_provider_count` 不被两层重复计数灌水。`HostInfo.Hostname` 是中文站点名（如「抖音百科」）不是域名，只作展示元数据，不用于任何基于 host 的信任判断。

## Query Profile Router

| profile | 查询扩展 | 主要用途 |
|---|---|---|
| `general` | 原查询 + 市场/代码 | 通用背景 |
| `news` | `latest`、catalyst/risk；默认 `freshness_hours=72` | 有时间戳且超过窗口的候选剔除；无时间戳候选保留为 `freshness_state=unknown`，不得宣称属于 72h |
| `filing` | SEC/IR/交易所/巨潮/披露易站点限定 | 公告、监管、财报原文 |
| `rumor` | M&A、insider、analyst action、investigation、financing | 传闻与早期信号发现；不验证真伪 |
| `hot` | movers、unusual volume、most active、trending | 注意力/异动候选；不等于方向 alpha |
| `dividend` | dividend history、payout、FCF coverage、ex-date | 分红质量研究 |

高级操作符按提供方能力保留：`site:`、`filetype:`、精确短语、排除词、`OR`。脚本不把用户输入拼入 shell，只做 URL 编码。

## 聚合、去重与独立性

输出先按 URL 规范化去重，再按规范化标题合并：
- 去掉 fragment 和常见 `utm_*` 跟踪参数。
- 保留原始 `engines`，同时合并成 `provider_families`；Bing Web 与 Bing News 只算一个 provider family。
- `independent_provider_count` 只表示被多少独立索引发现，不等于事实被多少独立来源证实。
- 搜索引擎跳转 URL 标记 `redirect_unresolved=true`；回抓失败时不得写成原始来源。

## 输出契约

脚本输出：
- `plan`：语言、市场、profile、查询扩展、将要发送到哪些提供方。
- `source_health[]`：`ok|fail|blocked|skipped`、HTTP/错误类别、延迟、候选数。
- `filter_diagnostics`：raw、无关/无效、stale、未来时间戳与接受候选计数，用于解释“HTTP 有结果但聚合后为空”。
- `candidates[]`：标题、URL、snippet、原查询命中词、`discovery_relevance`、发现引擎、provider family、观察时间、`freshness_state/freshness_eligible`、`discovery_only=true`。至少命中实体/ticker 和足够的查询词，否则在聚合前剔除搜索导航与无关同名结果。
- `data_gaps[]`：被挡、解析失败、结果不足、敏感查询被拒绝。
- `privacy`：外部传输状态和敏感查询门。

`candidates[]` 不直接写 Evidence Ledger。回抓原文成功后，另建正常 `EvidenceItem`；若只有搜索摘要，最高 C 级、`monitoring_only`。

## 故障与隐私处理

- 403/429：`blocked`，不通过 cookie/代理池规避；切换下一个 provider。
- 解析为空：`fail=parse_empty`，不把 HTTP 200 当成功。
- 所有源都失败：返回非零或 `ok=false`（视是否至少有候选），同时给 DataGap。
- `news` 有明确旧时间戳：剔除；时间戳缺失或不可解析：保留 `freshness_state=unknown` 并写 `published_at_unknown` DataGap，不能宣称满足 freshness 窗口。
- 敏感查询命中：默认 fail-closed，不出网；先把查询改写成不含秘密的公开命题。
- 查询 provider 必须在 `plan.selected_engines` 中；不允许脚本静默追加外部服务。

## 与主决策链的关系

- 搜索候选 → 回抓原文 → EvidenceItem / Conflict Ledger → Mira readiness → Decision Compiler。
- `rumor` / `hot` 结果只映射到既有 `grok_web`、`x_frontline`、`endogenous_structure`、`data_quality` 或相应真实证据模块。
- 本层不新增 module、不新增评分动作、不复活 L0、不放宽 position cap。

## 来源与迁移边界

方法参考：ClawHub `@gpyangyoujun/multi-search-engine` v2.1.3。吸收语言路由、提供方多样性、操作符、限速和聚合思路；拒绝“无外部传输”错误表述、自动跨司法辖区路由、cookie 获取/重试和全量 16 引擎盲扫。具体来源、版本与拒绝项登记在 `source-map.md`。
