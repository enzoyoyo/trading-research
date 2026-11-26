# Scenario Regression Tests · 复杂场景回归集

## 目的

防止 `trading-research` 在复杂市场环境下用不同话术给出互相冲突的动作。每次重大升级后，用这些场景检查 Decision Compiler 是否仍按风控优先运行。

## 样例场景

| # | 场景 | 期望输出 |
|---|---|---|
| 1 | 基本面强 + 跌破 Put Wall 未收回 | 短线 L0；中线保留跟踪；hard_veto=true |
| 2 | 财报强 + active_deleveraging | 不允许 L3；新仓最高 L1 |
| 3 | forced_liquidation + 用户问抄底 | 空仓 L0；已持有 L4/L5；不讨论“值不值”作为短线理由 |
| 4 | X 一线从业者爆料利好，但无公告/财报/市场数据验证 | 只生成假设，提高观察优先级，不提高仓位 |
| 5 | 多个 X KOL 同时转同一截图 | 仍算单源 Weak，不能当多源验证 |
| 6 | 半导体 X 线索显示交期拉长 + 公司 IR 佐证 + 毛利上行 | X 作为辅助 EID，可支持供应链假设，但仓位仍由数据/结构编译 |
| 7 | COR1M 地量 + VIX 未动 | 标 `dispersion_extreme`，预防性降 beta，不写“已经踩踏” |
| 8 | COR1M 单日 +15% / 5日 +30% | `correlation_unwind_active`，按 active_deleveraging/forced_liquidation |
| 9 | 近月 Gamma 负、聚合 Gamma 正 | 日内按 near expiry 风控；中线可写 aggregate 但不覆盖日内 veto |
| 10 | 港股基本面强 + 成交额断层/南向走弱 | 仓位 cap 下调，不能按美股高流动性逻辑执行 |
| 11 | A 股题材强 + 炸板率高/连板退潮 | 短线降级，不能只看财报/政策 |
| 12 | 宏观 defend + 个股创新高 | 允许相对强势观察/持有，追高降级 |
| 13 | secondary/lockup/mega financing 明确 | 先下调流动性倍率，确认吸收后再升级 |
| 14 | 官方公告与 X 传闻冲突 | 官方/财报优先；X 进入 Conflict Ledger |
| 15 | 数据源鉴权失败 | 触发续期或标 auth_gap；不得静默跳过 |
| 16 | 原上游 L1 + 09:40 continuity pass | 最终仍 L1；只能 `recompile_intraday`，不得提升动作/倍率 |
| 17 | 09:40 continuity fail | close-to-open trade L0，退出/减仓 |
| 18 | 负 gamma 但数据 stale/delayed | 只收紧到 L0/L1 或 data gap；不得据此续持 |
| 19 | equity thesis 可行 + option BBO stale/wide/thin | option branch L0；underlying branch 保持 upstream，不得串台 |
| 20 | EOD 样本不足 | 不调权；原多因子与 hard gates 永不改变 |
| 21 | 港股基本面强 + board-lot unit/value 或 odd-lot 路由未知/过期 | `data_quality` 封顶 L0/WATCH，禁止精确下单数量；强基本面不得覆盖 lot identity 缺口 |
| 22 | 8 份 SEC filing 均来自同一 `source_family=sec_edgar`，另有偏多基本面 | 仍只有 1 个独立底层来源；`data_quality:source_coverage` 必须把最终动作压到 L0/WATCH/0，不能按 filing 数量加票 |

## v2.35 双层 fixture

- `templates/short-cycle-structure-evals.jsonl`：直接跑 `scripts/short_cycle_structure.py`，覆盖 checkpoint/gamma/option/EOD 行为。
- `templates/scenario-regression-evals.jsonl`：只把 overlay 生成的既有 module signals 送进 `decision_compiler.py`，验证 tighten-only 与 underlying/option branch 隔离。

两套都由 `scripts/validate_skill.py` 运行；任一通过场景抬高 upstream cap、过期 gamma 放宽、坏 option BBO 污染 underlying、EOD 自动写权重，或把同一 SEC family 的多份 filing 当成多源，均为回归。

## Balder 单篇预测语义边界（E01–E15）

这 15 条 fixture 只把“缺少可复现定义”投影为现有 `research_readiness` L0/WATCH/0 信号，和一个故意偏多的 `fundamentals` 信号一起送入既有 Decision Compiler。它们不实现 Balder 模型、不复制单篇阈值、不新增 module/policy，也不把缺失值写成 0；目标是证明任何来源特定预测话术都不能绕过 Evidence → Mira → Compiler。

| ID | 反事实/边界 | 必须保持未知的内容 |
|---|---|---|
| E01 | 多周期表述 | 明确预测 horizon 与每个 horizon 的独立结果 |
| E02 | 多情景叙事 | 冻结的情景集合、概率或权重 |
| E03 | 缺观测/停牌 | unknown 不能伪装成 no-change 或命中 |
| E04 | 方向对、幅度未定义 | direction 不自动证明 magnitude/calibration |
| E05 | 超时规则 | 可观测起点、截止时钟与时区 |
| E06 | 资产映射 | ticker/证券/benchmark 的 point-in-time 身份 |
| E07 | 绝对收益 vs alpha | benchmark、窗口与超额收益分母 |
| E08 | 原因 vs 中介 | 因果变量、中介路径和替代解释 |
| E09 | path/no-touch | 触碰、收盘、盘中顺序与 gap 语义 |
| E10 | 必要 vs 充分 | 必要条件不得升级为充分交易条件 |
| E11 | 同 bar 歧义 | intrabar 先后顺序与可执行 observation |
| E12 | 公司行动 | 拆并股、分红等 point-in-time 调整 provenance |
| E13 | 收益 vs 波动 | return signal 不自动成为 volatility forecast |
| E14 | MRVL/NFLX taxonomy | 单篇来源分类不得泛化为 universe/causal rule |
| E15 | 退出遗憾 vs 做空成功 | avoided loss 不证明 profitable short 或可成交收益 |

所有 15 条的期望完全相同：`compiled_action=L0`、`entry_permission=WATCH`、`final_position_multiplier=0.0`、`hard_veto=false`；人工补证后也只能回到正常 Evidence admission，不能由 fixture 自行升级。

## v2.54 财报期权预测边界

以下 fixture 验证 `references/earnings-event-options-prediction-gate.md` 只收紧、不新建方向模型：

| 场景 | 既有 module | 期望 |
|---|---|---|
| 面板发表于财报公布后 | `research_readiness` | L0/WATCH/0；只算事后复盘 |
| `P(up)<50%` 却标 `BULLISH LEAN`，映射规则未公开 | `conflict_ledger` | 认知型 veto；人工复核 |
| raw aggregate P/C 被解释为 directional open-buy flow | `calculation_quality` | L0/WATCH/0 |
| `fresh positioning v/OI` 缺 OI 发布时间或使用次日 OI | `quant_robustness` | L0/WATCH/0 |
| implied-vs-realized 研究信号偏多，但上游只允许 L1 | `quant_robustness` | 仍为 L1；不得抬高 upstream cap |
| LLM 回放已发生事件但没有真实 knowledge cutoff / 时间隔离 | `quant_robustness` | L0/WATCH/0；仅 `research_hypothesis` |

这些场景不证明任何财报期权 edge；它们只证明事后截图、无签名成交量、未来 OI、黑箱派生分数或知识泄漏不能绕过 Evidence → Mira → Compiler。

## v2.55 多周期 / 信号融合 / GEX 冲突边界

| 场景 | 既有 module | 期望 |
|---|---|---|
| 异质 horizon 共用一个方向分 | `conflict_ledger` | 认知型 veto；BLOCK/0 |
| 相关信号算术平均试图抬倍率 | `calculation_quality` | L0/WATCH/0 |
| 多 vendor GEX flip 冲突后取平均价 | `gamma` tighten-only | L0/WATCH/0；不得抬 upstream |

总 scenario fixture 数以 `templates/scenario-regression-evals.jsonl` 行数为准（当前含 v2.54/v2.55 增补）。

## 机器化期望

未来可转成 `templates/scenario-regression-evals.jsonl`，每条至少包含：

```json
{"name":"x_unverified_frontline_bullish","inputs":{"x_frontline":"unverified_bullish","filing":"none"},"expect":{"max_action_level":"L0","position_raise":false}}
```

## Output-Quality Golden-Set · posture 回归（v2.19 新增）

上面 4 套结构 eval（routing / market-router / method-router / scenario-regression）只验「编译器 I/O 是否正确」，抓不到「同样输入下研究 posture 是否被某次升级悄悄放宽」这类语义/质量退化。`templates/output-quality-golden-set.jsonl` 冻结一组研究 fixture，用 `scripts/output_quality_regression.py` 过 `decision_compiler.py`，**判 posture 不判逐字**（对措辞鲁棒），作为第 5 套 suite 挂进 `self_optimization_check.py` 的每日门。

每条 fixture 的 `expected_posture` 字段与判定规则：

| posture 字段 | 含义 | 退化判定（fail） |
|---|---|---|
| `action_at_most` | 允许的最激进 long 动作上限（L0<L1<L2<L3；卖出/未知按最保守处理） | 编译动作比上限更激进 |
| `hard_veto` | 是否应触发硬否决 | 期望 `true` 却编译成 `false` |
| `max_multiplier` | 仓位倍率上限 | 实际 `final_position_multiplier` > 上限 |
| `must_dominate` | 必须主导决策的风控 guard（gamma / risk_regime / liquidity / data_quality / dispersion_crowding / x_frontline 等） | guard 不在 `dominant_constraints ∪ hard_veto_modules` |

用法：
- 日常 lint：`python3 scripts/output_quality_regression.py`（lint 全部 golden case；已并入每日 eval 门）。
- 升级前后比对：改动前 `--baseline /tmp/oq-baseline.json` 建基线 → 打补丁 → `--compare /tmp/oq-baseline.json`；**动作放宽 / veto 丢失 / 倍率抬升 / 必带 guard 掉失**任一 = regression，回滚本次 patch。

当前 7 条 golden case 覆盖：gamma veto、active_deleveraging 降杠杆、forced_liquidation 抄底拦截、X 一线未验证利好、COR1M 地量离散度、数据源鉴权缺口降级、港股南向流动性 cap——均派生自上表场景，把「正确的保守 posture」固化成可回归断言。

> **LLM 语义层（互补，不在确定性 preflight 内跑）**：简洁回复契约、不过度声称等纯语义判定，改动 reply template 时用 `eval-harness` skill 的 binary-eval / pass@k 打分，不手搓评分器。
