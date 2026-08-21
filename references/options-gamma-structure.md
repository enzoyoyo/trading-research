# Options / Gamma Structure · 期权墙执行规则

## 触发条件

遇到以下任一情况必须读本文件：
- 用户给 Gamma Exposure / GEX / Put Wall / Call Wall / Gamma Flip / Zero Gamma 截图或文字。
- 美股高波动、期权活跃标的，如 TSLA、NVDA、RKLB、PLTR、RGTI、MSTR 等。
- 用户问“跌下来能不能接”“冲上去能不能追”“开盘跌破某价位怎么办”。

## 核心字段

| 字段 | 含义 | 决策作用 |
|---|---|---|
| Spot / Last Price | 当前价格 | 判断价格相对期权墙位置 |
| Put Wall | 大量 Put 集中价位，常被当作短线支撑/风险线 | 跌破且未收回时，禁止把下跌当低吸机会 |
| Call Wall | 大量 Call 集中价位，常为短线压制/止盈区 | 接近且无法突破时，不追高 |
| Gamma Flip / Zero Gamma | Dealer gamma 由正转负的关键价位 | 位于下方时波动放大，仓位下调 |
| Aggregate GEX | 总 Gamma 暴露 | 负 Gamma 环境中趋势更容易加速；若和前值相比明显衰减，才可写“GEX 出逃” |
| IV / skew | 波动率与偏斜 | 判断是否因事件/恐慌导致期权定价极端；**左尾 skew 极端化说明 downside hedge demand 很强** |


## 自动取数路径

优先直接运行：

```bash
python3 .../scripts/options_gamma.py <美股代码> --json            # 聚合 45 天窗口（中线结构）
python3 .../scripts/options_gamma.py <美股代码> --near --json     # 近月/0DTE 单到期日（日内 dealer 对冲驱动）
python3 .../scripts/options_gamma.py <美股代码> --expiry 2026-06-05 --json  # 指定单到期日，对应软件单到期日 Gamma 图
```

数据源规则：
- 主源：CBOE delayed JSON `https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json`，免费、无 key、约 15 分钟延迟，含 open_interest / iv / CBOE Greeks。
- 兜底：yfinance + Black-Scholes，仅当 CBOE 不可用时降级；输出会标 `source=yfinance_bs`，可信度低于 CBOE。
- A股/港股通常不走该脚本；若无活跃上市期权，回到量价、资金和盘口结构。

## 2026 Cboe 美股单名期权延长交易时段 · Session Gotcha

SEC 已批准 Cboe 对**部分 multi-listed equity options**开放延长交易时段（SR-CBOE-2025-079；Federal Register 2026-10951）：GTH 7:30–9:25 ET，Curb 16:00–16:15 ET，启动仍受 OPRA/OCC/会员准备度与 Cboe 实施清单约束。

- 从 2026-07-13 附近开始，若用户问美股单名期权/Gamma 的盘前、盘后或开盘前风险，必须标注 `option_session=GTH|Curb|RTH|unknown` 与数据时间戳；不要把 GTH/Curb 的稀薄成交当作 RTH 墙位确认。
- `options_gamma.py` / CBOE delayed JSON 若未明确区分 session，输出只能写 `session_gap=mixed_or_unknown`；期权墙、GEX 出逃、Call/Put Wall 稳定性需要 RTH 复核或同口径连续快照。
- 延长时段只提高“需刷新/需观察”优先级，不单独提高 action level 或 position cap；若盘前期权信号与正股盘前价格、RTH 前值、财报/公告冲突，进入 Conflict Ledger，由 Decision Compiler 保守裁决。
- 法规/交易规则事实锚点优先用 SEC/Federal Register/Cboe rule filing；Cboe 新闻稿可作实施日期线索，但必须回到 rule filing / approved list 复核。

## ⚠️ 单到期日 vs 聚合（最易踩坑，必读）

**日内交易看的是近月/0DTE 的 gamma 结构，不是聚合窗口。** 聚合 45 天窗口常被远月大量正 gamma 拉成「正 gamma、回踩就接」的假象，而**当天到期那张图可能是深度负 gamma**——这正是「跌破 Put Wall 整体下移很厉害」的物理来源。

- 用户给的是**单一到期日截图**（软件上选了某个 Expiration）→ 必须用 `--near` 或 `--expiry <该日期>` 复现，别用聚合默认值对比，否则 flip/regime 对不上、会给错决策。
- 实测 RKLB（2026-06-05）：聚合视角 = 正 gamma / flip 168，近月单到期日 = **负 gamma / 总GEX −6M**。两者决策含义相反。**冲突时以近月为准做日内风控。**
- 报告里若同时给两视角，要标清「日内（近月）」vs「中线（聚合）」，不能混为一谈。

## 站稳「墙区间」框架（正 gamma 时的核心玩法）

正 gamma 环境下 dealer 倾向把价格**钉在 Put Wall ↔ Call Wall 区间内**做均值回归：回踩 Put Wall 是接的位置、冲 Call Wall 是减的位置，区间本身是可交易的「合法座位」。**整个判断就一句：能不能站稳这个区间。**

- **站稳区间内**：正常区间操作（低吸 Put Wall、高抛 Call Wall），但不赌突破。
- **开盘直接打穿 Put Wall（gap 跌破、未快速收回）**：区间下沿失守 → **不接盘**。不是低吸机会，是结构破位。
- **跌破后转负 gamma**：dealer 由「压波动」转「追跌助涨」，卖盘自我强化，期权墙会整体下移 → 风险优先，等重新站回 Put Wall/Flip 再谈。
- **贴着 Call Wall 冲不破**：上沿压制，不追高；真突破且 Call Wall 上移才算趋势确认。

## 日频波动率状态读法 · VRP 门（v2.30，源自 Balder 2026-07-02 SPX read）

墙区间框架只回答「dealer 想把价格钉在哪」，不回答「钉不钉得住」。钉得住与否由 **variance_risk_premium（VRP）** 裁决——隐含波动 vs 已实现波动的差：

- `implied_1d_move = spot × IV_index / sqrt(252)`（如 VIX 16.2 → SPX 日隐含 ≈1.02%）。
- realized 用**两条口径同时算**：close-to-close 与 **Garman-Klass**（含日内高低开收，捕捉「收盘平静但日内大摆」）；两者分歧大 = 日内波动被收盘价掩盖。
- `VRP = implied − realized`。**VRP < 0（realized 超隐含）= `range_expansion_warning`**：期权卖方在亏钱补 hedge，正 gamma pinning 的可信度必须下调，墙区间可能被打穿。

四象限速查：

| gamma 状态 | VRP > 0 | VRP < 0 |
|---|---|---|
| 正 gamma | pin 最可信，区间操作合法 | `range_expansion_warning`：区间可能扩张，降杠杆、不满仓做均值回归 |
| 负 gamma | 隐含已贵，趋势易加速但 hedge 成本高 | **最危险象限**：波动自我强化且卖方在流血，风险优先 |

执行纪律：
1. 报告墙区间时**必须带一行 VRP 读数**（implied vs realized c2c / Garman-Klass），只报墙位不报 VRP = 结构读数不完整。
2. sanity check：对照 14 日均日波幅——隐含点位小于近期日均波幅时，「区间不被打穿」是逆着近期现实的假设，须显式说明。
3. 财报季把 M7/板块龙头的**相对 IV 横截面排序**当 positioning clue（谁的 IV 被抬到超过公认龙头 = 注意力/仓位集中所在），只作线索不定方向。
4. 墙位与**事件缺口起点**（财报/重大消息跳空处）重合时为 confluence，该位置权重更高；缺口失守的处理见 `second-order-supply-shock-mapping.md` 的 leader_gap_integrity。
5. VRP 只会**收紧**读数（下调 pin 可信度、降杠杆建议），不得作为加仓或做空波动率的独立理由。

## 执行规则

### Skew + 指数 GEX（杀杠杆视角）

1. **Skew 极度左偏 ≠ 自动见底。** 它更常代表机构在加价买 crash hedge；只有 skew 回落、VIX 缓和、墙位稳定后，反弹质量才提高。
2. **“指数 GEX 出逃”必须有前值对比。** 只有同一指数、同一到期视角、同一算法口径下，看到总净 GEX 明显衰减或墙位系统性下移，才能写“出逃”。只有当前快照时，只能写 `negative gamma proxy` / `dealer support weak`。
3. **Skew 左偏 + GEX 变脆 + Put Wall 下破**：默认按杀杠杆处理，bounce 不先当反转。
4. **单名股与指数要分开。** 个股 `options_gamma.py` 只能告诉你该票自己的 dealer 结构；用户若问“指数 GEX 出逃”，优先看 SPY/SPX 的同口径对比，而不是拿单票代替指数。

1. **跌破 Put Wall 且未快速收回：禁止接盘。** 即使长期叙事好，也只能等重新站回 Put Wall / Gamma Flip。
2. **位于 Gamma Flip 下方：仓位降级。** 短线波动可能自我强化，只能给轻仓试错或观察。
3. **Call Wall 压制明显：追高降级。** 突破前不要把冲高当趋势确认。
4. **期权墙整体下移：风险优先。** 说明市场重新定价下行保护。
5. **没有可靠期权数据：写缺口，不编造。** 对期权活跃标的，缺数据会降低短线执行置信度。

## 多 vendor / 交易所 Conflict Ledger（v2.55）

GEX、Call/Put Wall、Gamma Flip **不是交易所官方统计**，而是带库存假设的模型输出。公开框架至少包括 SqueezeMetrics（2016 GEX 白皮书）、SpotGamma、MenthorQ/VolSignals/GammaEdge 等产品，以及 Cboe 对 SPX 0DTE **净**对冲相对期货流动性的第一方描述。

**强制步骤**（缺一则只能标 `gamma_model_conflict` / data gap，不得用单一 flip 价抬动作）：

1. 记录每个数字的 `provider`、`as_of`、`expiry_set`（近月 vs 聚合）、`sign_convention`、是否含 0DTE。
2. 若两家 flip/wall 价或 gamma 符号冲突 → 写入 Conflict Ledger，**禁止平均**成一个价；日内风控取更保守上界（通常更靠近负 gamma / 更宽失效带）。
3. 名义成交额 ≠ 净 gamma：Cboe 强调 0DTE 客户流常接近买卖平衡，净对冲相对 ES 流动性往往很小；不得用“0DTE 成交额巨大”单独证明 dealer 决定论。
4. Vendor 叙事与 Cboe/学术净暴露研究冲突时并列双方，标注利益冲突；仍只收紧，不抬仓。
5. IV skew / IV spread 若用于方向线索，必须先控制借券费（Muravyev–Pearson–Pollet）；高借券费子集降权或剔除——细节见财报预测门与 quant 门，本层只禁止未控 fee 的 skew→方向捷径。

Positive gamma、call wall 或 pin 不能独立抬升上游 cap。Dealer inventory 不可直接观测。

## 输出格式

```markdown
期权/Gamma 结构：
- Spot：
- Put Wall：
- Gamma Flip / Zero Gamma：
- Call Wall：
- GEX / IV / skew：
- provider / expiry_set / sign_convention / conflict_with：
- 当前状态：支撑内 / 跌破Put Wall / Gamma Flip下方 / Call Wall压制 / 数据缺口 / 多源冲突
- 决策影响：提高试错 / 维持观察 / 禁止接盘 / 降仓 / 止盈
- 等待信号：重新站回Put Wall / 站上Gamma Flip / Call Wall上移或突破 / 期权墙稳定 / 冲突解除
```
