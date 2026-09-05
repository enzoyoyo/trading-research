# Attention & Rumor Triage · 热点、异动与传闻分流

> 主落点声明：编译进 `endogenous_structure`；来源可信度与事实回抓仍走下文列出的证据模块。

## 目的

把“大家都在讨论”拆成两个互不替代的轴：
- **attention**：关注度/传播速度/行情异动有多强。
- **credibility**：信息是否有原始来源、身份、时间与独立交叉验证。

高热度不代表高可信，高可信也不代表高收益。两轴必须分开，禁止用关键词命中数直接产出买卖信号。

## 触发

- 用户问“今天什么最热、异动、爆量、viral、buzz”。
- 用户问 M&A 传闻、内部人买卖、分析师升降级、监管调查、融资/解禁、X 上的早期说法。
- 实时新闻层发现未经官方确认、但可能改变事件窗口的线索。

## 数据车道

| 车道 | 首选 | 用法 |
|---|---|---|
| 行情异动 | LongBridge `top_movers/anomaly/rank_list/industry_rank/quote/trades` | 验证价格、量、时间，不解释原因 |
| 新闻广度 | LongBridge `news/news_search` + `multi_source_search.py --profile news|hot` | 发现标题与原文候选 |
| 官方/监管 | `filings`、SEC、CNINFO、HKEX、交易所、公司 IR | 确认/否定传闻的事实锚 |
| 内部人/持股 | LongBridge `shareholder*`、`corp_action`、SEC Form 4/13D/13G | 区分申报、交易、滞后与主体 |
| X/社媒 | Hermes Grok/X | 必须走身份、原创性、时间、交叉验证门 |
| 预测市场 | Polymarket layer | 仅 market_pricing 先验，不验证公司事实 |

## 结构化分流

### Attention state
- `quiet`：单一弱线索，无行情/搜索广度确认。
- `emerging`：至少两个 provider family 发现，或价格/量异动开始出现。
- `broad`：新闻、搜索、行情三类中至少两类同时升温。
- `crowded`：多平台高热 + 价格/期权/成交拥挤；进入 endogenous_structure 风险检查。

### Credibility state
- `unverified`：匿名/二手/搜索摘要/无时间。
- `single_source`：有可定位原始发言或媒体，但无独立确认。
- `corroborated`：至少两个真正独立来源，且不是转载同一匿名源。
- `officially_confirmed`：监管、交易所、公司公告、正式 filing 明确确认。
- `contradicted`：官方或高等级证据否定。

搜索引擎数量只影响 attention，不自动提升 credibility；同一通讯社被 10 家媒体转载仍是一条来源。

### Cross-source metric discipline（v2.38）

Reddit/X mentions、news article count、Polymarket trade count、buzz score、bullish percentage 与预测市场概率量纲不同，禁止直接平均。每行必须保留 `provider_family/metric_name/metric_definition/sample_window/sample_size/observed_at/stale_after/source_reliability_ceiling`：

- 只有 metric 定义、量纲与采样窗一致时才能报告 spread/dispersion。
- 宽分歧进入 Conflict Ledger 或提高 verification priority，不直接判多空。
- 紧密一致最多提高 attention；没有官方/原始证据时 credibility 不变。
- 缺样本量、时间窗或来源独立性时，alignment 状态必须是 `insufficient_basis`。

运行时来源覆盖与多标的公平补抓见 `intelligence-coverage-and-watch-triggers.md`。

## Rumor taxonomy

| event_type | 必查 | 默认姿态 |
|---|---|---|
| `ma_takeover` | 8-K/公告、13D/13G、公司声明、反垄断/交易所 | 未确认最高 watch |
| `insider_activity` | Form 4/申报日期、交易代码、主体、数量、价格、是否计划性出售 | 申报滞后必须标注 |
| `analyst_action` | 机构原文/可信媒体、评级、目标价、日期、旧值 | sell-side opinion，不是基本面事实 |
| `regulatory_investigation` | 监管原文、案号/文件号、公司披露 | 关键词标题不等于正式调查 |
| `financing_dilution` | S-3/ATM/配股/供股/可转债/锁定期 | 进入 issuance_overhang |
| `earnings_guidance` | 公司 IR、8-K、财报、电话会原文 | guidance ≠ realized fact |
| `supply_chain_whisper` | 身份、时间、客户/供应商双侧验证 | 最高 hypothesis/watch priority |

## 允许的决策影响

- `attention=emerging|broad` 且可信度未过门：`raise_watch_priority` / `needs_verification`。
- `crowded`：可降低仓位上限或提高拥挤风险，不得因为热度加仓。
- `officially_confirmed`：把**官方原文**作为新 EvidenceItem 进入 filing/fundamentals/market_data；传闻文本本身仍不加分。
- `contradicted`：进入 Conflict Ledger；若原 thesis 依赖该传闻，触发 falsifier/recompile。

## 禁止事项

- 禁止把 “rumor/hearing/sources say” 关键词累计成可信度。
- 禁止把 likes/retweets/搜索排名当概率或胜率。
- 禁止静态公司名映射猜 ticker；必须过 security resolver。
- 禁止复制外部 skill 的 BUY/HOLD/SELL 或固定 impact score。
- 禁止自动创建 LongBridge alert 或 cron；提醒属于用户显式请求后的独立动作。

## 与现有模块映射

- 社媒未验证：`x_frontline` / `grok_web`，最高 watch。
- 新闻/搜索摘要：`data_quality` + discovery candidate，不进事实 ledger。
- 价格/量异动：`market_data` / `endogenous_structure`。
- 官方 filing：`filing` / `fundamentals`。
- 融资稀释：`endogenous_structure`（issuance_overhang）。
- 全部仍由 Mira + Decision Compiler 裁决，不新增动作等级。

## 来源

参考 ClawHub `@udiedrichsen/stock-analysis` v6.2.0 的 Hot Scanner / Rumor Scanner。仅吸收多车道发现、传闻类别与“先发现再研究”的产品意图；拒绝 keyword-only impact score、X cookie/Bird CLI、全量 `.env` 传递、静态 ticker 地图和独立 BUY/HOLD/SELL 合成器。
