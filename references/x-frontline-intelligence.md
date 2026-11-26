# X Frontline Intelligence · X 一线情报层

## 核心判断

X/Twitter 的投研价值不是“观点更多”，而是能更快看到产业链、政策现场、工程实践、供应链上下游、客户/开发者/从业者的一线实时信息。它尤其适合半导体、AI 基建、软件生态、能源、电力、地缘政治等高信息密度赛道。

但 X 只能作为 **frontline clue source（一线线索源）**，不能单独作为事实锚点，更不能单独提高仓位。它的正确位置是：更早发现变量 → 生成待验证假设 → 用身份、来源、时间、市场数据和官方材料交叉验证。

## 何时触发

出现以下任一场景时，必须启用本层：

- 用户要求用 Grok / X / Twitter 查最新产业链、政策、板块或公司动态。
- 半导体、AI 供应链、算力、电力、光模块、存储、先进封装、机器人、军工、地缘政治等高信息密度主题。
- 传统媒体/券商研报滞后，但一线从业者、工程师、供应链账号、政策观察者已在讨论新变化。
- 市场价格异动先于公告，需判断这是现场信号、二手转述、KOL 叙事，还是噪音。

## 工具路径

默认使用 Hermes 已登录 Grok：

```bash
hermes chat -Q --provider xai-oauth -m grok-4.3 -q "<X/全网投研检索问题>"
```

规则：

- 使用 `xai-oauth` / Hermes 内置 Grok。
- 不使用 `grok2api`，不处理 cookie，不走本地 Grok gateway。
- Grok 不可用时，降级到 `web_search` / `web_extract` / 行情与公告源，并标注 `Hermes Grok unavailable`。

## 三重验证门

X 信息进入决策前，必须通过三重验证门：

### 1. 身份验证 Identity Gate

对每条 X 线索标注发言者身份：

| 身份类型 | 例子 | 可信度上限 |
|---|---|---|
| 一线从业者 / 工程师 / 供应链人员 | fab、EDA、设备、云厂商、上游材料、客户现场人员 | 最高 Medium，但需交叉验证 |
| 公司/高管/官方账号 | 公司 IR、CEO、产品负责人、政府部门 | 可作为线索；事实仍以公告/文件为准 |
| 专业分析者 | 半导体 analyst、行业专家、政策研究者 | Medium/Weak，取决于来源透明度 |
| KOL / 财经大V / 匿名账号 | 热门线程、截图、传言 | Weak，只能做线索 |
| 机器人/搬运/二手摘要 | 无原创信息 | 不进入证据账本，除非找到原始源 |

### 2. 交叉验证 Cross-check Gate

X 线索至少需要一个独立源验证，才能进入核心假设：

- 官方文件：SEC / HKEX / 巨潮 / 交易所公告 / 政府文件。
- 公司材料：IR、earnings call、10-K/10-Q、公告、新闻稿。
- 市场数据：LongBridge、AkShare、CBOE、WindClaw。
- 产业链旁证：上下游公司披露、招聘、招标、中标、进出口、产能/交期/价格。
- 多个 X 账号不等于多源；如果都引用同一截图/同一原始帖，仍算单源。

### 3. 时间验证 Time Gate

X 的优势是快，所以必须显式标注时间：

- `posted_at`：原帖发布时间。
- `observed_at`：本次搜索看到的时间。
- `market_reaction_window`：信息发布后价格/成交/期权是否已有反应。
- `staleness`：72 小时内为实时线索；超过 72 小时默认转背景，不得写成“最新催化”。

## 证据等级

X 线索默认进入 `Tier 4 / Weak / reliability <= 0.5`。

只有同时满足以下条件，才可升级为 `Medium clue`，但仍不能单独提高仓位：

1. 发言者身份可识别且与主题有直接关系；
2. 时间戳清楚；
3. 有至少一个非 X 独立来源交叉验证；
4. 能映射到 12 类决策变量之一；
5. 有清晰 falsifier 或后续验证节点。

禁止事项：

- 禁止把 X 热度当成事实。
- 禁止把 KOL 观点当作一线信息。
- 禁止用“很多人都在说”替代独立来源。
- 禁止把 X 线索单独写成“财报验证 / 官方确认 / 供应链确认”。
- 禁止因 X 线索单独提高仓位；最多只能提高观察优先级或生成假设。

## Evidence Ledger 字段

X 线索进入证据账本时，必须至少包含：

```yaml
x_frontline_signal:
  eid: E
  source: "Hermes Grok/X"
  author: "<display name or handle>"
  original_url: "https://..."
  author_identity: "frontline_practitioner|official|analyst|kol|anonymous|repost"
  identity_confidence: "high|medium|low"
  published_at: "<ISO date or timezone-aware timestamp>"
  published_time_precision: "exact|minute|date_only"
  retrieved_at: "<timezone-aware timestamp>"
  access_state: "public|partial|subscriber_only|blocked"
  originality: "original|reply|repost|screenshot|second_hand"
  inference_label: "direct_assertion|framework_inference|self_reported_performance|not_found_publicly"
  provenance_mode: "direct_quote|source_summary|framework_inference|unverified"
  claim_type: "fact|reported_metric|guidance|forecast|assumption|opinion|market_pricing|derived_calculation|rumor_signal"
  decision_variable: "narrative_delta|financial_validation|policy_regulatory_delta|crowding_risk|..."
  supporting_eids: ["E..."]
  contradicting_eids: ["E..."]
  reliability_ceiling: 0.5
  decision_impact: "create_hypothesis|raise_watch_priority|needs_verification|ignore_noise"
```

## 新增公开来源维护规则（原 public-source-overlays 独有段落）

新增任何公开来源（Macro 页面、公开交易帖子等）时，必须写清楚：`source / access_boundary / derived_rule / verification_requirement`。不得把付费内容、登录后内容或不可复核的传闻写成公共事实。

## 决策影响

| 状态 | 动作影响 |
|---|---|
| 未验证 X 线索 | 只生成假设，不提高仓位 |
| 身份可识别但未交叉验证 | 提高观察优先级，不进入 L1/L2 晋级主证据 |
| 已被官方/市场/产业链数据交叉验证 | 可作为辅助 EID，与强证据共同参与决策 |
| X 热度很高但价格/数据不确认 | 标记 `narrative_heat_without_confirmation`，防止追高 |
| X 信号与公告/财报冲突 | 官方/财报优先，X 只作为冲突线索 |

## 社媒观点 × 技术触发

当 X/KOL 帖子同时给出估值锚、基本面催化和技术入场条件（例如“跌破大资金认购价，但等 RSI14 < 30 再进”）时，必须加载 `references/social-technical-entry-gate.md`：

- 估值锚只决定 watchlist，不等于买点。
- 基本面催化只生成假设，需要原始来源/公告/产业链材料交叉验证。
- 作者声称的回测胜率必须有样本、窗口、费用、walk-forward，否则只能记为 `backtest_unverified`。
- 明确技术触发未满足时，Decision Compiler 默认 `watchlist/no_trade`，不得因为作者口碑或价格锚直接入场。

## 半导体等高信息密度赛道的特殊用法

在半导体、AI 基建、电力、先进封装、光模块、存储等赛道，X 重点查：

- 工程师/从业者对瓶颈、良率、交期、产能、价格、替代路线的现场描述。
- 上下游公司、客户、供应商、设备商的时间线变化。
- 行业会议、技术标准、开源项目、芯片 tape-out、客户认证、订单节奏。
- 是否有 “sell-side/media 还没写，但一线已经在讨论” 的新变量。

但所有线索必须回到：订单、产能、价格、毛利、capex、客户验证、监管文件或市场结构数据。否则只保留为 Hypothesis Ledger，不进入仓位计算。

## KOL 轮巡抓取纪律（v2.30）

维护一份**高信噪 KOL 轮巡清单**（当前实例：@citrini / @Balder13946731 / @Franktradinglog；实例可换，标准是长期输出可验证框架而非喊单）。轮巡规则：

- **窗口分层**：近 72h 内容 = 实时信号候选（进当期研究）；近 20 天 = 背景语料（校准该 KOL 的框架与立场）。
- **只收长帖/文章**，跳过纯情绪短推；Substack 等有公开全文的优先抓全文进 Evidence Ledger（带 URL + 时间戳）。
- **订阅墙内容标 `subscriber_only_gap`**：只知道标题/预览时，登记缺口，禁止根据标题脑补内容。
- **`promotion_conflict` 标注**：KOL 同时在卖订阅/课程/带货时，其「战绩展示」帖单独降档。

### 标准化方法卡 handoff（v2.42）

只有公开原始 URL 能支撑可复用方法时，才在同一 `research_provenance.v1` bundle 添加 `kol_method_cards`；完整 schema 与机器 validator 见 `references/source-grounded-research-provenance.md`、`scripts/kol_method_card.py`。固定生命周期必须按顺序覆盖：`public_claim → falsifiable_thesis → universe_selection → entry → add_reduce → stop_invalidation → take_profit → exit → sizing_risk → post_trade_calibration`。缺失环节写 `not_found_publicly`，不能删 key 或从付费预览推断。

- `fact_claim→fact`、`experience_report→opinion`、`rumor/meme→rumor_signal`，KOL 别名不得越过 `data-contracts.md` 主枚举。
- 每一环保留 author、original URL、published/retrieved time、access state、market/time horizon、supporting/contradicting EIDs、direct/inference/self-report 标签与 unknowns。
- 卡片必须区分 `portable_parts` 与 `do_not_port`；具体点位、精确交易复制、账户收益和自报 PnL 一律不移植。
- 卡片 `position_multiplier=0.0`，只能触发 `tighten/refresh_source/request_manual_review`；不能抬 action level/position cap/reliability，不能复活 L0。

### kol_model_signal（KOL 自报模型/战绩的处理）

KOL 自报预测命中（如「模型预测收盘点位精确命中」）或展示历史战绩时，一律记 `kol_model_signal`：

- 幸存者偏差不可排除（失败的预测不会被同等展示），**只作线索，不作方法有效性证据**。
- 处理：登记进 decision memory，由 `calibration_scorecard.py` 用后续实际数据**事后实测**该 KOL/该类信号的命中率；实测有 edge 才在 Overnight Ensemble Ranker 的 KOL 成分（封顶 0.15）内生效，无 edge 自动归零。
- 禁止因为一次公开命中提高该 KOL 的 reliability ceiling。
