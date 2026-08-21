# WindClaw A 股备用数据通道

## 结论

WindClaw 只有在运行时健康检查通过后，才可作为 trading-research 的 A 股备用与交叉验证通道；不要替代原有证据账本。正确位置是：

1. A 股行情、结构、新闻、研报、公告出现 AkShare / LongBridge / Web 缺口时，WindClaw 作为高质量备源补证。
2. 对 A 股个股和市场主线问题，WindClaw 的金融语料可以作为“机构化总结语料”，但所有关键数字仍需尽量回链到公告、财报、行情或可复现工具。
3. WindClaw skill 里的 A 股方法框架可以吸收为路由模块，但不能把 WindClaw 输出原样当作最终结论。

## 运行时验证入口

- WindClaw 安装路径：`/Applications/WindClaw.app`
- WindClaw 状态目录：`~/.openclaw-windclaw/`
- WindClaw runtime session 文件：`.windclaw-aigw-session`
- Hermes MCP 预期配置（使用前必须 `hermes mcp test ...`）：
  - `windclaw-web`：Wind MCP `internet_search`
  - `windclaw-quote`：A 股市场/个股/板块实时表现工具
- 本 skill 的直接桥接脚本：`scripts/windclaw_bridge.py`

注意：Wind session 是敏感运行态凭证，只能放在 `~/.hermes/.env` 或 WindClaw runtime 文件中，不能写入 skill、报告或日志正文。

## 可用工具形态

### 1. Hermes Native MCP（下一次新会话可直接出现工具）

配置在 `~/.hermes/config.yaml`：

- `windclaw-web` → `mcp_windclaw_web_internet_search`
- `windclaw-quote` → `mcp_windclaw_quote_quote_get_market_realtime_performance`
- `windclaw-quote` → `mcp_windclaw_quote_quote_get_stock_realtime_performance`
- `windclaw-quote` → `mcp_windclaw_quote_quote_get_sector_realtime_performance`

用途：实时资讯搜索、A 股市场/个股/板块当前表现补证。

限制：Hermes 当前进程不会自动获得新增 MCP 工具。配置后需新开 Hermes 会话或重启 gateway。Wind session 过期时，先打开 WindClaw 重新登录，再同步 `WIND_SESSION_ID`。

### 2. 直接桥接脚本（当前会话也可用）

```bash
python3 scripts/windclaw_bridge.py health --json
python3 scripts/windclaw_bridge.py reference "贵州茅台 公司速览" --json
python3 scripts/windclaw_bridge.py data "贵州茅台 最新估值 行情 成交额" --json
python3 scripts/windclaw_bridge.py document "贵州茅台 最新公告" --doctype 3 --json
python3 scripts/windclaw_bridge.py web "贵州茅台 最新新闻" --freshness 最近一周 --count 5 --json
```

脚本读取顺序：

1. `WIND_SESSION_ID` 环境变量
2. WindClaw runtime 文件 `~/.openclaw-windclaw/**/.windclaw-aigw-session`

脚本不会打印 session 原文。

## WindClaw 工具与 Evidence Ledger 映射

| WindClaw 能力 | 适用问题 | Evidence type | 可靠性上限 | 使用纪律 |
|---|---|---|---|---|
| `quote_get_market_realtime_performance` | A 股大盘今天强弱、涨跌家数、情绪温度 | `structure` / `quote` | A | 必须标时间；和 LongBridge/AkShare 至少一项交叉 |
| `quote_get_stock_realtime_performance` | 单只 A 股当前行情、异动、成交结构 | `quote` / `flow` | A | 只做当前快照，不替代 K 线与公告 |
| `quote_get_sector_realtime_performance` | 板块强弱、主线扩散、行业轮动 | `structure` | A | 和涨停池/板块梯队交叉 |
| `wind_financial_reference_content` | 个股速览、基本面、机构观点、财报解读 | `research` / `fundamental` | B+ | 语料是总结，不是原始公告；核心数字需再找原始来源 |
| `get_wind_data` | 结构化金融数据、估值、宏观、筛选 | `quote` / `fundamental` / `macro` | A- | 若字段口径不透明，标注 Wind 口径 |
| `document_search` | 新闻、研报、公告、会议、法规 | `news` / `filing` | B 到 S | 公告类可升 S；媒体/研报仍需交叉验证 |
| `internet_search` | 联网新闻和资讯补搜 | `news` / `social` | B/C | 不单独提高仓位 |

## A 股查询模板

### 个股总控

```text
{公司名或代码} 公司速览
{公司名或代码} 个股基本面分析
{公司名或代码} 个股资金面分析
{公司名或代码} 近日异动原因
{公司名或代码} 机构研究汇总
{公司名或代码} 投资者问答精粹
```

### 市场主线 / 板块

```text
A股 今日市场主线 题材周期 资金流 成交结构
{板块名} 板块表现 龙头 中军 补涨 持续性
{板块名} 政策催化 产业逻辑 资金合力
```

### 风险与资金

```text
A股 市场风险 情绪退潮 高位股反馈 成交额
北向资金 行业流向 风格切换 连续性
{公司名或代码} 龙虎榜 资金流 成交结构
```

### 公告/研报

```text
{公司名或代码} 最新公告 业绩预告 问询函
{公司名或代码} 机构观点 分歧 预期差
```

## WindClaw skill 可吸收的方法模块

WindClaw 内置 A 股 skills 的价值在于“问题路由与分析模板”，可以映射到 trading-research：

| WindClaw skill | 吸收位置 |
|---|---|
| `个股问答总控` | A 股单票问题先识别：能不能买/为什么涨跌/短期怎么看/中期逻辑/估值/公告财报/买卖点 |
| `A股市场主线识别` | A 股市场结构：主线、次级热点、龙头/中军/补涨、情绪周期、明日观察点 |
| `资金流成交结构` | Evidence → flow/structure：成交额×位置、换手质量、资金流可信度、增量资金判断 |
| `北向资金行为` | A 股/港股风格判断：连续性、集中度、偏好行业、趋势信号 vs 短期噪音 |
| `研报共识分析` | Hypothesis/Conflict：共识、分歧、已定价、预期差、卖方观点质量 |
| `事件驱动短线催化` | Hypothesis：事件性质、催化级别、预期差、三情景推演 |
| `市场风险雷达` | Position Cap：系统性风险、情绪风险、风格风险、仓位降级 |
| `板块比较` / `题材周期持续性` | A 股主线持续性和相对强弱比较 |
| `基金重仓拥挤度` / `机构持仓股东分析` | crowding 与机构筹码结构补充 |

## 使用边界

1. WindClaw 是备用与交叉验证源，不允许“一源定结论”。
2. WindClaw 语料返回的公司、行业、财务数字必须标明“Wind 语料/数据口径”，并尽量用公告或财报复核。
3. 若 WindClaw 与 AkShare/LongBridge/公告冲突：先降级动作，报告写冲突，不挑数字。
4. WindClaw session 失效时，不要假装已用；写 `WindClaw unavailable/session expired`，然后回退 AkShare/web_search。
5. 不把 WindClaw 私有 session、headers、token 写入任何报告、skill 或 commit。

## 进入三账本的规则

WindClaw 输出进入 Evidence Ledger 时，`source` 必须写清：

```json
{
  "source": "WindClaw wind_financial_reference_content / get_wind_data / document_search / MCP quote",
  "timestamp": "实际调用时间",
  "raw_fact": "只写事实，不写解释",
  "reliability": 0.65,
  "cross_check": ["AkShare/LongBridge/CNINFO/Web EID"]
}
```

建议 reliability：

- WindClaw quote / get_wind_data：0.75-0.90，取决于字段口径和时间戳
- WindClaw document_search 公告：0.85-0.95
- WindClaw document_search 新闻/研报：0.60-0.80
- WindClaw financial reference content：0.60-0.75
- WindClaw web search：0.50-0.70
