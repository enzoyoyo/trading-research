# 盘口快照 + 分钟 K 线 → LLM 研判打包模板

> 目的：把「五档快照 + 1 分钟 OHLCV」结构化打包成 Markdown，供 LLM 做**研究判断**（不是交易指令）。「每个判断必须引用具体证据」约束与本 skill 的证据纪律一致（`references/evidence-ladder.md`、`references/data-contracts.md` 的 `evidence_ids`/EID 体系）。
>
> `no_order_execution=true`。本模板产出只进 Tier 2 Evidence + Research Chain（`SKILL.md` 固定流程步骤 5）或 `hypothesis_registry.py`，**永远不直接映射成动作等级、仓位倍率或下单指令**。任何"多/空/加仓/止损"字样出现在本模板输出里，都只是研究假设的方向标签，必须再走 `references/decision-compiler.md` + `scripts/decision_compiler.py` 才可能影响真实动作等级。
>
> 与 `references/a-share-intraday-microstructure-strategies.md` 配套使用：本模板打包的原始快照/K线可以附带该文档 7 个信号的 `raw_output`（如果已跑过 `scripts/microstructure_signals.py`），但附带的信号**必须原样保留 `validation_posture: train_only` 标签**，不得因为塞进了 LLM 判断上下文就被默认当作已验证事实。

## 反幻觉铁律（不可协商）

1. **每一条 LLM 判断必须至少引用一个具体的 `bar_ref` 或 `tick_ref`**，格式见下方「引用格式」。没有可回指证据的判断，视为无效判断，不得写入报告或进入证据链。这与 `evidence-ladder.md` "没有可回指证据的判断不允许被标记为已确认"是同一条纪律的应用，不是新规则。
2. 禁止对空白区间（无快照/无 K 线覆盖的时间段）做插值式判断；缺口必须显式写"数据缺口"，不得脑补。
3. 禁止把 `microstructure_signals.py` 的 `fired=true` 直接转述成"已确认的操纵/主力行为"——`fired=true` 只代表"满足了公式定义的模式匹配条件"，不代表该模式已被验证有效（全部信号仍是 `train_only`，详见配套文档）。
4. 本模板的输出主体是**观察与假设**，不是**结论与指令**。凡是判断需要影响仓位或动作等级，必须先经过 `hypothesis_registry.py create` 登记，再走 `factor-validation-strict-gate.md` 的验证链条，不能从本模板直接跳到 Decision Compiler。

## 引用格式

- `bar_ref`：`{symbol}#{timeframe}@{bar_index}`，例如 `600519.SH#1m@37`（第 37 根 1 分钟 K 线，从当日开盘计数）；同时标注该 bar 的墙钟时间，例如 `600519.SH#1m@37(09:52:00+08:00)`，双重定位防止 index 口径歧义。
- `tick_ref`：`{symbol}#tick@{snapshot_ts}`，例如 `600519.SH#tick@09:52:03.412+08:00`（快照采集时刻，精确到毫秒，因为免费源约 3 秒轮询，秒级都可能有歧义）。
- 一条判断可以同时引用多个 `bar_ref`/`tick_ref`；至少 1 个是硬性要求，多个更好。
- 严禁使用"近期""刚才""前面几根"这类无法回指的模糊表述替代具体 ref。

## 输入打包结构（喂给 LLM 之前，先按此结构组装 Markdown）

```markdown
# {{SYMBOL}} {{NAME}} 盘口快照研判素材包

## 元信息
- 标的：{{SYMBOL}} {{NAME}}
- 打包时间：{{PACKAGE_TS}}（本地时区，ISO 8601）
- 数据源：{{DATA_SOURCE}}（如：腾讯/东财 公开五档快照；LongBridge 1m OHLCV）
- 数据保真度声明：五档快照为 {{SNAPSHOT_INTERVAL_SECONDS}} 秒轮询近似，非真逐笔；1 分钟 OHLCV 为标准聚合数据
- `no_order_execution=true`；本素材包仅供研究判断，不构成交易指令

## 五档快照（最近 {{N_SNAPSHOTS}} 帧）
| tick_ref | 买五-买一 (价/量) | 卖一-卖五 (价/量) | 最新价 | 累计成交量 | 涨跌停状态 |
|---|---|---|---|---|---|
| {{TICK_REF_1}} | {{BID_L5_L1_1}} | {{ASK_L1_L5_1}} | {{LAST_1}} | {{CUM_VOL_1}} | {{LIMIT_STATE_1}} |
| ... | ... | ... | ... | ... | ... |

## 1 分钟 OHLCV（最近 {{N_BARS}} 根）
| bar_ref | 开 | 高 | 低 | 收 | 成交量 | 成交额 |
|---|---|---|---|---|---|---|
| {{BAR_REF_1}} | {{O_1}} | {{H_1}} | {{L_1}} | {{C_1}} | {{VOL_1}} | {{AMT_1}} |
| ... | ... | ... | ... | ... | ... | ... |

## 已触发微观结构信号（若已运行 scripts/microstructure_signals.py；全部 train_only，非确认事实）
| signal_id | fired | strength | evidence (tick_refs/bar_refs) | input_fidelity | validation_posture |
|---|---|---|---|---|---|
| {{SIGNAL_ID_1}} | {{FIRED_1}} | {{STRENGTH_1}} | {{EVIDENCE_REFS_1}} | {{INPUT_FIDELITY_1}} | train_only |
| ... | ... | ... | ... | ... | train_only |

（无信号触发或未运行检测脚本时，本节写「未运行 microstructure_signals.py / 本期无信号触发」，不得留空造成"未提及=不存在"的歧义。）

## 数据缺口
- {{DATA_GAP_1}}（如："09:30:00-09:33:00 快照缺失，来源限流"）
```

## LLM 判断输出结构（要求 LLM 按此结构回应，而不是自由发挥）

```markdown
## 观测摘要
- {{OBSERVATION_1}}（引用：{{REF_1}}）
- {{OBSERVATION_2}}（引用：{{REF_2}}）

## 研究假设（非结论，非指令）
1. {{HYPOTHESIS_STATEMENT_1}}
   - 支持证据：{{SUPPORTING_REFS_1}}（至少 1 个 bar_ref/tick_ref，缺失则本条假设视为无效）
   - 反面观察：{{COUNTER_OBSERVATION_1}}（诚实列出不支持该假设的证据，没有反面观察也要写"未观察到明显反证"）
   - 失效条件：{{INVALIDATION_CONDITION_1}}（价格/量能到什么状态就说明这条假设已经不成立）

2. {{HYPOTHESIS_STATEMENT_2}}
   - 支持证据：{{SUPPORTING_REFS_2}}
   - 反面观察：{{COUNTER_OBSERVATION_2}}
   - 失效条件：{{INVALIDATION_CONDITION_2}}

## 数据局限性自陈
- {{LIMITATION_1}}（例如："本期五档快照存在 {{GAP_DURATION}} 缺口，09:30 附近的判断置信度降低"）
- 若素材包引用了 `snapshot_diff` 类信号（`ignition`/`spoofing`/`wall_breaker`/`limit_leak`），必须在此重申：该信号是快照差分近似，非真逐笔数据，结构性存在误判可能

## 免责声明（固定文本，不得删改或弱化）
本研判基于 {{SNAPSHOT_INTERVAL_SECONDS}} 秒级快照与分钟级 K 线的公开数据，不包含真逐笔委托/成交数据；所有假设均为 `train_only`，尚未经过 `factor-validation-strict-gate.md` 的同宇宙随机对照与 OOS 验证；`no_order_execution=true`，本输出不构成投资建议，不直接映射为任何动作等级或仓位调整；如需进入决策链路，必须先经 `hypothesis_registry.py create` 登记，再走 `factor_engine.py` 验证。
```

## 后续处理路径

```
本模板输出（研究假设，附 bar_ref/tick_ref 证据）
  → 人工/agent 判断是否值得跟踪
  → 值得跟踪 → hypothesis_registry.py create（status=open 或 train_only）
  → 后续需要真正影响仓位 → factor_engine.py / factor-validation-strict-gate.md 验证链
  → 只有 confirmed_alive 才可能作为正向 module_signal 提交给 decision_compiler.py
```

不允许的路径（红线，明确禁止）：

```
本模板输出 ──✗直接──▶ decision_compiler.py / 仓位调整 / 下单
```

## 使用边界

- 本模板不是新的动作等级系统，不新增仓位上限或风险规则；封顶/收紧语义仍以 `references/decision-compiler.md` 的 Cap & Tighten-Only Registry 为唯一权威。
- 本模板不产生、不读取、不需要任何交易凭据；纯文本打包 + LLM 文本判断，无执行副作用。
- 本模板与 `templates/universal-equity-report.md` 是不同层级：后者是 Tier 0/1/2 主回复的唯一权威格式，本模板只服务于"盘口快照类原始素材如何打包给 LLM 做初步研究判断"这一前置步骤，其输出若要进入正式报告，仍需套用 `universal-equity-report.md` 的证据/质量门格式。
