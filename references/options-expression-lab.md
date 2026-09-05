# 股票观点 → 期权表达 → 组合条件压力测试

`scripts/options_expression_lab.py` 是独立研究计算器；`scripts/test_options_expression_lab.py` 是明确标为 synthetic 的合成测试。它不生成订单、不读账户、不输出 ModuleSignal，不替代 Decision Compiler。每次输出都固定 `no_order_execution=true`、`hypothesis_only=true`、`compiler_isolated=true`，最高 `readiness=hypothesis_only`。上游机制 packet 可以引用结果与缺口，不能凭候选排名提高动作等级。

## 与已有能力分工

- `scripts/options_gamma.py`：聚合 Gamma、墙与市场结构，不代表某一蝶式的收益。
- `scripts/short_cycle_signals.py`：期权执行质量与短周期窗，不提供到期收益或组合对冲验证。
- `scripts/earnings_implied_distribution.py`：ATM straddle 中价隐含幅度，不是 physical probability，亦不是实际可成交价格。
- 本模块：单腿 / 垂直价差 / 对称蝶式的完整合约输入、natural 入场成本、到期几何、到期前条件模型、联合持仓压力情景及容量。

## 调用与输入合同

```bash
python3 scripts/options_expression_lab.py --input research-input.json --output research-result.json
python3 scripts/options_expression_lab.py --inspect-chain existing-chain.json --output chain-gap-audit.json
python3 scripts/options_expression_lab.py --self-test
```

Python 入口为 `analyze(payload)`。CLI 退出码：0 = 研究计算可用但无执行权限；2 = blocked；self-test 失败为 1。金额输出保留十进制字符串，`unbounded`/`infinity` 是显式无限尾部，不输出不合法 JSON Infinity。

顶层必需：

| 字段 | 含义 |
|---|---|
| `as_of` | 带时区的研究/入场基准时点 |
| `data_mode` | `observed` 或 `synthetic`；名称不构成数据验证 |
| `policy.max_quote_age_seconds` | 明确给定报价最大年龄 |
| `policy.max_quote_skew_seconds` | 多腿报价最大时间差 |
| `policy.max_spread_fraction` | 每腿 `(ask-bid)/ask` 上限，非 mid 分母 |
| `candidates` | 见下方，每项有唯一 `id` |
| `scenarios` | 每项唯一 `id`、带时区 `at`、`prices` 字典；全部是条件输入，不默认等概率 |

每个 candidate：`id`、`kind=single/vertical/butterfly`、正整数 `units`、`legs`、`costs`。腿的 `signed_qty` 指每一组的带符号合约数，正数买入、负数卖出；组数单独由 `units` 扩大。

每腿必需字段：

```
contract_id, underlying, expiry, last_trade_at, right, strike,
signed_qty, multiplier, currency, exercise_style, settlement_style,
settlement_session, identity_evidence_ref, standard_deliverable_verified,
quote_source, quote_asof, bid, ask, bid_size, ask_size
```

支持标准美国 OCC 合约 ID；ID 根、YYMMDD、C/P、千分之一行权价必须与显式 terms 一致。`expiry` 是准确结算时刻，不能只写日期；`last_trade_at` 是最后可交易时刻，不得在此之后把报价作为新仓研究。两者不可等同推断。`identity_evidence_ref` 应指向已读的交易所/供应商 identity 证据；布尔 `standard_deliverable_verified=true` 必须来自上游核实，本模块不自称验证证据文件。调整合约、未知 deliverable、FLEX 不支持，禁止补默认 multiplier。

所有腿必须同 underlying、结算时刻、right、multiplier、currency、exercise/settlement style 与 session。single 仅 ±1；vertical 两个不同 strike 的 ±1∓1；butterfly 升序 strike 必须等宽且严格 `+1:-2:+1`。SPX 标准产品需额外 `option_root`：SPX 为 AM、SPXW 为 PM，且均 European/cash/100；未知或其他产品须重新实现明确产品合同，不能套此映射。

`costs` 必需 `entry_total`、`expiry_total`、`model_exit_total`、`evidence_ref`。三项均为**整个 requested units 的总金额**，不是每股/每腿费用；零只能显式提供并有证据或明确 synthetic 假设，不缺省造零。佣金、交易所费、结算/行权费等应合并到对应总额。真实费用有条件分档而不能化成固定总额时，本版本不支持，需要先形成适用方案或保留 gap。

## 成本、容量与到期几何

Natural 入场 debit 为 `units × Σ(signed_qty × multiplier × [ask if buy else bid])`，可为负 credit。另加明确费用。没有用 mid 冒充入场可成交；即使报价完整，组合报价/同时成交/滑点仍未保证。

显示容量为 `min(floor(side_size/abs(signed_qty)))`，例如中腿卖 2 张、bid_size=10，最多显示 5 组。只读顶档 snapshot 不代表隐藏深度、稳定容量或实际能成交；不以 open interest/volume 替代盘口尺寸。缺 side size、超容量、过时、不同步、倒挂、过宽报价均 blocked。

到期内在价值为 `Σ(q × multiplier × max(0,S-K))`（call）或 put 的 `max(0,K-S)`，再减 natural debit、entry 与 expiry 费用。最大损益/盈亏平衡通过 `S≥0` 的 0 点、所有 strike 拐点与右尾斜率求解，绝不以有限网格最大值代替真实最大值。输出同时保留 minimum/maximum PnL 和可能存在的零收益区间。

这只是合约内在价值结算边界。美式提前行权、实物交割腿未同时处理、除息、到期 pin/后续股票敞口，可能使真实生命周期不同；不将 contractual max loss 宣传为账户绝对最大损失。未建模借贷、保证金与税务。

## 到期前模型和期限

`scenario.at == expiry` 才能 `expiry_scenario`。早于 expiry 必须给 `scenario.model`：

```
name: black_scholes_european
rate: 显式连续利率
dividend_yield: 显式连续收益率
iv_by_contract: {完整合约ID: 波动率小数}
assumptions: 清楚写模型假设
input_evidence_ref: 输入/情景依据
```

时间采用准确时刻差的 ACT/365 日历年。BS 模型仅 European；候选 American 到期前仍阻断，不能忽视提前行权。已有 American 持仓可使用下述明确假设的 scenario marks，不能拿 BS 冒充其模型。输出每腿模型价格、delta/gamma、每单位 IV 的 vega、每日 theta 与剩余时间；模型价格不是可成交 exit。模型净 PnL 减明确 model_exit_total。输入的 IV/价格变化是假设，不是预测。无模型时不能把 expiry payoff 当隔日 PnL；1D-TE、T+1 和剩余日历时间是不同概念。新候选的结算后再投资未建模，晚于该候选 expiry 的场景被拒绝；已有持仓可用下文显式现金结算政策对齐到共同评估时刻。

## 联合对冲、stress 与概率

持仓支持股票与期权混合，并可与不同 underlying / expiry 的 SPX 蝶式在同一个 payload 下比较。同一 scenario 的 `at` 是所有资产共同评估时刻；各自剩余期限单独由各自 expiry 减 at 计算。

股票保持 `holdings[{kind:stock,symbol,currency,signed_shares,reference_price,as_of,exposure_evidence_ref}]`。`reference_price` 是当前标记价，不是历史买入成本。

期权持仓使用：

```
kind: option
contract_id, underlying, option_root (SPX必要), expiry, last_trade_at,
right, strike, signed_qty, multiplier, currency,
exercise_style, settlement_style, settlement_session,
identity_evidence_ref, standard_deliverable_verified,
as_of, exposure_evidence_ref,
reference_mark, reference_mark_asof, reference_mark_evidence_ref,
costs: {model_exit_total, expiry_total, evidence_ref}
```

`reference_mark` 为研究时点可得的每单位期权当前 mark，不能从历史 entry price 推断；完整 OCC identity 校验与标准 deliverable 条件仍适用。`as_of` 与研究时间相同；reference mark 须满足年龄门，并与新候选各腿 quote 满足同一 skew 门。持仓 costs 是当前之后退出/结算的**整项持仓总费用**，含必要行权与清算成本。

到期前持仓增量 PnL = signed_qty × multiplier × (scenario_mark − reference_mark) − model_exit_total。历史 `historical_entry_cost` 可保留为信息，但**不参与此计算**，避免再扣一遍已发生成本。新蝶式仍按新 ask/bid natural debit 加 entry fee 买入，逻辑与持仓 baseline 分开。

American 持仓在到期前须给每个情景的显式 mark：

```
scenario.marks_by_contract[contract_id]: {
  mark, at, expiry, underlying_price, iv,
  mode: "assumption",
  assumptions: "说明该 mark、IV 情景与假设持仓维持至评估时刻的条件",
  evidence_ref: "情景方法/输入引用，不得写成未来已观察报价"
}
```

mark 的 at 必须等于共同 scenario.at，expiry 必须匹配合约，underlying_price 必须与 `scenario.prices[underlying]` 完全相同；iv 明确提供，不从股价涨跌推断。American mark 低于立即行权内在价值会拒绝。`mode=observed` 的未来 mark 一律拒绝。这里不建立 American 提前行权概率树，未声称 mark 可成交或已校准。

European 持仓可同样使用显式假设 mark，或使用上述 BS；多 underlying 时可通过 `model.by_underlying[underlying]={rate,dividend_yield}` 分别指定利率和收益率，`iv_by_contract` 始终按完整合约 ID。旧顶层 rate/dividend_yield 仍可作为**明确统一假设**，不是自动查得的事实。一个场景可同时给股票、American mark、European model 与已到期资产的现金。

持仓期限比新蝶式更早时，晚于其 expiry 的 scenario 必须给：

```
scenario.settlements_by_contract[contract_id]: {
  at: "该合约的准确 expiry 时刻",
  underlying_price: "到期结算情景价，不能偷用更晚的评估价",
  cash_policy: "settle_to_cash_and_carry", cash_carry_rate: 0,
  physical_delivery_policy: "immediate_cash_equivalent_liquidation",
  assumptions, evidence_ref
}
```

physical_delivery_policy 仅 physical 合约强制，cash 合约可省。把到期净现金按明确连续 carry rate 带到共同评估时刻；若保留实物股票、存在分红或再投资策略，需要另建模型，本版本不猜。恰好到期的 physical 持仓同样必须明确即时现金等值清算政策、假设及证据引用，不能静默消除交割后股票风险。新候选自身仍要求场景不晚于其 expiry；未知的持仓跨期限处理只阻断 hedge 比较，不阻断独立候选收益研究。

所有场景必须同时给持仓标的与新候选 underlying 的价格。同币种才能加总；不同 currency、缺 IV、mark 时间/价格/expiry 不一致、跨 expiry 政策缺失等进入 `hedge_data_gaps`，不会把独立蝶式的完整候选计算误判成 blocked。`baseline_scenario_details` 保留逐持仓增量 PnL、mark/模型依据和剩余期限。
对冲比较还须 `related_exposure_evidence_ref`，并显式覆盖 `stress_tags` 的 `tail`、`basis_break`、`correlation_failure`。标签只是输入合同，需要上游真实设计压力内容；单纯写标签不验证其经济充分性。SPX 蝶式只在有限区间获利，不能被称为普遍个股方向对冲。所有 hedge metrics 都是**给定联合场景下的条件结果**，永不写“已证实有效”。

透明 Pareto 三维：最大化给定场景最坏损失减幅、最小化入场成本、最大化正基准收益场景中最低上行保留比率。只返回不被严格支配的候选；唯一候选、亏损对冲也可能属于 Pareto 集，这绝不是推荐。无正基准收益场景便不能计算上行保留维度，不生成完整排名。没有收益最大自动下单路径。

`expected_pnl` 与 `probability_profit` 默认 null。仅明确提供 `weight_qualification` 才检查：`status=qualified`、`measure=physical`、非空 `validator/method/validation_evidence_ref/limitations`、事前 `frozen_at`、未过期 `valid_until`、完全覆盖 `scenario_ids`、同一 `horizon_at`，以及各场景非负且精确合计 1 的 `weight`。这仍是“以外部资格证据为条件”，本模块不独立验证概率模型；expected_pnl 是这些权重下的情景算术，不等于真实获利概率或校准通过，模型 Greeks 也不赋予概率资格。risk-neutral/普通网格/权重缺失/未来资格或不同期限均不计算概率。风险中性隐含区间不能偷换成实际获利概率。

## 输入核查与公开参考

`--inspect-chain` 检查链文件的身份、逐腿报价时间、乘数、结算方式与费用依据。合约列表可读不代表存在可执行报价；缺失报价或权限时保持 blocked，并列出缺口。

公式与产品边界参考：

- [OCC/OIC Long Call Butterfly](https://prd-web.optionseducation.org/strategies/all-strategies/long-call-butterfly)：同期限等宽 `1:-2:1`，中间价最大盈利、翼外最大亏损、盈亏平衡及提前行权/到期风险。
- [Cboe SPX specifications](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications/)：实际产品结算、行权、multiplier 与最后交易时间；不能仅凭 SPX 名称猜 AM/PM。
- [OCC/OIC Black-Scholes Formula](https://prd-web.optionseducation.org/advancedconcepts/black-scholes-formula)：模型依赖 spot、strike、期限、IV、利率、股息；美式提前行权需适合的模型，理论价格不等于市场成交价格。




模型若显式给 `by_underlying`，每个被估值标的必须有自己的rate/dividend_yield条目；缺项列gap，不静默回退到另一标的或全局参数。未给分标的映射时，原有显式共享参数接口仍可用于已声明的模型情景。
