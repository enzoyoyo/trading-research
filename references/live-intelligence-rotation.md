# Live Intelligence Rotation · 每次调用都要更新的实时情报层

## 核心原则

`trading-research` 不能只靠静态交易法。每次调用都必须先回答：**今天/此刻，有哪些新事实会改变胜率、赔率或仓位？这些事实属于四象限宏观、内生市场结构，还是公司层面？**

默认实时情报优先级：
1. Grok 已登录全网搜索 / X 社媒信号（`hermes`，xai-oauth）：**只作实时发现层，不作事实锚点**。先读 `references/grok-web-research-layer.md` 与 `references/x-frontline-intelligence.md`；Grok 全网结果必须有原始 URL/时间戳，X 线索必须做身份识别、交叉验证、时间验证。
2. 行情数据：**LongBridge CLI** + **AkShare**；A 股可用 **WindClaw quote/data/document/reference** 做备用交叉验证。
3. Web 搜索/抓取：`web_search` / `web_extract`；WindClaw `internet_search` 可作为 A 股金融资讯补源。`live_intel_run.py` 在 Grok health 或请求失败时默认自动调用 stateless `multi_source_search.py --profile news`；敏感查询由 privacy gate 阻断，可用 `--no-search-fallback` 显式关闭。
4. 期权/Gamma：`scripts/options_gamma.py`。
5. 用户输入：成本、持仓、时间周期、偏多/偏空假设。
6. 覆盖层：新闻/政策/宏观四象限/内生市场结构/LongBridge行情/IBKR只读账户座位。

## 每次调用的强制步骤

```bash
python3 ~/.claude/skills/trading-research/scripts/live_intel_run.py <标的> --plan-only
python3 ~/.claude/skills/trading-research/scripts/live_intel_run.py <标的> --json
```

如果 Grok 不可用：
- 不要停；切换到 `web_search` / `web_extract` / 市场数据 skill。
- `web_search` 覆盖不足或失败时，再用 `multi_source_search.py --profile news --allow-external-search --json`；`live_intel_run.py` 的默认失败路径会自动执行同一兜底，敏感查询仍 fail-closed。搜索候选不是事实证据，必须回抓原文。
- 输出里必须写明：`Hermes Grok unavailable` 以及替代源。

## 新闻/政策/宏观/结构预检（每次必做）

读取以下参考并输出：
- `top-down-broker-decision-framework.md`
- `macro-dashboard-four-pillar.md`
- `endogenous-market-structure-playbook.md`

最低输出：
- 过去 72 小时新闻是否改变胜率/赔率/证伪条件；
- 政策/监管是顺风、逆风还是中性；
- 宏观四象限分别是什么状态，合成 `attack|neutral|defend`；
- 内生市场结构分别是什么状态，是否存在拥挤/被动/发行/解禁压力；
- 微观基本面是否验证叙事；
- Longbridge 行情/交易时段是否可用；A 股 WindClaw 备用验证是否可用；IBKR 只读账户上下文是否可用。

## X 一线情报处理（必须作为线索源）

X/Grok 的投研价值在于更快看到产业链一线从业者、政策现场、工程实践、供应链上下游的原始信息，尤其适合半导体等高信息密度赛道。但 X 不是事实锚点。

每条 X 线索必须输出：

- `author_identity`：frontline_practitioner / official / analyst / kol / anonymous / repost。
- `posted_at` 与 `observed_at`。
- `originality`：original / reply / repost / screenshot / second_hand。
- `claim_type`：使用 `data-contracts.md` 主枚举；旧别名 `fact_claim/experience_report/rumor/meme` 按 Claim Type Canonical Enum 映射。
- `cross_check_needed` 与已找到的非 X 交叉验证 EID。
- `decision_impact`：只能是 `create_hypothesis / raise_watch_priority / needs_verification / ignore_noise`，不得单独提高仓位。

不能通过身份识别、交叉验证、时间验证的 X 信息，默认是噪音或假设，不进入仓位计算。

## 期权/Gamma 结构预检（触发时必做）

若用户提供 Gamma Exposure / Put Wall / Call Wall / Gamma Flip 截图，或标的是美股高波动/期权活跃票：
- 读取 `references/options-gamma-structure.md`。
- 检查 Spot 是否跌破 Put Wall、是否位于 Gamma Flip 下方、Call Wall 是否压制、期权墙是否整体下移。
- 把结论转成仓位动作：`禁止接盘 / 仅观察 / 试错 / 降仓 / 止盈`。

## 把情报转成胜率变量

每条新信息只能以这 12 类进入决策：
1. 叙事增强
2. 叙事证伪
3. 财报验证
4. 交易确认
5. 宏观四象限状态
6. 杀杠杆 / liquidity regime
7. 拥挤/风险
8. 被动资金 / ETF 机制
9. 发行 / 解禁 / 增发 overhang
10. 轮动结构（leader vs #2 / #3）
11. 执行窗口
12. 账户座位影响

不能进入以上分类的信息，默认只是噪音，不提高仓位。

## Hermes 已登录 Grok 使用与故障处理

- 默认只用 Hermes 内部已登录 Grok：`hermes chat -Q --provider xai-oauth -m grok-4.3`。
- 不调用 `grok2api`，不调用本地 Grok gateway，不处理 cookie/clearance。
- 验证方式：`hermes chat -Q --provider xai-oauth -m grok-4.3 -q "Return exactly: OK_XAI_OAUTH_CONFIG_TEST"`。
- Grok 若只返回总结、不给原始链接/时间戳，必须标注 `agent_reported_clue`，不进入仓位计算。
- Grok 找到的原始来源要按 `source-reliability-policy.md` 重新定级：Grok 本身不加分，原始来源才是 EID。

## Hermes child-agent tool-limit discipline

Hermes 桌面 profile 可能挂载 200+ 工具；xAI 等 provider 会拒绝超过其工具数上限的请求。子 agent 做实时情报调用时不能继承完整 profile 工具列表，必须显式限定 toolset。

推荐命令形态：

```bash
hermes chat -Q -t web --provider xai-oauth -m grok-4.3 -q '<live intel prompt>'
```

`live_intel_run.py` 类脚本优先用环境变量限定默认 toolset：

```bash
export HERMES_LIVE_INTEL_TOOLSETS=web
python3 scripts/live_intel_run.py --health --json --timeout 30
python3 scripts/live_intel_run.py --market A --json --timeout 90 '<target>'
```

健康检查门：必须先拿到 `OK_HERMES_GROK_SEARCH_READY`，才能把子 agent 结果当作实时情报层使用。

失败降级链（不得停摆）：`web_search` → `web_extract` → LongBridge news/filings → `multi_source_search.py` 多索引发现 → A 股公开源；不得捏造 Grok/X 结果。403/429 不用 cookie 绕过，直接记录 source health 并切源。

## 量化/回测/模型输出处理

用户要求量化、因子、回测、模型、筛选器时，先读 `references/open-source-quant-research-patterns.md`：
- 无 walk-forward / 成本 / 容量 / 回撤 / no-lookahead 检查 → 最高研究假设；
- 通过稳健性检查 → 可参与排序或风险上限，但不自动提高 L 级；
- 真实组合分析必须输出 `portfolio_risk_budget`，账户只读缺失时不给具体数量。

## 轮动优化闭环

每次输出都要在报告末尾给：
- 本次主导方法与配权。
- 为什么这么配权。
- 失效后调权规则。
- 复盘字段：`预测方向 / 触发条件 / 证伪条件 / 复盘日期 / 结果归因`。
