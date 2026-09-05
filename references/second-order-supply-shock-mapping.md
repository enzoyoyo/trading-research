# Second-Order Supply Shock Mapping · 二阶供给冲击映射

> 主落点声明：编译进 `endogenous_structure`；合同与资产代际事实仍按下文进入 `fundamentals`。

> 来源边界：参考 Frank（@Franktradinglog）2026-07-01 Meta 算力长帖、2026-07-01 NeoCloud 分层帖、2026-07-01 MU 缺口纪律帖、2026-07-02 执行帖，Balder 2026-07-01 云分化帖。文中 Meta/CRWV/NBIS/APLD/IREN/MU 等是 **2026-07 实例，禁止照抄**；机制可迁移、名单须按当期事实重画。

## 定位

当一个主题（算力、存储、电力、任何要素供给）出现「供给冲击/过剩恐慌」类头条时，市场第一反应通常是把主题内所有票当同一回事砸。本文件把这类冲击拆成五步可复核检查，找出**误伤**与**真实受损**的分界。与 `capex-cashflow-duration-rotation.md` 是姊妹层：那边按「花钱方/收钱方现金流久期」切，这边按「叙事真伪 + 合同结构 + 资产代际」切；两层都编译进 `endogenous_structure|fundamentals`，不重复对方内容。

## Step 1 · 叙事-事实一致性检验（narrative_fact_consistency）

头条叙事必须与同一主体的**可观察行为**三角验证，输出 `consistent|inconsistent|mixed`：

- 机制实例（2026-07，仅示意）：头条说「Meta 卖算力 = 算力过剩」；但同一时期 Meta 签 1.6GW 多年长约、且被上游限容量——**卖旧资产与锁新供给同时发生**，"过剩"叙事 `inconsistent`，更合理解释是库存管理/资产周转。
- 纪律：叙事与行为矛盾时，禁止把头条写成 verified_fact；登记 `narrative_fact_consistency=inconsistent` 进 Conflict Ledger，砸盘解释转向 Step 4（技术性替代解释）。
- 三角验证素材：公告/8-K、合同期限与金额、capex 指引、上游供应商披露、招聘/用电/建设等物理证据。

## Step 2 · 主题内暴露分层（theme exposure tiering）

同一主题的公司按**对冲击变量的真实暴露**分层，禁止用「概念相同」替代「暴露相同」：

| 层 | 定义 | 冲击传导 | 2026-07 NeoCloud 实例（仅示意） |
|---|---|---|---|
| `theme_beta_proxy` | 流动性好、被当作主题总 beta 的代理票 | 叙事恶化时**最先、最猛**被砸，与自身基本面可以无关 | CRWV / NBIS |
| `contracted_infrastructure` | take-or-pay 长约的电力/地产/机柜商 | 现货价格几乎不传导；真实风险 = 交付延期/融资/超支/对手方履约 | APLD / WULF / CIFR / HUT |
| `spot_operator` | 收入直接暴露在现货租赁价/利用率 | 冲击**直接**传导到收入 | 裸算力现货商；IREN 为混合型 |
| `locked_buyer` | ToB 长约锁定需求价格、是要素净买方 | 现货跌价反而受益（更便宜的边际供给） | 三大云 |

- 输出：每个候选票一行 `exposure_tier` + 传导路径一句话。
- 纪律：`theme_beta_proxy` 的暴跌不能作为 `contracted_infrastructure` 或 `locked_buyer` 基本面恶化的证据；反之亦然。

## Step 3 · 资产代际分化（asset_generation_bifurcation）

要素资产有代际结构时，「卖旧产能」≠「过剩信号」：

- 机制实例（仅示意）：H100/H200 转向推理/微调/广告推荐负载，GB200/GB300/Rubin 承担前沿训练——卖/租出上一代 = 库存管理与资产周转，**不构成**新一代供需宽松的证据。
- 纪律：任何「XX 在抛售产能」的看空论点，必须先回答「抛的是哪一代、新一代的合同/排产状态如何」；答不出 = `asset_generation_bifurcation=unchecked`，论点降级为 hypothesis。

## Step 4 · 技术性替代解释检查（technical_alternative_check)

价格动作在写成基本面确认之前，必须排除技术性解释：

- 候选替代解释：空头回补（short interest 高的票逆势反弹）、资金轮动、期权对冲流、指数/被动流（对照 `known_flow_calendar`）、流动性差放大波动。
- 纪律：未排除技术性解释前，价格动作最高记 `price_action_clue`，不得写成「市场确认了 XX 基本面判断」。

## Step 5 · 认知与执行分离 + 领头羊缺口完整性（leader_gap_integrity）

Frank 原则：「市场可以是愚蠢的，但有时你需要先跟着卖。」——**分析上判定市场误读，不豁免持仓纪律**：

- 认知与执行分离：Step 1-4 得出「误伤」结论，只改变 watchlist 与回补优先级，**不构成**逆势扛单或加仓的理由；持仓管理仍由结构信号与 Decision Compiler 裁决。
- `leader_gap_integrity`（硬触发）：主题领头羊/龙头**跌破财报（或重大事件）跳空缺口、且次日未能收回**时，触发板块级多头 de-risk 进入 L4 讨论——财报缺口是事件定价锚，失守 = 市场收回了对该事件的定价。
  - 机制实例（仅示意）：MU 跌破财报缺口次日未收回 → 清仓全部半导体多头。
  - 缺口锚与 gamma 墙位/关键位形成 confluence（如缺口起点恰为最大正 gamma 位）时，该位置权重更高；见 `options-gamma-structure.md`。
- 该触发只作用于「同主题多头敞口收紧」，不自动生成做空信号。

## 输出格式

```yaml
second_order_supply_shock:
  theme: "<主题>"
  headline: "<冲击叙事一句话>"
  narrative_fact_consistency: "consistent|inconsistent|mixed"
  consistency_evidence: ["<可观察行为1>", "<行为2>"]
  exposure_tiers:
    - symbol: "<ticker>"
      tier: "theme_beta_proxy|contracted_infrastructure|spot_operator|locked_buyer"
      transmission: "<冲击如何传导到收入/成本，一句话>"
  asset_generation_bifurcation: "checked|unchecked"
  technical_alternative_check: "short_covering|rotation|passive_flow|none_found"
  leader_gap_integrity: "intact|broken_unrecovered|broken_recovered|n/a"
  action_note: "misjudged_selloff → watchlist 优先级；leader_gap broken_unrecovered → 同主题多头 de-risk 进 L4 讨论"
```

## 与 Decision Compiler 的关系

- 编译进 `endogenous_structure`（beta 代理砸盘/技术性流）与 `fundamentals`（合同结构/资产代际）；不新增 module、不新增动作等级。
- `narrative_fact_consistency=inconsistent` 只能**否定看空叙事的证据等级**，不能反向作为加仓理由（误伤 ≠ 立即回补）。
- `leader_gap_integrity=broken_unrecovered` 是收紧型硬触发：同主题多头进 L4 讨论；缺任何交叉验证（Step 1-4 未做完）时，本层结论最高 L0/L1。
- 认知与执行分离写进主回复：分析结论与持仓动作分两行，不许互相顶替。

## 常见误判

1. **概念等于暴露**：把 take-or-pay 长约商当现货商砸/买。
2. **卖资产=过剩**：不查资产代际就把库存管理读成需求见顶。
3. **反弹=平反**：空头回补当成市场认错。
4. **分析对了就扛单**：判定误伤后无视领头羊缺口失守继续持有。
5. **缺口当普通支撑**：财报缺口是事件定价锚，失守的信息量远大于均线破位。

## 失效条件

- 主题内公司合同结构不可得（私有/披露差）→ `exposure_tier` 标 `unverified`，该票不得进入「误伤」名单。
- `risk_regime=active_deleveraging` → 全主题按系统性风险处理，本层分界失效。
- 领头羊本身发生公司特有事件（会计/诉讼/管理层）→ `leader_gap_integrity` 对板块的外推无效，只作用于该票。
