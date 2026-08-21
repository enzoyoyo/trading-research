# Earnings Call & Guidance Interpretation

## 适用场景

用户提供财报电话会 transcript/纪要，或问「这次指引是好是坏」「管理层这句话什么意思」「这次电话会有没有释放信号」。不适用于纯数字财报解读（走 `fundamental_snapshot.py` 与既有 `fundamentals` 主线）——本文件只覆盖**电话会文本本身**的结构化解读。

## 结构化解读维度

1. **Guidance vs 一致预期偏离度** — 管理层给出的下季度/全年指引数字，与市场一致预期（分析师共识）的差值方向和幅度。没有一致预期数据源时标 `data_gap`，不得用「感觉偏保守/偏乐观」代替数字。
2. **管理层语言信号分层** — 同一句话按语气分三层，禁止混为一谈：
   - **承诺**（committed）：给出可验证的具体数字/时间点（"Q3 毛利率将回到 45%"）。
   - **对冲**（hedged）：用条件句/软化词包裹方向性表态（"如果需求环境配合，我们有信心…"）。
   - **回避**（deflected）：被问到具体问题但答非所问，或用"we don't guide on that"类话术拒答。
     回避本身是信号（尤其是分析师追问三次仍回避的话题），不是「没有信息」，应记入证据但标注 `claim_type=opinion` 而非 `fact`。
3. **QA 环节权重高于 prepared remarks** — prepared remarks 是提前打磨过的公关稿，QA 环节的即兴应答更能反映管理层真实把握度；解读时 QA 段落证据优先级更高。
4. **对比历史电话会口径** — 同一管理层历次电话会对同一指标的措辞是否发生系统性软化/强化（如连续两季度都把"稳健增长"换成"具有挑战性的环境"），比单次措辞更有信号价值；无历史对比数据时明确说明本次是「单次快照，无法判断趋势」。

## 引文必须走 research_provenance.v1

任何直接引用 transcript 原话的结论，必须通过 `research_provenance.v1`（hash + 行锚点）建立可追溯的引用链，复用现有 `provenance_guard.py` 校验，不得另起炉灶：

```json
{
  "schema_version": "research_provenance.v1",
  "source_documents": [{"document_id": "DOC1", "original_url": "<transcript 来源>", "content_sha256": "<原文 hash>"}],
  "quote_anchors": [{"quote_id": "Q1", "document_id": "DOC1", "verbatim_text": "<原话，不超过一句必要引文>"}],
  "framework_claims": [{"framework_claim_id": "FC1", "quote_anchor_ids": ["Q1"], "status": "supported"}]
}
```

- 未走通引用链的转述性总结，`analysis_claims` 的 `provenance_mode` 必须标 `framework_inference`，不能伪装成原话引用。
- `verified` 只代表溯源链完整，不代表管理层说的是真的——真实性判断仍归 `fundamentals`/`endogenous_structure` 的交叉验证。
- 完整 schema 边界见 `references/data-contracts.md` § ResearchProvenanceBundle 与 `references/source-grounded-research-provenance.md`。

## 与财报反应先验联动

电话会解读结论必须与 `references/event-reaction-memory.md`（`scripts/event_reaction_journal.py`）联动：
- 解读产出「指引超预期/低于预期」结论后，若该标的已有历史财报反应记录（`relationship_graph.py` 的先验），要交叉核对本次市场实际反应是否符合先验方向；不符合时不强行找理由圆场，如实记录 `reaction_diverged_from_prior`。
- 电话会当天/次日的价格反应本身进入 `event_reaction_journal.py` 的事件层记录，供未来同一管理层/同一行业的下一次电话会做历史对比（对应维度 4）。

## Decision Compiler 映射

不新增模块，全部走既有 `fundamentals`/`event_proximity`/`research_readiness`：

| 信号 | module | sub_framework | 封顶/收紧行为 |
|---|---|---|---|
| Guidance vs 一致预期偏离度（有可信共识数据源） | `fundamentals` | `earnings_call_guidance` | 正常参与裁决，不单独加分 |
| 管理层语言分层（承诺/对冲/回避），无法交叉验证 | `fundamentals` | `earnings_call_guidance` | 最高 L1 watch |
| 引用链未完整（`research_provenance` 为 `partial`） | `research_readiness` | — | 按 `research_readiness` 既有规则，`partial` 最多映射 L1，`blocked` 最多 L0 且 `position_multiplier=0.0` |
| 财报/电话会窗口内（earnings_blackout） | `event_proximity` | — | 沿用既有规则：移出可交易集，无论票数多高（`decision-compiler.md` 行 198） |

## 边界

- 不做 transcript 全文翻译/摘要类工作——只做上述四维结构化解读，其余内容属于内容生产而非投研，超出本 skill 范围。
- 没有 transcript 原文只有二手转述（如财经媒体摘要）时，`originality` 标 `second_hand`，引文规则依然适用，不得当一手原文处理。
- 不构成投资建议；结论必须标注 transcript 来源与发布时间。
