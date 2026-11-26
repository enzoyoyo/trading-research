# Grok Web Research Layer · Hermes 已登录 Grok 全网研究层

## 目的

把 Hermes 已登录的 Grok / xAI OAuth 搜索能力纳入 `trading-research`，但只把它当成**实时发现层**，不把 Grok 的总结直接当事实。

核心边界：
- Grok 擅长发现“此刻市场在讨论什么、哪些原始链接值得查、哪些变量可能刚出现”。
- 事实锚点仍必须回到公告、财报、交易所、LongBridge/行情、SEC/HKEX/巨潮、CBOE、公司 IR、可复现数据源。
- Grok 不能单独提高仓位；只能生成假设、提高观察优先级、提示需要刷新哪些数据。

## 工具路由

| 需求 | 首选工具 | 边界 |
|---|---|---|
| Grok 全网实时检索 / 市场讨论 / 最新线索 | `hermes chat -Q --provider xai-oauth -m grok-4.3 -q "<query>"` | 要求返回 URL/时间/原始来源；否则只能算 `agent_reported_clue` |
| X/Twitter 原帖、账号、线程、图像/视频理解 | Hermes `x_search` 或 Grok/X 搜索 | 走 `x-frontline-intelligence.md` 三重验证门 |
| 普通网页、新闻、公告、GitHub/文档 | `web_search` + `web_extract` | 用网页原文作为 EID，不用 Grok 摘要替代原文 |
| 行情/财报/估值/账户/期权 | LongBridge / AkShare / CBOE / WindClaw / broker read-only | Grok 不替代结构化数据源 |

禁止：
- 不调用 `grok2api`，不处理 cookie，不走本地 Grok gateway。
- 不把 Grok 的“我查到/市场认为”写成事实。
- 不用多个转述同一原帖的页面伪装成多源交叉验证。

## 查询分解

每次使用 Grok 全网搜索时，至少拆成 4 类 query：

1. **Official / Filing**：公司公告、SEC/HKEX/巨潮、IR、财报电话会。
2. **Market Data / Structure**：行情、期权、资金流、ETF/passive flow、发行/解禁/回购。
3. **Industry / Supply Chain**：客户、供应商、良率、交期、capex、价格、招聘、招标。
4. **Social / Narrative**：X、Reddit、论坛、KOL、从业者评论，只作线索。

输出必须按 query 类别标注，不允许把社媒线索混进 official/fundamental 栏。

## Evidence Ledger 字段

```yaml
grok_web_signal:
  eid: E
  source: "Hermes Grok Web Search"
  query: "原始查询"
  observed_at: "YYYY-MM-DDTHH:mm:ssZ"
  original_url: "https://... | unavailable"
  original_source_type: "official|filing|company_ir|market_data|media|github|x_social|forum|unknown"
  claim_type: "fact|reported_metric|guidance|forecast|assumption|opinion|market_pricing|derived_calculation|rumor_signal"
  timestamp_quality: "exact|date_only|relative|missing"
  source_grade: "S|A|B|C|D"
  reliability_ceiling: 0.0
  cross_check_eids: []
  decision_impact: "create_hypothesis|raise_watch_priority|needs_verification|ignore_noise"
```

`reliability_ceiling` 规则：
- 有原始官方/交易所/公司/行情链接：按原始来源重新定级。
- 只有 Grok 摘要、没有 URL 或时间：最高 C，不能进入仓位计算。
- 只有社媒热度：最高 C/D，只能进入 Hypothesis Ledger。
- 与公告/财报/市场数据冲突：进入 Conflict Ledger，官方/财报/市场数据优先。

## 决策编译映射

```yaml
module_signal:
  module: "grok_web|x_frontline|data_quality"
  max_action_level: "L0|L1"
  position_multiplier: 0.0-1.0
  hard_veto: false
  reason: "unverified_grok_summary | source_laundering_risk | verified_original_source"
  repair_signal: "fetch original URL / verify with filing / refresh market data"
```

- `unverified_grok_summary`：最高 L0。
- `verified_original_source`：Grok 本身不加分，原始来源进入对应模块。
- `source_laundering_risk`：多个网页/账号引用同一源，仍按单源处理。

## 搜索提示模板

```text
请使用 Grok 全网搜索。目标：{ticker/theme}。
只输出过去 72 小时可能改变胜率、赔率、仓位或证伪条件的信息。
每条必须给：原始 URL、发布时间、来源类型、事实/观点/传闻分类、是否有非社媒交叉验证。
不要把市场情绪当事实；不要把多个转述当多源。
最后列出：需要用 LongBridge/公告/财报/CBOE/SEC/HKEX/巨潮刷新验证的数据点。
```

`agent_summary` 属于 `data-contracts.md` 的旧别名，未回抓原文前按 `assumption`/clue 处理。

## 质量红线

- Grok 只能缩短发现时间，不能降低证据门槛。
- 如果 Grok 与结构化数据冲突，优先信结构化数据；Grok 只说明“市场可能在误读”。
- 如果 Grok 发现的是新催化，但价格已经反应，报告必须写 `market_reaction_window`，避免追高。
- 如果 Grok 无法给原始来源，默认 `no_necessary_action`，不升级动作。
