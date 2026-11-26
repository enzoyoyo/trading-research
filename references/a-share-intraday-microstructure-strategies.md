# A 股日内盘口微观结构信号 · 经济逻辑与晋级路径

## 定位与红线（先读）

本模块提供 7 个盘口与形态信号，由 `scripts/microstructure_signals.py` 计算，逐信号数学定义在 `references/formulas/`。输入为五档快照与分钟 OHLCV，输出采用确定性 schema。工程测试验证计算与合同，不代表预测优势。

**全部 7 个信号的 `validation_posture` 起步值为 `train_only`，且目前实际状态仍是 `train_only`（未晋级）。** 没有一个信号被允许直接映射成 Decision Compiler 的正向 `ModuleSignal` 或仓位倍率。晋级路径见下方「晋级路径」一节；4 个 `snapshot_diff` 信号还有额外的永久限制。

## 数据保真度物理上限（贯穿全文的前提）

免费公开源只能拿到 **~3 秒轮询的五档快照**，没有真正的逐笔委托/逐笔成交（tick-by-tick order & trade）数据。这是物理约束，不是实现疏忽。

输入合同为 `MicrostructureSignal.raw_output` 新增必填字段 `input_fidelity: "tick" | "snapshot_diff"`，并定两条硬规则：

1. 信号声明的 `inputs` 含 `tick_by_tick`，但实际喂的是快照序列时，必须标 `snapshot_diff`，且 `computation_ref`（即对应的 `references/formulas/*.md`）必须写明近似方法、误差来源与失效条件。
2. `input_fidelity="snapshot_diff"` 的信号**永远不得**晋级出 `train_only`，除非用真逐笔数据重新验证。

信号与数据保真度对照：

| 信号 | 保真度 | 备注 |
|---|---|---|
| OrderWall 托压单 | 高 | 五档快照直接可算 |
| OrderImbalance 失衡 | 高 | 五档快照直接可算 |
| TD 九转 / K线形态 / 指标类 | 高 | 1 分钟 OHLCV 直接可算 |
| LimitLeak 开板漏水 | 中 | 快照差分近似（买一封单量骤变） |
| Ignition 大单点火 | 中低 | 快照间价量跳变近似逐笔成交 |
| WallBreaker 破墙 | 中低 | 相邻快照卖档消失+价升近似逐笔扫单 |
| Spoofing 虚假撤单 | 低（结构性误报） | 挂单出现→消失且无对应成交额近似逐笔委托 |

升级路径明确写在矩阵备注里：换成 LongBridge/券商 L2、QMT 等真逐笔数据源即可抬保真度，信号口径本身不变——但那是另一轮研究，本次不做。

## Contract A 字段对照（与 `scripts/microstructure_signals.py` 逐字段一致）

| signal_id | name_cn / name_en | category | inputs（声明） | computation_ref | input_fidelity（实际） |
|---|---|---|---|---|---|
| `order_wall` | 主力托压单 / OrderWall | order_book_depth | level2_5tick | `formulas/order_wall.md#v1` | tick |
| `order_imbalance` | 买卖盘失衡 / OrderImbalance | order_book_depth | level2_5tick | `formulas/order_imbalance.md#v1` | tick |
| `ignition` | 大单点火 / Ignition | order_book_depth | tick_by_tick | `formulas/ignition.md#v1` | snapshot_diff |
| `spoofing` | 虚假撤单 / Spoofing | order_book_depth | tick_by_tick | `formulas/spoofing.md#v1` | snapshot_diff |
| `wall_breaker` | 压单破墙 / WallBreaker | order_book_depth | tick_by_tick | `formulas/wall_breaker.md#v1` | snapshot_diff |
| `limit_leak` | 涨停开板漏水 / LimitLeak | limit_up_game | level2_5tick | `formulas/limit_leak.md#v1` | snapshot_diff |
| `td_sequential` | 九转序列 / TDSequential | indicator | ohlcv_1m | `formulas/td_sequential.md#v1` | tick |

`raw_output` 统一是 `{fired: bool, strength: float 0..1, evidence: {tick_refs: [], bar_refs: []}, input_fidelity}`。`fired=true` 必须带非空 `evidence`，否则视为 bug（`scripts/validate_signal_output()` 强制校验）。

---

## 1. OrderWall · 主力托压单

**盘口痕迹**：某一档（买一或卖一附近，`L1..L5`）在相邻快照间的挂单量净增量 `ΔQ_t(p)` 超过历史中位数的 `k` 倍（默认 `k=2.0`），且同时满足绝对量门槛（`q_min=10000` 股）与金额门槛（`a_min=100万` 元），公式见 `formulas/order_wall.md#v1`。

**经济逻辑（谁留下这个痕迹、为什么）**：能在单次快照间隔（~3 秒）内一次性挂出远超市场平均挂单量、且金额过百万的委托，只有资金体量较大的参与者（游资/机构/主力资金池）才做得到。这类挂单如果压在卖一附近，通常是在人为制造供给压力、试探接盘意愿或为了拉高建仓成本前先洗掉浮筹；如果托在买一附近，通常是在护盘、吸引跟风盘或制造"有人接"的视觉信号。这不是新理论——是经典的挂单博弈假设，哨兵把它做成了可计算指标，但**假设本身尚未被验证**。

**适用市场阶段**：日内任意阶段，但公式内置 `opening_buffer_seconds=180` 开盘缓冲（开盘 3 分钟内不触发，因为开盘阶段挂单量本身波动巨大，容易把正常的价格发现误判为托压），且封板/跌停锁死时段（`LimitLocked_t`）会被过滤（涨跌停封死后挂单结构失去博弈含义）。

**失效场景**：
- 大单可能是分批建仓/减仓的正常挂单节奏，与"操纵意图"无关，公式无法区分动机，只能识别"异常大"这一必要不充分条件。
- 高频算法或做市商的正常报价刷新也可能在瞬间制造大额净增量，产生假阳性。
- 该信号只看单一价位的净增量，无法区分"真实托压"与"挂单后迅速撤单"（后者更接近 Spoofing 的定义域，两个信号可能对同一现象给出不同解读，需交叉验证而非二选一采信）。

**保真度**：`input_fidelity=tick`（矩阵：高）——五档快照直接可算，不存在近似误差层。但"高保真度"只代表输入数据可信，不代表信号本身有 alpha；保真度高不等于已验证。

---

## 2. OrderImbalance · 买卖盘失衡

**盘口痕迹**：五档买盘总量 `B_t` 与卖盘总量 `A_t` 的比值 `R_t = B_t/A_t`（或反向）超过阈值 `r`（默认 `3.0`），且总量达到最低门槛（`q_min=10000`），公式见 `formulas/order_imbalance.md#v1`。

**经济逻辑**：买卖盘挂单量的悬殊失衡，在教科书式的市场微观结构理论里对应"订单流不平衡"（order flow imbalance），常被作为短期价格压力方向的代理变量——挂单堆积的一侧代表当前愿意在该价位交易的潜在对手盘更充裕，价格短期内更容易被推向挂单较少（阻力更小）的一侧。这是被广泛研究过的经典因子（微观结构文献中常称 OFI），但**A 股散户主导、T+1、涨跌停制度**的市场结构和成熟市场做市商主导的场景差异很大，因子在境外市场的有效性不能直接搬到 A 股。

**适用市场阶段**：日内连续竞价阶段；集合竞价阶段的五档结构含义不同（撮合机制不同），公式未对集合竞价做特殊处理，使用时应人工过滤。

**失效场景**：
- 单一大单挂在某一侧即可制造失衡表象，与"广泛参与者共识"是两回事——失衡可能只反映一个账户的行为，而非市场整体情绪。
- 失衡后价格既可能顺失衡方向运行（订单流理论的预期），也可能因为失衡本身诱多/诱空后反向修正（尤其是当大额挂单本身就是策略性挂单而非真实成交意图时）——两种结果在公式层面无法区分，只能靠 walk-forward 回测检验哪种效应在 A 股占主导。
- 分母趋近 0（一侧几乎无挂单）会让比值极不稳定，公式用 `Valid_t = (B_t>0)∧(A_t>0)∧(B_t+A_t≥q_min)` 做了基本过滤，但极端流动性缺失时段仍需人工降级。

**保真度**：`input_fidelity=tick`（矩阵：高）——直接可算，无近似层。

---

## 3. Ignition · 大单点火

**盘口痕迹**：相邻快照间累计成交量增量 `ΔV_t` 超过历史中位数的 `k` 倍（默认 `k=3.0`）且不低于绝对门槛，同时价格突破前一快照的卖一价（`ActiveBuyProxy_t`），公式见 `formulas/ignition.md#v1`。

**经济逻辑**：短时间内放量且价格穿越卖一，符合"主动性买盘扫单"的典型痕迹——愿意以更高价格主动成交而非被动挂单等待，通常代表买方情绪紧迫（抢筹、消息驱动、程序化建仓）。这类"点火"事件在动量交易文献中常被视为短期动量延续的早期信号（主动买盘倾向于持续一段时间，因为背后的信念或算法执行不会一次性完成）。

**适用市场阶段**：日内任意非开盘缓冲阶段；对涨停/跌停附近的价格行为需要与 LimitLeak/涨跌停博弈类信号联合解读，因为封板/开板本身会制造巨量成交但含义完全不同。

**失效场景（尤其重要，因为是近似信号）**：
- `formulas/ignition.md#v1` 明确列出的 `ErrorSources`：`unknown_trade_aggressor`（无法判断真实主动方，只能用"价格触及前卖一"做代理，如果实际是被动成交在高位被撮合，会误判为主动买）、`within_3s_path_aliasing`（3 秒窗口内价格可能来回穿越多次，快照只能看到首尾两个点，中间路径信息丢失）、`book_trade_timestamp_skew`（盘口快照与成交量字段的时间戳可能不完全对齐）、`hidden_liquidity`（隐藏盘/大宗交易不进五档，无法感知）。
- `InvalidWhen` 明确列出的失效条件：标的/session 变化、时间戳缺失或非单调、快照间隔超过 `max_gap_ms=6000`、累计成交量重置（如新交易日）、样本不足、缺失前一快照卖一价、盘口字段缺失、非有限数值。
- **`FidelityLock = train_only_until_tick_by_tick_revalidation`**：本信号永久锁定 `train_only`，不得因为任何回测结果好看就晋级，除非切换到真逐笔成交数据重新验证整个假设。

**保真度**：`input_fidelity=snapshot_diff`（矩阵：中低）。`Approximation = adjacent_snapshot(Δcumulative_volume, Δlast_price, previous_best_ask)`——用相邻快照的成交量差分和价格穿越代理"逐笔主动买"，结构性无法区分"一笔大单"与"多笔小单在 3 秒内叠加"，这两种情况的后续动量含义可能完全不同。

---

## 4. Spoofing · 虚假撤单

**盘口痕迹**：某价位挂单在短生命周期内（`Lifetime ≤ L_max=2` 快照）出现又消失，撤单比例 `CancelRatio ≥ c_min=0.8`（即挂单里至少 80% 是撤掉的，不是成交掉的），且撤掉的部分中被真实成交匹配的比例 `MatchedRatio ≤ m_max=0.2`（即消失的挂单里至多 20% 能用同期成交量解释），公式见 `formulas/spoofing.md#v1`。

**经济逻辑**：真实交易意图的挂单要么被执行（对应真实成交量），要么长期挂在盘口等待。短生命周期 + 高撤单比例 + 低成交匹配的组合，符合"挂单本身不打算成交，只是用来影响其他参与者的价格预期或制造虚假的供需假象"这一操纵行为的定义特征（诱多/诱空、试盘、砸盘前的烟雾弹）。这是证券市场操纵研究中的经典模式（spoofing/layering），美股监管机构对其有明确的执法先例。

**适用市场阶段**：日内任意阶段，尤其在关键点位（整数关口、前高前低、涨跌停附近）更常被观察到，因为这些点位的挂单对其他参与者心理影响更大，操纵动机更强。

**失效场景（本信号是保真度最低的一个，需要格外谨慎）**：
- `formulas/spoofing.md#v1` 的 `ErrorSources`：`no_order_id`（免费源没有订单号，无法真正追踪"同一笔委托"的生命周期，只能用同价位挂单量的出现/消失做代理，可能把两笔不同的委托误判为一笔）、`repricing_vs_cancel_ambiguity`（改价行为在快照层面和撤单+重新挂单无法区分，真实的改价不是操纵）、`level5_queue_exit`（挂单可能只是被挤出五档可见范围，不是真的撤单）、`trades_at_other_prices`（同一时间窗口内其他价位的成交会干扰 `MatchedRatio` 的计算）、`within_3s_invisible_lifecycle`（3 秒窗口内的挂单增减路径不可见）。
- 快照差分下，本信号的保真度为**"低（结构性误报）"**——这不是参数没调好，是数据源的物理限制决定了误报率有下限，调参无法消除。
- **`FidelityLock = train_only_until_tick_by_tick_order_and_trade_revalidation`**：永久锁定 `train_only`，必须有真逐笔委托 + 成交数据（含订单号或至少委托流水）才可能重新验证。

**保真度**：`input_fidelity=snapshot_diff`（矩阵：低，结构性误报）。这是 7 个信号中风险最高的一个：任何下游使用者如果把 `fired=true` 直接解读为"确认存在操纵"，属于对信号本质的误读——它最多是"存在这种模式的统计可能性"，且这个统计可能性本身还没有被验证过真假阳性率。

---

## 5. WallBreaker · 压单破墙

**盘口痕迹**：卖一/卖档大额挂单（`Wall_j(p) > G_j`，`k=3.0` 倍历史中位数）在连续快照中持续非增（被消耗而非补充：`nonincreasing(Q_{j..k}(p))`），价格同步非降（`nondecreasing(ticks(last))`），撤除比例 `RemovedFraction ≥ b_min=0.8`，成交匹配比例 `ExecutedFraction ≥ e_min=0.5`（即消耗至少一半能用真实成交量解释），且最终价格突破该价位（`ticks(last_k) > ticks(p)`），公式见 `formulas/wall_breaker.md#v1`。

**经济逻辑**：与 Spoofing 相反的方向——这里要求"消耗有真实成交量支撑"（`ExecutedFraction` 门槛），意在识别"压单被真实买盘扫掉"而非"压单自己撤了"。如果一堵大额卖单墙被真实成交持续吃掉且价格随之推高，说明买方力量强到愿意付出溢价主动吃掉挂在上方的浮动筹码，是相对更强的短期看涨确认信号（比单纯的价格突破更可信，因为验证了"确实有人在花钱买"而不只是"挂单消失了"）。

**适用市场阶段**：日内连续竞价阶段，尤其是关键阻力位（前高、整数关口、均线压力位）附近更有解释力，因为这些位置的挂单墙通常代表市场公认的心理阻力。

**失效场景**：
- `ErrorSources`：`cancel_vs_execution_ambiguity`（`ExecutedFraction` 门槛已经过滤了一部分，但无法做到 100% 区分"真实执行"与"撤单后又被其他成交巧合覆盖"）、`trades_at_other_prices`（同期其他价位的成交会污染累计成交量差分）、`within_3s_ordering_loss`（3 秒窗口内的挂单/成交先后顺序信息丢失）、`hidden_liquidity`（大宗交易等不进五档）、`independent_price_jump`（价格突破可能是外部消息驱动的独立跳空，恰好与压单消耗同时发生，并非因果关系）。
- 公式要求 `S_min ≤ SweepSteps ≤ S_max=3`——如果扫单过程跨越了超过 3 个快照（约 9 秒以上），公式判定为"不够连续"而不触发，可能漏掉真实但节奏较慢的破墙。
- **`FidelityLock = train_only_until_tick_by_tick_trade_revalidation`**：永久锁定 `train_only`，需真逐笔成交数据重新验证。

**保真度**：`input_fidelity=snapshot_diff`（矩阵：中低）。

---

## 6. LimitLeak · 涨停开板漏水

**盘口痕迹**：涨停封板后（`WasLocked_{t-1}`：前一快照涨停价买一封单量 `Seal_{t-1} > 0` 且达到最低门槛），下一快照的封单量相对骤降超过阈值（`DropFraction_t ≥ d_min=0.5`，即封单量掉了至少一半）或涨停直接被击穿（`Opened_t`：最新价低于涨停价），公式见 `formulas/limit_leak.md#v1`。

**经济逻辑**：涨停封单量是市场对"继续封住"这个共识的直接体现——封单越厚，市场认为封板越稳。封单量骤降通常意味着大额买单撤退（对后续走势失去信心、或主动撤单准备砸盘）或被大额卖单击穿消耗，这是打板/炒作类 A 股短线交易者高度关注的经典盘口信号（俗称"开板"或"炸板"预警），因为开板后的走势分化极大（有的迅速反包重新封板，有的直接大幅回落），提前感知封单变化能争取反应时间。

**适用市场阶段**：仅适用于涨停封板后的时段；跌停对称逻辑本信号未实现（公式与实现只覆盖涨停侧，见 `formulas/limit_leak.md#v1` 中 `L = ticks(limit_up_{t-1})`）。

**失效场景**：
- `ErrorSources`：`within_3s_open_reseal_aliasing`（开板后 3 秒内可能又被重新封回，快照采样可能完全错过这个瞬间过程，只看到"掉了又满"的头尾两态，误判为持续开板）、`queue_replenishment`（封单量下降可能只是暂时的排队位置变化，随即被新买单补上，不代表真实撤退）、`queue_reordering`（大额买单可能只是价格不变但排队顺序调整，被误判为"消失"）、`missing_deeper_levels`（只看涨停价这一档，看不到其后备力量）。
- 涨停价发生变化（如临时停牌复牌、除权除息导致涨停价重算）会使公式失效，`InvalidWhen` 已列入 `limit_up_price_change`。
- **`FidelityLock = train_only_until_higher_frequency_limit_queue_revalidation`**：永久锁定 `train_only`，需要更高频的封单队列数据（而非仅仅是逐笔，是接近实时的封单流水）才可能重新验证。

**保真度**：`input_fidelity=snapshot_diff`（矩阵：中）——四个 snapshot_diff 信号里保真度相对最高，因为只依赖同一价位挂单量的直接观测（不需要推断"主动买卖方向"或"订单生命周期"这类更复杂的隐藏状态），近似链条比 Ignition/Spoofing/WallBreaker 短。

---

## 7. TDSequential · 九转序列

**盘口痕迹**：1 分钟 OHLCV 序列上，收盘价连续 9 根低于（Setup 多头）或高于（Setup 空头）4 根之前的收盘价（`BuySetup_i = close_i < close_{i-4}`），构成 Setup(9)；Setup 完成后继续统计非连续计数的 Countdown 条件（`close_i ≤ low_{i-2}` 等）直到 13，构成 Countdown(13)，公式见 `formulas/td_sequential.md#v1`。这是 Tom DeMark 提出的经典技术分析指标，不是哨兵原创。

**经济逻辑**：TD 序列的假设基础是"价格趋势的持续存在一个统计学上的耐力上限"——连续同方向的价格运动在计数达到临界值后，参与趋势一方的边际买方/卖方力量趋于衰竭，短期均值回归概率上升。这是经典技术分析框架内部自洽的逻辑，但**不依赖任何基本面或资金流信息**，纯粹是价格序列自身的模式识别；其有效性历来存在争议，尤其是否存在跨市场、跨制度的普适性。

**适用市场阶段**：日内趋势/摆动阶段皆可计算，但 A 股 T+1（无法日内反向操作应对信号）与涨跌停限制（价格无法连续运动足够多根 bar 达到 Setup/Countdown 条件，尤其在连续涨跌停的极端行情中）会改变经典 TD 序列在境外连续竞价、无涨跌幅限制市场中的统计特性，不能假设跨市场参数直接适用。

**失效场景**：
- 公式区分 `ConfirmedSetup`（`closed_i`，即该 bar 已完全走完再计数）与 `GhostSetup`（`¬closed_i`，当前未收盘 bar 的"预告"计数）——如果误用未收盘 bar 的 ghost 计数当作确认信号，会在盘中反复出现"即将触发又撤销"的抖动，必须严格只在 `ConfirmedSetup_i`/`ConfirmedCountdown_i` 时判定 `fired=true`。
- 涨跌停限制下价格可能连续多日同方向运动但每日振幅极小（一字板），此时 Setup/Countdown 计数在数学上仍然递增，但其"趋势衰竭"的经济含义是否成立存疑——一字板的价格行为是流动性缺失而非趋势衰竭。
- 与其它 6 个盘口类信号不同，本信号完全不涉及订单簿信息，是纯价格形态假设，需要独立于盘口信号族做 walk-forward 检验，不能因为"保真度高"就假设其 alpha 更可信——保真度只衡量输入数据的准确性，不衡量假设本身的有效性。

**保真度**：`input_fidelity=tick`（矩阵：高）——1 分钟 OHLCV 直接可算，无近似层；但如上所述，高保真度与是否有 alpha 是两个独立维度。

---

## 晋级路径（`train_only` → `confirmed_alive`，全部 7 个信号统一适用）

```
raw signal (microstructure_signals.py)
  → hypothesis_registry.py create（status=train_only，登记本文档的 hypothesis_id，见下表）
  → factor_engine.py / factor_panel.py / factor_backtest.py（实际跑 walk-forward、成本、容量、no-lookahead 检查）
  → factor-validation-strict-gate.md（同宇宙随机对照零假设 + 多重检验校正 + OOS train/test 分割 + 四态裁决）
  → 只有裁决结果为 confirmed_alive，才可能作为正向 module_signal 输入
  → decision_compiler.py（仍需过 Cap & Tighten-Only Registry 的既有风控约束，不因子好就绕过风控）
```

关键规则复述（不是新规则，是既有门槛的应用）：

- `factor-validation-strict-gate.md` §4 四态分类：全样本+OOS 都跑赢随机对照才是 `confirmed_alive`；仅 train 集跑赢 = `train_only`（过拟合证据，不是"弱一点的 alpha"）；负 `alpha_t` = `reversed_strict`；与随机对照统计不可区分 = `noise`。`train_only`/`reversed_strict`/`noise` 一律最高 hypothesis/watch，不得作为加分信号。
- 缺 `random_ic_mean`/`alpha_t`/`n_factors_scanned` 的因子类结论，`readiness_level` 封顶 `research_hypothesis`，`position_multiplier=0.0`。
- `input_fidelity=snapshot_diff` 的 4 个信号（`ignition`/`spoofing`/`wall_breaker`/`limit_leak`）**即使跑出 `confirmed_alive` 级别的回测结果，也不得脱离 `train_only`**，除非同时完成真逐笔数据的重新验证——这是契约 v1.1 的硬规则，回测表现不能替代数据源升级。
- `hypothesis_registry.py` 本身不裁决、不改仓位，只是账本；真正的晋级判定权在 `factor-validation-strict-gate.md` 的四态裁决与 `decision_compiler.py` 的 Cap Registry，不在本文档，也不在任何单一 agent 的主观判断。

## Hypothesis Registry 登记

使用者按 `signal_id` 创建自己的 train_only 假设，在外部映射中保存返回的 hypothesis_id。`SIGNAL_SPECS` 默认不内置任何账户或历史注册标识；模块本身不自动写账本。后续晋级需要独立数据与完整验证，不能用注册成功代替有效性证明。

## 与既有 skill 证据纪律的关系

本文档及其信号定义不新造一套证据体系。`raw_output.evidence.{tick_refs, bar_refs}` 沿用契约 A 已定义的回指机制，与 skill 既有的 `evidence_ids`/EID 体系是同构但独立的命名空间（微观结构信号回指的是快照/K线索引，不是 `EID-*` 标识符）；两者都遵循同一条底层纪律：**没有可回指证据的判断不允许被标记为"已确认"**（`fired=true` 必须带非空 evidence，否则视为 bug）。`templates/intraday-snapshot-to-llm.md` 进一步把这条纪律应用到 LLM 研判输出上。
