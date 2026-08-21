# Source-Grounded Research Provenance · 来源、引用、框架与分析溯源

## 1. 目标

本契约把外部研究材料转成机器可验证的证据链：

```text
SourceDocument
  → QuoteAnchor
  → FrameworkClaim
  → AnalysisClaim
  → BehaviorCrossCheck（可选）
  → KOLMethodCard（可选；复用同一来源/框架/EID）
  → 现有 Evidence / Mira / ResearchReadiness / Decision Compiler / Decision Memory
```

它解决五个问题：

1. 原始来源和分析框架不能混成一层。
2. “原文引用”必须能回到固定来源文本与位置。
3. 框架推演不能伪装成来源原话或已验证事实。
4. 支撑结论的主张必须写反证搜索与 falsifier。
5. 缺证据时必须降级，不得杜撰，不得靠高分或自然语言气势越权。

这不是第二套 Evidence、评分器、Decision Compiler 或 Memory。它只产生 `verified | partial | blocked` 的溯源就绪度，以及只收紧的 `research_readiness` 建议；永远 `no_order_execution=true`。

## 2. 何时必须使用

以下场景应生成 `research_provenance.v1` bundle：

- 从外部仓库、文章、访谈、研报、PDF、网页或用户材料提炼方法论。
- 最终报告使用直接引文或声称“某人/某机构说过”。
- 把来源内容蒸馏成框架，再用框架推演股票、行业或市场结论。
- 做“公开表态 vs 持仓/行为/披露”的言行交叉验证。
- 拟把外部研究主张写入 Decision Memory、假设注册表或长期复盘材料。
- 把 KOL/公开作者的方法蒸馏成可复用交易生命周期，而不是照抄喊单或战绩。

Tier 0 的单纯实时行情查询、无外部材料的机械计算可不生成 bundle；现有 EID、SourceHealth、时间戳和 DataGap 规则仍然有效。

## 3. 上游学习来源与 clean-room 边界

本机制参考并红队审计了：

- 仓库：`https://github.com/lyra81604/zhengxi-views`
- 固定 commit：`44e0c48657e93c5bf8340fcd2d0f5bac3a4b17d1`
- 固定 tree：`cb3f50b5d1a4b723dcfa4feb2e028047fe8c4fff`
- `git archive HEAD` SHA-256：`e2008c730284e760e7db1fa2cb296e8dbf052e402ce30f60f991ba0b2349b306`
- tracked files：141
- tag：0
- 审计时工作树：clean

### 3.1 源码级发现

| 机制 | 上游证据 | 强制等级 | 审计判断 |
|---|---|---|---|
| 语料、方法、基金数据分层 | `SKILL.md:24-28` | 目录/流程软约束 | 值得抽象为内容层与框架层分离 |
| 先检索再回答 | `upstream/SKILL.md:41-63`、`upstream/scripts/search_corpus.py:50-78` | prompt + 检索脚本 | 能发现来源，不能校验最终回答是否忠于原文 |
| 原话、推演、待核实三分 | `SKILL.md:82-101` | prompt 软约束 | 值得改造成 `provenance_mode` 枚举和 validator |
| 语料索引 | `upstream/scripts/build_index.py:18-70`、`upstream/references/corpus_index.json` | 可执行索引 | 有 URL/path/date，但没有 claim-to-source 引用链 |
| 六维评分 | `upstream/references/scorecard.md:11-44`、`upstream/scripts/score_fund.py:144-148` | 部分机械、部分人工 | 关键维度仍主观，不能迁入动作/仓位链 |
| 反方与失败条件 | `upstream/references/method.md:86-99`、`upstream/evals/evals.json:12-17` | 文档/eval 描述 | 没有 conflict ledger、schema、runner 或 CI |
| 长期记忆 | `upstream/scripts/fetch_any_fund.py:41-77` | 文件缓存 | 只是 7 天缓存，不是决策记忆或校准系统 |
| 不杜撰 | `README.md:21-27`、`SKILL.md:93-101` | prompt 声明 | 没有输出 validator，容易被绕过 |

### 3.2 Adopt / Adapt / Reject

**Adopt**

- 来源内容、框架提炼、应用分析分层。
- 先检索原始材料，再陈述观点。
- 原话、框架推演、待验证三分法。
- 表态与公开行为交叉验证的研究意识。

**Adapt**

- URL/path/date 索引 → 稳定 ID、内容 SHA-256、位置锚点、采集边界。
- 自然语言引用纪律 → 确定性 `provenance_guard.py`。
- 反方提醒 → `counterevidence_status`、反证引用和 falsifier。
- 言行对照 → 主体、时间、任期对齐，并禁止把相关性写成因果。
- 长期留存 → 复用现有 append-only Decision Memory，只存验证过的 ID/hash 关联。

**Reject**

- 不复制 `upstream/references/corpus/`、`upstream/references/fund_data/`、人物专属观点或长篇原文。
- 不迁入固定权重 100 分评分、精确分数示例或“像某基金经理会买”的结论。
- 不迁入抓取脚本、TLS `verify=False`、明文 HTTP、skill 目录缓存或邮件/cron 工作流。
- 不新增第二套 Evidence、动作等级、Compiler、Memory 或交易执行路径。

### 3.3 版权边界

上游根 `LICENSE:25-28` 明确第三方语料与基金数据版权不随代码 MIT 许可转移；部分手记还包含禁止复制/派发/发布条款。故本 skill 只迁移通用治理机制，不迁移正文、数据快照或人物专属知识库。

`public_excerpt` 的 300 字符上限是本 skill 的保守输出门，不是法律意见，也不自动赋予转载权。许可未确认时 validator 返回 warning，并把就绪度降为 `partial`。

## 4. `research_provenance.v1` 顶层结构

```json
{
  "schema_version": "research_provenance.v1",
  "as_of": "2026-07-14T00:00:00Z",
  "source_documents": [],
  "quote_anchors": [],
  "framework_claims": [],
  "analysis_claims": [],
  "behavior_cross_checks": [],
  "kol_method_cards": [],
  "no_order_execution": true
}
```

硬约束：

- `as_of` 必须是带明确时区的 ISO/RFC3339 时间。
- 六个 section 必须是 JSON object 数组；`kol_method_cards` 可省略以兼容 v2.41 及更早 bundle，省略等价空数组。
- stable ID 在各自 section 内唯一。
- `no_order_execution` 必须显式为 `true`。
- 不接受未知枚举值静默漂移。

完整可执行样例：`templates/research-provenance-bundle-pass.json`。

## 5. SourceDocument

```json
{
  "document_id": "DOC1",
  "source_type": "primary_document",
  "title": "文档标题",
  "publisher": "发布主体",
  "published_at": "YYYY-MM-DD",
  "retrieved_at": "RFC3339 with timezone",
  "access_state": "public | partial | subscriber_only | blocked",
  "original_url": "https://...",
  "local_path": "capture.txt",
  "content_sha256": "hex",
  "license_scope": "public | user_provided | paid_restricted | vendor_restricted | unknown",
  "storage_scope": "transient | private | tracked_allowed",
  "redistribution_allowed": "yes | no | derived_only | unknown"
}
```

规则：

- `document_id/source_type/title/publisher/published_at/original_url` 是最低溯源字段。
- `as_of` 不得晚于可信运行时；`published_at` 可用 date-only 或带时区时间，若提供 `retrieved_at` 则必须带时区，并满足 `published_at ≤ retrieved_at ≤ as_of`。
- `access_state` 一旦提供只能是 `public|partial|subscriber_only|blocked`；partial 只降级，subscriber-only/blocked 不得支撑 semantic claim 或 direct quote。
- `original_url` 只允许 `http://` 或 `https://`。
- `local_path` 必须位于 bundle 所在目录之内，禁止 `../` 越界。
- 有本地捕获时必须提供正确 `content_sha256`。
- 无本地捕获可以作为来源摘要背景，但返回 `source_capture_missing` warning；通常不能建立直接引文。唯一例外是下述 purpose-bound KOL 私有捕获 attestation，且该路径永远保持 partial/watch-only。
- 原始秘密、cookie、token、付费材料正文不得放进 tracked skill 目录。
- 被 `kol_method_cards` 引用的来源还必须带 `author/retrieved_at/access_state/published_time_precision`；日期粒度只能配 `date_only`，精确时间必须带时区，不得捏造缺失时分秒。

## 6. QuoteAnchor

```json
{
  "quote_id": "Q1",
  "document_id": "DOC1",
  "verbatim_text": "短引文",
  "locator": {"line_start": 10, "line_end": 11},
  "speaker": "明确主体",
  "quote_use": "internal_evidence | public_excerpt"
}
```

规则：

- direct quote 必须引用已本地捕获且 hash 一致的 SourceDocument。若原始捕获因隐私/版权不能进入 Skill，只有同时满足 `bundle_purpose.kind=kol_method_handoff`、来源 `access_state=partial`、`capture_bytes_storage_scope=private`、manifest disposition 为 `private_capture_not_distributed`、QuoteAnchor 带 `verbatim_sha256` 并绑定 manifest quote ID/hash、且 `accepted_claim_ids=[]` 与 `canonical_eids=[]` 时，guard 才接受窄化 attestation；它仅证明复核者当时见过这些私有 bytes，不证明公开页面、作者身份、投资结论或报告 Evidence。
- `verbatim_text` 必须是来源文本的原样子串；只忽略空白字符差异。
- 禁止拼接两段、改写、补词或加入省略号后仍声称 verbatim。
- `locator` 行范围必须真实覆盖引文。
- `public_excerpt` 最长 300 字符；转载许可未确认时降为 `partial`。
- 长文只做摘要 + 原文链接；不为“证明检索成功”复制大段正文。

## 7. FrameworkClaim

```json
{
  "framework_claim_id": "FC1",
  "statement": "从来源抽象出的通用框架",
  "quote_anchor_ids": ["Q1"],
  "derivation_note": "从原文到框架的推导步骤",
  "scope": "适用范围",
  "status": "supported | mixed | rejected | needs_verification",
  "counterevidence_status": "searched_none_found | supporting_only | mixed | contradicted | not_checked",
  "counterevidence_refs": []
}
```

规则：

- `supported/mixed` 必须关联存在的 QuoteAnchor。
- 每个框架都必须完成反证搜索；`not_checked` 直接 blocked。
- `counterevidence_status=contradicted` 时不得仍标 `supported`。
- `rejected/needs_verification` 框架不得支撑新的 framework inference。
- 框架状态是研究治理判断，不是客观真值或投资评分。

## 8. AnalysisClaim

```json
{
  "claim_id": "AC1",
  "statement": "本次分析主张",
  "provenance_mode": "direct_quote | source_summary | framework_inference | unverified",
  "source_document_ids": [],
  "quote_anchor_ids": [],
  "framework_claim_ids": ["FC1"],
  "evidence_refs": ["E1"],
  "decision_use": "context_only | watch_only | supports_conclusion",
  "counterevidence_status": "searched_none_found",
  "counterevidence_refs": [],
  "falsifier": "什么事实会推翻本主张"
}
```

规则：

- `direct_quote` 必须关联有效 QuoteAnchor。
- `source_summary` 必须关联 SourceDocument。
- `framework_inference` 必须关联可用 FrameworkClaim。
- `unverified` 只能用于 context/watch，不能支撑结论。
- `supports_conclusion` 必须同时有 falsifier 和已执行的反证搜索；`counterevidence_status=mixed|contradicted` 不得进入 durable conclusion。
- 本契约不替代现有 `evidence_refs`；最终事实仍须进入 Evidence Ledger，并接受 reliability/freshness/conflict 检查。

## 9. BehaviorCrossCheck

```json
{
  "cross_check_id": "BC1",
  "statement_claim_id": "AC1",
  "behavior_evidence_refs": ["E2"],
  "subject_match": "confirmed",
  "temporal_alignment": "aligned | lagged",
  "tenure_alignment": "confirmed | not_applicable",
  "result": "supports | contradicts | mixed | not_enough_evidence",
  "causality_claimed": false
}
```

规则：

- 必须关联已存在的 AnalysisClaim，并披露非空的行为证据 ID；证据 ID 的真实性、来源等级和新鲜度继续由现有 Evidence Ledger 校验。
- `supports/contradicts/mixed` 必须确认主体、时间窗口和任职/控制关系。
- 季度持仓是滞后披露，不得伪装成实时资金流。
- 相关行为最多用于 corroborate/contradict；`causality_claimed` 必须为 `false`。
- 主体、任期或时间无法对齐时只能 `not_enough_evidence`。

## 9.1 KOLMethodCard（v2.42 可选扩展）

`kol_method_cards` 是 `research_provenance.v1` 的可选 evidence handoff，不是第二套 truth、评分、动作或记忆系统。缺省/旧 bundle 保持原行为。公开来源样例与三位作者的诚实缺口见 `templates/kol-method-cards-public-ledger.json`。

每张卡必须保留：

```json
{
  "method_card_id": "KMC-...",
  "author": "Author",
  "author_handle": "@handle",
  "market_scope": ["US"],
  "time_horizon": ["intraday", "swing"],
  "source_document_ids": ["C3"],
  "lifecycle": {
    "public_claim": {},
    "falsifiable_thesis": {},
    "universe_selection": {},
    "entry": {},
    "add_reduce": {},
    "stop_invalidation": {},
    "take_profit": {},
    "exit": {},
    "sizing_risk": {},
    "post_trade_calibration": {}
  },
  "promotion_gaps": [],
  "subscriber_gaps": [],
  "portable_parts": [],
  "do_not_port": [],
  "unknowns": [],
  "decision_boundary": {}
}
```

十个 lifecycle key 必须全部存在；公开资料没有某一环时也不能删字段，必须用 `evidence_status=unknown`、`inference_label=not_found_publicly`、`provenance_mode=unverified`、`claim_type=assumption`、空 source/framework/EID 数组和非空 `unknowns` 明示缺口。`supported` 只表示公开来源支持“作者这样表达过”，不表示方法有 alpha 或能直接行动。

每个 lifecycle stage 的固定 handoff：

```json
{
  "evidence_status": "supported | partial | unknown | not_applicable",
  "summary": "方法或明确 not_found_publicly",
  "source_document_ids": [],
  "quote_anchor_ids": [],
  "framework_claim_ids": [],
  "inference_label": "direct_assertion | framework_inference | self_reported_performance | not_found_publicly",
  "provenance_mode": "direct_quote | source_summary | framework_inference | unverified",
  "claim_type": "canonical enum only",
  "supporting_eids": [],
  "contradicting_eids": [],
  "unknowns": []
}
```

边界映射必须在进入 EvidenceItem 前完成：

| KOL 标签 | 规范 provenance | 规范 `claim_type` / 后果 |
|---|---|---|
| `direct_assertion` | `source_summary` 或有精确 QuoteAnchor 的 `direct_quote`；后者必须把非空 `quote_anchor_ids` 关联到 stage 自己的 `source_document_ids` | 使用主枚举；作者观点通常为 `opinion`，不能因“直接说过”升成事实 |
| `framework_inference` | `framework_inference` + 有效 FrameworkClaim | `assumption` 或 `opinion` |
| `self_reported_performance` | `unverified` | 固定 `opinion`，`evidence_status` 只能 partial/unknown；永不提高 reliability |
| `not_found_publicly` | `unverified` | 固定 `assumption`；显式 unknown，不得脑补 |

`fact_claim/experience_report/rumor/meme` 等 KOL 别名不得进入卡片；先按 `data-contracts.md` 映射到规范 `claim_type`。`supporting_eids/contradicting_eids` 是现有 EvidenceItem ID handoff，不是卡片自建证据账本；上游 ledger 未提供 canonical EID 时必须保留显式空数组并写入 `unknowns`，不得在卡片内伪造 EID。

SourceDocument 的 `access_state` 仅允许 `public|partial|subscriber_only|blocked`：来源或任一 lifecycle stage 为 `partial` 都令 bundle 至少为 `partial`（最高 L1）；subscriber-only/blocked 来源只能被 `subscriber_gaps` 引用，不能用于 lifecycle 或 promotion gap，每个受限 source row 都必须由至少一个非空 subscriber-gap source reference 覆盖。所有 gap row 的 `source_document_ids` 都必须非空。promotion/订阅/自报战绩必须分别留痕；可回源自报绩效必须保留 `self_reported_performance + unverified + opinion` 并令 bundle 至少为 `partial`，完全无可回源材料时必须改用 `not_found_publicly`/unknown 语义。自报 promotion gap 继续固定 `can_raise_reliability=false`。具体点位、收益、账户比例和 exact-trade imitation 放入 `do_not_port`。

每张卡必须固定以下单向边界：

```json
{
  "allowed_effects": ["tighten", "refresh_source", "request_manual_review"],
  "forbidden_effects": ["raise_action_level", "raise_position_cap", "raise_reliability", "revive_l0", "order_execution"],
  "position_multiplier": 0.0,
  "self_reported_performance_can_raise_reliability": false,
  "no_order_execution": true
}
```

实现由 `scripts/kol_method_card.py` 提供并由 `provenance_guard.py` 调用；两者只校验/封顶，不执行搜索、写 Memory、创建 watch/cron 或下单。

## 10. 溯源就绪度与动作边界

带 admission 语义的 bundle 还可声明：

```json
{
  "bundle_purpose": {"kind": "report_evidence | kol_method_handoff", "target_ids": ["TARGET-ID"]},
  "claim_coverage": {
    "source_document_ids": ["DOC1"], "framework_claim_ids": ["FC1"],
    "analysis_claim_ids": ["AC1"], "accepted_claim_ids": [],
    "watch_only_claim_ids": [], "context_only_claim_ids": ["AC1"],
    "rejected_claim_ids": [], "canonical_eids": [], "target_binding": "TARGET-ID"
  },
  "source_capture_manifest": [{
    "document_id": "DOC1", "content_sha256": "hex", "media_type": "text/plain",
    "source_role": "semantic", "disposition": "reviewed",
    "review_locator": "review-index:1", "supported_claim_ids": ["AC1"]
  }]
}
```

`bundle_purpose/claim_coverage` 对历史 capture bundle 保持可选；缺省时输出 `bundle_role=capture_only`、accepted IDs 为空、不可作为报告 admission。声明 purpose 后，target、source/framework/analysis stable IDs 与非空 coverage 必须可达；watch/context 集合必须与每条 AnalysisClaim 的 `decision_use` 精确一致，accepted/rejected 必须分列且互斥。manifest 是可选的 capture 分类层：semantic 必须绑定 claim，nonsemantic 禁止绑定；semantic raster 需要 hash、正整数尺寸和 review-index/DOM locator，text/plain 不能替代 image-only evidence。

KOL 私有捕获 attestation 是不能复制原邮件/原图时的最小泄露例外，不是一般 hash 旁路：只能用于 `kol_method_handoff`，必须逐 quote 固定 hash/locator，必须保留 partial、空 accepted/canonical 集合、watch/context-only 和 nonmaterial Memory；`report_evidence`、verified readiness、结论支持或 action promotion 均不得使用该路径。任何 quote/hash/locator/manifest 不一致都 fail-closed。

历史 `capture_only` KOL card bundle 仍可读取旧的 snapshot/retrieval 排序；它不会获得 claim admission 或报告 authority。无卡 bundle 与所有 purpose-bound bundle 均严格执行 source time ordering，新 KOL handoff 不得使用此兼容路径。

| 结果 | 含义 | 建议模块信号 |
|---|---|---|
| `verified` | contract 无错误、无 warning | 不生成额外 signal；仍需过 Evidence/Mira/Compiler |
| `partial` | 例如无本地捕获或转载许可未确认 | `research_readiness` 最高 L1，tighten-only |
| `blocked` | 引文、hash、反证、引用链、schema 等失败 | `research_readiness` 最高 L0、`position_multiplier=0.0` |

约束：

- `verified` 只表示溯源结构完整，不表示投资结论为真。
- 新 guard 不会提高 action level、position cap 或 multiplier。
- `hard_veto=false`：它是认知就绪度封顶，不替代市场风险 hard veto。
- 任何输出都固定 `no_order_execution=true`。

## 11. Decision Memory 接入

validator 返回：

```json
{
  "memory_link": {
    "provenance_bundle_sha256": "...",
    "source_document_ids": ["DOC1"],
    "quote_anchor_ids": ["Q1"],
    "framework_claim_ids": ["FC1"],
    "analysis_claim_ids": ["AC1"],
    "behavior_cross_check_ids": [],
    "kol_method_card_ids": ["KMC-..."],
    "accepted_claim_ids": [],
    "rejected_claim_ids": [],
    "canonical_eids": [],
    "materiality_eligible": false,
    "admission_status": "verified | partial_watch_only | blocked_gap_only"
  }
}
```

接入纪律：

- 不新增数据库表；把 `memory_link` 放入现有 decision payload 的 `payload_json`/附加上下文。
- 只有 purpose-bound、verified 且同时有 accepted claim 与 canonical EID 的非 KOL handoff 才可能令 `materiality_eligible=true`；仍须保留现有 `evidence_ids/hypothesis_ids/conflict_ids`。
- `partial` 只允许作为 watch/research note，不进入 calibration/materiality。
- `blocked` 不得固化为已接受事实；若需留痕，只记录 DataGap、纠错或拒绝原因。
- raw source 和长引文留在受许可约束的 artifact；Memory 主要存稳定 ID 与 bundle hash，避免复制和污染。
- 后续发现错误时 append 新纠错事件，不覆盖旧 bundle 或旧 decision。

## 12. CLI

```bash
python3 scripts/provenance_guard.py \
  --bundle templates/research-provenance-bundle-pass.json \
  --pretty

python3 scripts/provenance_guard.py \
  --bundle templates/kol-method-cards-public-ledger.json \
  --pretty

python3 scripts/validate_report.py report.md \
  --provenance-bundle provenance.json
```

`validate_report.py` 保持向后兼容；传入 bundle 时会拒绝 `blocked`，并把 `partial` 报告封顶 L1。

退出码：

- `0`：bundle 通过；可能是 `verified` 或只有非阻塞 warning 的 `partial`。
- `1`：结构可解析，但 provenance validation blocked。
- `2`：文件不可读、JSON 非法或根节点不是 object。

## 13. 关键错误码

| 错误码 | 含义 |
|---|---|
| `unsupported_schema_version` | schema 不匹配 |
| `invalid_as_of` | 时间缺时区或不可解析 |
| `future_as_of` / `source_*_invalid` / `source_time_order_invalid` | bundle/source 时间在未来、不可解析或乱序 |
| `source_access_state_invalid` / `source_access_blocked` | 访问枚举非法，或受限来源越权支撑 semantic 内容 |
| `order_execution_boundary_missing` | 未显式声明研究只读边界 |
| `source_metadata_missing` / `invalid_source_url` | 来源链字段不足或 URL 非 HTTP(S) |
| `source_path_outside_bundle` | 本地路径越界 |
| `source_hash_mismatch` | 捕获内容已变化或 hash 错误 |
| `quote_not_exact` / `quote_locator_mismatch` | 引文被加工或位置不成立 |
| `quote_attestation_hash_mismatch` / `private_capture_attestation_invalid` | KOL 私有捕获的 quote hash 或窄化条件不成立 |
| `public_excerpt_too_long` | 对外短引超过保守上限 |
| `framework_counterevidence_not_checked` | 框架未做反证搜索 |
| `framework_status_conflict` | 已被反证却仍标 supported |
| `analysis_*_missing` | 分析主张存在悬空来源/引用/框架 |
| `unverified_claim_supports_conclusion` | 未验证主张越权支撑结论 |
| `analysis_falsifier_missing` | 结论型主张没有失败条件 |
| `analysis_counterevidence_conflict` | mixed/contradicted 反证仍试图支撑结论 |
| `claim_coverage_*` / `bundle_purpose_*` | purpose、target、claim IDs 或 decision-use 集合未绑定 |
| `capture_manifest_*` | source role、hash、media、尺寸、review locator 或 claim binding 不成立 |
| `behavior_alignment_insufficient` | 主体/时间/任期不足以做言行判断 |
| `behavior_causality_forbidden` | 把行为相关性误写成因果 |
| `duplicate_id` / `invalid_*` | stable ID 或枚举发生自由文本漂移 |
| `kol_source_*` / `kol_*_at_*` / `kol_access_state_*` | KOL 来源缺作者、URL/发布时间/抓取时间或访问状态 |
| `kol_lifecycle_missing_stage` / `kol_inference_label_missing` | 生命周期或 direct/inference/self-report 标签不完整 |
| `kol_noncanonical_claim_type` / `kol_*_mapping` | KOL 别名未映射到规范 claim/provenance |
| `kol_direct_quote_anchor_missing` / `kol_direct_quote_source_mismatch` | direct quote 缺精确锚点或锚点来源不属于该 stage |
| `kol_partial_evidence_stage` / `kol_self_reported_performance_unverified` | lifecycle partial 或自报绩效触发 partial/L1 tighten-only |
| `kol_self_reported_source_missing` | 自报绩效无可回源 source；必须改用公开未找到语义 |
| `kol_restricted_source_used` | 订阅/blocked 内容越权支撑方法 |
| `kol_restricted_source_gap_missing` / `kol_restricted_source_outside_subscriber_gaps` | 受限来源未被 subscriber gap 覆盖或在其他位置越权引用 |
| `kol_gap_source_missing` | gap row 缺非空且属于卡片的 source ID |
| `kol_decision_boundary_invalid` | 卡片试图加动作、加仓、抬可信度、复活 L0 或下单 |

## 14. 验证与回归

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_provenance_guard.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_kol_method_card.py
python3 scripts/provenance_guard.py --bundle templates/research-provenance-bundle-pass.json --pretty
python3 scripts/validate_skill.py --all
```

负向测试必须至少覆盖：

- 拼接/省略号/改写后冒充原话。
- hash 与 locator 不匹配。
- schema、section、stable ID 和枚举漂移。
- 引用不存在的来源、引文或框架。
- 反证未检查、被反证框架仍 supported。
- unverified claim 支撑结论。
- 结论缺 falsifier。
- 言行对照缺主体/时间/任期或宣称因果。
- 版权未知却公开长摘录。
- direct quote 缺锚点、引用不存在的锚点或锚点来源不属于 stage。
- partial stage、自报绩效和受限来源 gap 覆盖未能正确收紧 readiness。
- subscriber/promotion gap 使用空 source ID，或受限来源出现在 subscriber gap 之外。
- `no_order_execution` 缺失。

## 15. 永久红线

- 不用来源数量、搜索引擎数量或精确分数替代证据质量。
- 不把框架推演写成某人的原话。
- 不把媒体标题、摘要、RAG 命中或用户提供的伪引文自动升格为 verified fact。
- 不把人物专属观点、第三方全文语料或未经许可的数据快照迁入通用 skill。
- 不把持仓相关性写成观点导致行为的因果证明。
- 不让 provenance guard 成为新动作模块、下单器、cron、邮件或外部 alert。
- 不把 KOL 方法卡变成喊单复制器；社媒发现只能收紧、刷新或交人工，不能抬动作/仓位/可信度或复活 L0。
