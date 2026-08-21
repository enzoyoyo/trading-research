# ETF Selection & Rotation

## 适用场景

用户问「哪个 ETF 更好」「该不该换仓」「这个行业主题 ETF 现在能不能追」「ETF 还是直接买正股」。不适用于个股选股（走 `method-rotation-matrix.md`）或指数期货/期权对冲（走 `a-share-derivatives-ipo.md` / `options-gamma-structure.md`）。

## 筛选维度（同类 ETF 对比时全部过一遍）

1. **费率**（管理费+托管费）— 结构事实，非每日变量。
2. **跟踪误差** — 与标的指数的年化偏离度；数据缺口时不得假设为 0。
3. **流动性** — 日均成交额、买卖价差、做市商深度；决定能否无冲击成交。
4. **规模（AUM）** — 过小基金有清盘/流动性枯竭风险；A 股场内基金 <2 亡清盘预警线（`references/data-source-playbook.md` 未覆盖清盘阈值时按交易所公告口径，不臆测具体数字）。
5. **溢价/折价率** — 场内 ETF 相对 IOPV 的偏离；A 股此项为**实时可得**（见下），美股/港股需比对 NAV 披露延迟。
6. **持仓集中度** — 前十大持仓权重，判断是否名不副实（如"科技 ETF"实为单一巨头代理）。

## 数据源（已实测）

| 维度 | 市场 | 命令/函数 | 实测结果 |
|---|---|---|---|
| 实时报价+溢价折价+流动性+资金流 | A 股场内 ETF | `ak.fund_etf_spot_em()` | 已测通，1549 只全市场快照；关键列 `最新价`/`IOPV实时估值`/`基金折价率`/`成交额`/`换手率`/`主力净流入-净额` |
| 净值历史 | A 股场内 ETF | `ak.fund_etf_fund_info_em(fund="510050", start_date, end_date)` | 已测通，仅 `单位净值`/`累计净值`/`日增长率`，**不含费率**；费率/跟踪误差需 Grok/web_search 补 |
| 实时报价 | 美股/港股 ETF | `python3 scripts/longbridge_query.py quote SPY.US 2800.HK --json` | 已测通，ETF 与个股同接口；不返回费率/AUM/持仓集中度 |
| K线 | 美股/港股 ETF | `longbridge_query.py candle <symbol> --period day --count N --json` | 复用个股 K 线路径，无需单独适配 |
| 费率/跟踪误差/AUM/持仓集中度（三市场通用） | 全部 | Hermes Grok / web_search | 结构性慢变量，无实时 API；每次结论必须带查询日期，超过 90 天须重查 |

**DataGap**：LongBridge 与 AkShare 均无费率/跟踪误差/持仓集中度字段。这三项永远走 Grok/web_search，缺查不到就在数据缺口区块写明，不得假设「费率与同类平均持平」。

## 行业/主题 ETF 轮动

轮动信号不新建方法论层——判断该行业当下该用哪套选股权重（游资情绪 / Serenity / 华源叙事 / 内生结构），查 `references/method-rotation-matrix.md` 对应场景行；ETF 只是该行业的一篮子代理，轮动逻辑与行业本身的方法论权重完全复用。

ETF vs 个股替代决策的核心问题：**用户想要的是行业 beta 还是个股 alpha**。
- 只想要行业方向、不判断个股胜率 → ETF，优先选流动性/跟踪误差最优的一只，不做多只叠仓（重复暴露）。
- 判断出行业内有明确胜出个股（供应链瓶颈/份额集中度证据） → 直接持股，ETF 反而摊薄 alpha；需要 `endogenous_structure` 与 `fundamentals` 的个股证据支撑，不能仅凭「行业景气」就替代为个股。

## Decision Compiler 映射

不新增模块，全部复用既有 `REGISTERED_MODULES`，用 `sub_framework` 区分信号类型：

| 信号 | module | sub_framework | 封顶/收紧行为 |
|---|---|---|---|
| 费率/跟踪误差/AUM/持仓集中度（结构事实） | `fundamentals` | `etf_structural_quality` | 数据缺口只降级，不阻断；不单独提高 action level |
| 溢价折价率/流动性/换手率 | `liquidity` | （沿用既有语义，无需新 sub_framework） | 折价率异常扩大只收紧，不作为加仓理由 |
| 主力净流入/资金流方向 | `participant_flow` | （沿用既有） | 与决策方向一致最多 `confidence += 0.1`；唯一可加分项，不提高 action level（Cap & Tighten-Only Registry） |
| 行业轮动动量/相对强弱 | `endogenous_structure` | `etf_sector_rotation` | 缺同期货 or 期权确认时最高 L1 watch |

清盘风险（规模持续低于阈值 + 折价率持续走阔）视为 `risk_regime` 的 tighten-only 输入，触发时 `holding_directive` 最高收紧到 `REDUCE`，不因流动性尚可就豁免（这是市场风险型 hard veto 集合之一的延伸判断，最终以 `references/decision-compiler.md` Redline Cap 大表当次判定为准）。

## 边界

- ETF 期权（50ETF/300ETF/500ETF 期权）不在本文件范围，走 `references/a-share-derivatives-ipo.md`。
- 债券/商品/杠杆反向 ETF 的净值路径不在本文件范围（杠杆 ETF 有复利损耗，需单独提示，不适用本文件的溢价折价框架）。
- 不构成投资建议；ETF 选择结论必须标注数据截止时间与费率/跟踪误差的查询日期。
