# 杠杆拥挤与离散度回归 Playbook · Leverage-Crowding & Dispersion-Unwind

> 这是 deleveraging-liquidity-squeeze-playbook（流动性挤兑层）之外的**第二条 regime 通道**。
> 流动性挤兑层回答"是不是在卖资产换现金"；本层回答"杠杆有没有堆到极致、相关性是否正在回归 1"。
> 来源：实盘复盘框架（示例）（半导体做空 + 韩国左尾）的可迁移、可验证部分。

## ⚠️ 迁移纪律：机制可复用，实例不可套用

本文里有两种东西，用前先分清，否则会把"这次的故事"误当成"永远的规则"：

| 类别 | 内容 | 怎么用 |
|---|---|---|
| **可迁移机制**（method） | 杠杆→离散度→相关性回归1 的因果链；COR1M/VIXEQ 的统计门槛；杠杆单票 ETF 爆破*模式*；监管强制卖盘*这一类*；gamma-flip 触发*逻辑* | **每次都适用**，跨市场、跨标的复用 |
| **一次性实例**（instance，仅 2026-06 半导体/韩国 case study） | 2x SK 海力士、MSTU/MSTX、韩国 10% 上限、QFLP 预提税、PDT 取消、COR1M=6.33、SPX HVL=7495、AVGO/NFP/Meta 催化剂 | **只是举例说明机制**，分析其它市场/时点时**禁止照抄**；必须用当时当地的真实事实重新填，无对应项就写「N/A / 缺口」 |

下面凡出现具体公司名、点位、税率、监管条款，一律属"实例"——读它是为了理解机制长什么样，不是把它搬进新报告。COR1M/VIXEQ 本身是 CBOE 标普 500 口径，反映的是**美股**杠杆拥挤；分析 A 股/港股时它只能作为"全球风险背景"，不能直接当本地信号。

## 核心命题

**暴跌的原动力从来不是基本面，而是杠杆。** 催化剂只决定时点，结构决定方向。

> "所谓的暴跌，本质上就是相关性回归 1 的过程。"

把它拆成一条因果链：

```
牛市浮盈 → 资金加杠杆、分散押在彼此独立的单票（主要半导体）→ 相关性被压到地量(COR1M)
        → 个股投机溢价拉到极致(VIXEQ−VIX) → 结构脆弱、燃料堆满
        → 任意冲击(催化剂) → 相关性瞬间从≈0 弹回 1 → 所有票一起跌
        → 杠杆被迫同时平仓 → 多杀多、越卖越跌、越跌越卖（自我强化）
```

判这条链是否成立，先看两个 CBOE 指标（自动取数，见下），再辅以 call/put ratio 等情绪指标。

## 1. 两个核心指标的原理与读数

### COR1M（CBOE 1 个月隐含相关性指数）— 拥挤/离散度

- **原理**：指数方差 ≈ 成分股平均方差 × 相关性。当个股都在涨、却彼此独立地涨（资金分散押各自的故事），相关性被压到地量，指数被人为压平。
- **危险点**：相关性地量 = 燃料堆满。任何冲击会让相关性在一瞬间从≈0 弹回 1，所有票一起跌、所有杠杆同时被平。
- **读数门槛**（脚本用历史全样本分位，2006 起）：
  - `≤ 5% 分位`：离散度极端、**结构性埋雷**（示例复盘里 COR1M 收 6.33 ≈ 历史最低，就是这一档）。
  - `≤ 25% 分位`：拥挤累积中。
  - **单日 ≥ +15% 或 5 日 ≥ +30%**：相关性**正在回归 1** = 多杀多进行中（最高危）。

### VIXEQ（CBOE 标普 500 成分股波动率指数）— 单股投机/杠杆

- **原理**：VIX 用 SPX 指数期权，算"整个指数的恐慌"；VIXEQ 对每只成分股个股期权各算一次类 VIX，再按市值加权，算"平均每只个股的恐慌"。
- **关键读数是 VIXEQ − VIX 溢价**：当单股 call 买入极度激进、投机高度集中在 MU/SNDK/INTC/NOK 这类名字上时，个股期权被买得很贵，VIXEQ 相对 VIX 的溢价拉到极致。**这是单股投机与杠杆最直接的读数。**
  - 溢价 `≥ 80% 分位`：单股投机过热。

### 自动取数（修好了 Yahoo 取不到的坑）

```bash
python3 scripts/dispersion_crowding.py --json
```

- 数据源 = CBOE delayed JSON（免费、无 key，与 `options_gamma.py` 同一套 endpoint）：
  - `https://cdn.cboe.com/api/global/delayed_quotes/quotes/_COR1M.json`（现值）
  - `https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_COR1M.json`（历史）
  - VIXEQ / VIX 同构。
- **不要用 Yahoo 拉 `^VIXEQ`/`^COR1M`——Yahoo 对这两个指数 404**（已实测，曾导致整次会话空转）。
- 脚本输出 `dispersion_crowding_state` ∈ {`benign` / `crowding_building` / `dispersion_extreme` / `correlation_unwind_active` / `unavailable`}，并给出映射 regime 与动作影响。
- call/put ratio 等辅助情绪指标无稳定免费源 → 留手填入口，缺则记缺口，不脑补。

## 2. 催化剂 vs 结构（判读纪律）

导火索是什么**不重要**。AVGO 财报、SemiAnalysis 对 MU 的负评、"过热"的 NFP、Meta 增发——全是"萨拉热窝的那一声枪响"。与今年 1 月底白银一样：Warsh 上台只是导火索，真正理由是暴涨中堆积的满载浮盈杠杆一旦出逃就引发多杀多。

**纪律**：
- 不要把分析重心放在"找导火索"。先判结构（杠杆/拥挤/离散度），结构脆 + 催化剂到位 = 才是动手时点。
- 不做 dead bull / dead bear：结构翻空了就尊重结构，不为旧观点辩护。
- 这不是基本面崩塌。半导体若被杠杆盘恐慌杀跌，应在另一侧择机重建；**但在拥挤叠杠杆的极值上，纪律要求先降 beta、开保护**。

## 3. 杠杆单票 ETF 左尾爆破模型（重点新增）

最危险的左尾形态，来自**全球最大单股 ETF 类杠杆产品**（例：2x SK 海力士；对照 2024 年 11 月 MSTR 系 2 倍 ETF MSTU/MSTX）。

机制：

1. ETF 触及 swap 交易对手的杠杆上限 → 被迫将大比例基金配置进**看涨期权**以维持目标敞口（MSTU/MSTX 当时约 27%）。
2. 制造出"**任何价格皆需买 delta**"的非弹性需求 → 把隐含波动率推升至失控；ETF 对标的上涨日的巨幅已实现波动有显著贡献，反过来移动 vol 市场。
3. **跌停 = 没有减仓窗口**：标的开盘直接跌停，ETF 无法在跌停板卖出对冲、无法降杠杆，只能带着远超 2x 的杠杆进入次日。
4. 隔日标的继续下杀 → ETF 被击穿甚至清盘 → 恐慌从首尔蔓延到香港，可能引发金融海啸并传导更远。

**信号清单（出现即升级左尾警戒）**：
- 标的 100 vol 之上还在构建/放大杠杆 ETF（成功概率极低）。
- 标的 call 需求走出抛物线后**开始松动**——往往是抛售临近的信号（"call 需求一旦松动，往往即抛售临近"）。
- 标的有单日跌停制度且接近跌停 → 评估"无对冲窗口 → 隔日超额杠杆"的链条。

## 4. 监管 / 结构性强制卖盘（与"被动供给"并列的一类）

不是基本面，也不是情绪，而是**规则逼出来的卖盘 / 抽走的边际买盘**：

| 类型 | 例子 | 机制 | 影响 |
|---|---|---|---|
| 单一持股硬上限 | 韩国 10% 单一持股上限 | SK Hynix/Samsung 持续涨 → 大量基金突破上限 → 合规被动卖出数十亿美元再平衡 | 这些票从此边际转净卖出；监管给了硬性需求上限；机构转向"影子股"（如 SK Square） |
| 边际买盘被抽走 | 中国收紧跨境交易通道、QFLP 退出预提税 ~10%→25% 且部分追溯 | 侵蚀过去两年对美股科技板块构成边际买盘的中国资金 | 边际买盘减少 = 承接变薄，下跌更易加速 |
| 规则改动放大波动 | 6/4 落地的 PDT 规则取消（25k 门槛取消、改实时盘中保证金） | 在最糟时点放大波动 | 把正常回撤更容易推成下跌螺旋 |

**判读**：把这类信号当成"硬性供给/承接缺口"，先下调流动性倍率、再谈能不能追。与 endogenous-market-structure-playbook 的 `issuance_overhang` 并列消费。

## 5. HVL / Gamma-Flip 作为做空研究的最高胜率触发点

不去猜顶、不赌财报，而是**等结构自己翻空、做市商自己变成强制卖方的那一刻**。

- 预设点位 = HVL（high vol level，例：SPX 7495）。
- HVL **之上**：做市商整体正 gamma → 压制波动。
- 跌穿 HVL：正 gamma 翻 **−gamma** → 做市商被迫越跌越卖 → 制造更大系统性卖压。**在这个位置加空，胜率最高。**
- 落地：用 `options_gamma.py <SYM> --near`（日内单到期日）确认 Gamma Flip 位置与是否已转负，别用聚合默认值（聚合常把近月负 gamma 洗成假性正 gamma）。详见 `references/options-gamma-structure.md`。
- 研究输出口径（本 skill 默认 `no_order_execution`）：给"结构翻空触发点 + 翻空后风险加速路径"，不给下单指令。

## 6. 与其它 regime 层的拼接

| 层 | 文件 | 回答的问题 |
|---|---|---|
| 流动性挤兑 | `deleveraging-liquidity-squeeze-playbook.md` | VIX/黄金/AAPL/长端利率/Skew/GEX 六信号 → 是不是在卖资产换现金 |
| **离散度回归（本层）** | 本文件 + `dispersion_crowding.py` | COR1M/VIXEQ → 杠杆拥挤到没到极致、相关性是否正在回归 1 |
| 内生结构/拥挤度 | `endogenous-market-structure-playbook.md` | 仓位/被动/发行/轮动结构 |

**合成规则**：
- `correlation_unwind_active` 出现 → 等价于杀杠杆已进行中，按 `active_deleveraging`／`forced_liquidation` 处理，覆盖任何静态高分。
- `dispersion_extreme`（埋雷）但六信号尚未触发 → 按 `deleveraging_watch` 预防：先降 beta、开保护，禁止在极值追多。
- 两层同时亮红 = 最高危：先保命，不讨论"值不值"。

## 7. 报告最小输出格式

字段是 schema，值必须用**当次分析的真实数据**填。文本字段写当时当地的事实，无对应项写 `N/A` 或 `缺口`——**不要保留下面的示例文字**（它们是 2026-06 半导体/韩国 case study，照抄到别的市场就是错）。

```yaml
leverage_crowding_dispersion:
  cor1m: {last: <num>, pctile_all: <0-1>, chg_1d_pct: <num>, chg_5d_pct: <num>}   # 来自 dispersion_crowding.py
  vixeq_minus_vix: {premium: <num>, premium_pctile: <0-1>}
  state: <benign|crowding_building|dispersion_extreme|correlation_unwind_active|unavailable>
  catalyst_vs_structure: "<结构 vs 催化剂判读：本次的真实催化剂是什么、结构是否主导>"
  levered_etf_left_tail: "<本标的/市场是否存在杠杆单票ETF爆破链；无则 N/A>"
  regulatory_forced_sell: ["<本市场的硬性强制卖盘/承接缺口；无则留空>"]
  decision_impact: "<基于以上的仓位/保护动作>"
```

> 示例填法（仅供对照，**勿复制**）：当年半导体那次 `state: correlation_unwind_active`，`catalyst_vs_structure` 写"结构主导，AVGO财报/NFP/Meta增发只决定时点"，`levered_etf_left_tail` 写"2x海力士跌停无对冲窗口→隔日超额杠杆→击穿/传染"，`regulatory_forced_sell` 列韩10%上限/QFLP预提税/PDT取消。换个标的就全部重填。
