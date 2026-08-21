# A-Share Derivatives & IPO Subscription

## 覆盖范围

A 股 ETF 期权（50ETF/300ETF/500ETF）、可转债（双低框架+强赎条款风险）、新股/可转债打新（申购类 Tier 0/1 场景）。美股/港股期权走 `options-gamma-structure.md`；本文件不重复其框架，且**明确二者不可直接移植**（见下）。

## A 股 ETF 期权

### 为什么美股 Gamma 框架不能直接搬过来

美股 `options-gamma-structure.md` 的 Put Wall / Call Wall / Gamma Flip 判定依赖逐档 strike 的 OI × dealer gamma 敞口重建，前提是能拿到可靠的做市商持仓结构代理（CBOE 数据+做市商对冲行为假设）。A 股期权市场目前只能拿到：

- 逐合约现价/行权价/涨跌幅（无法直接推做市商敞口）
- 逐合约希腊字母（delta/gamma/vega/theta，来自交易所官方风险指标）
- **按标的**聚合的总成交量/总持仓量/认沽认购比（无法拆到逐档 strike 的净 gamma 敞口）

也就是说，能算出"認沽/認購比、总 OI 结构"这类方向性情绪信号，但算不出可信的逐档 Put Wall/Call Wall 价位。**任何 A 股期权分析都不得声称给出了"Gamma Flip 价位"或"Put Wall 支撑位"——这是编造，不是保守估计。**

### 数据源（已实测，2026-07-19）

| 数据 | 函数 | 实测结果 |
|---|---|---|
| 逐合约行情+行权价（50ETF 期权链） | `ak.option_finance_board(symbol="华夏上证50ETF期权", end_month="YYMM")` | 已测通，返回当前价/前结价/行权价/涨跌幅，24 档 |
| 逐合约希腊字母（delta/gamma/vega/theta/IV） | `ak.option_risk_indicator_sse(date="YYYYMMDD")` | 已测通，762 行，交易所官方风险指标，覆盖全部上交所期权合约 |
| 按标的聚合 P/C 比 + 总 OI + 成交量 | `ak.option_daily_stats_sse(date="YYYYMMDD")` | 已测通，5 个标的（50/300/500ETF 等），含 `认沽/认购`、`未平仓合约总数`、`未平仓认购/认沽合约数` — **这是最有信息量的单次调用** |

### 可用信号 vs 不可用信号

| 可以做 | 不能做 |
|---|---|
| 认沽/认购比偏离历史区间 → 情绪极端信号 | 声称精确 Gamma Flip 价位 |
| 总持仓量环比变化 → 参与度信号 | 声称精确 Put Wall/Call Wall 支撑压力位 |
| 隐含波动率（`IMPLC_VOLATLTY`）相对标的历史波动率 | 逐档 net dealer gamma 敞口重建 |
| 逐合约 delta 判断该合约的实值/虚值程度 | 用美股 VRP 门框架（`options-gamma-structure.md` 那套）直接套用 |

## 可转债（双低框架 + 强赎条款风险）

### 数据源（已实测）

`ak.bond_zh_cov()` — 已测通，1035 只全市场快照，关键字段：`债现价`（债券价格）、`转股溢价率`（conversion premium）、`转股价值`（conversion value）、`正股价`、`转股价`、`信用评级`。

### 双低框架

"双低" = 债现价低 + 转股溢价率低，两者都低代表下有债底保护、上有转股期权价值，且期权部分尚未被市场充分定价。用 `bond_zh_cov()` 的 `债现价` 与 `转股溢价率` 两列直接排序即可筛出候选池；不新建评分公式，双低本身就是筛选标准，不做加权合成分。

### 强赎条款风险（收紧信号，不是买卖建议）

多数可转债条款约定"正股价连续 N 个交易日 ≥ 转股价 130%"触发强制赎回。`正股价 / 转股价` 比值可以算出**距离触发条件的粗略接近度**，但：

- 实际触发条件（连续天数、比例阈值、是否已被公司公告提前赎回）因券而异，必须查该债券募集说明书或交易所公告确认，`bond_zh_cov()` 的通用字段不能替代逐券条款核实。
- 比值接近 130% 时只能标注"临近强赎观察区间"，不能断言"即将强赎"——这是 tighten-only 信号，只降级持有可转债的安全边际判断，不触发卖出建议。
- 已被公司公告强赎的债券，转股期权价值归零，必须尽快转股或卖出——这条是硬事实判断，不是 Decision Compiler 裁决范畴，一旦发现应直接如实告知用户核实公告，不要等下一轮裁决。

## 新股/可转债打新（Tier 0/1 场景）

打新申购本身是低风险的申购流程类问题（中签概率、申购上限、申购日期），不是仓位风险决策，**不需要走完整 Decision Compiler 管线**——按 Tier 0/1 直答格式处理：

```
用户，{{BOND_NAME}} 今日可申购，申购代码 {{CODE}}，申购上限 {{LIMIT}} 张，正股 {{UNDERLYING}} 现价 {{PRICE}}/转股价 {{CONV_PRICE}}（转股价值 {{CONV_VALUE}}）。

数据时间：{{DATA_TIMESTAMP}}；来源：AkShare bond_zh_cov
```

`ak.bond_zh_cov()` 的 `申购日期`/`申购代码`/`申购上限`/`中签率` 字段直接覆盖此场景；A 股新股（非可转债）打新走 AkShare 对应的 `stock_zh_a_new`/`新股日历` 类函数，如未实测通过则明确标注 `data_gap`，不得编造申购日期或中签率数字。

## Decision Compiler 映射

不新增模块：

| 信号 | module | sub_framework | 封顶/收紧行为 |
|---|---|---|---|
| ETF 期权 P/C 比、总 OI 结构、IV | `endogenous_structure` | `a_share_option_positioning` | **最高 L0/L1，永不 hard veto**——明确区别于美股 `gamma` 模块的 Put Wall hard veto 权限，因为逐档敞口不可重建 |
| 可转债双低筛选（债现价+转股溢价率） | `fundamentals` | `convertible_bond_dual_low` | 正常参与裁决，不单独加分 |
| 强赎临近观察 | `fundamentals` | `convertible_bond_dual_low` | tighten-only，只降级安全边际，不触发卖出信号；已公告强赎的直接如实告知，不等裁决 |
| 打新申购信息 | — | — | Tier 0/1 直答，不进入 Decision Compiler |

## 边界

- A 股期权分析中出现"Gamma Flip"/"Put Wall"/"Call Wall"字样即视为误用美股框架，必须改写为"认沽认购比"/"总持仓结构"等实际可支撑的表述。
- 强赎条款判断只做接近度提示，逐券条款以公司公告为准，不臆测。
- 不构成投资建议；打新中签率/申购上限等数字必须标注数据时间，不使用记忆中的旧值。
