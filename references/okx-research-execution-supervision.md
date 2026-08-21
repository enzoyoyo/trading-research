# OKX 研究与执行监督适配层

## 目的

把 OKX 的公开市场数据、只读账户状态和外部执行回报接入 `trading-research` 的既有 Evidence → Mira → Decision Compiler 主链，同时保持三条硬边界：

1. `scripts/decision_compiler.py` 仍是唯一动作权威；不新增 OKX 专属动作等级或 Compiler module。
2. `trading-research`、对话 Agent 与监督脚本始终 `no_order_execution=true`；不创建、修改或取消订单。
3. 自动化只改善身份核验、可观测性、对账和纪律，不承诺盈利。

本层覆盖公开行情、账户只读快照、策略心跳、订单/成交状态、账实对账和暂停建议。Demo 执行由独立 `~/.hermes/okx-demo-trading` 接收已过门的结构化 bundle；live 执行没有接入。研究 Skill 与对话 Agent 不持有该引擎凭据或调用其写接口。

## 1. 先拆清 OKX 产品边界

不得把以下能力统称为“OKX Wallet API”：

| 能力 | 身份与结算 | 主要接口 | 本 Skill 默认权限 | 关键风险 |
|---|---|---|---|---|
| OKX CEX API V5 | OKX 交易账户、交易所订单簿 | REST + Public/Private WebSocket | public / read-only | 账户权限、订单状态、WS 断线、地区资格 |
| Unified Tokenized Stocks | CEX `SPOT` 产品；USDT 报价；价格敞口，不是底层公司股东权利 | API V5 instruments/ticker/books/candles；账户侧走 CEX | public / read-only | 24/7 与美股现货时段错位、发行人/公司行动、地区限制、短上市历史 |
| OKX Wallet Trader Mode | 自托管钱包内的策略/交易体验 | Wallet/产品专属接口 | 仅建模和只读证据 | 钱包签名、链上资产、gas、授权与合约风险 |
| OKX DEX / OnchainOS | 链上聚合、报价、路由、交易构建 | Web3/DEX API | public / quote-only | chain/token 合约身份、滑点、MEV、交易签名 |
| OKX Agent Trade Kit | 本地 MCP/CLI，包含 market/account 读取与 spot/swap/futures/options/bot 等写工具 | 官方开源 toolkit 封装 CEX API | `--read-only` 且 write deny-by-default | 一个工具包同时含读写工具，不能整体暴露给对话 Agent |

`channel=cex_spot` 与 `channel=wallet_dex` 使用不同身份字段，禁止混装：

- CEX：`inst_id/inst_type/inst_category/state/tick_size/lot_size/min_size/mapping_scope=exact_exchange_instrument_only`；不得携带 `underlying_symbol/chain_id/token_contract/provider`。
- Wallet/DEX：`chain_id/token_contract/provider/underlying_symbol/mapping_scope=exact_chain_token`；不得携带 CEX instrument/increment 字段。
- 两类都必须有 `mapping_verified` 与 `region_eligible`；未知即阻断新开仓。

## 2. Unified Tokenized Stocks 的研究边界

OKX 2026-07-15 官方公告说明：产品以大写 `X` 前缀命名，USDT 报价、24/7 交易；产品提供底层股票或 ETF 的价格敞口，但不代表底层公司所有权或投票权。美国市场时段之外，定价包含最后收盘价与市场估计，因此不能把周末/隔夜价格直接当作底层现货确认。

截至 2026-07-27 的 EEA 公共 API 实测：

- `XMU-USDT`：`SPOT`、`instCategory=3`、`state=live`，公开 ticker/books/candles 可读。
- `XSKHY-USDT`：`SPOT`、`instCategory=3`、`state=live`，公开 ticker/books/candles 可读。

以上只证明公共产品端点当时存在且 live，不证明：

- 任一具体账户或地区有资格交易；
- 自然语言“美光/海力士”与某产品的法律/发行人映射已自动成立；
- 产品流动性足够；
- 用户应当买入。

每次研究都必须重新调用 instruments 端点核验 `instId`、类型、类别、状态、最小数量、tick/lot size、上市时间，并记录 `fetched_at`。营销页、搜索结果或名称相似不能替代 API 身份核验。

## 3. 公共市场快照

入口：

```bash
python3 scripts/okx_public_snapshot.py XMU-USDT --site eea
python3 scripts/okx_public_snapshot.py XSKHY-USDT --site eea
```

该脚本只调用公共 API V5：

- `GET /api/v5/public/instruments`
- `GET /api/v5/market/ticker`
- `GET /api/v5/market/books`
- `GET /api/v5/market/history-candles`

输出 `okx_public_market_snapshot.v1`，包含：

- 产品身份和上市年龄；
- 可直接交给 `entry_score.py` 的 `product_identity` 投影：精确交易所 instrument 已核验，`mapping_scope=exact_exchange_instrument_only`，地区资格保持 `null`；
- ticker、盘口中点、spread bps、top-5 双边名义深度；
- 已完成 1D/4H K 线数量、1 日/5 日可观察收益；
- `history_limited`、`credentials_used=false`、`public_endpoints_only=true`、`no_order_execution=true`。

纪律：

1. `state != live`、产品不存在、返回码非 `0`、关键字段为空或数值非法时 fail-closed。
2. K 线 `confirm=0` 只计为未完成，不进入历史收益基准。
3. 失败时不得把价格、仓位、余额或深度写成 `0`；监督层应保留最后有效值并标 stale。
4. `history_limited=true` 时不得做长期技术统计或声称历史稳健性。
5. 公共快照是 Evidence 输入，不是入场分数或订单指令。
6. OKX EEA books/history-candles 当前不回显 `instId`，identity 由固定 endpoint 请求参数与该次响应绑定；若响应未来主动回显 `instId`，必须与请求完全一致，否则 fail-closed。
7. 输入数值 finite 不代表派生数值安全：midpoint 必须用不会先执行 `bid + ask` 的稳定公式；per-level notional、top-five 累计 notional、1D/5D return 每一步算术后都必须重验 finite，禁止输出 JSON `Infinity/NaN`；正常值保留既有小数位展示，但正 midpoint/notional 若会被 round 成伪 `0.0`，必须保留原有限值。

## 4. 可解释 0–100 入场评分

入口：

```bash
python3 scripts/entry_score.py entry-score-input.json
```

`entry_score.v1` 是报告展示，不是第二套 Decision Compiler，也不提供正向 readiness action。输入必须带经过验证、可追溯且未过期的 `product_identity`；五组因子均为 0–5，并沿用固定权重：

| factor_id | 权重 | 从既有研究链映射 | 典型证据 |
|---|---:|---|---|
| `technical` | 20% | market_data / execution_window / social-technical entry gate | 趋势、关键价、相对强弱、完成 K 线 |
| `capital_flow` | 20% | participant_flow / endogenous_structure / liquidity | 主动资金、账户流、南向/北向、成交结构、订单流 |
| `sentiment` | 20% | attention / x_frontline / prediction prior / crowding | 注意力、叙事、拥挤、情绪极值与反转信号 |
| `fundamentals` | 25% | fundamentals / filing / supply-chain / cycle clocks | 财报、现金流、HBM/DRAM 周期、盈利预期、估值 |
| `macro` | 15% | macro / risk_regime / event proximity | 利率、美元、流动性、政策、地缘与事件窗口 |

```text
entry_score_100 = Σ(score_0_5 × canonical_weight × 20)
```

输出必须包括：

- `score_version`、产品身份、`as_of`；
- 每个因子的原始分、权重、0–100 分贡献、理由、EID、`observed_at/stale_after`；
- coverage、冲突、扣分/阻断项、置信度口径、阈值语义；
- `suggested_module_signals`：完整无阻断时为空；只有数据缺口、冲突、地区资格未知/不合格时才映射到现有 `data_quality`、`conflict_ledger`、`account` 阻断信号。

发布门：

- 五因子任一缺失、过期、无 EID、时间非法/未来、存在 API key、OKX auth header/signature 等敏感字段，或产品身份未验证/不可追溯/已过期：`status=insufficient_data`，`entry_score_100=null`。
- unresolved material conflict：可保留展示分，但 `unresolved_conflict=true`、入场 permission 为 `BLOCK`，并生成 `conflict_ledger` 认知型 hard veto。
- `region_eligible=null`：可保留研究展示分，但追加 `data_quality` hard veto；`region_eligible=false`：追加 `account` hard veto。两者均不得入场，且都只能 `holding_directive=HOLD`，不得仅凭资格字段机械退出已有持仓。
- `complete` 只表示算术与证据契约完整，不等于可交易；此时 `entry_permission_ceiling=null`、`suggested_module_signals=[]`、`compiler_effect=none`，分数不能抬高上游 action level、仓位上限或绕过 hard gate。

展示区间是描述性范围：`80–100=L2–L3`、`60–79=L1`、`40–59=L0`、`<40=L4–L5 复核区`。最终动作仍以 `scripts/decision_compiler.py` 为准；低分也不能单独机械卖出，已有持仓须结合市场风险型和认知型 veto 语义。

## 5. 监督快照与账实对账

入口：

```bash
python3 scripts/okx_execution_supervisor.py okx-supervision-input.json
```

输入契约 `okx_execution_snapshot.v1` 只接受已经由独立适配器采集的结构化快照；脚本本身不读凭据、不连私有接口、不发订单、不创建 STOP 文件。

建议状态枚举：

- `market_data_status`: `live | stale | unavailable`
- `strategy_status`: `running | paused | stopped | faulted`
- `order_status`: `intent | submitted | acknowledged | partially_filled | filled | canceled | rejected | unknown`
- `position_reconciliation_status`: `matched | drift | pending`
- `credential_scope`: `none | read_only`（仅描述 Supervisor 投影接口的数据权限）；Demo 执行源可另写 `source_credential_scope=read_trade`，但必须同时 `snapshot_projection_read_only=true`
- `execution_permission`: `disabled | paper | live_approved`（仅描述独立外部执行引擎）

启动/持续同步顺序：

1. REST 拉 instrument/account/positions/open orders/fills 基线。
2. live 强制私有 WS 订阅 account/positions/orders；fills 频道可用时一并订阅。Demo 首阶段允许 `private_transport=rest_polling`，但必须 `rest_polling_healthy=true`、间隔 `<=30` 秒并保留 warning；该例外不得外推到 live。
3. WS 断线后先标 stale，不清零；重连后必须做 REST 全量对账，再恢复 fresh。
4. 本地账本保存 intent、client order id、审核轨迹；交易所回报是外部成交事实。
5. position 必须按 `instrument_id/quantity` canonical 行对账；CEX spot 与 Wallet token 的 quantity 必须是有限且非负数，零值只接受正零，IEEE/string/原始 JSON 数字负零（如 `-0.0`、`"-0"`、raw `-0`）属于 malformed state；CLI JSON 边界必须在标准解析器丢失符号前保留 raw `-0` 的负 sign bit；超大整数转浮点发生 overflow 时必须按 malformed row 阻断并输出结构化错误，不能 traceback 或回显原值；同一侧不得出现重复 instrument 行，双方相同的负值、负零或相同重复行都不能被聚合后判 matched。open order 必须按 `order_id/instrument_id/state/quantity` canonical 行对账；fill 必须按 `fill_id/order_id/instrument_id/side/quantity/price/fill_ts`（及可选 fee 字段）canonical 行对账。同 ID 明细差异、App 手工交易、漏单、重复单、数量差异或未知 state 均进入 drift/pending。
6. `mode=live` 还必须由外部快照提供独立审批、IP 白名单、禁提现、凭据已轮换四项布尔确证；Supervisor 只读这些状态，不读取凭据值。

`scripts/okx_execution_supervisor.py` 输出：

- normalized feeds 及 `age_seconds/stale/last_good_value`；`last_good_value` 只投影 market/account/orders 各自显式允许字段，并强制有限数值、非负计数、RFC3339 时间或短币种码类型，不透传任意上游元数据；非法或转换 overflow 的允许字段只输出字段级 blocker，不回显原值；
- position/order/fill mismatch；显式 `position_tolerance` 必须严格小于 instrument lot size，大于或等于一个 lot 时策略本身阻断并回退到浮点 epsilon，同 ID 订单/成交明细漂移分别单列 `order_detail_mismatches/fill_detail_mismatches`；
- execution item-level `sCode/sMsg` 汇总；
- `pause_required` 与独立的 `pause_effective`；
- canonical `position_reconciliation_status=matched|drift|pending`、`order_state_status=known|unknown|partial_failure`；
- `connection.private_ws_connected/private_transport/rest_polling_healthy/poll_interval_seconds/rest_baseline_complete` 与白名单化 EntryScore 投影；
- 健康状态不发 ModuleSignal；只有阻断项才复用既有 `data_quality/conflict_ledger/account/liquidity/execution_window`，全部标 `tighten_only/cannot_raise_upstream`；
- `new_entries_allowed`、`analysis_only`、`market_analysis_allowed`；
- `no_order_execution=true`。

## 6. fail-closed 条件

以下任一条件都必须暂停新开仓：

- 私有模式产品身份或地区/账户资格未知；public 模式可保留 `region_eligible=null` 做市场分析，但不可形成账户级许可；
- 关键行情或账户 feed stale/unavailable；
- snapshot/feed/heartbeat 使用未来时间戳；
- live 私有 WS 断线；Demo 未满足 ≤30 秒健康 REST polling 或 REST 全量对账未完成；
- 策略无心跳、stopped/faulted；
- order state unknown；
- 持仓数量非法（包括 spot/token 负数）、持仓/订单/成交账实不一致；
- 批量回报 item-level 部分失败；
- spread 超策略上限、流动性不足；
- account/portfolio hard redline；
- 入场评分缺失/insufficient/投影非法、五因子 coverage/固定权重公式不一致、置信度非 high、Conflict Ledger 未解决或 `entry_permission_ceiling=BLOCK`；
- 急停已激活；
- 凭据权限、IP 白名单或审批不合格。

`pause_required=true` 只是 `demo/live` 的监督结论；只有独立 Supervisor/Execution Engine 真正暂停后才能写 `pause_effective=true`。`public/read_only` 没有控制面，固定以 `new_entries_allowed=false` 表达阻断，不得声称需要或已执行暂停。研究层不得把“建议暂停”写成“已暂停”。

## 7. 独立 Demo Execution Engine（已接入）

工作区：`~/.hermes/okx-demo-trading`。它不是本 Skill 的子模块，而是权限隔离的 Demo CEX SPOT runtime；没有 live 模式、live host、提现能力或对话 Agent 写入口。

单向 handoff：

1. 研究层保存 `okx_demo_research_bundle.v1`，包含原始 EntryScore 请求、原始 Decision 请求、研究 run id、过期时间、最大入场价、止损价与请求名义金额；不得包含任何凭据字段。
2. 外部 runtime 不信任持久化分数或 envelope，而是按 `config/trusted_research.json` 的 SHA-256 固定值重放 `entry_score.py` 与 `decision_compiler.py`；hash 变化、编译失败、score 不完整、Decision 非 strict pass、资格/身份/时间/证据异常都阻断。
3. 提交同时要求 `--submit`、本地 `ARMED`、Demo 三要素、`x-simulated-trading: 1`、固定 `https://openapi.okx.com`、账户 instrument gate、flat/无挂单、fresh ticker、点差/价格/止损/名义金额/日频上限全部通过。
4. `ARMED` 只能由 fresh flat gate 创建；STOP 立即删除 `ARMED`。STOP 只暂停新开仓，生命周期核对和风险退出继续运行。
5. 每笔订单先 fsync `order_intent`，再提交 limit order；使用 `clOrdId` 与 `expTime`。transport unknown ack 只按 client id 查询一次，不盲重试；仍未知则创建 `PAUSE.json`。
6. 入场单超时先写 cancel intent，再撤单并确认；部分成交转为受管持仓。止损触发只提交数量受限的卖出 limit；退出单超时撤单后暂停，不自动改价连发。
7. venue positions/orders/fills 与本地 trade state 全量对账；App 手工交易、foreign order、数量/明细漂移都会创建 PAUSE，禁止新开仓。
8. 凭据仅允许完整环境变量或 owner-only、group/other 无权限的 `~/.hermes/.env`；Demo 与 live 不混用，旧泄露凭据永久不可用。

主要入口：

```bash
cd ~/.hermes/okx-demo-trading
python3 scripts/compile_proposal.py /path/to/research-bundle.json
python3 scripts/run_demo_gate.py
python3 scripts/arm_demo.py
python3 scripts/run_demo_order.py /path/to/research-bundle.json --submit
python3 scripts/run_supervision.py --instrument XMU-USDT --loop
python3 scripts/runtime_control.py stop --reason user_emergency_stop
```

当前是单标的/单规则 pilot：`max_open_positions=1`、禁止 pyramiding、每笔与总敞口双封顶；先积累完整 Demo 生命周期样本，再讨论多标的或 live。live 仍需另行设计、审批和对抗审查，不能从 Demo 自动升级。

## 7a. 底层锚定与基差校验（runtime 0.3.0）

tokenized wrapper 的价格必须与底层股票交叉校验，但**不能拿常规收盘价直接对比**。wrapper 是 24/7 定价，底层美股在收盘后仍有盘前/盘后/隔夜成交；用错时点的参考价会算出伪折价。

2026-07-28 实测（同一份 wrapper 快照，`XMU-USDT` mid ≈ 855）：

| MU.US 参考价 | 数据年龄 | 基差 |
|---|---:|---:|
| 盘前 | 1.7 秒 | `+17 bps` |
| 隔夜 | 2.2 小时 | `+10 bps` |
| 盘后 | 10.2 小时 | `−339 bps` |

因此选择规则固定为 **时间距离 wrapper 观测时刻最近的参考价**（`selection_reason=closest_observed_at_to_wrapper_observation`），并同时输出全部参考价的基差与各自 `age_seconds` 供人工复核。

底层数据源全部免费公开、零新增凭据：

- LongBridge CLI `MU.US`：一次调用返回 regular / pre_market / post_market / overnight 四段价格及各自时间戳。
- Yahoo chart：`000660.KS`（SK 海力士本土）、`SKHHY`（OTC ADR）、`KRW=X`（USDKRW）。请求必须显式清空本机 HTTP/HTTPS 代理，否则连接失败。

```bash
cd ~/.hermes/okx-demo-trading
python3 scripts/underlying_anchor_check.py --instrument XMU-USDT
python3 scripts/underlying_anchor_check.py --instrument XSKHY-USDT
```

输出 `underlying_anchor_snapshot.v1`。纪律：

1. `XSKHY-USDT` 的映射倍数尚无发行文件证实：对 ADR 隐含 `9.22`，对本土股折美元隐含 `0.129`，两者矛盾。固定 `mapping_status=unverified`、`basis_status=unknown`、blocker `underlying_mapping_unverified`，`references` 必须为空，**不得输出任何基差判断**。
2. 底层取数失败、字段缺失、时间戳缺失或超过配置年龄一律 `basis_status=unknown` 加 blocker；禁止写 0，禁止表述为「基差正常」。
3. 严格 tighten-only：只能产生阻断新开仓的 blocker，不能提高 EntryScore、放宽任何上限、复活 L0 或生成正向 ModuleSignal。输出恒带 `tighten_only=true`、`cannot_raise_entry_score=true`、`cannot_relax_limits=true`、`read_only=true`。
4. 阈值与候选源写在 runtime 的 `config/underlying_anchors.json`，不在本 Skill 维护。

## 7b. 五阶晋级阶梯与阶段化敞口

runtime 从「离线通过 → 可武装」的两态改为五阶，避免中间没有台阶：

| 阶段 | 含义 | 通过判据（全部基于 journal 真实事件） | 单笔 / 总敞口 |
|---|---|---|---:|
| S0 | 离线验证 | 全量测试、pins、secret audit 有通过记录 | `0 / 0` |
| S1 | 只读连接 | 私有 gate 至少通过一次，证明 Demo 环境、账户资格与产品可交易 | `0 / 0` |
| S2 | 干跑观察 | ≥5 个不同 UTC 日期有 decision 记录，且无 unexplained 状态 | `0 / 0` |
| S3 | 单次闭环 | ≥1 个完整生命周期（下单→成交或撤单→对账 flat）＋≥1 次重启后状态正确 | `50 / 50` USDT |
| S4 | 连续运行 | ≥20 个不同 UTC 日期有记录，且 manual_intervention 为 0 | `100 / 200` USDT |

```bash
python3 scripts/promotion_status.py            # JSON
python3 scripts/promotion_status.py --render   # 生成 PROMOTION.md
```

纪律：

1. 判据是**证据**不是时间：没有对应 journal 事件就不晋级，评估器为纯只读，不写控制文件、不下单。
2. 实际生效敞口恒为 `min(阶段上限, policy 硬顶)`。对抗测试确认把 `promotion.json` 的阶段值改成 `999999`，实际仍被钳制在 policy 的 `100 / 200`。阶段只能收紧，永远不能放宽。
3. 账实 drift 必须分类为 `spec_gap`（规格缺一条规则，应回写合同）或 `infrastructure_fault`（真实故障）。未分类 drift 计为 `unclassified` 并阻断 S4——「忍不住手工干预」本身就是规格缺口的信号，不是纪律问题。

## 7c. 单向学习回路与静默看门狗

Demo lane 不是断头路：完成的生命周期必须回流研究侧校准，否则模拟盘无法反推优化。

```bash
python3 scripts/emit_learning_packet.py        # okx_demo_learning_packet.v1
python3 scripts/heartbeat_watchdog.py          # 健康时 stdout 为空
python3 scripts/daily_line.py                  # ET 单行中文摘要
python3 scripts/render_strategy_contract.py --check   # 人话合同漂移自检
```

纪律：

1. learning packet 固定 `materiality_eligible=false`、`read_only=true`，与 paper 校准同一分桶；证据不足写 `insufficient_evidence`，禁止编造预测概率、盈亏或反事实结果。
2. 反事实归因沿用既有五分量词汇：`missed_signals`、`noise_trades`、`early_exit`、`late_exit`、`overtrading`；五项之和必须等于 shadow 与 real 的差额，对不上按 data-quality gap 处理，不得强行配平。
3. 消费保持**人工**：packet 只作只读证据喂给 `paper_outcome_calibration_feed.py` 与 `calibration_scorecard.py`，绝不自动改写研究结论、`policy.json` 阈值或晋级判据。自动改写会污染证据链。
4. 看门狗健康时标准输出为空（退出码 `0`），告警 `1`，无法判定 `2`；不引入 SMTP 凭据面，也不自建 cron，是否接入定时由用户决定。

## 8. 面板和告警

面板只读、默认 30 秒刷新，展示时间明确标注 `ET`，至少包含：

- 行情与 freshness；
- 策略 heartbeat/status；
- 持仓、订单、成交与 reconciliation；
- 当前入场评分、因子贡献、冲突与置信度；
- hard gates、`pause_required/pause_effective`；
- 连接状态和最近一次 REST baseline。

禁止：下单按钮、策略参数编辑、账户权限变更、将面板直接暴露公网。除独立急停通道外，面板不能改变交易行为。

机器实现：`scripts/okx_monitor_dashboard.py` 消费已编译的 `okx_execution_supervision.v1`，原子写出 `index.html` 与 `supervision.json`：

```bash
python3 scripts/okx_monitor_dashboard.py \
  --snapshot /path/to/supervision.json \
  --output-dir /path/to/local-dashboard
```

- 页面只通过相对路径每 30 秒读取 `supervision.json`；不访问远程 API，不打开 WebSocket。
- 时间显示统一转换为 `America/New_York`（ET）；移动端降为单列。
- 基础静态面板无表单、按钮、可编辑字段、策略参数或订单控件。独立 companion server 可增加两个同源控件：唯一写动作 `/api/stop`，以及无凭据、无执行函数的只读状态问答；不得增加订单或参数入口。
- companion server 只接受 loopback、RFC1918、IPv6 ULA 或 Tailscale CGNAT 地址，拒绝 wildcard/public bind；默认 `127.0.0.1`，不得暴露公网。
- 页面读取失败时保留浏览器中已展示的 last-good 值，提示阻断新仓，不清零。
- Dashboard 投影必须验证模式矩阵：`public=(credential_scope:none, execution_permission:disabled, private_ws_connected:null, strategy.status:not_available)`；`read_only=(read_only, disabled)`；`demo=(read_only, paper)`；`live=(read_only, live_approved)`。任何模式都不能仅凭伪造 `new_entries_allowed=true` 绕过 exact channel identity、market/account/orders freshness、orders REST baseline、EntryScore 完整五因子与固定权重重算、连接、策略、账实对账、blocker 和 pause 状态；`entry_permission_ceiling` 必须保留在 canonical 投影并重验。live 还必须在发布前重验四项 safety attestation，验证后不把 attestation 写入页面 JSON。

## 9. 半导体震荡阶段的应用

研究 XMU/XSKHY 等候选产品时，先分析底层公司/行业，再分析 tokenized wrapper：

1. **底层基本面**：HBM/DRAM/NAND 价格、供需、capex、库存、客户集中、先进封装、盈利预期和估值。
2. **周期三时钟**：基本面、情绪、估值是否错位；AI 龙头进入震荡时重点看二阶导数与财报反应质量。
3. **拥挤与结构**：主题 ETF/被动流、资金定位、波动/期权结构、leader gap、相对强弱。
4. **wrapper 风险**：24/7 场外时段估值、USDT 计价、发行人/公司行动、上市历史、价差、深度和地区资格。
5. **仓位纪律**：缺账户快照不报具体数量；高波动、短历史、宽价差、低深度或 wrapper/底层价格偏离时只收紧。

自动化不能证明盈利，只能减少错标产品、使用过期行情、重复下单、手工漂移未发现和状态不确定时继续开仓。

## 10. 验收场景

必须通过以下场景：

1. 策略进程停止/心跳过期 → `pause_required=true`、新仓阻断。
2. App 手工买入造成 position drift → `conflict_ledger` hard veto、新仓阻断。
3. OKX/执行连接中断 → 保留 last good value、标 stale，禁止显示 0。
4. 私有 WS 恢复但 REST baseline 未完成 → 仍阻断。
5. 批量回报一项失败 → partial failure，订单状态不可假定完成。
6. 产品身份未知/混用 CEX 与 Wallet identity → 阻断。
7. public-only 模式 → 可做市场分析，不可给账户级执行许可。
8. 手机端面板 → 核心状态无需横向滚动，告警与对话可读；不得出现下单控件。
9. 持仓相差恰好一个 lot → 始终判 drift；策略显式容差必须严格小于 lot size，大于或等于一个 lot 时策略本身阻断。
10. 同一 `order_id` 的 instrument/state/quantity 漂移 → `order_state_status=unknown`、新仓阻断。
11. ticker 与 books 的 BBO 不同 → `last/24h` 仍取 ticker，但输出 `bid/ask/spread` 必须与 top-5 depth 同源于 books，不拼接两个时点的盘口；books 任一发布档位 size 必须严格大于 0，bids 必须严格降序、asks 必须严格升序，不能从零量或乱序档位发布伪 BBO，也不能靠更深档正 notional 掩盖零量档；midpoint、单档/累计 notional、return 等派生结果只要变成非有限数就整份 snapshot fail-closed。
12. 同一 `fill_id` 的 order/instrument/side/quantity/price/timestamp 任一漂移 → `order_state_status=unknown`、新仓阻断。
13. EntryScore 缺失、insufficient、冲突、公式/范围非法或带 `entry_permission_ceiling=BLOCK` → demo/live `new_entries_allowed=false`；Dashboard 不能通过丢字段恢复权限。
14. CEX identity 带 Wallet-only `underlying_symbol`，或 Wallet identity 缺 chain/token/provider/underlying → 三层 fail-closed。
15. 普通原因文本中嵌入 RSA/EC/OPENSSH/DSA PEM header（含 NFKC 兼容字符）→ 输入阶段拒绝，不得回显或落盘。
16. CEX spot / Wallet token 双方 position 都携带相同负 quantity → reconciliation 仍必须 `pending/unknown` 并阻断新仓。
17. CEX spot / Wallet token 双方 position 都携带 IEEE float `-0.0` 或字符串 `"-0"` → 必须保留 sign-bit 语义并按 malformed state 拒绝，不能 canonicalize 成正零后判 matched。
18. Supervisor CLI 输入的原始 JSON 数字字面量 `-0` → `_load_payload()` 必须把该 lexical negative zero 保留为带负 sign bit 的数值，并在 CEX/Wallet position 对账前拒绝；普通 raw `0` 仍保持合法正零。
19. 公开行情、EntryScore、Supervisor position/last-good、Dashboard canonical projection 或 Decision Compiler multiplier 输入 400 位可解析整数 → 各自必须按 invalid numeric/invalid score/malformed row/invalid projection/contract error fail-closed；CLI 必须返回结构化 JSON，不得 traceback、写入面板或把异常值回退成中性乘数。EntryScore/Supervisor/Dashboard 的输入错误 exit 1；Decision Compiler 成功生成 `strict_failed/legacy_failed` 阻断 envelope 时保持既有 exit 0，只有无法生成 envelope 的加载/解析异常 exit 1。

输入敏感键在 EntryScore、Supervisor、Dashboard 三层均按 Unicode NFKC 后的 canonical key 递归拒绝；点号、空格、连字符、下划线、camelCase、全角兼容字符和任意 `OK-ACCESS-*` 变体不得绕过。NFKC 后仍含非 ASCII 字母数字的 schema key fail-closed，阻断 Cyrillic/Greek 同形绕过。三层同时先对字符串值做 NFKC，再拒绝任意位置嵌入的高置信 prefixed key、JWT、Bearer、AWS access key 与 generic/RSA/EC/OPENSSH/DSA PEM 私钥 header，不能只做整串 full match。公共 `token_contract/token_address` 仍作为产品标识保留。Dashboard 写盘前只保留页面实际消费的 canonical 字段，并对 identity、last-good、五因子公式/ceiling、状态、标识符与 blocker 重新做类型/格式投影，未知 metadata 不落盘。

## 11. 来源锚点

访问日期均为 2026-07-27：

- OKX API V5：<https://www.okx.com/docs-v5/en/>
- OKX Unified Tokenized Stocks 公告：<https://www.okx.com/en-us/help/okx-to-list-unified-tokenized-stocks-for-spot-trading>
- OKX Agent Trade Kit：<https://github.com/okx/agent-trade-kit>，审计固定提交 `ed431ba12e7eec4e6ef8222109a1f3bcda95599c`。

Agent Trade Kit 只作适配边界参考：固定提交同时暴露只读与写入工具，写工具有 `isWrite=true` 元数据；本 Skill 未复制其 TypeScript 代码、配置、凭据、日志或交易实现。
