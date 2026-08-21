# Two-System Trading Architecture · 模拟仓学习系统 × trading-research 核心系统

## 核心结论
交易体系拆成两套独立但可联动的系统：

1. **System A：每日自动决策交易的模拟仓系统**
   - 位置：`${HOME}/.hermes/longbridge-paper-trading/`
   - 职责：发现信息、生成交易决策、在 LongBridge Demo A/C 执行模拟仓订单、记录结果、复盘学习。
   - 允许动作：仅当 `paper_account_gate=pass` 且 `account_channel == lb_papertrading` 时允许模拟订单。
   - 禁止动作：永不碰实盘；不得绕过 gate；不得用未验证社媒消息直接加仓。

2. **System B：`trading-research` 核心分析与能力升级系统**
   - 位置：`${HOME}/.hermes/skills/trading-research/`
   - 职责：维护投研框架、证据门、Decision Compiler、风控规则、方法论和 skill 自优化。
   - 默认边界：不执行交易；只接收 System A 的复盘学习包，并在 materiality/conflict gate 通过后升级自身能力。

两者通过文件化接口联动，而不是耦合成一个黑盒：

```text
System A 交易与复盘 → learning_packets/*.json → System B materiality/conflict gate → trading-research skill upgrade
System B 分析框架/风控规则 → references + scripts → System A 决策 agent / proposal validator
```

## System A：每日自动决策交易的模拟仓系统

### 目标
不是“模拟买卖”，而是形成可审计的学习闭环：

```text
能力发现 → 最新信息抓取 → 清洗去噪 → trading-research 分析 → 决策编译 → Demo 下单 → 持仓跟踪 → 复盘 → 学习包 → 策略/skill 候选升级
```

### 每次决策前必须做

1. **能力发现**
   - 运行：`~/.hermes/longbridge-paper-trading/scripts/paper_capability_inventory.py`
   - 输出：运行时可见 skill 数量、相关 skill、MCP server、推荐工具路径。
   - 目的：明确本次可调用哪些搜索、数据、新闻、社媒、行情、MCP、文件脚本能力。

2. **决策上下文包**
   - 运行：`~/.hermes/longbridge-paper-trading/scripts/paper_decision_packet.py`
   - 内容：paper gate、policy、持仓、现金、行情、市场状态、最新报告、source roster、能力清单。
   - 规则：packet 只是基线；真正下单前仍必须刷新时效数据。

3. **多源信息抓取**
   - X/Twitter：`x_search`，默认 seed handles 见 `config/source_roster.json`，但不得局限于 seed。
   - Web/news：`web_search` / `web_extract`。
   - LongBridge：MCP 优先；CLI/SDK 兜底。
   - 其它可用 MCP/skills：根据 capability inventory 主动选择。

4. **清洗、去噪、归类、筛选**
   - 去重：多个账号引用同一截图/新闻只算一个源。
   - 时间：标注 `posted_at` / `observed_at` / 是否过期。
   - 身份：官方 / 一线 / 分析师 / KOL / 搬运。
   - 相关性：必须能映射到可交易资产、板块、宏观变量或风控变量。
   - 排除：无关热闹、过期消息、无来源断言、纯情绪喊单。

5. **调用 `trading-research` 原有分析逻辑**
   - Macro Dashboard / Endogenous Market Structure / Deleveraging-Liquidity-Squeeze Playbook。
   - Evidence Ladder（含证据等级数值映射）。
   - X Frontline Intelligence。
   - Mira Quality Gates。
   - Quant Robustness Gate。
   - LongBridge Data Layer。
   - Decision Compiler。
   - Paper Trading Gate。

6. **输出可执行决策**
   每个候选订单必须有：
   - symbol / side / quantity / limit_price / order_type。
   - thesis：为什么买/卖。
   - signals：哪些信号支持。
   - invalidation：什么情况下证明错了。
   - max_loss_pct：单笔最大容忍亏损。
   - expected_holding_days：预期持有周期。
   - used_skills / used_mcp_tools / source_reports：用于后续归因。
   - `metadata.decision_envelope`：所有 `intent=open` 必须携带 `decision_envelope.v1`，绑定 proposal ID、symbol、strategy、policy hash、compiler source hash、权限、hard veto、as-of 与 expiry。
   - Executor 对缺失、未知、过期、hash 漂移或 `entry_permission=BLOCK/WATCH` 的新开仓 fail-closed；机械 reduce/exit 不受该开仓凭证阻断。

7. **执行前安全门**
   - 运行：`${HOME}/.hermes/scripts/longbridge-paper-gate.sh`
   - 必须满足：`paper_account_gate=pass`、`account_channel=lb_papertrading`、token 在 isolated HOME。
   - 失败：只允许 research，禁止下单。

8. **执行与审计**
   - 运行：`~/.hermes/longbridge-paper-trading/scripts/paper_trade_executor.py --execute --proposals <file>`。
   - 只允许限价单 LO。
   - 先写 journal，再提交订单。
   - 禁止实盘账户、禁止 short、默认禁止加已有持仓。

9. **持仓跟踪**
   - 运行：`~/.hermes/longbridge-paper-trading/scripts/paper_position_snapshot.py`。
   - 输出：`journal/paper_position_snapshots.jsonl`。

10. **复盘与学习包**
    - 运行：`~/.hermes/longbridge-paper-trading/scripts/paper_learning_report.py` 和 `~/.hermes/longbridge-paper-trading/scripts/paper_learning_review.py`。
    - 输出：`reports/*.json` 与 `learning_packets/*.json`。

### Learning Packet Schema Contract

- System A 生成的学习包必须带 `schema_version`，当前唯一自动接收版本：`paper_learning_packet.v2`。
- `paper_learning_packet.v1` 及任何缺失/未知版本只能作为人工历史参考，不进入自动 materiality/conflict gate。
- System B 先验证版本，再读取 summary、materiality 或 upgrade candidates；schema mismatch 必须 fail-closed 并标注 `packet_schema_mismatch`，不得尽力解析。

### Trade Lifecycle Contract

- materiality、calibration、learning report 和 trade review 的样本单位统一为 `unique_completed_trade_lifecycle`。
- ID 规则：`paper:<symbol>:<entry_proposal_id>`；partial exits 共享 entry lifecycle，不能各算一笔。
- `reduce_submitted`、未确认成交、unmatched exit 和未完全退出的 lifecycle 不进入完成样本。
- strategy `diagnostic|paused` 只阻断新 entry，不能阻断机械 exit、fill reconciliation、position snapshot 或复盘。

### 复盘必填字段
每次交易结束、止盈、止损、阶段目标达成或 thesis 失效后，复盘必须回答：

- 当初为什么买？
- 为什么卖/为什么继续持有？
- 哪些判断正确？
- 哪些判断错误？
- 哪些数据有效？
- 哪些信息噪音过大？
- 哪些 skill/MCP 帮助最大？
- 哪些信号需要提高/降低权重？
- 哪些风控规则需要调整？
- 是否产生可反哺 `trading-research` 的规则？

### 学习目标
优化目标不是单一胜率，而是：

- win rate
- average win/loss
- average R multiple
- max drawdown
- thesis accuracy
- data/source effectiveness
- skill/MCP attribution
- turnover/slippage proxy
- exposure discipline

## System B：trading-research 核心分析与能力升级系统

### 目标
让 `trading-research` 保持为核心投研大脑，而不是被模拟仓交易噪音污染。

### 输入
- System A 的 `learning_packets/*.json`。
- 交易关闭后的结构化复盘。
- 重复出现的有效/无效信号。
- 可验证的 source weighting 变化。
- 冲突/失败 case。

### 升级门槛
不得因为单笔交易或单次浮盈浮亏升级 skill。

只有满足以下条件之一，才允许升级 `trading-research`：

1. 至少 5 笔关闭交易暴露出同一类判断缺陷或有效规则。
2. 至少 20 笔关闭交易支持仓位/风控规则调整。
3. 某个数据源/skill/MCP 连续多次证明有效或噪音极大，有明确归因。
4. 新规则能被现有 Evidence Gate / Decision Compiler 接纳，且不冲突。
5. 能写成通用方法，而不是一次性个案补丁。

### 升级流程

```text
learning_packet → materiality gate → conflict check → skill-quality-gate audit → patch trading-research → run validation/audit → update source map/version
```

### 禁止升级
- 单笔成功交易。
- 单笔亏损后的情绪化补丁。
- X/KOL 未验证观点。
- 无法复现的盘感。
- 与现有风控 hard veto 冲突的仓位放大规则。

## 联动文件接口

| 方向 | 文件 | 用途 |
|---|---|---|
| A 内部能力发现 | `state/capability_inventory_*.json` | 每次决策前记录可用 skills/MCP/tools |
| A 决策输入 | `decision_packets/paper_decision_packet_*.json` | 给 agent 的实时决策基线 |
| A 订单审计 | `journal/paper_orders.jsonl` | 验证/提交/拒绝/替换订单日志 |
| A 交易结果 | `journal/paper_outcomes.jsonl` | open/closed outcome 与复盘字段 |
| A 持仓跟踪 | `journal/paper_position_snapshots.jsonl` | 浮盈浮亏与组合风险曲线 |
| A → B 学习 | `learning_packets/paper_learning_packet_*.json` | 给 trading-research 的升级候选输入 |
| B → A 规则 | `trading-research/references/*.md` + `scripts/*.py` | 分析框架、证据门、风控、Decision Compiler |

## Cron 分工

- `LongBridge Demo Paper Learning Cycle` (`7b83f5ea92dd` + `327f90d45676`)：脚本型，静态 cron 拆成两段，只覆盖 US/HK/CN/SG 可能交易窗口：`7b83f5ea92dd` 负责 Asia/HK/CN 日盘与 US 晚间开盘窗口（`*/10 9-11,13-15,21-23 * * 1-5`），`327f90d45676` 负责 US 常规盘中国时间后半夜窗口（`*/10 0-5 * * 2-6`）。每次运行前再用 `~/.hermes/longbridge-paper-trading/scripts/market_session_guard.py` 调 LongBridge 官方 trading days 判断真实交易日与允许时段；非交易日/非交易时段静默退出。有动作或错误才通知。10 分钟是默认平衡点：比 30 分钟更适合持仓监控，又不会像 1-5 分钟那样制造过多 API/日志压力。
- `LongBridge Demo Daily Intelligence Paper Decision Agent` (`0f3a23b24364`)：工作日 21:05 Asia/Shanghai，LLM 驱动，先由 `longbridge_paper_decision_market_guard.sh` 注入 US 官方交易日/预开盘窗口 guard；`allowed=false` 时必须立即停，不抓取、不写 proposal、不执行。`allowed=true` 时再抓取 X/web/news/MCP，执行完整 research → proposal → Demo paper execution → review 流程。该 agent 不得递归创建 cron。
- `Trading Research Daily Self-Optimization` (`94cb8dfe1563`)：每天自检 `trading-research`，只在 materiality/conflict gate 通过后升级 skill。

## 安全边界
- 实盘永不自动下单。
- `trading-research` 本体不执行订单。
- System A 只在 Demo A/C gate pass 后执行模拟订单。
- 所有自动交易必须有 proposal、thesis、signals、invalidation、risk cap、journal。
- 没有来源，不进决策；没有复盘，不算学习；没有 materiality，不升级 skill。

## OKX 的 venue-neutral 扩展（v2.48）

OKX 不改变 A/B 主权边界，而是在两者之间增加两个非权威适配角色：

```text
System B Research
  └─ public/read-only adapter → Evidence + entry_score.v1
                                   ↓
                           Decision Compiler（唯一动作权威）
                                   ↓ gated instruction（本 Skill 不发送）
Independent Execution Engine（未来、默认 Demo）
                                   ↓
Read-only Supervisor → freshness / reconciliation / pause_required
```

- **Research Agent**：只读公共/账户证据，不能访问交易写工具。
- **Monitor/Supervisor**：监控 heartbeat、行情、账户、持仓、订单、成交和对账；只能输出 `pause_required`，不能下单。
- **Execution Engine**：唯一可能拥有 Demo/live 写权限的独立进程；不属于本 Skill，必须另行审批、最小权限和审计。
- **Decision Compiler**：继续是唯一动作语义权威；OKX 监督结果只映射到现有 `data_quality/conflict_ledger/account/liquidity/execution_window`。

不得把 OKX CEX、Wallet/DEX 与 Agent Trade Kit 混为同一执行通道。完整身份、状态和故障契约见 `okx-research-execution-supervision.md`。
