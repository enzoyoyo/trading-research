# Overnight Ensemble Ranker · 隔夜集成投票排序层

## 目的

把「今晚一篮子美股里，哪一只最值得隔夜做多」这个**横截面选择**问题，单独抽出来，用一个可审计的集成投票排序解决。

已有的 `US Close-to-Open Execution Overlay` 回答的是**单票纵向**问题——「这一只值不值得隔夜拿」。它不回答**横向**问题——「我的 universe 里今晚谁排第一」。本层补的就是这个缺口：对 universe 做集成打分→排序→只取头部，再交给 Close-to-Open overlay 和 Decision Compiler 逐一裁决。

这不是新交易系统，也不是新动作模块。它是 `grok_web` / `prediction_market_prior` 同一档纪律的**发现/排序先验**：只能改变 watch priority 和候选顺序，自身 `position_multiplier=0.0`，不复活 L0、不提高 action level、不提高 position cap。

## 来源与逆向拆解（Balder「Opus Picks」, 2026-06）

> 多源核对铁律：以下是从 X 账号 `@Balder13946731`（"Balder's Opus Picks"）公开帖与订阅卡片截图逆向出来的**方法结构**，不是已验证的盈利系统。该账号自述运行成本高于订阅费、互动量极低、**未公开任何带成本/walk-forward 的实盘 P&L**。因此本层只吸收他的**结构**，所有预测数字一律按 `hypothesis` 处理，必须过 `Quant Robustness Gate` 才能参与排序权重。

| 他做的事（原文证据） | 逆向出的机制 | 技能里对应层 |
|---|---|---|
| "Buy ~3:50 PM ET, sell ~9:40 AM ET tomorrow" / 只买第一名的隔日 call、收盘买开盘卖 | 隔夜 close-to-open 执行结构 | **已有** `us-close-to-open-execution-overlay.md` |
| "ranked by ensemble vote"，输出 `# / TICKER / VOTE / PREDICTED% / SIDE` | 横截面集成投票排序 + 投票分（agreement）与预测幅度（magnitude）两个独立轴 + 方向（CALL/PUT）| **本层（新）** + `calibration_scorecard.py` |
| "不是看指标的系统，Opus 24h 动态分析期权、交易订单、事件、momentum、影响力大的 KOL，动态调权" | 多因子集成：期权结构 + 盘口/收盘竞价单流 + 事件邻近度 + 动量/相对强度 + KOL 线索，权重随 regime 调整 | gamma=`options_gamma.py`、breadth=`dispersion_crowding.py`、事件=`event_reaction_journal.py`、KOL=`x-frontline-intelligence.md`（仅线索） |
| "再加息环境下要看贴现的是谁"、不抄底 AMZN/GOOG、买存储+CPU | 利率/折现率 regime 过滤 + 板块轮动选先复苏、韧性强的子板块 | **已有** `capex-cashflow-duration-rotation.md` + `leverage-crowding-dispersion-playbook.md` |
| "选流通性好的期权"、"开盘 spread 大没卖成功"、"不要贪不要急"、"君子不立危墙之下"、事件前可降杠杆 | 流动性/价差门 + 成本纪律 + 事件降仓 | **已有** Close-to-Open overlay 的 cost/liquidity gate + `risk_regime` |

### v2.26 补充：2026-06-24 订阅卡片新增的结构细节

> 同一账号 2026-06-24 的「Tonight's Picks」卡片比 v2.24 当时看到的多出几处可操作机制，按同样纪律吸收（结构吸收、数字按 hypothesis、KOL/政治成分封顶）。

| 他做的事（原文证据） | 逆向出的机制 | 技能里对应层 |
|---|---|---|
| "tinted rows clear the conviction floor (\|total vote\| ≥ 4) = tradeable, dim rows below floor (no edge). 13 clear it." / 全名单展示但只有过阈值的行可执行 | 投票分有最低**信念阈值**：低于阈值=无 edge=不可交易（不是只排后面）；阈值之上才进可交易集 | **本层（v2.26 新增）** `conviction_floor` gate |
| 独立的 "OPTION 💰" 列（小负 %，与 PREDICTED 分开） | 期权腿的成本/损耗 hurdle（theta + spread）；标的预测涨幅必须先盖过它 | **已有** Close-to-Open overlay 的 `estimated_cost_bps`/net_return；本层只对标的建模，期权腿单独由成本/流动性门裁决 |
| "国会山股神已经买入 \$INTC \$UBER"（Pelosi STOCK Act 披露），且 INTC 同时在当晚 picks #3 | 国会/政治交易披露作为一个**情绪/持仓**投票成分 | **本层（v2.26 新增）** `political_disclosure`——进 vote 但**封顶 0.10**、权重默认低先验、由 `calibration_scorecard.py` 按「有/无披露」分桶**实测自调**（有 edge 留住、是噪音自动归零），必标 ≤45 天滞后 |
| "我还让他把 \$MU derisk 来避免财报" / 高票名次也主动从可交易集剔除 | 财报/二元事件**黑名单否决**：持仓窗口内有财报 → 无论票数多高都移出 head set | **本层（v2.26 强化）** earnings blackout veto（把既有 event_proximity 软降权强化为硬剔除） |
| "本周有 1650 亿美元的抛盘再平衡" | 季末/指数重构再平衡**资金流日历**作为 breadth/event 输入 | **已有** `dispersion_crowding.py` breadth + event_proximity；本层把再平衡日历列为已知 flow 事件 |

核心结论：他这套约 80% 已被技能现有层覆盖。v2.24 补的是横截面集成排序 + vote/predicted 两轴校准；**v2.26 再补四件**——信念阈值门（`conviction_floor`）、国会披露**校准自调成分**（进 vote、封顶 0.10、权重由校准实测自调、必标滞后）、财报黑名单硬否决、期权成本 hurdle 口径。全部仍是发现/排序先验，不新增动作等级、`position_multiplier=0.0` 不变。

不吸收的部分（证据优先）：他未公开战绩→不当已验证 edge；"AVGO 380 铁底/有信心新高"这类不可证伪的口头信心→不进证据；他把 KOL 直接喂进投票→技能保持更严纪律，KOL 永远只是 `frontline_clue` 且权重封顶。短日历期权的 theta/gamma/价差风险→本层只对**标的隔夜收益**建模，期权杠杆是另一层、由流动性门单独裁决，不把脆弱的期权机制塞进预测器。

### v2.27 补充：2026-06-25 Pre-Open Read / 开盘前退出管理

> 2026-06-25 截图与公开帖补足了一个关键边界：Opus Picks 不是日内无限持有系统，作者明确说“最多只预测到开盘 10 分钟；开盘前 pre-read 强可以拿到 9:40，否则立刻卖”。这不是新的选股 alpha，而是 close-to-open 交易的**开盘前退出管理门**。

| 他做的事（原文证据） | 逆向出的机制 | 技能里对应层 |
|---|---|---|
| "最多只预测到开盘10分钟...pre-read强可以一直拿到9:40，否则立刻卖掉" | 预测 horizon 明确止于 open + 10min；pre-read 只决定退出节奏 | `us-close-to-open-execution-overlay.md` 的 `pre_open_read` gate |
| `Pre-open call: OPTIMISTIC`，理由为 overnight gains holding into open、SPY/QQQ 盘前强、8 个仓位均值 +6.92%、7/8 green | 用市场确认 + 篮子健康度判断是否允许 winners 持有到 09:40 | `pre_open_read.inputs.index_premarket` + `basket_health` |
| "Opus picks 一定要开盘跑路...赚得不够多，等第二天的 picks" | 隔夜系统不把未兑现收益硬变日内单；收益不足也不追，等待下一轮信号 | `exit_rule=by_09_40_or_new_decision` |
| "CTAs 可能趁高开抛售" + "UVXY 太低，日内大 V 反弹机会" | 已知系统性流 + 波动率不确认/hedge 可能便宜；这是 risk/hedge 线索，不是大盘方向证明 | `known_flow_events` + `vol_confirmation/hedge_mispricing` |

吸收结论：`Overnight Ensemble Ranker` 仍只排「今晚谁优先看」；真正的开盘处理放进 Close-to-Open overlay。新增 `pre_open_read` 输出要求：每次引用本层的隔夜候选，若进入次日盘前，必须补 SPY/QQQ/sector ETF、候选篮子绿盘比例与平均涨幅、winners holding、UVXY/vol_confirmation、known_flow_events、spread/liquidity。强读数只允许观察到 09:40；弱读数必须收紧退出；09:40 后继续持有必须另跑 Decision Compiler。

## 适用范围与硬边界

| 项目 | 规则 |
|---|---|
| 市场 | **仅限美股**（与 Close-to-Open overlay 同口径）；A股/港股禁止套用 |
| 性质 | advisory 排序先验；保持 `no_order_execution` |
| 动作权限 | 只改 watch priority / 候选排序；`position_multiplier=0.0`；不新增 L 级、不提 action level / position cap |
| 投票成分 | 必须来自既有模块信号；KOL 成分封顶（见下），且只能作 `x_frontline` 线索 |
| 预测数字 | `predicted_overnight_return` 默认 `hypothesis`；无 walk-forward + 成本 + no-lookahead 不得用于排序权重 |
| 下游 | 头部候选**逐一**过 `US Close-to-Open Execution Overlay` + `Decision Compiler`；任何 hard_veto / risk_regime 块照旧优先 |

## 集成投票口径

```yaml
overnight_ensemble_ranker:
  market_scope: US_only
  universe: <pre-committed liquid list>            # 不按历史 OOS 收益挑票（选择偏差）
  per_symbol_components:                            # 每项归一到 [-1, 1]，缺失记 null 不补 0
    options_structure: <from options_gamma.py>      # 期权墙/gamma posture；负 gamma 贴 put wall → 该票方向降级
    close_auction_flow: <MOC/LOC imbalance|null>    # 收盘竞价单流，15:50/15:55 ET 后可得；缺失记 null
    event_proximity: <from event_reaction_journal>  # 事件邻近度；binary event 未拆情景 → 该票降权
    momentum_rs: <trend/相对强度 from kline>         # 动量 + 相对大盘强度
    breadth_regime: <from dispersion_crowding.py>    # 板块广度/相关性；correlation 回归 1 → 全局降权
    capex_duration: <from capex rotation overlay>    # 利率/折现率下谁被贴现
    kol_clue: <from x_frontline; capped |w|<=0.15>   # 仅线索，权重封顶，须身份+交叉+时间验证
    political_disclosure: <from Capitol Trades/Quiver/UW; capped |w|<=0.10; weight calibration-gated, low prior; null if none>  # v2.26(C) 国会/政治披露进 vote 但封顶 0.10；权重默认低先验 hypothesis，由 calibration_scorecard.py 按「有/无披露」分桶实测自调（有 edge 留住、是噪音→权重归零）；必带 disclosure_lag_days（STOCK Act ≤45 天）
  vote: weighted_mean(components)                    # agreement/方向一致度，∈[-1,1]
  conviction_floor: <calibrated; default hypothesis>  # v2.26 信念阈值；|vote| < floor → no_edge → 不进 head set（最高 watch）；须经 calibration_scorecard.py 按桶校准，不得写死为已验证盈利门槛
  earnings_blackout: true                            # v2.26 持仓窗口(收盘→次日开盘)内有财报/二元事件 → 无论票数多高，移出可交易集
  disclosure_lag_days: <int|null>                    # v2.26 political_disclosure 的 STOCK Act 披露滞后天数（≤45）；始终随票输出（可见性），即使校准把其权重压到 0 也照样显示
  predicted_overnight_return: <quant model output>   # 幅度估计，独立于 vote；默认 hypothesis
  side: CALL if vote>0 else PUT                       # 方向是输出，非预设
  rank_by: vote desc                                  # 同分用 predicted、再用流动性 tie-break
  take: top_1..top_3 from {names: |vote|>=conviction_floor and not earnings_blackout}  # 先过阈值+黑名单，再取头部（对应"13 clear it"）
```

权重纪律：
- regime 动态调权允许，但**任何单一成分不得主导**；KOL 成分 `|w| ≤ 0.15` 硬封顶。
- `vote` 与 `predicted` 不得互相反推填补——它们是 agreement 与 magnitude 两个独立轴；二者长期错配由 `calibration_scorecard.py` 抓出来（Brier / 可靠性分箱）。
- 缺成分记 `null`，不补 0、不猜；缺失过多（如 ≥3 项 null）该票最高只能进 watch，不进头部。
- **`conviction_floor`（v2.26）是方差过滤器，不是 edge 证明**：对应 Balder「\|total vote\|≥4 才 tradeable、13 clear it」。但学术上隔夜超额收益统计稳健性低、开盘价那一下部分是数据假象（在 9:31 而非开盘价执行就削弱）→ floor 默认 `hypothesis`，须用 `calibration_scorecard.py` 按 regime/方向/动作桶校准；未校准前只能用来排除明显低信念票，**不得当作已验证的盈利门槛**。Balder「9:40 卖」而非「开盘价卖」恰好规避了开盘价数据假象，这条执行细节保留。
- **`political_disclosure`（v2.26·C）进 vote 但封顶 0.10、权重由校准自调**：它是一个 vote 成分（和 Balder 一致），但 `|w| ≤ 0.10`（比 KOL 的 0.15 更紧），且**不写死任何先验信念**——权重默认低先验 `hypothesis`，由 `calibration_scorecard.py` 把 `disclosure_present` 作为一个分桶维度、用实盘结果实测：披露票若长期跑赢则权重留住（封顶内可上调），若与随机无异则权重**自动归零**。依据两面都摆明：post-2012 平均预测力弱（2012–2020 部分样本与随机选股无差异），但 Balder 用的是「今晚高关注披露」这个子集、其披露日散户跟风 flow 有时机价值——孰真孰假交给数据，不靠 a priori 拍板。必带 `disclosure_lag_days`（STOCK Act ≤45 天，滞后披露 ≠ 当日催化）。校准样本不足前，权重保持低先验、不得当作已验证 edge。

## Decision Compiler 映射

`overnight_ensemble_ranker` 不是独立动作模块，按发现/排序先验编译（同 `grok_web` 规则 9、`prediction_market_prior` 规则 11、`quant_robustness` 规则 10、`execution_window` 规则 12）：

| 输入状态 | module_signal | 默认动作上限 |
|---|---|---|
| 仅排序输出、无下游 Close-to-Open/Compiler 复核 | `execution_window`（排序先验） | L0；只提高 watch priority |
| 头部候选已过 Close-to-Open overlay + 上游允许 L1/L2 | 转入对应模块裁决 | 不超过上游动作等级；`position_multiplier` 由上游决定，排序层自身 =0.0 |
| `predicted_overnight_return` 缺 walk-forward/成本/no-lookahead | `quant_robustness` | L0/L1；预测数字只作 hypothesis，不进排序权重 |
| KOL 成分超过封顶或未通过身份/交叉/时间验证 | `x_frontline` | 不提高仓位；仅提高观察优先级 |
| `|vote| < conviction_floor`（未过信念阈值，v2.26） | `execution_window`（排序先验） | L0；no_edge，不进 head set，最高 watch |
| `political_disclosure` 国会/政治披露成分（v2.26·C） | `x_frontline` | 进 vote 但**权重封顶 0.10、由校准自调**；不独立提高仓位/动作等级；必标 `disclosure_lag_days` |
| 持仓窗口内有财报/二元事件（earnings_blackout，v2.26） | `event_proximity`/`execution_window` | 移出可交易集，无论票数多高 |
| 非 US / A股 / 港股 | `execution_window` | L0，回到对应市场框架 |
| risk_regime=active_deleveraging/forced_liquidation 或 gamma hard_veto | 对应高优先级模块 | 排序结果作废，回到更保守上限 |

**铁律**：排序层永远在 redline cap 之下。它能改变"先看谁"，永远不能改变"能不能买、买多少"。

## 输出格式

```markdown
Overnight Ensemble Ranker (US-only, advisory):
- regime_check: risk_regime / breadth / gamma 是否允许隔夜做多
- conviction_floor: <值> | status: hypothesis/calibrated；cleared: N / universe_size（对应"13 clear it"）
- ranked (仅列过 floor 且非 earnings_blackout):
  | # | ticker | vote | predicted% | side | top_driver | gaps |
- below_floor (no_edge, watch only): [...]
- earnings_blackout_removed: [...]（持仓窗口内有财报/二元事件）
- head_candidates → 逐一进 Close-to-Open overlay + Decision Compiler
- next_morning_pre_open_read_required: true（若持有到次日盘前，必须由 Close-to-Open overlay 输出 strong/neutral/weak）
- predicted_status: hypothesis / gate_passed（须注明 walk-forward+成本+no-lookahead）
- kol_weight_used: <=0.15
- political_disclosure_weight_used: <=0.10（校准自调；calibrated/hypothesis；须标 disclosure_lag_days；null 若无）
- do_not_apply_to: A股/港股
- refresh_if: regime 变、收盘竞价单流更新、事件日历变化、conviction_floor 重校准、gate 重测
```

## Pitfalls

1. **把排序当买入信号**：排序只解决"先看谁"，动作仍由 Close-to-Open overlay + Decision Compiler 决定。
2. **信预测数字**：`predicted%` 没过 Quant Robustness Gate 就是猜测，不能进权重、不能进仓位。
3. **KOL 喂大权重**：Balder 把 KOL 当投票输入；技能不学这条，KOL 永远封顶线索。
4. **按历史 OOS 收益挑 universe**：选择偏差/过拟合；universe 必须预先承诺、整体回测。
5. **抄他的口头信心**："铁底/必新高"不可证伪，不进证据。
6. **套到 A股/港股**：制度不同，禁止泛化（同 Close-to-Open overlay）。
7. **短日历期权当无摩擦**：本层只对标的隔夜收益建模；期权杠杆是另一层，受流动性/价差门单独约束。
8. **给国会披露过大权重 / 当已验证 edge（v2.26·C）**：它进 vote 但 `|w| ≤ 0.10` 硬封顶，且权重默认低先验、由校准自调——不要手动调高、不要在校准样本不足时就当成已验证 edge。必标 `disclosure_lag_days`（STOCK Act ≤45 天）。
9. **把 `conviction_floor` 当已验证盈利门槛（v2.26）**：它是方差过滤器、须校准，不是 edge 证明；未校准前只能排除明显低信念票。
10. **在阈值以下交易（v2.26）**：`|vote| < conviction_floor` = no_edge = 不做（对应 dim rows）；不要因为"也是正票"就买。
11. **把高票当免死金牌（v2.26）**：earnings_blackout 优先于票数——持仓窗口内有财报/二元事件，票再高也移出可交易集（Balder 的 MU derisk）。
12. **把 pre-open read 当新 alpha（v2.27）**：盘前 optimistic 只允许把赢家观察到 09:40，不能新增买入、不能扩大仓位、不能把隔夜单改成日内/波段单。
13. **把 UVXY 低位当大盘必反弹（v2.27）**：UVXY 是波动率/hedge 线索；价格下跌但 UVXY 不确认时，只能标注 risk/hedge mismatch，不能单独提高动作等级。

## Source anchors

- X `@Balder13946731`（"Balder's Opus Picks"）公开帖与订阅卡片截图，2026-06 与 **2026-06-24「Tonight's Picks」卡片**（conviction floor `|total vote|≥4`、OPTION 💰/TOTAL VOTE/PREDICTED 三列、all names shown、Pelosi `$INTC` 国会披露）；逆向方法结构，非已验证盈利系统。
- X `@Balder13946731` 2026-06-25 `Pre-Open Read` 公开帖/订阅截图：`Pre-open call: OPTIMISTIC`，SPY +0.96%、QQQ +2.57%、8 positions avg +6.92%（7/8 green），以及作者公开说明“最多只预测到开盘10分钟；pre-read 强可以拿到 9:40，否则立刻卖”。
- 国会/政治交易（v2.26·C）：Capitol Trades / Quiver Quantitative / Unusual Whales / StockActWatch 等公开追踪源；STOCK Act 45 天披露窗口；post-2012 市场效率研究显示平均预测力被削弱、2012–2020 部分样本与随机选股无显著差异 → 故作 `political_disclosure` vote 成分但**封顶 0.10、权重由 `calibration_scorecard.py` 按 disclosure_present 分桶实测自调**（有 edge 留住、是噪音归零），不靠 a priori 拍板。
- 隔夜异象稳健性批评（v2.26）：overnight 超额收益统计稳健性低（"more random walk than repeatable strategy"）、在 9:31 而非开盘价执行时显著削弱（开盘价部分是数据假象）→ `conviction_floor` 默认 hypothesis 须校准，Balder「9:40 卖」优于「开盘价卖」。
- 复用：`references/us-close-to-open-execution-overlay.md`（执行结构 + FRBNY overnight drift / Lou-Polk-Skouras / Alpha Architect 成本批评 / NYSE-Nasdaq auction）、`references/capex-cashflow-duration-rotation.md`、`references/leverage-crowding-dispersion-playbook.md`、`references/social-technical-entry-gate.md`（同源 Balder case）、`references/x-frontline-intelligence.md`、`references/decision-compiler.md`、`scripts/calibration_scorecard.py`、`scripts/options_gamma.py`、`scripts/dispersion_crowding.py`、`scripts/event_reaction_journal.py`。
