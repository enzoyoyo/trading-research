# Universe Governance

## Purpose

System A (LongBridge paper-trading automation, `${HOME}/.hermes/longbridge-paper-trading`) runs a two-layer trading universe (Core + Probation) and a discovery pipeline that can promote candidates into it. This document is System B's **read-only governance contract** over that process: it defines health thresholds, an audit duty, and the promotion gate — so research can catch drift without ever writing to System A's config, journal, or state. `trading-research` has no execution authority here; it only reads and reports.

Source of truth (read-only, never modified by this skill):
- `~/.hermes/longbridge-paper-trading/config/paper_policy.json` — `universe_core`, `universe_probation`, `universe_refresh`.
- `~/.hermes/longbridge-paper-trading/journal/shadow_ledger.jsonl` — shadow (hypothetical) samples used for `veto_quality` / `discovery_hit_rate`.
- `learning_packets/*.json`'s `paper_learning_packet.v2` schema, `universe_health` block.

## Two-layer universe (as implemented in System A)

- **Core**: pre-committed symbols, full sizing (`risk_per_trade_pct` at policy default). Not cherry-picked from backtests.
- **Probation**: populated by System A's `universe_refresh.py` discovery pipeline (`gen_top_movers`, `gen_anomaly`, `gen_screener`, `gen_sector_rotation`, `gen_earnings_calendar`, `gen_options_flow`, `gen_smart_money`, `gen_x_themes`). Reduced sizing, time-boxed holding (`max_holding_days`), auto-expires (`auto_expire_days`) if it never graduates.

## Health threshold table (`universe_health`, learning packet v2)

| 指标 | 健康 | 警戒/违约 | 说明 |
|---|---|---|---|
| `coverage_30d_pct` | > 65 | 警戒 < 50 | 过去 30 日有信号评估记录的 universe 占比；过低说明大量标的被冻结、发现管线没在真正扫描 |
| `top1_order_share` | < 0.25 | 违约 > 0.40 | 单一标的占全部订单的比例；过高说明信号源仍集中在少数高波动票（donchian 锚定问题的直接症状） |
| `order_hhi_30d` | — | 警戒 > 0.15 | 订单集中度 HHI；和 `top1_order_share` 一起判断分散是否只是账面好看 |
| `probation_admitted_30d` | 目标周 3–8 | 低于目标周期 = 发现管线可能停摆；持续超出目标周期上限 = 检查流动性/去重门是否形同虚设 | 每周新纳入 probation 的候选数 |

任一指标进入警戒/违约区间，System B 的周报告须在结论里显式标注该指标名与数值，不得只报告合格项。

## 审计条款（每周执行）

System B 每周 digest（`learning_digest.py`）必须核查：System A 写入的每一条 `universe_change` 事件（记录在 learning packet 里）是否都带 `evidence_ref`。

- 全部带 → 正常放行。
- 任一条缺失 `evidence_ref` → 记 `governance_gap`，在当周 digest 里显式列出缺失的事件（symbol + 时间戳），不得静默跳过或替 System A 补造证据。

## 升 Core 条款（红线，System A 不得自我豁免）

Probation 标的只有同时满足以下两条才允许升入 Core：
1. 通过 System A `quant/backtest.py` 的 robustness gate（walk-forward、成本、容量、回撤、no-lookahead 检查——见 `references/source-reliability-policy.md` 关于回测结论的证据门）。
2. 21:05 日报 agent 对该标的重新跑一次回测并确认。

System B（本 skill）对升 Core 决策**只有只读审计权，没有执行权**：可以在 digest 里指出"某标的满足毕业统计门槛但未见回测门记录"这类审计发现，但不得建议绕过、代跑、或伪造回测确认来加速升级。发现管线本身也只允许写 `universe_probation`，永远不能直接写 `universe_core`——这是 System A 侧的红线，System B 审计时以此为验收基准。

## 影子指标定义

- **`veto_quality`**：被否决样本的平均假想收益（shadow_ledger 里 `sample_type` 为否决/未入场类的样本，按其后续走势算出的假想收益均值）。**为负 = 否决得好**（说明否决对了，避开了亏损）；为正且较大 = 否决可能过严，正在放过有效机会。
- **`discovery_hit_rate`**：发现管线候选在被发现后 T+20 交易日跑赢基准（SPY 或对应市场基准）的比例。用于判断发现管线本身的候选质量，与"是否被采纳交易"无关——即使候选没被选中下单，也计入命中率统计（因为 shadow ledger 会为未入场候选保留假想跟踪）。

两个指标当前在 `universe_health` 中可能为 `null`（样本量不足时），此时 digest 只能报告"样本不足，暂不下结论"，不得用外推或臆测数值填充。

## 与 relationship_graph.py 的联动

`scripts/relationship_graph.py build --universe-file <path>` 支持两种输入：
- 纯文本文件（每行一个 symbol，`#` 开头为注释）——原有行为不变。
- System A 的 `paper_policy.json`——脚本会自动识别 JSON 结构并读取 `universe_core` + `universe_probation.symbols`，两个字段任一缺失则忽略该层，不报错。字段缺失不代表 System A 出错,只代表该层暂时为空（例如 probation 刚清空）。

用法示例（只读，不联网，用于验证 universe 解析）：
```bash
python3 scripts/relationship_graph.py build \
  --universe-file ~/.hermes/longbridge-paper-trading/config/paper_policy.json \
  --list-only
```

不传 `--universe-file` 时行为完全不变（回退到脚本内置的 11 个种子标的），这样两套系统共用同一份宇宙定义，消除"System A 已经在交易某标的，但 System B 的关系图谱里没有它"这种漂移。

## 与现有框架衔接

- 证据分级、来源可信度按 `references/evidence-ladder.md` 与 `references/source-reliability-policy.md` 执行；本文件只新增 universe 特有的健康阈值和审计条款，不重复定义证据等级。
- Portfolio Risk Gate（`references/portfolio-risk-gate.md`）管的是账户级仓位风险；本文件管的是 System A 交易标的池本身的健康度和治理条款，两者正交，不重叠。
- `no_order_execution`：本文件全程只读引用 System A 数据，不生成、不修改、不建议绕过任何 System A 的写入路径。
