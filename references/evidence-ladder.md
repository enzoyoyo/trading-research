# Evidence Ladder · 证据分级与红旗

> 蒸馏自 serenity-skill `references/evidence-ladder.md`，整合现有信号优先级体系。用于在研究过程中对所有证据源做标准化分级。

## 证据等级

### Strong（强证据）— 可用于高信心结论

- SEC/HKEX/上交所/深交所/北交所正式申报文件
- 年报、半年报、季报、临时公告
- Earnings call transcript、官方投资者演示材料
- 正式客户合同、中标公告、产能预订、预付款公告
- 监管审批文件、环评/能评批复、地方项目备案
- 专利、标准文件、技术论文、认证记录
- 交易所问询函回复

### Medium（中证据）— 用于佐证或交叉验证

- 权威财经媒体（Reuters/Bloomberg/WSJ/FT/Nikkei/财新/证券时报）
- 行业期刊、贸易出版物
- 行业协会数据
- 公司官网和产品页面
- 卖方/专业研究（前提：假设可见）
- 上下游公司公开披露中可交叉验证的信息

### Weak（弱证据）— 仅作线索，需更强来源确认

- Grok/X/社媒/论坛/一线从业者实时线索（只有通过身份、交叉、时间验证后才可作为辅助证据）
- 来源不明的截图
- 未署名的渠道调研
- 单纯价格/成交量异动（无基本面验证）

## 每条主张必须

1. 标注来源类型和证据等级
2. 区分已确认事实 vs 推断
3. 弱证据主张明确标注"线索待验证"
4. 不用弱证据单独支撑高优先级排名
5. X/Grok 线索必须写清 `author_identity / posted_at / observed_at / originality / cross_check_eids`；缺任一项时 reliability 上限 0.5，且不得提高仓位

## 候选标的证据标准

每个 Top 候选至少包含：

- **1条 Strong/Medium 来源的业务地位证据**（它在产业链的什么位置、控制什么）
- **1条 Strong/Medium 来源的需求/产能/财务证据**（订单、客户验证、营收结构、毛利）
- **标注主要缺失证据**（哪一条有了就能升优先级）
- **清楚的证伪条件**（什么事实出现会推翻判断）

## 七大红旗 🚩

发现以下任一信号 → 自动降一档置信度，两项以上 → 不列入 Top 排名：

| # | 红旗 | 具体表现 |
|---|---|---|
| 1 | **单客户依赖+未证实** | 核心论据依赖一个未具名客户/传闻 |
| 2 | **社交媒体驱动** | 股价主因KOL/社区热度，非基本面变化 |
| 3 | **融资前置** | 机会兑现前需要大额融资，稀释风险高 |
| 4 | **应收/存货>收入增速** | 应收和存货增长持续快于收入增长 |
| 5 | **毛利不改善** | 声称有稀缺性/定价权，但毛利率无改善 |
| 6 | **管理层话术** | 频繁使用主题词汇，但分部数据无变化 |
| 7 | **客户+营收模糊** | 客户未具名、营收影响含糊、无订单证据 |

## 证据等级数值映射（原 evidence-grade-map.md 独有段落）

统一 `Source Reliability Policy`、本文件与 Evidence Ledger 的 `reliability/freshness` 数值，避免同一证据在不同文件里被重复解释：

| Source Tier | 对应本文件等级 | reliability 建议 | 能否主导结论 | 能否单独提高仓位 | 示例 |
|---|---|---:|---|---|---|
| S / Tier 1 | Strong | 0.90-1.00 | 可以 | 可以，但仍需时间戳 | 交易所/监管/SEC/HKEX/巨潮/公司财报原文/LongBridge 行情/CBOE 结构数据 |
| A / Tier 2 | Strong/Medium | 0.80-0.90 | 可以 | 可，但需至少一个交叉验证 | 公司 IR、正式新闻稿、权威市场数据、AkShare/WindClaw 可复现输出 |
| B / Tier 3 | Medium | 0.60-0.80 | 辅助主导 | 不应单独提高仓位 | Reuters/Bloomberg/WSJ/FT/财新/卖方研报/行业协会 |
| C / Tier 4 | Weak / Clue | 0.30-0.50 | 不可 | 不可 | Grok/X/社媒/KOL/论坛/评论区/未验证现场线索 |
| D / Tier 5 | Invalid | 0.00 | 不可 | 不可 | 无来源截图、群聊传闻、搬运且找不到原始源 |

### 晋级硬规则

- L1：至少 3 个 EID，且其中至少 1 个为 S/A/B，弱证据不能超过主证据数量的一半。
- L2：至少 4 个 EID，其中至少 2 个为 S/A/B，且至少 1 个直接验证核心假设。
- L3：必须有新增 S/A 级证据或市场结构确认；C/D 级证据不能触发 L3。
- 任何未裁决冲突存在时，不得升到 L2 以上。
- 空数据、解析失败、无时间戳数据不得生成 EID；必须进入 Data Gap Ledger。

### X/Grok 特例

X/Grok 的价值是快和原始，不是天然可靠。默认：

```yaml
source_tier: C
ladder: Weak / Clue
reliability_ceiling: 0.5
position_impact: cannot_raise_position_alone
```

只有通过身份验证、交叉验证、时间验证后，才可作为辅助 EID 进入决策；仍不得单独提高仓位。

### Mira Evidence Posture 映射（v2.7）

`Source Tier` 与 `evidence_category` 是两条轴：

| 轴 | 回答的问题 | 示例 |
|---|---|---|
| Source Tier / reliability | 来源本身有多可信 | SEC/Longbridge/CBOE/公司公告/X 线索 |
| claim_type / evidence_category | 这条信息在当前结论里能做什么 | `market_pricing`、`management_guidance`、`derived_calculation`、`weak_signal` |

关键映射规则：
- Longbridge/CBOE/AkShare 的行情或结构输出可以是高 reliability，但 `claim_type=market_pricing`，不能写成基本面验证。
- 公司 guidance 来源可为 S/A，但 `evidence_category=management_guidance`，必须等兑现或被独立验证后才可升级为事实。
- agent/脚本计算的 peer rank、CAGR、Gamma 解读、仓位倍率属于 `derived_calculation`，必须有 formula、upstream EID 或 calculation_ref。
- X/Grok 一线线索统一 `evidence_category=weak_signal`，即使作者身份高，也只能先提高观察优先级。
- MiroFish-style 群体模拟统一 `evidence_category=modeled_scenario`，默认 reliability ceiling 0.20；只能生成 scenario_prior / evidence_collection_plan，不能支撑 L1+ 或提高仓位。
- `stale/contradicted/unknown` 的 claim 不得支撑 L1+；先进入 DataGap / Conflict Ledger / `needs_refresh`。

## 与现有框架衔接

- 证据等级标注需写入 `Intelligent Research Contract`（`references/intelligent-research-contract.md`）的证据账本中。
- 红旗检查是 Decision Cascade 的预审步骤——通过后再进入评分卡和仓位计算。

## 相关文件

| 文件 | 关系 |
|---|---|
| `bottleneck-scorecard.md` | evidence_quality 维度以本文件为准 |
| `serenity-method.md` | Step 6（收集当前证据）引用本文件 |
| `expected-returns-framework.md` | 预期三分法中「算出来」对应 Strong，「问出来」对应 Medium |
| `intelligent-research-contract.md` | 证据账本中标注来源等级 |
| `decision-cascade.md` | 红旗触发 → 阻断晋级流程 |
