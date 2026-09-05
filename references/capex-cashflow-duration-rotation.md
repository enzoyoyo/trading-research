# Capex Cashflow Duration Rotation · Capex 收钱方/花钱方与现金流久期轮动

> 主落点声明：编译进 `endogenous_structure`；`macro` / `fundamentals` 是下文的次级证据落点。

## 核心命题

同一个 AI / 半导体叙事里，先拆两类公司：

1. **Capex 花钱方 / 未来变现方**：现在投入数据中心、GPU、网络、电力、模型与云基础设施，利润更多依赖未来平台化、订阅、推理需求或广告/云复利兑现。
2. **Capex 收钱方 / 当前现金流方**：把芯片、存储、网络、IP 授权、设备或材料卖给花钱方，当前收入、订单、毛利和 EPS 更快兑现。

利率/折现率变化会改变两类公司的相对吸引力：

- **折现率上行 / term premium 抬升 / 鹰派环境**：未来现金流被打折，市场更偏好当前可见现金流；Capex 收钱方可能相对占优。
- **折现率下行 / 实际利率回落 / 好鸽派 risk-on**：远期现金流久期重新值钱，Capex 花钱方和高 beta 资产可能重新占优。
- **坏鸽派 / 衰退式降息**：不是简单利好长久期，需回到 macro 四象限和 earnings risk。

这不是单票买卖信号，而是一个**跨标的解释和方法配权 overlay**。

## 触发条件

当出现以下问题或信号时加载本 overlay：

- 用户问 AI 链 / 半导体 / 大科技为什么明显分化。
- 社媒/KOL 提到“谁花钱谁收钱”“Capex”“折现率”“高利率下买半导体避开 hyperscalers”。
- 同一天出现：MSFT/AMZN/GOOG/META 等 Capex 花钱方走弱，而 MU/ARM/AMD/AVGO/MRVL/NVDA/设备/网络链走强，或反过来。
- Fed/Waller/Powell/CPI/PCE/10Y/30Y/real yield 触发利率预期变化，但股票反应不是“全体成长股同涨/同跌”。

## 分类表

| 类型 | 现金流时间分布 | 常见标的/环节 | 优先验证项 | 利率敏感性 |
|---|---|---|---|---|
| `capex_payer_future_monetizer` | 现在花钱，未来靠平台/云/广告/推理变现 | hyperscalers、AI 平台、部分软件 | Capex、FCF margin、ROIC、AI monetization KPI、云收入增速 | 长久期，折现率上行时估值更脆弱 |
| `capex_receiver_current_cashflow` | 当前卖货/授权/长约，现金流更近 | GPU、存储、ASIC、网络芯片、IP、设备、材料 | 订单、backlog、ASP、毛利、EPS 上修、客户集中度 | 相对短久期，但受周期和估值拥挤约束 |
| `hybrid_bridge` | 当前有收入，但相当部分仍靠未来份额/渗透率 | AMD、ARM、MRVL 等视具体数据而定 | 当期收入兑现 + 市占率/客户进展 + 估值 | 两头敏感，必须逐票拆 |

> 例子只是路由提示，不是固定分类。每次都要用最新财报、分部收入、Capex/FCF、订单和共识修正确认。

## 必查数据

最低证据要求：

1. **行情横截面**：同日/同周 payer basket vs receiver basket 相对表现，注明时间戳和数据源。
2. **利率变量**：10Y/30Y、real yield、Fed path 或事件来源；不要只用“偏鹰/偏鸽”口头判断。
3. **现金流证据**：Capex、FCF、毛利、收入确认、backlog/订单、EPS/consensus revision 至少选 2 类。
4. **结构证据**：拥挤度、期权/Gamma、ETF/passive、short squeeze、Meme/动量是否在放大价格表现。

LongBridge 优先取：`quote`、`financial_report_latest`、`financial_statement`、`consensus`、`valuation`、`industry_valuation`；缺数据时降级并标注。

## 输出格式

```yaml
capex_cashflow_duration_rotation:
  trigger: "AI capex payer/receiver divergence | rate shock | social thesis"
  rate_regime: "hawkish_discount_rate_up | good_dovish_risk_on | bad_dovish_growth_scare | mixed"
  payer_basket:
    examples: ["MSFT.US", "AMZN.US", "GOOG.US"]
    relative_performance: "underperforming|outperforming|mixed"
    cashflow_duration: "long"
  receiver_basket:
    examples: ["MU.US", "ARM.US", "AMD.US", "AVGO.US", "MRVL.US"]
    relative_performance: "underperforming|outperforming|mixed"
    cashflow_duration: "shorter_or_more_current"
  interpretation: "market is rewarding current AI cashflow over future AI monetization"
  evidence_grade: "S|A|B|C|D"
  decision_mapping:
    module_signal: "macro|fundamentals|endogenous_structure"
    action_effect: "relative_preference_only; no standalone_upgrade"
```

## 与 Decision Compiler 的关系

- 本 overlay **不新增 module**，只把输出映射到 `macro`、`fundamentals` 或 `endogenous_structure`。
- 它只能解释相对强弱、调整研究优先级、约束仓位倍率；**不得单独提高 action level 或 position cap**。
- 如果证据只有截图/社媒，没有行情、财报/订单或利率变量交叉验证，最高只能作为 `frontline_clue` / `watch priority`。
- 若与 `risk_regime`、Gamma hard veto、账户风险或基本面 falsifier 冲突，后者优先。

## 常见误判

1. **把 AI 当成一个整体交易**：同一叙事下，花钱方和收钱方现金流久期不同。
2. **把“高利率利空成长”写得太粗**：当前现金流成长股和远期现金流成长股受影响不同。
3. **把单日涨跌当基本面确认**：也可能是 short squeeze、动量/Meme、ETF/passive 或 Gamma 放大。
4. **把鸽派简单等于大厂利好**：先区分好鸽派 risk-on 还是坏鸽派 growth scare。
5. **把供应链收钱方永久看多**：收钱方也会有周期、客户集中、估值拥挤、订单前置和库存风险。

## 学习模板

看到类似观点时，按这 6 句压缩：

```text
1. 它在说哪个宏观变量？
2. 这个变量改变了哪类现金流的折现？
3. 谁是 Capex 花钱方，谁是 Capex 收钱方？
4. 当前行情是否验证 payer vs receiver 的相对强弱？
5. 这是基本面兑现、估值重估，还是仓位/结构放大？
6. 什么条件会证明这个解释失效？
```

## 失效条件

- 利率下行但 payer basket 继续弱，说明问题可能不是折现率，而是盈利/Capex 回报疑虑。
- receiver basket 没有订单/EPS/ASP 支撑却继续涨，说明更可能是仓位和动量，不应上调基本面置信度。
- payer basket AI monetization KPI 明确兑现、FCF 仍强，且估值已回落，则“花钱方被高利率压制”的解释需要降级。
- 宏观进入 `active_deleveraging` / `forced_liquidation` 时，本 overlay 失效，回到系统风险优先。
