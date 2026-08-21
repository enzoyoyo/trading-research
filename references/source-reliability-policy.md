# Source Reliability Policy · 来源可信度与缺口处理

## 1. 来源等级

| 等级 | 来源 | 用法 |
|---|---|---|
| S | 交易所/监管/公司公告/SEC/CNINFO/HKEX/财报原文 | 可作为事实锚点 |
| A | LongBridge/AkShare/CBOE/WindClaw quote 或 get_wind_data 等可复现工具输出 | 可作为行情/结构事实，但需标时间戳与工具口径 |
| B | 权威财经媒体、公司 IR、财报电话会整理、WindClaw financial reference content | 可作辅助事实，关键结论需交叉验证 |
| C | Grok 全网摘要、搜索结果页/标题/摘要、Grok/X、社媒、论坛、一线从业者实时线索 | 只能作为发现层/线索层；必须原始 URL、身份识别、交叉验证、时间验证；不能单独提高仓位 |
| D | 无来源截图、群聊传闻、模糊说法 | 不进入仓位计算 |

### 1.1 原文、公开观点与持仓披露

- 监管/交易所/基金正式定期报告中的持仓披露按原始文档定级，可作滞后事实；必须标报告期、发布日期、任职/控制关系，不能当实时资金流。
- 发布主体官网上的本人访谈、手记或演讲可作“此人公开表达过该观点”的 B 级锚点；不能单独验证行业、公司或收益判断为真。
- 媒体转述、标题、二手摘编最高 C；能回抓官方原文后，按原始来源重新定级。
- 方法论蒸馏属于 `framework_inference`，不是 direct quote；必须走 `references/source-grounded-research-provenance.md`。
- KOL 方法卡只证明“公开来源表达过/可蒸馏出该方法”；它不验证 edge。自报收益、命中率、账户曲线固定为 `self_reported_performance + unverified + opinion`，永不提高 reliability ceiling。
- 直接引文必须有本地捕获、内容 SHA-256 和行定位；拼接、改写、加省略号后不得继续标 verbatim。
- 来源许可未知时默认 `redistribution_allowed=unknown`；只做内部短引或摘要+原始链接，不复制长篇正文。

### 1.2 美股监管来源与传输层（v2.53）

- SEC EDGAR accession/原始文档/XBRL 是 S 级监管事实源；LongBridge `filing` 是 A 级可复现索引/传输层。LongBridge 返回 SEC URL 不等于新增第二个事实源。
- LongBridge 内部 `provider_id`、标题或发布时间不能替代 accession、CIK、filed_at、report period；缺 accession 的索引行只进 DataGap，不生成官方 filing EID。
- 同一 accession 被 LongBridge、SEC 直连或其他解析器重复返回时只算一个 `source_family=sec_edgar`；未知底层聚合源不计独立性。
- 来源 family 覆盖低于 L1+ 门槛时复用 `data_quality:source_coverage`：1 family/任一 unknown → L0；2 families → 最高 L1；≥3 才取消该额外 cap。该规则只收紧，不自动提高动作。

## 2. 新鲜度 SLA

| 类型 | 新鲜度要求 |
|---|---|
| 实时行情/盘前盘后 | 分钟级；过期需标“收盘/延迟/不可用” |
| CBOE Gamma | 约 15 分钟延迟；日内决策需说明 delayed |
| A 股龙虎榜/涨停/炸板 | 交易日级；非交易日用最近交易日并标注 |
| 新闻/政策/监管 | 默认过去 72 小时；老新闻只能作背景 |
| 财报 | 最新报告期；必须标报告期和发布日期 |
| 宏观数据 | 指标发布日期；事件日前后提高权重 |
| 账户座位 | 必须是本次会话或用户提供的最新只读数据 |

## 3. 冲突处理

1. 官方公告优先于媒体，媒体优先于社媒。
2. 行情多源冲突时，优先 LongBridge 实时；若异常，交叉 AkShare/Yahoo/交易所并降级。
3. 新闻利好未被公告或财报验证，不得提高仓位。
4. 财报红线优先于情绪强度。
5. 市场结构破位优先于中线叙事；中线观点不能替代短线止损。
6. X/Grok 一线线索与官方/财报/市场数据冲突时，官方/财报/市场数据优先；X 进入 Conflict Ledger，不进入仓位提升。
7. 账户缺口优先于交易冲动；没有账户只读数据，不给具体买卖数量。
8. Grok 全网搜索只负责发现来源；如果能给出原始官方/公司/交易所/行情链接，按原始来源重新定级；如果只有 Grok 摘要，最高 C 且不得入仓位计算。
9. 多搜索引擎重复命中只提高 discovery confidence/attention，不提高 claim credibility；同一通讯社或匿名源被转载多次仍算一个来源。

## 4. 数据缺口如何影响动作

| 缺口 | 最高动作 |
|---|---|
| 证券身份无法唯一解析 | 暂停，先澄清或搜索确认 |
| 无行情/价格时间戳 | 观察 |
| 无公告/财报但问题涉及基本面 | 观察 |
| 美股期权活跃票无 Gamma 且问题涉及接盘/追高 | 最高试错，且必须写缺口 |
| A 股短线缺涨停/炸板/龙虎榜/情绪周期 | 最高观察/试错；可用 WindClaw 市场/板块/个股表现补证但不可替代龙虎榜 |
| 港股成交额/流动性缺失 | 最高观察 |
| IBKR/LongBridge 账户上下文缺失 | 不给账户级加减仓数量 |
| 无 falsifier 或 review clock | 不给交易动作 |
| X/Grok 线索缺身份/时间/交叉验证 | 不提高仓位；最多提高观察优先级 |
| Grok 全网结果缺原始 URL 或时间戳 | 最高观察；只能列为 `agent_reported_clue` |
| 来源溯源为 `partial`（无本地捕获/转载许可未确认） | 最高 L1；只作 context/watch，不进入 calibration/materiality |
| 来源溯源为 `blocked`（hash/引用/反证/引用链失败） | 最高 L0，`position_multiplier=0.0`；不得写入已接受 claim |
| 美股公司证据只有 1 个已知底层 family，或存在 unknown family | L0/WATCH，`position_multiplier=0.0`；多 transport/多 filing 不加票 |
| 美股公司证据只有 2 个已知底层 families | 最高 L1，`position_multiplier<=0.5` |
| KOL 方法卡来源为 `partial` / subscriber-only / blocked，或十阶段映射缺项 | partial 最高 L1；结构/受限来源越权为 blocked/L0；只能刷新来源或人工复核 |
| 回测/因子/模型结论缺 walk-forward、成本、容量、回撤、no-lookahead 检查 | 最高研究假设；不提高仓位 |

## 5a. 自动兜底链规则（原 intel-source-registry.md 独有段落）

每个数据点获取按以下顺序执行，成功即停：

```
T1 主源 → T2 备源 → T3 兜底 → 终极兜底 → 缺口标注
```

- **失败重试规则**：T1 失败后最多重试 2 次（只对网络失败，不对认证/格式失败）。
- **降级规则**：主源（T1/T2）缺失 → 仓位上限立即乘对应倍率。
- **圆桌规则**：3 个以上数据维度缺失 → 自动降到 L0 观察。

> 逐市场逐维度的主源/备源/兜底映射以 `references/market-source-matrix.md` 为准（LongBridge MCP 优先）。

## 5b. Capability、availability 与 claim 分离（v2.38）

- `capability_state=unsupported`：来源不覆盖，不代表公司没有该事实。
- `auth_missing/rate_limited/error`：当前不可用，不代表数值为 0。
- `delayed/cached`：必须带 `observed_at/stale_after/fallback_level`，只能按参考覆盖处理。
- 多标的研究按 `intelligence-coverage-and-watch-triggers.md` 生成每个 `target × dimension` 的 coverage 与公平补抓队列。
- `covered_live/covered_reference` 只说明拿到数据；数据内容仍需过 Evidence reliability 和 claim posture。

## 6. 禁止事项

- 禁止把 Grok/X 的观点当作事实。
- 禁止把 Grok 全网摘要当成原始来源；必须回抓原文或标注缺口。
- 禁止把 X 一线线索单独写成供应链确认、财报验证或仓位提升理由。
- 禁止用“市场都在说”替代证据。
- 禁止用单次高收益回测替代风险预算、成本、容量和样本外检验。
- 禁止没有时间戳的价格、估值、资金流。
- 禁止在报告中输出 token、secret、cookie、账户敏感号。
- 禁止把工具失败包装成“已抓取”。
- 禁止把搜索标题、摘要、排名、provider 数量直接写成已验证事实。
- 禁止把框架推演、用户提供的伪引文或拼接文本标成来源原话。
- 禁止把第三方全文语料、许可不明的数据快照或长篇摘录复制进 tracked skill；默认短引+摘要+原始链接。
- 禁止用 KOL 方法卡、自报 PnL、订阅促销或帖子热度提高 action level、position cap、reliability，或复活已被上游封为 L0 的标的。
