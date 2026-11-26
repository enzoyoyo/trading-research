# Macro Dashboard · 四象限宏观仪表盘

来源边界：本框架吸收自 `themarketbrew.com/macro` 及其公开可访问子页（流动性、经济、通胀利率、情绪）的结构，不使用订阅周报、登录后内容或不可验证的付费结论。

目标：每次美股、港股美元敏感资产、指数、AI 链、宏观问题，都先回答一个问题：现在更像 `attack`、`neutral` 还是 `defend`？

## 1. 四个桶，不许混写

| 桶 | 必查指标 | 输出问题 | 对交易的直接影响 |
|---|---|---|---|
| 流动性 Liquidity | WALCL、TGA、RRP、银行准备金、NFCI、SOFR、EFFR、净流动性方向 | 市场里的钱是变多还是变少？ | 决定估值扩张能否持续 |
| 经济 Economy | GDP、衰退概率、CFNAI、零售销售、工业产出、就业、收益率曲线/信用利差 | 利润底盘在变好还是变差？ | 决定 cyclical / defensives / duration 偏好 |
| 通胀与利率 Inflation & Rates | Core PCE、CPI、10Y/30Y、实际利率、DXY、能源/电力/铜等资源压力 | 通胀在降温还是反扑？折现率在帮忙还是拆台？ | 决定久期和高估值资产仓位 |
| 情绪 Sentiment | Fear & Greed、VIX、VVIX、SKEW、breadth、junk demand、put/call | 市场是乐观、麻木、还是在偷偷买保险？ | 决定追高容忍度与尾部风险贴现 |

## 2. 每个桶的最小输出格式

每次必须对四个桶分别输出：

1. `current_state`：宽松 / 中性 / 收紧（或健康 / 放缓 / 压力）。
2. `recent_trend`：近 1-4 周改善 / 恶化 / 背离。
3. `next_trigger`：下一个最可能改写结论的发布或价格变量。
4. `market_implication`：对成长 / 周期 / 防御 / 高估值 / 高 beta 的直接影响。

示例：

```yaml
liquidity:
  current_state: fragile_tight
  recent_trend: deteriorating
  next_trigger: TGA drawdown pace / RRP exhaustion
  market_implication: rebound can continue but chasing needs haircut
```

## 3. 合成规则：attack / neutral / defend

| 条件 | 合成结论 | 默认动作 |
|---|---|---|
| 流动性改善 + 经济不恶化 + 通胀回落/利率下行 + 情绪未过热 | `attack` | 可给正常仓位，成长/高 beta 可放大 |
| 四桶互相打架，或只有 1-2 个桶支持风险偏好 | `neutral` | 只做证据最硬的方向，仓位打折 |
| 流动性收紧 + 经济恶化或通胀/长端利率抬头 + 情绪脆弱 | `defend` | 降久期、降杠杆、降高 beta |

## 4. 关键背离解释（本次升级重点）

1. `流动性弱 + 情绪强`：优先解释为估值/情绪拉升，而不是健康牛市；追高降级。
2. `经济放缓 + 通胀降温`：偏向软着陆 / 长久期友好，但若流动性差，只能反弹不等于趋势牛。
3. `AI 叙事强 + 电力/能源/材料 capex 上行`：不自动等于 AI 通缩；允许“增长强 + 通胀粘 + 久期承压”同时成立。
4. `AI capex 花钱方弱 + 收钱方强`：不要把 AI 当成一个整体交易；先拆 `capex_payer_future_monetizer` vs `capex_receiver_current_cashflow`，再用 `capex-cashflow-duration-rotation.md` 判断折现率/现金流久期是否主导分化。
5. `VIX 低位 + SKEW 上升 + breadth 弱`：表面 calm，内部并不健康；防尾部风险成本上升。

## 5. Balder overlay：Fiscal dominance + private QT

1. 财政支出、赤字与利息回流可以暂时托住增长与风险偏好，这属于 `growth support`，不能忽略。
2. 但 mega IPO、增发、发债、AI infra 融资、指数纳入前后的被动资金再分配，会形成 `private_qt` / `liquidity_drain`。
3. 所以宏观结论不能只写“Fed 宽松/紧缩”；必须同时写：
   - `fiscal_support_state`
   - `private_qt_state`
   - 二者谁在主导边际变化。

## 6. 什么时候必须触发本框架

- 用户问“现在能不能进攻/防守/抄底/降风险”。
- 标的是美股指数、AI 大票、半导体、软件、港股美元敏感资产。
- 出现 VIX、黄金、AAPL、长端利率、Skew、GEX、TGA、RRP、IPO/增发/解禁 等词。
- 出现 Waller/Powell/Fed/CPI/PCE/利率变化后，AI Capex 花钱方与收钱方走势明显分化。

## 7. 输出纪律

- 四个桶必须分开写，不能用一句“宏观偏中性”糊弄。
- `attack/neutral/defend` 必须能回链到具体桶与 EID。
- 宏观桶只能约束仓位上限，不能代替个股基本面与市场结构判断。
- 当问题涉及“利好不涨/龙头不稳/能否追高/拥挤交易”时，四象限结论必须与 `endogenous-market-structure-playbook.md` 的 `narrative_stage / crowding / passive_flow / issuance_overhang / rotation_regime` 一起写出，缺其一则“为什么价格不按直觉走”的解释默认不完整（原 macro-structure-overlays 独有段落）。

## 8. Fed 反应函数与已知流日历（v2.30，源自 Frank 2026-06 系列）

### 8.1 reaction_function（数据客观，解读主观，交易的是反应函数）

CPI/PCE/NFP 等数据发布后，禁止只写「数据好/差」。每次数据事件必须写两行：

- `data_surprise`：数据 vs 共识的客观偏差（beat/miss/inline + 幅度）。
- `reaction_function_read`：市场/Fed 会怎么**解读**这个偏差——同一个数字在不同 regime 下含义相反（如衰退担忧期 bad news = bad news；降息预期期 bad news = good news）。价格对数据的**反应方向**本身就是 regime 证据，反应与直觉相反时登记进 Conflict Ledger。

### 8.2 known_flow_calendar（已知机械流不是信息）

月末/季末 passive 基金再平衡、指数纳入/剔除、OpEx、CTA 阈值翻转等**可预先知道日期的机械性资金流**，登记为 `known_flow_events`：

- 作用：解释「无消息的涨跌」、校准入场/离场时点、为 `pre_open_read` 与隔夜层提供背景。
- 边界：known_flow 只影响**时点与短线噪声解读**，不改变中线论点；不得把已知流当成方向信号加仓，也不得用它豁免结构破位（破位纪律仍优先）。
