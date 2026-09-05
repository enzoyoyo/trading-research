# Factor Research Engine · 因子研究执行器

## 1. 定位与边界

本引擎是纯研究执行器，负责把历史日线面板转换为可复现的因子统计、分位回测和严格四态裁决。它不下单、不产生动作等级、不授予入场权限，也不替代 Decision Compiler。

所有产物只能进入既有五个通道：

1. `ResearchExperiment`：完整实验合同与因子验证字段。
2. `hypothesis_registry`：所有四态结论都登记，改判走 update。
3. `module_signal(quant_robustness)`：仅 `confirmed_alive` 允许生成 tighten-only 建议；仍为 L0、0 倍率，不能抬高上游。
4. `watch_priority`：盘前只排序先看谁，不授权动作。
5. `EvidenceItem`：`type=quant_factor`、`variable=quant_robustness`、`claim_type=derived_calculation`，以 run_id 和 `calculation_ref` 为锚。

`references/decision-compiler.md` 与 `scripts/decision_compiler.py` 是动作裁决的唯一权威。本引擎不新增 module 枚举，不造第二套总分。

## 2. 因子注册表

| name | calculation_ref | direction_hypothesis | min_history_days | cross_section_only |
|---|---|---:|---:|---:|
| `mom_20_1` | `close[t-1]/close[t-21] - 1` | `+` | 22 | true |
| `mom_60_5` | `close[t-5]/close[t-65] - 1` | `+` | 66 | true |
| `rev_5` | `-(close[t]/close[t-5] - 1)` | `+` | 6 | true |
| `vol_20` | `stdev(daily_ret, 20)` | `-` | 21 | true |
| `turn_20` | `mean(turnover_rate, 20)` | `-` | 20 | true |
| `turn_ratio_5_60` | `mean(turnover_rate,5)/mean(turnover_rate,60)` | `-` | 60 | true |
| `range_pos_252` | `(close - min(low,252)) / (max(high,252) - min(low,252))` | `+` | 252 | true |
| `amihud_20` | `mean(abs(daily_ret)/amount, 20)` | `research` | 21 | true |

`direction_hypothesis="-"` 的因子自 v2.64 起按假设方向取向后对称裁决；v2.64 之前的 `"-"` 因子历史裁决存在按假设应验时仍只能落入 `noise` 的已知偏置。

注册表只含 OHLCV 与换手率可复现的价量因子。股息类、无 PIT 财务因子不进入 Phase A。`range_pos_252` 注册为 `default_enabled=false` / `explicit_long_window_only`：它不进入 `--factors all`，只能显式请求并先抓取独立长窗。其余 7 个因子组成默认集合。机器可读全表由：

```bash
python3 scripts/factor_engine.py list-methods --json
```

## 3. 预处理注册表

所有变换均逐交易日截面估计；严禁使用全样本均值、方差、最小值或未来信息。

| 类别 | 方法 | 参数/语义 |
|---|---|---|
| winsorize | `mad` | median ± 5×MAD |
| winsorize | `iqr` | Q1−3×IQR 至 Q3+3×IQR |
| winsorize | `quantile` | 1%–99% 截面分位 |
| standardize | `zscore` | 逐截面均值与总体标准差 |
| standardize | `robust_zscore` | median 与 1.4826×MAD |
| standardize | `rank` | 逐截面平均秩标准化 |
| neutralize | `ols_residual_size` | 对 `size_proxy=log(mean(amount,20))` 逐截面 OLS 取残差 |
| neutralize | `ols_residual_size_sector` | 可选行业虚拟项 + size；必须披露 `sector_basis=eastmoney_mixed_concept_unreliable` |

CLI 直接接受上述文档名；兼容短别名 `size` / `size_sector`，内部映射到同一实现。默认不做行业中性化。成交额只作为 size proxy，不冒充历史市值。

## 4. 已知坏模式排除清单

以下模式被代码结构和回归测试双重排除：

1. **未来收益方向算反**：前向收益唯一口径为 `price[t+h]/price[t]-1`；IC 与归因共用 `factor_engine.forward_return()`，不把 `shift(+)` 当未来收益。
2. **EWMA 前视且公式错误**：EWMA 整类不实现，不得加入方法注册表。
3. **全样本标准化**：所有标准化与中性化变量只在单个 trade_date 截面内估计。
4. **boxcox 全样本最小值平移**：boxcox 不实现；需要全样本统计量的变换一律禁入。
5. **跨 symbol 日期位移污染**：缓存为 per-symbol 文件；时序位移只在单 symbol 已排序序列内完成。
6. **停牌/退市收益静默填 0**：形成期数据不全就剔除；持有期中断以最后可得价结算并计入 `suspended_or_delisted_count`，绝不无声补 0。
7. **null 参与分箱**：先过滤 null，再检查 `min_cross_section`；无效截面占比超过 30% 时整包 `insufficient_data`，统计字段为 null。

额外除名：

- `ransac_winsorize`：异常时可能吞错返回原数据，违反 fail-closed。
- RF/GBDT/KRR 等 ML 中性化：同截面 fit-predict 易把过拟合伪装成净化。
- **「候选信号 + ML 二次筛选」两段式架构**：本系统的第二段（`factor-validation-strict-gate` 的同宇宙随机对照 + 固定 `T=3.5` + 四态裁决）已经就是二次筛选，且是无参数的。换成有参数的拟合器不构成增量，反而在 `n_factors_scanned` 覆盖不到的地方（模型族/特征集/超参网格）新增一层未计数的搜索——校正后的 `alpha_t` 看起来仍然合规，过拟合却已发生。另有两条结构性封死：`decision_compiler.py:93-95` 令 `quant_robustness` 下 `train_only/noise/reversed_strict` 的 `position_multiplier=0.0`（新信号一律 `train_only` 起步，故 ML 分数进 Compiler 必乘 0）；`factor_verdict.py` 硬编码 `decision_use <= ranking_support`（无 CLI 逃逸），产出天花板与现有整数投票同级。**只吸收其纪律**：因子有效性必须做条件分层检验，实现方式为在既有随机对照框架内显式分箱重跑（`factor_backtest.py --condition-by`），分箱数计入 `n_factors_scanned`，分箱结论只降级不晋级。
- AlphaPurify 原 `Exposures` 归因：未来收益方向错误，不复用。
- 独立 `trace()`、Arrow mmap、多进程、Plotly 报表：在 ≤100 标的小宇宙下没有必要，增加维护面。
- 任何 `except: return original_data`：异常必须转成结构化 `data_gaps` 或非零退出。

## 5. IC 与同宇宙随机对照

### 5.1 IC

每个交易日独立计算：因子值与 h 日前向收益分别转平均秩，秩向量的 Pearson 即 Spearman IC。horizon 默认 `1,5,10`，每个 horizon 独立输出，不跨周期合成。

### 5.2 随机对照

执行器注册两种可复现 null：

- `cross_section_shuffle`：同一交易日、同一有效股票截面内独立置换已算好的因子秩向量。
- `circular_rotation`：对每个标的的因子秩时间序列独立抽偏移 `k` 并循环平移，前向收益原位不动，再逐日重排横截面秩。每个标的独立抽 `k`；允许区间固定为 `2<=k<=T-2`，排除 `0/1/T-1` 退化旋转。它保留标的内序列持续性，只破坏因子与收益的时点对齐。

两种实现由同一个 `factor_engine.evaluate_observations()` kernel 提供；
`factor_backtest.py --condition-by` 复用该 kernel，不维护第二套 null。当前默认仍是
`cross_section_shuffle`：只有 ≥30 标的真实缓存对比中，每个可比因子×horizon 都同时满足
`circular random_ic_std > shuffle random_ic_std`、`circular alpha_t <= shuffle alpha_t`，
并且至少一条既有 `confirmed_alive` 明确降级，才允许把默认切到
`circular_rotation`。对比不可运行、任一不变量失败或四态变化清单为空，都保持旧默认。

- `random_ic_mean`：试验级 mean IC 的均值；`random_ic_std` 是同一试验分布的样本标准差。
- 对 h 日前向收益，先固定从首个有效截面开始按 `stride=h` 取非重叠截面，再计算 `alpha_t = (mean_real_ic - mean(trial_mean_ics)) / stdev(trial_mean_ics)`；h=1 保持逐日截面。
- 分母分布是“每次试验跨截面的均值 IC 分布”，不是单截面 IC，也不是相对 0 的 t 检验。
- 输出同时披露 `raw_section_count`、`effective_sections`、`section_stride`；`min_dates` 按 `effective_sections` 计数，不足即 `insufficient_data`。
- `n_factors_scanned` 是本次真实检验数 `因子数×horizon 数`，禁止手填。
- `T=3.5` 固定，不随本次族规模动态调整；`n_factors_scanned` 仅作多重检验披露，不得声称已做 Bonferroni/Holm/族规模自适应校正。
- `null_kind`、`seed`、`trials` 必须显式落盘；循环旋转还必须在 `null_audit` 持久化标的顺序、每标的 offset 合法边界与逐试验 `offsets_by_trial`。同输入、同参数、同 seed 必须产生相同结果。
- 面板逐标的记录 `panel_source`。同一标的历史一旦含多个来源，该标的只从随机对照退出、真实 IC 路径不删除，并记录 `cross_source_symbol_history_excluded_from_random_control`；退出后 null 截面不足仍 fail-closed。`qfq_rewrites_history` / `forward_adjust_rewrites_history` PIT caveat 不变。
- `python3 scripts/factor_engine.py run ... --compare-nulls --json` 显式输出逐因子×horizon 的两种 `random_ic_std`/`alpha_t`、`state_changes`、`downgrades`、不变量及默认建议；不得用空清单冒充有效升级。

裸 IC、裸 t>2、图上单调都不构成 alpha 证据。

### 5.3 条件分层验证（只收紧）

`factor_backtest.py` 可选 `--condition-by {vol_20_quintile,risk_regime}`；省略该参数时完全沿用原回测路径。条件值只取已登记且在形成日可得的变量：`vol_20_quintile` 使用全市场逐日 `vol_20` 中位数的滞后 1 个交易日值，并只用当时已知历史计算 expanding 五分位边界；`risk_regime` 使用 `risk_regime_snapshot.v1` 五态字符串的滞后 1 个交易日值。禁止用全样本边界、因子自身前向收益或任何未来信息分箱。

- 研究轮次用 `--condition-factors` 与 `--condition-horizons` 预声明完整 family；`n_factors_scanned = factors × horizons × k`，五个条件箱令 `k=5`。固定阈值仍为 `T=3.5`，不得因分箱增加而放松。未显式声明 family 的单因子运行只能输出 pair-level 证据，不能据此拒绝整个条件维度。
- 每个箱独立计算 `effective_sections`。任一箱 `< min_dates`（默认 40）即标记 `insufficient_sample`，不产出 verdict、不写 hypothesis；不得降低样本门槛补结论。
- 条件结论只允许降级：全局 `confirmed_alive` 在某箱为 `noise`/`reversed_strict` 时，该箱降为 `train_only`；全局 `train_only` 即使某箱原始结果通过，也不得晋级。
- 只有完整 family 中出现至少一个显式降级时才保留该条件维度，并对可裁决的每个「因子×条件箱」按 `train_only` 登记。hypothesis identity 固定为因子/条件/箱/horizon，不把会变化的四态写进 identity；falsifier 固定写明「下一轮滚动 OOS 中该分箱 `alpha_t < 3.5` 即降级」。只有完整 family 的全部箱均可裁决且零降级，才输出 `rejected_no_degradation`，不保留该维度、不写 registry；不可裁决或样本不足保持 pending。
- 先按全局序列固定 `stride=h` 的非重叠截面日期，再在同一日期网格内切条件箱，防止分箱各自起步造成抽样相位差。输出持久化 `null_kind`、`seed`、`null_trials`、滞后期、expanding `boundaries_by_date`/五态枚举、逐日分箱映射与 section dates，保证可复现。条件验证复用 §5.2 的同一 `evaluate_observations()` kernel，不另建 null 或 Compiler 路径；既有 `quant_robustness/train_only = 0.0` cap 保持不变。

离线快速检查与真实长窗研究命令：

```bash
python3 -m unittest scripts.test_factor_backtest
TR_CONDITION_UNIVERSE=(SPY QQQ IWM DIA SOXX SMH XLK XLF XLE XLI XLV XLY XLP XLU XLB XLC XLRE XBI KRE TLT HYG GLD SLV USO AAPL MSFT NVDA AMZN META GOOGL)
python3 scripts/factor_panel.py fetch --market US --symbols "${TR_CONDITION_UNIVERSE[@]}" --window-days 800 --source auto --json
python3 scripts/factor_backtest.py run --market US --symbols "${TR_CONDITION_UNIVERSE[@]}" --factor mom_20_1 --condition-by vol_20_quintile --condition-factors mom_20_1 mom_60_5 rev_5 vol_20 turn_20 turn_ratio_5_60 amihud_20 --condition-horizons 1 --null circular_rotation --null-trials 100 --seed 42 --min-dates 40 --register-conditions --save --json
python3 scripts/factor_backtest.py run --market US --symbols "${TR_CONDITION_UNIVERSE[@]}" --factor mom_20_1 --condition-by risk_regime --condition-factors mom_20_1 mom_60_5 rev_5 vol_20 turn_20 turn_ratio_5_60 amihud_20 --condition-horizons 1 --null circular_rotation --null-trials 100 --seed 42 --min-dates 40 --register-conditions --save --json
```

真实长窗若无可复现的逐日五态历史，`risk_regime` 分层必须 fail-closed；不得用当前快照回填历史。真实长窗回测不是本次升级的验收条件。

## 6. 四态规则

阈值 `T=3.5` 写死，不提供单因子豁免：

- `confirmed_alive`：train/test `alpha_t` 按 `direction_hypothesis` 取向后均 `>=T`，并且 train/test `mean_ic` 同号。
- `train_only`：train `alpha_t` 按 `direction_hypothesis` 取向后过门、test 取向后不过门。这是过拟合证据，不是“弱 alpha”。
- `reversed_strict`：全样本与 test `alpha_t` 按 `direction_hypothesis` 取向后均 `<=-T`，且全样本与 test 的实际 IC 方向均与假设相反。
- `noise`：其余。

`direction_hypothesis="research"` 有意冻结为只允许原始正向 `alpha_t` 确认、永不产生 `reversed_strict`；不得把它顺手对称化。

缺 `random_ic_mean`、`alpha_t` 或 `n_factors_scanned` 时不可裁决：`state=null`，readiness 封顶 `research_hypothesis`。

## 7. 两道门与 survivorship 天花板

### 7.1 两道门

第一道是 Factor Validation Strict Gate：同宇宙随机对照、固定 3.5 披露门、train/test 四态。该固定门源自 factor-zoo 的严格门槛纪律，但不是按本次族规模动态校正。

第二道是既有 Quant Robustness Gate：walk-forward、成本、容量、回撤、no-lookahead。`confirmed_alive` 不豁免第二道门；缺 backtest、`costs_included!="yes"`、缺 train/test 分段或 OOS `test.periods<3` 时，记 `missing_walk_forward`，`decision_use` 强制为 `hypothesis_only`。OOS `test.sharpe <= 0`（含成本）记 `oos_net_sharpe_nonpositive`，`decision_use` 强制为 `hypothesis_only`；0 为符号边界非可调参数。train/test 切分只按可结算的 `ls_returns` 长度计算，不按 formation epoch 数错切。

### 7.2 不可放开的天花板

当前宇宙来自用户给定的现存 watchlist，不是 point-in-time 历史成分，因此 survivorship bias 恒成立：

- `decision_use <= ranking_support`，永不给 `risk_cap_support`。
- `readiness_level <= working_view`。

这两个上限写死在 `scripts/factor_verdict.py`，没有 CLI 参数可以解除。

## 8. 数据口径与 PIT 声明

- A 股历史面板的 `auto` 链在东财健康门放行后使用 AkShare em→sina qfq 备源链；HK `auto` 链为 LongBridge forward → 健康的 AkShare em qfq → 新浪 qfq，并分别携带对应 `adjust_basis`；US `auto` 链先尝试 AkShare qfq，失败时可走已注册的 LongBridge forward 复权备源。
- qfq/forward 都会在公司行动后重写历史；`pit_caveats` 必须分别包含 `qfq_rewrites_history` / `forward_adjust_rewrites_history`。短窗口与全窗重拉只保证内部一致，不解决严格 PIT lookahead。
- 宇宙为当前存续标的列表；`pit_caveats` 必须包含 `survivorship_current_constituents`。
- A/HK 无 point-in-time 财务数据前，不做财务因子；股息敏感因子除名。
- 停复牌自然缺行必须被计数和声明。
- 可选行业键来自东财混合行业/概念列表，不能冒充精确申万行业。
- A 股涨跌停探测当前统一使用 `abs(pct_change)>=9.5%`，未区分 300/688 的 20% 与 ST 的 5%，会漏报或误报；`limit_move_days` 统计的是 `(symbol, epoch)` 次数，不是唯一交易日数。
- 分位分箱遇并列因子值时以 symbol 字典序切开；低基数因子的 long-short spread 可能由并列切分驱动，Phase A 只披露，不把该 spread 单独当 alpha 证据。
- 印花税 `sell` 只乘卖出侧 turnover，`both` 才乘总 turnover；fee/slippage 仍乘总 turnover。

## 9. 限流纪律

A 股批量历史面板只走健康门后的 AkShare em→sina qfq 备源链；HK `factor_panel --source auto` 先走 LongBridge forward，失败后才走健康的 AkShare em→sina qfq 链；US 可在既有 `factor_panel --source auto` 链使用 LongBridge forward 复权备源。不得为条件分层新增其他数据源：

横截面研究/部署宇宙必须至少 30 只，建议 50+ 以吸收 null 与 warm-up 损耗；8 只票 watchlist 按设计不能形成可部署 rank，`ranking_unavailable` 是正确的 fail-closed 结果，不是抓取故障。

- A/HK AkShare 串行请求，相邻调用默认至少 1.5 秒；单次最多 100 标的。
- 单标的失败最多重试一次，之后立即记缺口。
- 默认单次最多 100 标的，不得悄悄扩大。
- `factor_panel fetch` 默认窗口为 800 个自然日。按 245 交易日/年约得 537 行，可覆盖默认因子集合在 `h=1,5,10`、`min_dates=40` 下最严格的约 476 行需求，并保留余量。
- 每个因子×horizon 的基础关系必须满足 `window_trading_rows ≥ min_dates×h + warmup + h`；运行时采用更保守的 `required_rows = max(min_history_days + h×(min_dates+1), ceil((min_history_days+h)/0.30))`，同时覆盖前向收益尾部和最多 30% 截面排除门。
- 窗口不足必须输出 `panel_window_too_short_for_factor`，并同时披露全宇宙 `available_rows_min`、`available_rows_median` 与 `shortest_symbols`，不得把一只新上市标的造成的短板误报为因子无效。
- `range_pos_252` 不属于默认集合；显式研究时按约 870 个交易日单独抓取至少 1300 自然日长窗。若仍不足，必须发上述 gap，不得给裸 `insufficient_data`。
- qfq 刷新全窗口重拉；新鲜缓存跳过出网。
- `panel_manifest.json` 记录 `calls_made`、fetch 时间、行数、adjust basis 与 gaps。
- provider 失败属于数据缺口，不通过改参数或填 0 伪装为策略结果。

## 10. run → verdict → registry 生命周期

```text
factor_panel fetch/status
        ↓ per-symbol qfq cache + manifest
factor_engine run --save
        ↓ IC / random control / train-test / run_id
factor_backtest run --save
        ↓ costs / walk-forward / failure modes
factor_verdict judge
        ↓ ResearchExperiment + factor_validation + hypothesis_payload
factor_verdict judge --register
        ↓ hypothesis_registry create
复检或改判 --update-id hyp_…
        ↓ hypothesis_registry update + link-evidence
30 天未更新
        ↓ hypothesis_registry stale 提示重跑（不自动改变状态）
```

示例合同在 `templates/factor-experiment-example.json`。全部四态都必须登记，避免 `train_only`、`noise`、`reversed_strict` 在报告后失踪。registry 只记录研究生命周期，不触发订单、提醒或外部副作用。

## 11. 常用命令

```bash
python3 scripts/factor_panel.py fetch --market A --symbols-file watchlist.txt --window-days 800 --json
python3 scripts/factor_engine.py run --market A --symbols-file watchlist.txt --factors all --horizons 1,5,10 --neutralize ols_residual_size --save --json
# 长窗因子显式 opt-in，不与默认配置混跑：
python3 scripts/factor_panel.py fetch --market A --symbols-file watchlist.txt --window-days 1300 --json
python3 scripts/factor_engine.py run --market A --symbols-file watchlist.txt --factors range_pos_252 --horizons 1 --save --json
python3 scripts/factor_backtest.py run --market A --symbols-file watchlist.txt --factor mom_20_1 --fac-shift 1 --save --json
python3 scripts/factor_verdict.py judge --engine-run <run_id> --backtest-run <path> --register --json
python3 scripts/hypothesis_registry.py stale --days 30
```

所有脚本均保持 `no_order_execution: true`。
