# Earnings Event Options Prediction Gate · 财报期权预测门

## 适用场景

用户提供财报预测面板、期权资金流截图、`P(up)` / implied move / P/C volume / IV skew / fresh positioning / system lean / 历史 beat rate，或要求用 LLM 重放历史财报并优化提示词时，必须先走本门。

本门只补充 Evidence 与 Quant Robustness 的准入纪律：**不新增方向模型、动作等级、Compiler module 或仓位上限；通过本门也不自动提高动作等级。** 财报窗口的 `earnings_blackout`、`no_order_execution` 与既有 Decision Compiler 优先级保持不变。

## 1. 先分开四个目标

同一面板常把四件不同的事混成一个“看涨分”，必须拆开评估：

1. **方向**：财报后收益为正还是负。
2. **幅度**：绝对跳空或绝对收益有多大。
3. **波动率相对价值**：realized move 是否高于 option-implied move。
4. **交易收益**：具体股票/期权组合在 bid/ask、滑点、IV crush、theta、期限和对冲后是否赚钱。

方向命中不证明幅度命中；幅度超过 implied 不证明方向模型有效；公司股价上涨不等于买 Call 赚钱；历史 beat 不等于股价上涨。

## 2. Participant Flow 第一性检查

财报前期权的边际参与者至少包括：方向投机者、波动率买方/卖方、持股套保者、covered-call 卖方、价差/跨式/宽跨式交易者、roll/平仓者和做市商。其成交动机与方向并不相同。

### Aggregate P/C 不具备天然方向

`put volume / call volume` 只有合约类型，没有以下信息：

- 买方还是卖方发起；
- 开仓、平仓还是 roll；
- 单腿还是多腿组合；
- 客户、机构、做市商或自营账户；
- delta-equivalent / vega-equivalent 风险；
- strike、expiry、moneyness 和成交相对 bid/ask 的位置。

因此，`P/C=0.46` 不能直接改写成“买入 Call 的资金是 Put 两倍多”。只有能够 point-in-time 识别 buyer-initiated / seller-initiated、open-buy / open-sell、multi-leg 与账户类别的 signed flow，才可登记为待检验方向因子；缺任一关键字段时，对方向权重为 0，并映射到现有 `calculation_quality` / `data_quality` 收紧。

Pan–Poteshman 的结果来自 Cboe 特殊数据中的 **buyer-initiated open-buy** 期权成交，不是普通公开聚合 P/C；其文献结论不能给公开 raw P/C 继承可信度。

### OI 与 signed flow 的字段边界

OCC/OIC 明确：未平仓量本身既不看涨也不看跌，且开平仓配对在**日终清算后**才能确定。因此当日成交量与当日 OI 变化都不能识别“当天新开的方向仓”。可用的最小字段集参考 Cboe Open-Close Volume Summary：participant type（customer / professional / broker-dealer / market maker）× buy/sell × open/close；缺其中任一维度，方向权重为 0。

## 3. Point-in-time 事件快照

任何“财报前预测”必须在结果公布前冻结并保留不可变快照。最小字段：

```yaml
event_id:
symbol:
earnings_release_ts:        # RFC3339 + 时区
release_session:             # BMO | AMC | intraday | unknown
snapshot_cutoff_ts:          # 必须早于 release_ts
underlying_observed_at:
option_chain_observed_at:
consensus_observed_at:
consensus_provider:
option_expiry:
spot:
atm_straddle_mid:
implied_move_formula:
historical_earnings_window:
signed_flow_definition:
oi_publication_ts:
model_id_and_version:
training_cutoff_ts:
prompt_sha256:
feature_schema_sha256:
scorer_sha256:
example_set_sha256:
knowledge_cutoff:
prompt_trial_count:
schema_variant_count:
metric_variant_count:
lockbox_id:
direction_target:
magnitude_target:
volatility_target:
outcome_window:
transaction_cost_model:
```

硬规则：

- `published_at >= earnings_release_ts` 的面板是 `ex_post_narration`，只能复盘，不能计入预测命中率。
- 盘后财报的收益窗口从财报前收盘到下一交易日开盘/收盘；盘前财报则使用当天开盘/收盘。混用窗口即不可比较。
- 共识、期权链、标的价格、财报日历和模型输入均须使用当时可见版本；事后修订值不得回填历史特征。
- OI 通常按日发布；若用次日 OI 解释当日“fresh positioning”，必须标 `lookahead_risk=true`，不得进入动作信号。
- 历史事件必须保留全样本，不能只保存成功图。

## 4. 内部一致性与黑箱门

### 4.1 概率与标签

若 `P(up)<50%` 却给 `BULLISH LEAN`，只有在公开、冻结且可重放的映射能解释基准率、赔率、收益幅度或非 50% 阈值时才可消除冲突。否则进入 `conflict_ledger`，不能把标题当作概率模型结论。

### 4.2 派生字段

`system lean`、`model tilt`、`flow z`、`fresh positioning v/OI` 等字段必须公开：

- 公式或 `calculation_ref`；
- 输入字段与发布时间；
- 训练样本、训练截止日期和 universe；
- 阈值与标签映射；
- 缺失值处理；
- 校准方法和样本外结果。

缺失时按 `calculation_quality` / `quant_robustness` L0/WATCH 处理，不得作为独立 EID。

### 4.3 Beat streak

“过去 8 季度 100% beat”只描述 reported EPS/revenue 相对某一共识，不等于财报后方向。还须控制 whisper、guidance、利润率、Capex、估值、财报前漂移、市场/行业 beta 和预期拥挤。`8/8` 是小样本描述，不能单独形成方向概率或提高仓位。

## 5. Implied vs realized 的正确用途

默认可复现口径是：选取**财报公布后第一个到期日**、最接近 forward 的 ATM strike，按财报前最后一个可交易且流动性合格的 NBBO 快照计算：

```text
implied_move_premium_pct = (ATM_call_mid + ATM_put_mid) / spot_or_forward
```

若 forward 不可靠，可用 spot 近似但必须标 `forward_gap=true`。该值是 straddle 权利金对应的**盈亏平衡幅度**，不是上涨概率，也不是物理世界真实波动的无偏预测。若换算为 one-sigma move，必须另列公式和字段，禁止与 raw premium move 混用。历史比较应优先使用**同一标的过去财报的一日绝对反应**，而非普通日历史波动。

- `historical_earnings_move - implied_move` 是待检验的**波动率相对价值**信号，不是方向信号。
- 对 option branch 必须用可成交 bid/ask、手续费、滑点、delta hedge 与 IV crush 复算；mid-price 结果只算 frictionless upper bound。
- **不得预设 VRP 符号**：不同论文在 earnings-announcement-day 与 pre-earnings 持有窗口得到相反的 long-straddle 平均收益；窗口、样本、筛选、成本与尾部控制共同决定结果，不能把“卖波动有溢价”设为先验。
- 卖跨的历史高胜率不等于安全。任何 short-volatility 候选必须 defined-risk，并报告 CVaR / expected shortfall、最大单事件亏损、最大回撤、保证金与跳空/停牌处置；胜率不能替代尾部风险。
- 文献在不同样本与期权结构上结论不完全一致，且交易成本可能吞掉表面收益；因此只能进入 `quant_robustness` 排序/收紧，不能自动抬高动作或仓位。

## 6. LLM 历史重放与提示词元学习

“告诉模型忘掉未来”不能建立可靠知识边界。对模型知识截止前已经发生的财报，提示词式 simulated ignorance 不能证明预测能力。

允许的两条路径：

1. **前瞻预注册**：事件前冻结模型、prompt、schema 和数据快照，事件后统一评分。
2. **真实时间隔离回放**：模型知识截止早于事件，检索层严格截断，数据快照与索引均为 point-in-time，并做泄漏扫描。

任何历史重放若缺模型版本、knowledge cutoff、prompt/schema/scorer/example-set hash、工具时间过滤或全样本日志，最高 `readiness_level=research_hypothesis`、`position_multiplier=0.0`。

提示词“元学习/自我进化”本质上是有限样本 model selection：

- 必须使用 purged nested walk-forward：内层选择 prompt/schema，外层只估泛化；同一 issuer、fiscal period、accession 的派生样本不得跨 train/test，标签窗口重叠时须 purge + embargo。
- 每轮 prompt、schema、metric 或 few-shot 变体都计入试验预算，记录 `prompt_trial_count` / `schema_variant_count` / `metric_variant_count`；试验次数不能只记最终赢家。
- 最终 lockbox 只允许运行一次。看过 lockbox/test 错误后再改 prompt，该集合即转为 development evidence，必须更换新 lockbox；否则属于测试集调参和多重检验偏差。
- 可用 closed-book probe、before/after 时间切分构造、Min-K%/perplexity 类 membership audit、n-gram/近似改写检查作为污染风险 falsifier；但这些检测只能标 `leak_risk=low|medium|high|unknown`，不能证明闭源模型从未见过样本。`high/unknown` 不得作为唯一升级证据。
- 数值抽取与 schema 合规优先用确定性 scorer；LLM-as-judge 只能作附属解释质量，并须版本化与回归。

## 7. 校准与 Quant Robustness Gate

### 方向模型

- 预注册 `p_up`，报告 Brier、log loss、calibration slope/intercept、分桶 reliability；accuracy 仅作辅指标。
- 对比简单基线：历史正向率、行业/市场方向、财报前漂移、恒定基准率。
- expanding-window / walk-forward，按公司与时间双重隔离；阈值只在训练窗选择。

### 幅度模型

- 报告 MAE、median absolute error、分位数覆盖率和 implied-move 基准。
- 明确定义 close-to-close、close-to-open、open-to-close，不混用。

### 波动率相对价值与交易收益

- 分开报告 `abs(realized_move) - implied_move` 与实际 straddle P&L。
- 纳入 bid/ask、手续费、容量、成交概率、IV crush、到期与 hedge 规则。
- 报告均值/中位数、胜率、最大回撤、尾部损失和跨时期稳定性。

没有 walk-forward、成本、容量、回撤、no-lookahead、随机/简单基准和多重检验控制时，只能登记为 `open` 假设。

## 8. Adopt / Adapt / Reject

| 帖中机制 | 裁决 | 本 Skill 落点 |
|---|---|---|
| 结构化 schema 作为输入种子 | Adopt | 事件前 point-in-time 快照；hash + cutoff |
| 全样本校准、监控过拟合 | Adopt | `hypothesis-lifecycle.md` + `factor-validation-strict-gate.md` + calibration scorecard |
| implied move vs 历史财报 move | Adapt | 波动率相对价值；只进 `quant_robustness`，tighten-only |
| IV skew / term structure | Adapt | positioning/hedge-demand clue；不单独定方向；须先剔除高借券费标的 |
| raw P/C volume 直接推聪明钱方向 | Reject | 未签名聚合量对方向权重为 0 |
| fresh positioning v/OI 无发布时间 | Reject | `lookahead_risk`; OI 时间未证实时 L0 |
| 黑箱 system lean | Reject | 无公式/阈值/训练截止即 `calculation_quality` 缺口 |
| 8/8 beat 直接推出上涨 | Reject | 小样本、已定价、未控制 guidance/whisper/估值 |
| 事后成功截图作为命中率 | Reject | `ex_post_narration`; 不进入 calibration denominator |
| LLM 回放历史 + 提示词“忘记未来” | Reject/Adapt | 只接受前瞻预注册或真实时间隔离回放 |

## 9. Decision Compiler 映射

不新增 module：

| 缺口/信号 | 既有 module | 默认影响 |
|---|---|---|
| 面板发布时间不早于财报 | `research_readiness` | L0/WATCH/0；仅复盘 |
| 概率与标签映射冲突 | `conflict_ledger` | 认知型 veto；人工复核 |
| raw P/C 被当作 directional signed flow | `calculation_quality` | L0/WATCH/0 |
| OI 发布时间不明或次日回填 | `quant_robustness` | L0/WATCH/0 |
| 黑箱派生分数 | `calculation_quality` | L0/WATCH/0 |
| 历史回放无真实时间隔离 | `quant_robustness` | `research_hypothesis`; 0 仓位 |
| implied-realized 信号通过初筛 | `quant_robustness` | 可参与研究排序；不得抬高 upstream cap |
| 财报窗口 | `event_proximity` / `execution_window` | 沿用 `earnings_blackout`，移出可交易集 |

## 10. 证据锚点与边界

- Pan & Poteshman, *The Information in Option Volume for Future Stock Prices*, NBER Working Paper 10925 / RFS 2006: https://doi.org/10.3386/w10925
- Kaeck/Johannes 等，*Option Pricing of Earnings Announcement Risks*: https://doi.org/10.1093/rfs/hhy060
- Milian, *The Efficiency of Weekly Option Prices around Earnings Announcements*: https://doi.org/10.3390/jrfm16050270
- Gao, Xing & Zhang, *Anticipating Uncertainty: Straddles around Earnings Announcements*, JFQA 53(6):2587–2617: https://doi.org/10.1017/S0022109018000285
- Chung & Louis, *Earnings announcements and option returns*, Journal of Empirical Finance 40:220–235: https://doi.org/10.1016/j.jempfin.2016.07.010
- Muravyev, Pearson & Pollet, *Why does options market information predict stock returns?*, JFE 172:104153: https://doi.org/10.1016/j.jfineco.2025.104153
- OCC / OIC, *Options FAQ — open interest is neither bullish nor bearish; open/close pairing is end-of-day*: https://www.optionseducation.org/referencelibrary/faq/general-information
- Cboe DataShop, *Open-Close Volume Summary*（participant type + buy/sell + open/close 才是 signed flow 的最小字段集）: https://datashop.cboe.com/cboe-options-open-close-volume-summary
- Li et al., *Simulated Ignorance Fails*: https://arxiv.org/abs/2601.13717
- Shi et al., *Detecting Pretraining Data from Large Language Models*: https://arxiv.org/abs/2310.16789
- Cawley & Talbot, *On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation*, JMLR 11(70):2079–2107: https://www.jmlr.org/papers/v11/cawley10a.html

这些来源支持“哪些机制值得检验”和“哪些回测会泄漏”，不证明任一具体面板已有样本外 edge。任何作者自报命中率、截图或订阅引流都只能进入 provenance / watch 层，不能提高 reliability、动作等级或 position cap。
