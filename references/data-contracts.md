# Data Contracts · trading-research 证据数据结构

本文件定义脚本和报告共同使用的最小 JSON 结构。字段可以缺，但缺失必须进入 `data_gaps`，不能脑补。

## Claim Type Canonical Enum

主枚举：`fact | reported_metric | guidance | forecast | assumption | opinion | market_pricing | derived_calculation | rumor_signal`。所有证据层默认使用这套枚举。

| 旧/场景别名 | 规范映射 | 说明 |
|---|---|---|
| `fact_claim` | `fact` | X 一线事实声称，仍需 verification_status 标注 |
| `experience_report` | `opinion` | 一线经验默认是观点/样本，不是可泛化事实 |
| `rumor` | `rumor_signal` | 传闻线索 |
| `meme` | `rumor_signal` | 情绪/梗图只作弱线索 |
| `agent_summary` | `assumption` | Agent 摘要必须回抓原文；未回抓前按 assumption/clue 处理 |


## SecurityIdentity

```json
{
  "query": "300846.SZ",
  "market": "A",
  "symbol": "300846",
  "exchange": "SZ",
  "canonical_symbol": "300846.SZ",
  "longbridge_symbol": "300846.SZ",
  "akshare_symbol": "300846",
  "company_name": null,
  "currency": "CNY",
  "timezone": "Asia/Shanghai",
  "trading_rules": ["T+1", "limit_up_down"],
  "confidence": "high",
  "ambiguity": []
}
```

## EvidenceArchive

```json
{
  "target": "TSLA",
  "generated_at": "2026-06-05T00:00:00Z",
  "identity": {},
  "commands_run": [],
  "data_sources": [],
  "macro_dashboard": {},
  "endogenous_market_structure": {},
  "grok_web_signals": [],
  "search_candidates": [],
  "intelligence_coverage": {},
  "research_watch_triggers": [],
  "x_frontline_signals": [],
  "kol_method_cards": [],
  "prediction_market_signals": [],
  "execution_window_signals": [],
  "research_experiments": [],
  "evidence_ledger": [],
  "hypothesis_ledger": [],
  "conflict_ledger": [],
  "method_signal_matrix": [],
  "position_cap_calc": {},
  "critical_gaps": [],
  "red_flags": [],
  "suggested_module_signals": [],
  "report_contract_status": "draft_context_only | partial_evidence | evidence_ready"
}
```

## EvidenceItem

```json
{
  "eid": "E1",
  "type": "quote | kline | financial | filing | news | social | grok_web | x_frontline | prediction_market | macro | gamma | account | fund_flow | a_share_raw | passive_flow | issuance | lockup | sentiment | risk_regime | backtest | quant_factor | portfolio_risk | execution_window",
  "source": "LongBridge quote",
  "timestamp": "2026-06-05T00:00:00Z",
  "raw_fact": "事实原文或结构化摘要",
  "variable": "financial_validation | narrative_delta | trading_confirmation | trend_structure | money_flow_confirmation | dragon_tiger_list | valuation_snapshot | concept_membership | official_disclosure | grok_web_clue | original_source_gate | x_frontline_clue | x_identity_gate | x_cross_check_gate | x_time_gate | prediction_market_clue | prediction_market_prior | resolution_risk | liquidity_quality | crowding_risk | passive_flow | issuance_overhang | macro_dashboard_state | rotation_regime | execution_window | macro_policy | account_seat | risk_regime | quant_robustness | portfolio_risk_budget",
  "direction": "Bull | Bear | Neutral",
  "strength": 0,
  "reliability": 0.8,
  "freshness": 1.0,
  "cross_check": [],
  "decision_impact": "raise | lower | unchanged",
  "claim_type": "fact | reported_metric | guidance | forecast | assumption | opinion | market_pricing | derived_calculation | rumor_signal",
  "source_speaker": "company | management | regulator | exchange | market | sellside | social | agent",
  "verification_status": "verified | disclosed | claimed | estimated | modeled | unverified | contradicted",
  "evidence_category": "verified_fact | reported_fact | company_statement | management_guidance | market_pricing | modeled_scenario | assumption | inference | estimate | weak_signal | stale | contradicted | unknown",
  "freshness_status": "current | acceptable_for_period | preliminary | stale | unknown",
  "conflict_status": "none | unresolved | contradicted | not_checked",
  "treatment": "use_normally | attribute | sensitize | haircut | source_gap | monitor | exclude | open_item",
  "readiness_impact": "supports_durable_conclusion | supports_working_view | monitoring_only | blocks_actionability | not_material",
  "calculation_ref": "CALC1 | formula_note | not_applicable",
  "source_language": "zh-CN | en | ja | ko | not_applicable",
  "translation_basis": "not_translated | agent_translation | provider_translation | official_translation | bilingual_source | not_applicable"
}
```

Mira v2.7 兼容规则：
- S/A/B/C/D 与 `reliability` 仍判断来源可信度；`claim_type/evidence_category/readiness_impact` 判断该 claim 能否支撑当前结论。
- `market_pricing` 只能说明市场如何定价，不能验证基本面为真。
- `company_statement` / `management_guidance` 是预期输入，不是已兑现事实。
- `derived_calculation` 影响动作时必须有 `calculation_ref`、公式说明或 calculation ledger。
- `weak_signal/stale/contradicted/unknown` 默认不能支撑 L1+，应进入 DataGap 或 Conflict Ledger。

## USCompanyEvidence v1（v2.53）

`scripts/us_company_evidence.py` 只服务美股公司只读证据，输出 `us_company_evidence.v1`；LongBridge 是 provider/transport，SEC EDGAR 是原始监管 `source_family`，两者不得混为独立事实源。

```json
{
  "schema_version": "us_company_evidence.v1",
  "market": "US",
  "symbol": "AAPL.US",
  "fetched_at": "RFC3339",
  "status": "ok | partial | fail",
  "parts": {
    "financial_report": {},
    "valuation": {},
    "regulatory_filings": [],
    "insider_trades": {},
    "shareholder_snapshot": {},
    "institutional_portfolio": {},
    "sec_companyfacts": {}
  },
  "regulatory_evidence": [
    {
      "provider": "longbridge | sec_edgar",
      "provider_family": "longbridge | sec_edgar",
      "source_family": "sec_edgar | unknown_aggregator",
      "source_kind": "regulatory_filing_index | regulatory_filing | sec_form4_derived",
      "form_type": "10-K | 10-Q | 8-K | 4 | 13F-HR | null",
      "accession_number": "0000000000-00-000000 | null",
      "issuer_cik": "0000000000 | null",
      "filed_at": "RFC3339 | YYYY-MM-DD | null",
      "report_period": "YYYY-MM-DD | null",
      "document_url": "https://www.sec.gov/Archives/... | null",
      "underlying_fact_key": "sec:<accession> | null",
      "retrieval_paths": ["longbridge_cli", "sec_edgar"],
      "independent_source_count": 1,
      "data_gaps": []
    }
  ],
  "source_health": [],
  "gaps": [],
  "no_order_execution": true
}
```

约束：
- `provider_id` 是 LongBridge 内部 ID，不是 SEC accession；只有可从 SEC URL/官方响应规范化出的 accession 才能生成官方 filing EID。
- `E300–E307` 为 `evidence_run.py` 的美股监管 filing 保留段；按 `underlying_fact_key=sec:<accession>` 去重，超出 8 条只保留在原始快照并登记 `filing_eid_limit`。
- 同一 accession 经 LongBridge 与 SEC 直连返回时合并 `retrieval_paths`，`independent_source_count` 始终为 1；多份 SEC filing 也不能机械当成多个独立来源 family。
- 聚合财报 E30 可以展示多个 `source_families`，但 `independent_source_count=1`；未知、`mixed` 或 `unknown_*` family 不制造独立票。
- 来源覆盖映射：1 个已知底层 family → `data_quality:source_coverage` L0/0；2 个 → 最高 L1/0.5；≥3 个才不额外收紧。任一 unknown family 与 1-family 已知证据并存仍按 L0。
- SEC 直连仅在 `SEC_EDGAR_IDENTITY` 含可联系邮箱时启用；缺失返回 `identity_missing`，HTTP/网络失败返回 `source_unavailable`/明确 error class，绝不改写成“无 filing”。默认缓存 TTL 6 小时，目录 700、文件 600。
- LongBridge 未给出发行人 CIK 时，可先读取 SEC 官方 `https://www.sec.gov/files/company_tickers.json`，只接受 exact ticker 且唯一 CIK 的映射；0 命中或多 CIK 均返回 `issuer_cik_missing`。ticker map 与 submissions/companyfacts 使用同一 identity、host allowlist、限速、8 MiB 上限和私有缓存边界。
- `SourceHealth` 必须含 `source/provider_family/source_family/status/checked_at/safe_summary`；SEC 行可另含 `from_cache`，不得保存原始 payload、header 或 identity。


## DecisionRequest v2 与 ModuleSignal

L1+ 或任何会进入 System A proposal 的决策必须使用 `decision_request.v2`。旧 payload 仅用于兼容历史回归，输出 `contract_status=legacy_unverified`，不得作为执行凭证。

```json
{
  "schema_version": "decision_request.v2",
  "decision_context": {
    "query_tier": "T0 | T1 | T2",
    "intent": "research | open | hold | reduce | exit",
    "has_position": false,
    "as_of": "RFC3339",
    "required_modules": ["risk_regime", "portfolio_risk_budget", "data_quality"]
  },
  "module_signals": [
    {
      "module": "risk_regime | forced_liquidation | liquidity_squeeze | dispersion_crowding | liquidity | gamma | macro | endogenous_structure | fundamentals | participant_flow | grok_web | x_frontline | prediction_market_prior | modeled_scenario | quant_robustness | portfolio_risk_budget | account | brokerage_portfolio_margin | data_quality | research_readiness | calculation_quality | ingestion_permission | execution_window | event_proximity | conflict_ledger | market_data | filing | a_share_raw_source",
      "sub_framework": "capex_duration | prosperity_davis | three_clocks | jevons | supply_shock | analog_prior | davis_double | option_execution | source_coverage | null",
      "max_action_level": "L0 | L1 | L2 | L3 | L4 | L5",
      "entry_permission": "WATCH | TEST | BUILD | ADD | BLOCK",
      "holding_directive": "HOLD | REDUCE | EXIT",
      "position_multiplier": 1.0,
      "hard_veto": false,
      "evidence_refs": ["EID-..."],
      "observed_at": "RFC3339",
      "stale_after": "RFC3339",
      "reason": ""
    }
  ]
}
```

兼容映射：L0→WATCH，L1→TEST，L2→BUILD，L3→ADD，L4→`BLOCK+REDUCE`，L5→`BLOCK+EXIT`。`position_multiplier=1.0` 才是中性；正 `0.0` 表示不允许新仓，不得再表达“本模块不加分”。乘数必须是可转换、有限且非负的数值；bool、NaN/inf、负数、IEEE/字符串/raw-JSON 负零、负下溢、不可转换值或整数转浮点 overflow 均为合同错误，不能静默 clamp 成已接受的零或回退到 `1.0`。

运行时规则：
- module 必须属于注册表；未知 module 在 legacy/v2 都 fail-closed。
- v2 的 `decision_context.query_tier/intent/has_position/as_of` 必须显式存在且类型正确；缺失时不得用默认 `open`、无持仓或当前时间补齐。
- v2 的 `required_modules` 必须全部出现；同一 `module+sub_framework` 不得重复。
- v2 的每个 signal（包括 L0）都必须有 `evidence_refs/observed_at/stale_after`；L0 仍可用 WATCH/BLOCK、hard veto、holding directive 或 multiplier 改变结果，不能豁免。Compiler 以调用时可信 runtime `now` 为时间权威：`observed_at` 不得晚于 `decision_context.as_of` 或 runtime `now`，`stale_after` 必须同时晚于两者；payload 不能靠回拨/前推 `as_of` 复活 stale/future signal。
- 任一 signal 的 `position_multiplier` 无法进入有限非负浮点域时，Compiler 必须返回 `invalid_position_multiplier:<module-key>`、`strict_failed/legacy_failed`、`L0/BLOCK/0.0`；CLI 必须在 parser boundary 以有界 `parse_int` 保留普通整数类型和 lexical `-0` sign bit，并把超长整数映射为不含原值的 invalid sentinel，使 Python 3.9/3.11 语义一致。成功生成阻断 envelope 按既有协议 exit 0，不得 traceback 或回显超大原值；只有语法损坏、文件读取等确实无法生成编译 envelope 的异常才 exit 1。
- strict v2 只有 `intent=open` 能形成 entry authority。`research/hold/reduce/exit` 即使不要求 open baseline，也强制 `entry_permission=BLOCK`、`final_position_multiplier=0.0`；已有持仓仍可由有效 signal 输出 REDUCE/EXIT。
- `entry_permission` 与 `holding_directive` 分轴合成；`EXIT > REDUCE > HOLD`，持仓动作不得再被 L0-L3 数值排序覆盖。
- Compiler 输出可作为研究结果；只有 `contract_status=strict_pass` 的 DecisionEnvelope 才能进入 System A open executor。

`sub_framework` 只恢复可解释性并为未来分框架校准留钩子；不得新增动作等级。

## SearchDiscoveryCandidate

搜索结果页只是“去哪回抓”的候选，不是 `EvidenceItem`。必须保留查询外发边界、provider health 与独立索引口径：

```json
{
  "candidate_id": "SDC-...",
  "query": "NVDA latest filing",
  "profile": "general | news | filing | rumor | hot | dividend",
  "market": "A | HK | US | unknown",
  "title": "...",
  "url": "https://...",
  "snippet": "...",
  "published_at": "RFC3339 | RFC822 | null",
  "freshness_state": "within_window | stale | future_timestamp | unknown | not_required",
  "freshness_hours": "integer | null",
  "freshness_eligible": true,
  "query_match_terms": ["NVDA", "earnings"],
  "discovery_relevance": 0.667,
  "engines": ["google_news", "bing_web"],
  "provider_families": ["google", "bing"],
  "independent_provider_count": 2,
  "observed_at": "RFC3339",
  "discovery_only": true,
  "redirect_unresolved": false,
  "claim_type": "rumor_signal | assumption",
  "verification_status": "unverified",
  "attention_state": "quiet | emerging | broad",
  "credibility_state": "unverified",
  "readiness_impact": "monitoring_only",
  "allowed_use": ["evidence_collection_plan", "watch_priority", "source_discovery"],
  "forbidden_use": ["verified_fact", "position_sizing", "order_execution"]
}
```

兼容规则：多个搜索引擎命中只提高 discovery confidence/attention，不提高 claim credibility；Bing Web/Bing News 只算一个 provider family；`news` 默认 72h，明确 stale/future 的候选剔除，缺少可解析时间戳的候选必须标记 `freshness_state=unknown/freshness_eligible=false` 并写 DataGap；回抓原始 URL 成功后新建普通 `EvidenceItem` 并按原始来源重新定级，不能直接“升级”搜索候选。

## IntelligenceCoverageMatrix

Coverage 只证明本次研究取得了哪些数据，不证明数据内容为真，也不能直接形成方向。`unsupported/auth_missing/rate_limited/error/missing/stale` 必须进入 DataGap，禁止转成零值或“无事件”。

```json
{
  "schema_version": "intelligence_coverage.v1",
  "as_of": "RFC3339",
  "targets": ["AAPL.US", "MSFT.US"],
  "requirements": [
    {"dimension": "quote", "criticality": "high", "min_independent_sources": 1},
    {"dimension": "news", "criticality": "medium", "min_independent_sources": 2}
  ],
  "observations": [
    {
      "target": "AAPL.US",
      "dimension": "quote",
      "source": "longbridge_quote",
      "provider_family": "longbridge",
      "status": "live | delayed | cached | unsupported | auth_missing | rate_limited | error | unknown",
      "observed_at": "RFC3339 | null",
      "stale_after": "RFC3339 | null",
      "data_present": true,
      "fallback_level": "T1 | T2 | T3"
    }
  ],
  "coverage": [],
  "coverage_id": "ICOV-...",
  "coverage_ratio": 0.0,
  "data_gaps": [],
  "fair_collection_queue": [],
  "suggested_module_signal": {
    "module": "data_quality",
    "sub_framework": "source_coverage",
    "max_action_level": "L0",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "evidence_refs": ["ICOV-..."],
    "observed_at": "RFC3339",
    "stale_after": "RFC3339",
    "tighten_only": true,
    "cannot_raise_upstream": true
  },
  "no_order_execution": true
}
```

每个 `target × dimension` 的 coverage state 只能是 `covered_live | covered_reference | stale | unsupported | unavailable | missing`。`delayed/cached` observation 缺 `fallback_level` 必须 fail-closed。公平队列先按全局 criticality 分层，再在同一层按标的 round-robin；预算不足时 low gap 不能排在其他标的 high gap 前。Coverage signal 的 `stale_after` 最多为 `as_of+15m`，且不得晚于可用 observation 中最早的 `stale_after`。

## ResearchWatchTrigger

`must_refresh_if` 的结构化版本，只描述何时重新抓取/重编译；不得创建外部 alert、cron、邮件或订单。候选必须先通过 `scripts/research_watch_trigger.py` 的 strict allowlist validator，验证后仍不执行任何外部副作用。

```json
{
  "schema_version": "research_watch_trigger.v1",
  "trigger_id": "RWT-...",
  "symbol": "AAPL.US",
  "trigger_type": "price_cross | filing_event | event_window | source_stale | data_gap_recovered | conflict_resolved",
  "condition": "above | below | cross_up | cross_down | occurs | becomes_stale | recovers",
  "threshold": null,
  "reference_value": null,
  "created_at": "RFC3339",
  "expires_at": "RFC3339",
  "cooldown_seconds": 3600,
  "rearm_rule": "manual | recross | after_cooldown | never",
  "one_shot": true,
  "evidence_refs": ["E1"],
  "on_trigger": "rerun_research | refresh_source | request_manual_review",
  "no_order_execution": true
}
```

触发发生后必须刷新事实并重新走 Decision Compiler；旧 action level 与 position cap 不得自动沿用。

## MacroDashboard

```json
{
  "liquidity": {"current_state": "fragile_tight", "recent_trend": "deteriorating", "next_trigger": "TGA drawdown", "market_implication": "rebound needs haircut"},
  "economy": {"current_state": "slowing_but_not_breaking"},
  "inflation_rates": {"current_state": "cooling_but_not_clean"},
  "sentiment": {"current_state": "greedy_but_fragile"},
  "composite": "neutral"
}
```

## EndogenousStructure

```json
{
  "state": "fragile_crowded",
  "narrative_stage": "crowded",
  "consensus_premium": "high",
  "leverage_state": "elevated",
  "passive_flow_state": "mixed",
  "issuance_overhang": "high",
  "rotation_regime": "secondary_bull",
  "decision_impact": "prefer pair trade / reduce size"
}
```

## XFrontlineSignal

```json
{
  "eid": "E40",
  "source": "Hermes Grok/X",
  "author_identity": "frontline_practitioner | official | analyst | kol | anonymous | repost",
  "identity_confidence": "high | medium | low",
  "posted_at": "2026-06-05T00:00:00Z | unknown",
  "observed_at": "2026-06-05T00:00:00Z",
  "originality": "original | reply | repost | screenshot | second_hand",
  "claim_type": "fact | reported_metric | guidance | forecast | assumption | opinion | market_pricing | derived_calculation | rumor_signal",
  "decision_variable": "narrative_delta | policy_regulatory_delta | x_frontline_clue | crowding_risk",
  "cross_check_eids": [],
  "reliability_ceiling": 0.5,
  "decision_impact": "create_hypothesis | raise_watch_priority | needs_verification | ignore_noise"
}
```

## KOLMethodCard · `research_provenance.v1` 可选 handoff（v2.42）

KOL 方法卡复用 SourceDocument、FrameworkClaim、EvidenceItem、Mira 和 Decision Compiler；它不是新证据账本或动作模块。顶层 `kol_method_cards` 可省略以保持旧 bundle 兼容；一旦出现，每张卡必须完整映射十阶段生命周期：

```json
{
  "method_card_id": "KMC-...",
  "author": "Author",
  "author_handle": "@handle",
  "market_scope": ["US"],
  "time_horizon": ["intraday | swing | multi_week"],
  "source_document_ids": ["C3"],
  "lifecycle": {
    "public_claim": {"inference_label": "direct_assertion", "provenance_mode": "source_summary", "claim_type": "opinion", "source_document_ids": ["C3"], "quote_anchor_ids": [], "supporting_eids": [], "contradicting_eids": []},
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
  "decision_boundary": {
    "allowed_effects": ["tighten", "refresh_source", "request_manual_review"],
    "forbidden_effects": ["raise_action_level", "raise_position_cap", "raise_reliability", "revive_l0", "order_execution"],
    "position_multiplier": 0.0,
    "self_reported_performance_can_raise_reliability": false,
    "no_order_execution": true
  }
}
```

每个 lifecycle stage 还必须显式带 `evidence_status/summary/source_document_ids/framework_claim_ids/unknowns`。`direct_assertion` 只能映射 `source_summary|direct_quote`；若为 `direct_quote`，必须带至少一个现存 `quote_anchor_ids`，且每个 anchor 的 SourceDocument 必须属于该 stage 的 `source_document_ids`。`framework_inference` 只能映射规范 FrameworkClaim + `assumption|opinion`；`self_reported_performance` 固定 `unverified+opinion` 且只能 partial/unknown，必须有可回源 source，否则改用 `not_found_publicly`；任一 partial stage 都令 readiness 至少为 partial/L1。subscriber-only/blocked source row 只能由非空 `subscriber_gaps.source_document_ids` 覆盖，不得进入 lifecycle 或 promotion gap。`not_found_publicly` 固定 `unverified+assumption`。详细约束、访问状态与错误码以 `source-grounded-research-provenance.md` 为准。

## GrokWebSignal

```json
{
  "eid": "E35",
  "source": "Hermes Grok Web Search",
  "query": "NVDA latest supply chain risk last 72 hours",
  "observed_at": "2026-06-05T00:00:00Z",
  "original_url": "https://... | unavailable",
  "original_source_type": "official | filing | company_ir | market_data | media | github | x_social | forum | unknown",
  "claim_type": "fact | reported_metric | guidance | forecast | assumption | opinion | market_pricing | derived_calculation | rumor_signal",
  "timestamp_quality": "exact | date_only | relative | missing",
  "source_grade": "S | A | B | C | D",
  "reliability_ceiling": 0.5,
  "cross_check_eids": [],
  "decision_impact": "create_hypothesis | raise_watch_priority | needs_verification | ignore_noise",
  "module_signal": {
    "module": "grok_web",
    "max_action_level": "L0",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "unverified_grok_summary | original_source_verified | source_laundering_risk"
  }
}
```

## PredictionMarketSignal

```json
{
  "eid": "E55",
  "source": "Polymarket public market data",
  "market_url": "https://polymarket.com/event/...",
  "market_question": "Will <event> happen by <date>?",
  "event_slug": "event-slug | unavailable",
  "condition_id": "0x... | unavailable",
  "yes_token_id": "... | unavailable",
  "observed_at": "2026-06-05T00:00:00Z",
  "implied_probability": 0.63,
  "probability_basis": "midpoint | last_trade | best_bid_ask | unavailable",
  "probability_momentum_1h": null,
  "probability_momentum_24h": null,
  "probability_momentum_7d": null,
  "spread": 0.03,
  "depth_bid": null,
  "depth_ask": null,
  "depth_imbalance": null,
  "volume_24h": null,
  "open_interest": null,
  "trade_flow_velocity": null,
  "liquidity_adjusted_reliability": 0.55,
  "resolution_rule_status": "clear | ambiguous | disputed | unavailable",
  "resolution_risk": "low | medium | high | unknown",
  "cross_venue_gap": null,
  "cross_check_eids": [],
  "claim_type": "market_pricing",
  "verification_status": "verified | disclosed | estimated | unverified",
  "readiness_impact": "monitoring_only | supports_working_view",
  "module_signal": {
    "module": "prediction_market_prior",
    "max_action_level": "L1",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "read_only_market_pricing_prior; cannot raise position cap"
  }
}
```

Prediction-market compatibility rules:
- Missing `market_url`, timestamp, spread/depth/liquidity, or resolution-rule check → treat as `prediction_market_clue`, highest L0.
- Even complete/liquid prediction-market data is `market_pricing`, not verified fact; it can set a scenario prior or watchlist priority only.
- `prediction_market_prior` never raises `position_multiplier` and never replaces LongBridge, filings, official/regulator/company materials, Mira readiness, or Decision Compiler.

## ExecutionWindowSignal

US close-to-open execution overlay 的结构化输出。它只降级执行窗口，不提高研究结论、动作等级或仓位上限。

```json
{
  "eid": "EW1",
  "market_scope": "US_only",
  "window": "close_to_open",
  "entry_window": "15:50-16:00 ET",
  "exit_window": "09:30-09:40 ET next session",
  "entry_proxy": "close | vwap_15_50_16_00 | auction_fill",
  "exit_proxy": "open | vwap_09_30_09_40 | opening_range_exit",
  "gross_return": null,
  "estimated_cost_bps": null,
  "net_return": null,
  "setup_type": "earnings_continuation | sector_continuation | analyst_company_event | end_of_day_pressure_reversal | macro_event_window | unavailable",
  "liquidity_status": "pass | weak | unavailable",
  "cost_edge_status": "positive_after_costs | marginal | negative | unavailable",
  "do_not_apply_to": ["A_share", "HK_stock"],
  "exit_rule": "sell_or_rejudge_by_09_40_ET; holding after 09:40 requires a new intraday/swing Decision Compiler run",
  "module_signal": {
    "module": "execution_window",
    "max_action_level": "L0 | L1 | L2 | L3",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "eligible_close_to_open | missing_us_scope | cost_edge_negative | binary_event_risk | liquidity_gap | no_overnight_catalyst"
  }
}
```

Compatibility rules:
- `market_scope != US_only` → overlay output must be L0 and the report must return to the relevant market framework.
- Missing liquidity/cost/overnight setup data → L0/L1; do not infer an edge from gross return charts.
- `execution_window` can reduce the final action cap or position multiplier; it never raises them above upstream Decision Compiler.

## ShortCycleStructureOverlay

US-only 的后置结构校验契约。它消费不可变的上游方向/动作上限，不重做多因子选向；输出既有 `execution_window` / `gamma` / `data_quality` signals，并把 underlying 与 option instrument 分开编译。

```json
{
  "schema_version": "short_cycle_structure.v1",
  "market_scope": "US_only",
  "checkpoint": "close | pre_open | open_0940 | eod_review",
  "observed_at": "RFC3339",
  "upstream": {
    "decision_id": "immutable",
    "direction": "long | short",
    "action_cap": "L0 | L1 | L2 | L3",
    "position_multiplier": 0.0,
    "thesis_valid": true
  },
  "checkpoint_verdict": "eligible | exit_at_open | exit_or_reduce_by_0940 | recompile_intraday | review_only | data_gap | inapplicable | no_trade",
  "continuity": {
    "status": "pass | mixed | fail | data_gap | not_evaluated",
    "score": null,
    "components": {},
    "missing": []
  },
  "gamma_range": {
    "status": "confirmed_live | delayed_reference | stale_for_checkpoint | invalid | unavailable",
    "zone": "below_put_wall | put_to_flip | flip_to_call | above_call_wall | unavailable",
    "regime": "positive | negative | unknown",
    "risk_state": "breakdown_amplification | negative_gamma_below_flip | pin_possible_not_directional | neutral"
  },
  "option_execution_quality": {
    "status": "not_requested | eligible | limit_only | no_trade",
    "reasons": [],
    "metrics": {}
  },
  "instrument_permissions": {
    "underlying": "upstream_only",
    "option_contract": "not_requested | eligible | limit_only | no_trade"
  },
  "underlying_module_signals": [],
  "option_module_signals": [],
  "must_recompile_after_0940": true,
  "eod_review": {
    "status": "not_evaluated | insufficient_samples | partial_data | reviewed",
    "metrics": {},
    "attribution_labels": [],
    "weight_update": {
      "applied": false,
      "recommended": false,
      "sample_count": 0,
      "suggested_weights": {}
    }
  },
  "upstream_unchanged": true,
  "no_order_execution": true
}
```

Compatibility rules:
- Output action cap must never exceed `upstream.action_cap`. Overlay `position_multiplier` is a relative `[0,1]` modifier; it must be compiled together with the immutable upstream module so the final compiled size never exceeds upstream size.
- `must_recompile_after_0940=true`: the close-to-open thesis expires at 09:40 ET even when continuity passes.
- SPX gamma must carry session/expiry/timestamp/source delay; delayed or stale walls cannot relax a decision.
- `option_module_signals` may append `data_quality:option_execution`; never inject that instrument-only failure into `underlying_module_signals`.
- EOD weight suggestions are advisory, require the minimum real closed sample count, and can only adjust overlay soft components; original multi-factor weights and hard safety gates are immutable.
- Full schema, policy defaults and mapping: `references/short-cycle-market-structure-overlay.md`; executable: `scripts/short_cycle_structure.py`.

## AShareRawSignal

A 股公开源直连桥接输出，用于腾讯行情/估值、东财板块/资金流、巨潮公告。只能补证和交叉验证，不直接提高动作等级。

```json
{
  "eid": "AS1",
  "source_layer": "a_stock_data_bridge",
  "upstream": "simonlin1212/a-stock-data v3.2.2",
  "command": "quote | concept | announcements | fund-flow | health",
  "canonical_symbol": "600519.SH",
  "observed_at": "2026-06-18T12:00:00Z",
  "source": "Tencent Finance | Eastmoney push2 | CNINFO",
  "field_notes": {"43": "amplitude_pct_not_pb", "46": "pb"},
  "cninfo_orgid_fallback": false,
  "eastmoney_rate_limited": false,
  "items": [],
  "data_gaps": [],
  "module_signal": {
    "module": "a_share_raw_source",
    "max_action_level": "L0 | L1",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "public_source_bridge; cross_check_required; cannot_raise_position_cap"
  }
}
```

Compatibility rules:
- `quote` 可支撑 A 股行情/估值快照，但字段口径必须保留；腾讯字段 43 是振幅，不是 PB。
- `concept` 的东财 slist 把行业/概念/地域混合返回，只能做板块归属线索，不能当精确行业分类。
- `announcements` 若 `cninfo_orgid_fallback=true`，公告缺失不能解释成公司无公告，必须作为 orgId 缺口。
- `fund-flow` 空值可能来自非交易时段、网络或东财风控；默认写 `eastmoney_rate_limit_or_network_gap`。
- 完整输出进入 Evidence Ledger 后仍要过 Mira Quality Gate 与 Decision Compiler。

## ModeledScenarioSignal

MiroFish-style ontology-first / swarm simulation 输出。它是模型情景，不是事实证据。

```json
{
  "eid": "MS1",
  "source_layer": "mirofish_swarm_simulation_patterns",
  "source_project": "666ghj/MiroFish",
  "source_license": "AGPL-3.0",
  "adoption_mode": "methodology_only_no_code_copied",
  "scenario_question": "复杂事件/舆情/政策/产业链冲击会如何扩散？",
  "ontology": {
    "entity_types": [],
    "edge_types": [],
    "scenario_variables": []
  },
  "simulation_trace": {
    "assumptions": [],
    "agent_groups": [],
    "rounds_observed": null,
    "synthetic_observations": [],
    "action_log_summary": []
  },
  "claim_type": "assumption",
  "evidence_category": "modeled_scenario",
  "verification_status": "modeled",
  "readiness_impact": "monitoring_only",
  "allowed_use": ["hypothesis_ledger", "scenario_prior", "watch_priority", "risk_watchlist", "evidence_collection_plan"],
  "forbidden_use": ["verified_fact", "financial_validation", "trading_confirmation", "position_sizing", "order_execution"],
  "module_signal": {
    "module": "modeled_scenario",
    "max_action_level": "L0",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "synthetic modeled scenario; real-world evidence required"
  }
}
```

Compatibility rules:
- `modeled_scenario` 不能写入 `verified_fact`，不能替代行情/公告/财报/交易所监管/公司原始材料。
- 模拟 Agent 采访、synthetic future path、swarm reaction 只能进入 Hypothesis Ledger 或 Evidence Collection Plan。
- 默认 `max_action_level=L0`、`position_multiplier=0.0`；不得提高 action level、position cap 或仓位乘数。
- 若后续真实证据验证了某个观察，只能把真实证据作为新的 EvidenceItem 进入对应模块；模拟文本本身仍不加分。

## ResearchExperiment

```json
{
  "hypothesis": "因子/模型/筛选器要验证的市场命题",
  "data_scope": "market/date/universe/source",
  "feature_set": [],
  "train_validation_test": "walk_forward | time_split | unavailable",
  "costs_included": "yes | no | partial",
  "robustness_checks": ["drawdown", "capacity", "slippage", "no_lookahead", "survivor_bias"],
  "failure_modes": [],
  "decision_use": "hypothesis_only | ranking_support | risk_cap_support | not_usable",
  "module_signal": {
    "module": "quant_robustness",
    "max_action_level": "L0",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "lookahead_bias | missing_costs | missing_walk_forward | robustness_passed"
  }
}
```

## LearningPacket · System A → System B

```json
{
  "schema_version": "paper_learning_packet.v2",
  "system": "paper_trading_learning_packet",
  "generated_at_utc": "2026-06-13T00:00:00Z",
  "inputs": {},
  "summary": {},
  "materiality_gate": {},
  "signal_stats": {},
  "source_reports": {},
  "closed_trade_reviews": [],
  "open_trade_watchlist": [],
  "review_questions": {},
  "skill_upgrade_candidates": [],
  "required_next_actions": []
}
```

Compatibility rules:
- Supported versions: `paper_learning_packet.v1`, `paper_learning_packet.v2`.
- `paper_learning_packet.v2` keeps the v1 fields above and may add System A diagnostics such as `risk_breach_events` and universe-health fields. System B may read those only as isolated paper-calibration / universe-governance evidence; they do not alter live sizing or bypass Decision Compiler.
- Missing or unknown `schema_version` → reject with `packet_schema_mismatch`; do not best-effort parse into skill upgrades.
- Legacy packets without schema are historical evidence only and require manual migration before materiality/conflict gate.

## SelfOptimizationRun

```json
{
  "status": "upgraded | no_necessary_upgrade | blocked",
  "materiality": "high | medium | low | none",
  "checked": ["local_validators", "grok_oauth", "github_reference_projects", "user_corrections"],
  "changes": [],
  "validation": [],
  "conflicts_avoided": [],
  "next_watch": []
}
```

## DataGap

```json
{
  "target": "TSLA.US",
  "dimension": "filing",
  "gap": "IBKR readonly account context unavailable",
  "impact": "不能给账户级仓位或具体加减仓数量",
  "fallback": "给一般仓位上限；如用户提供只读持仓再重算",
  "severity": "low | medium | high | blocker",
  "reason_code": "unsupported | auth_missing | rate_limited | error | stale | missing | insufficient_independent_sources"
}
```

## ResearchReadiness

```json
{
  "depth_mode": "quick_map | standard | deep_dive",
  "information_value": "low | medium | high",
  "knowability_status": "knowable | partially_knowable | unknowable_now | irreducible_uncertainty",
  "readiness_level": "draft | working_view | research_ready | actionable_with_caveats | watch_only | not_actionable | needs_refresh | research_hypothesis",
  "readiness_basis": "哪些 EID / calculation / conflict 让它达到或停留在该等级",
  "blocking_gaps": [],
  "stale_after": "YYYY-MM-DD | event_window",
  "must_refresh_if": [],
  "max_action_level_cap": "L0 | L1 | L2 | L3",
  "module_signal": {
    "module": "research_readiness",
    "max_action_level": "L0",
    "position_multiplier": 0.0,
    "hard_veto": false,
    "reason": "needs_refresh / calculation_gap / source_gap / irreducible_uncertainty"
  }
}
```

Readiness 不替代 L0-L5，只作为 Decision Compiler 的 `data_quality/research_readiness` 模块输入：
- `draft/not_actionable/needs_refresh` → 默认最高 L0。
- `working_view/watch_only` → 默认最高 L1。
- `research_ready/actionable_with_caveats` → 允许其它模块正常裁决，但不自动提高动作等级。
- `research_hypothesis`（v2.33）→ 默认最高 L0/L1，`position_multiplier=0.0`；因子/信号缺 `random_ic_mean`/`alpha_t`/`n_factors_scanned`，或状态为 `train_only`/`noise`/`reversed_strict`（`references/factor-validation-strict-gate.md`）落在此档；应同步登记进假设注册表（`references/hypothesis-lifecycle.md`），不因报告一次就失踪。
- `research_ready` 是 readiness_level 的取值；`research_readiness` 是 module 名。

## IngestionRecord

```json
{
  "ingestion_route": "user_file | public_api | web_read | vendor_export | portfolio_export | manual_note",
  "license_scope": "public | user_provided | paid_restricted | vendor_restricted | unknown",
  "storage_scope": "transient | private | tracked_allowed",
  "redistribution_allowed": "yes | no | derived_only | unknown",
  "evidence_log_mapping": ["E1", "E2"],
  "calculation_ledger_required": "yes | no",
  "notes": "用户私有/付费/账户数据默认不进入 tracked skill 目录"
}
```

## SourceHealth

```json
{
  "source": "LongBridge",
  "status": "ok | fail | unavailable | skipped | rate_limited",
  "capability_state": "live | delayed | cached | unsupported | auth_missing | rate_limited | error | unknown",
  "market": "A | HK | US | global | unknown",
  "dimension": "quote | filing | news | fundamentals | technicals | attention | prediction_market | account",
  "provider_family": "longbridge",
  "checked_at": "2026-06-05T00:00:00Z",
  "observed_at": "RFC3339 | null",
  "stale_after": "RFC3339 | null",
  "fallback_level": "T1 | T2 | T3 | null",
  "safe_summary": "不含 token/账号敏感值的摘要",
  "error_class": null
}
```

## ResearchProvenanceBundle（v2.39）

外部原文、方法论方法研究和言行交叉验证必须使用 `research_provenance.v1`，详细 schema、版权边界、错误码与 Memory admission 见 `source-grounded-research-provenance.md`。

```json
{
  "schema_version": "research_provenance.v1",
  "as_of": "RFC3339 with timezone",
  "bundle_purpose": {"kind": "report_evidence", "target_ids": ["TSLA"]},
  "source_documents": [{"document_id": "DOC1", "original_url": "https://...", "published_at": "YYYY-MM-DD", "retrieved_at": "RFC3339", "access_state": "public", "content_sha256": "..."}],
  "quote_anchors": [{"quote_id": "Q1", "document_id": "DOC1", "verbatim_text": "短引文"}],
  "framework_claims": [{"framework_claim_id": "FC1", "quote_anchor_ids": ["Q1"], "status": "supported"}],
  "analysis_claims": [{"claim_id": "AC1", "provenance_mode": "framework_inference", "framework_claim_ids": ["FC1"]}],
  "behavior_cross_checks": [],
  "kol_method_cards": [],
  "claim_coverage": {"source_document_ids": ["DOC1"], "framework_claim_ids": ["FC1"], "analysis_claim_ids": ["AC1"], "accepted_claim_ids": [], "watch_only_claim_ids": [], "context_only_claim_ids": ["AC1"], "rejected_claim_ids": [], "canonical_eids": [], "target_binding": "TSLA"},
  "source_capture_manifest": [{"document_id": "DOC1", "content_sha256": "...", "media_type": "text/plain", "source_role": "semantic", "disposition": "reviewed", "review_locator": "review-index:1", "supported_claim_ids": ["AC1"]}],
  "no_order_execution": true
}
```

契约边界：

- SourceDocument/QuoteAnchor/FrameworkClaim/AnalysisClaim 分层，不得把框架推演伪装成原话。
- 旧 bundle 可省略 `bundle_purpose/claim_coverage`，但只能作为 `capture_only`，不得声称报告权威；一旦声明 purpose，target 与非空 claim coverage 必须绑定，watch/context 集合必须逐项等于 AnalysisClaim 的 `decision_use`。
- `source_capture_manifest` 可选；semantic row 必须绑定有效 claim/hash/media/review locator，nonsemantic row 不得携带 claim；语义 raster 还必须有正整数尺寸，文本不得冒充 image-only coverage。
- 私有 capture 不得复制进 Skill。仅 `kol_method_handoff` 可用 hash/locator/quote-ID 全绑定的 private-capture attestation，且必须是 `partial`、空 accepted/canonical EID、watch/context-only、`materiality_eligible=false`；不得用于 `report_evidence` 或 verified admission，任一绑定漂移都 fail-closed。
- `verified` 只表示溯源链完整，不代表投资主张为真；最终结论仍过 Evidence、Mira 与 Decision Compiler。
- `partial` 最多映射 `research_readiness=L1`；`blocked` 最多 L0 且 `position_multiplier=0.0`。
- 输出的 `memory_link` 只复用现有 Decision Memory payload，不新增数据库或第二套长期记忆；accepted/rejected IDs 分列，blocked 的 accepted 固定为空且 `materiality_eligible=false`。
- KOL 卡片只能提供现有 EvidenceItem/FrameworkClaim 的 handoff；`partial≤L1`、`blocked≤L0`，自报 PnL 永不提高 reliability。
- 机器校验：`scripts/provenance_guard.py`；通过样例：`templates/research-provenance-bundle-pass.json`。

## OKXPublicMarketSnapshot（v2.48）

`okx_public_market_snapshot.v1` 只表示无凭据公共市场观测，不证明地区/账户资格、底层公司股东权利或可执行性。机器入口：`scripts/okx_public_snapshot.py`。

```json
{
  "schema_version": "okx_public_market_snapshot.v1",
  "venue": "okx",
  "channel": "cex_spot",
  "mode": "public",
  "site": "global | eea",
  "fetched_at": "RFC3339 with timezone",
  "instrument": {"inst_id": "XMU-USDT", "inst_type": "SPOT", "inst_category": "3", "state": "live", "tick_size": 0.01, "lot_size": 0.000001, "min_size": 0.001},
  "product_identity": {"mapping_scope": "exact_exchange_instrument_only", "mapping_verified": true, "region_eligible": null, "evidence_refs": ["OKX-PUBLIC-INSTRUMENT:XMU-USDT"], "observed_at": "RFC3339", "stale_after": "RFC3339"},
  "market": {"source_ts": "RFC3339", "last": 890.68, "bid": 890.99, "ask": 891.28, "spread_bps": 3.2543, "order_book_ts": "RFC3339", "freshness_status": "fresh", "top5_bids": [{"price": 890.99, "size": 0.46}], "top5_asks": [{"price": 891.28, "size": 0.49}]},
  "technical_observables": {"completed_1d_candles": 11, "completed_4h_candles": 70, "history_limited": true},
  "credentials_used": false,
  "public_endpoints_only": true,
  "no_order_execution": true
}
```

instruments/ticker 的响应身份必须与请求 `instId` 完全一致；OKX EEA books/history-candles 当前不回显 `instId`，其身份由 adapter 构造的固定请求 URL 与单次响应绑定，若未来响应行主动带 `instId` 则必须 exact match，否则拒绝。ticker 只提供 last/24h，bid/ask/spread 与 top-5 depth 必须来自同一份 books。任一 endpoint 失败、mixed instrument、future/stale（最大 120 秒）、零/倒挂盘口或非法数值均 fail-closed，不用 0 伪装缺失。所有 source numeric 与派生 numeric 都必须 finite；midpoint 使用稳定公式，per-level/aggregate notional 与 1D/5D return 每步算术后重新验证，禁止发布 `Infinity/NaN`；正常值可按既有精度 round，但正 midpoint/notional 若会被量化成 `0.0`，必须保留原有限值。

## EntryScore（v2.48）

`entry_score.v1` 是报告/readiness 投影，不是第二套动作编译器。机器入口：`scripts/entry_score.py`。

```json
{
  "schema_version": "entry_score.v1",
  "symbol": "XMU-USDT",
  "as_of": "RFC3339 with timezone",
  "product_identity": {
    "venue": "okx",
    "channel": "cex_spot",
    "instrument_id": "XMU-USDT",
    "instrument_type": "SPOT",
    "instrument_category": "3",
    "mapping_scope": "exact_exchange_instrument_only",
    "mapping_verified": true,
    "state": "live",
    "region_eligible": true,
    "evidence_refs": ["EID-*"],
    "observed_at": "RFC3339",
    "stale_after": "RFC3339"
  },
  "factors": {
    "technical": {"score": 0, "reason": "", "evidence_refs": ["EID-*"], "observed_at": "RFC3339", "stale_after": "RFC3339"},
    "capital_flow": {"score": 0, "reason": "", "evidence_refs": ["EID-*"], "observed_at": "RFC3339", "stale_after": "RFC3339"},
    "sentiment": {"score": 0, "reason": "", "evidence_refs": ["EID-*"], "observed_at": "RFC3339", "stale_after": "RFC3339"},
    "fundamentals": {"score": 0, "reason": "", "evidence_refs": ["EID-*"], "observed_at": "RFC3339", "stale_after": "RFC3339"},
    "macro": {"score": 0, "reason": "", "evidence_refs": ["EID-*"], "observed_at": "RFC3339", "stale_after": "RFC3339"}
  }
}
```

输出规则：

- `status=complete` 时发布 `entry_score_100`；五因子权重固定为 technical 20%、capital_flow 20%、sentiment 20%、fundamentals 25%、macro 15%。
- 五因子/产品身份/EID/freshness 任一缺失：`status=insufficient_data`，总分必须为 `null`。
- material conflict：保留解释分，但 `unresolved_conflict=true`，`entry_permission_ceiling=BLOCK`。
- 地区/账户资格未知时追加 `data_quality` veto；明确不合资格时追加 `account` veto。两者均可保留研究分，但不能入场。
- `status=complete` 且没有冲突/资格 blocker 时，`entry_permission_ceiling=null`、`suggested_module_signals=[]`、`compiler_effect=none`；分数绝不生成正向动作或仓位信号。
- 只有 insufficient data、material conflict、地区资格未知/不合格才允许生成 `data_quality/conflict_ledger/account` 的阻断信号；所有信号均为 `tighten_only=true`、`cannot_raise_upstream=true`。
- 地区不合格只阻断新仓并 `holding_directive=HOLD`；不能仅凭资格字段机械退出已有持仓。
- `channel` 只接受精确 `cex_spot` 或 `wallet_dex`；不得把任意 `cex_*` 当现货。CEX identity 不得携带 Wallet-only `underlying_symbol/chain_id/token_contract/provider`；Wallet/DEX identity 必须含 `underlying_symbol/chain_id/token_contract/provider` 且不得携带 CEX 字段。
- payload 含 API key、任意 `OK-ACCESS-*`、secret、passphrase、token、private key、header/signature 容器等字段必须 fail-closed；NFKC 后仍含非 ASCII 字母数字的 schema key 也拒绝，防止 Unicode 同形绕过。非敏感 key 下字符串任意位置嵌入的高置信 prefixed key/JWT/Bearer/AWS key/PEM 私钥值同样拒绝，不能只检查整个字符串 full match。

## OKXExecutionSnapshot 与 Supervision（v2.48）

完整语义见 `okx-research-execution-supervision.md`，机器入口：`scripts/okx_execution_supervisor.py`。

```json
{
  "schema_version": "okx_execution_snapshot.v1",
  "as_of": "RFC3339 with timezone",
  "venue": "okx",
  "channel": "cex_spot | wallet_dex",
  "mode": "public | read_only | demo | live",
  "credential_scope": "none | read_only",
  "execution_permission": "disabled | paper | live_approved",
  "no_order_execution": true,
  "instrument": {
    "inst_id": "XMU-USDT",
    "inst_type": "SPOT",
    "inst_category": "3",
    "mapping_scope": "exact_exchange_instrument_only",
    "mapping_verified": true,
    "region_eligible": true,
    "state": "live",
    "tick_size": 0.01,
    "lot_size": 0.000001,
    "min_size": 0.001
  },
  "evidence_refs": ["EID-*"],
  "connection": {"private_ws_connected": true},
  "feeds": {
    "market": {"status": "live | error | unavailable", "source_ts": "RFC3339", "max_age_seconds": 60, "last_good_at": "RFC3339", "last_good_value": {}},
    "account": {},
    "orders": {"rest_baseline_complete": true}
  },
  "strategy": {"status": "running | paused | stopped | faulted", "heartbeat_at": "RFC3339", "max_heartbeat_age_seconds": 120, "stop_new_entries": false, "allowed_instruments": ["XMU-USDT"]},
  "venue_state": {"positions": [{"instrument_id": "XMU-USDT", "quantity": 1}], "open_orders": [{"order_id": "o-*", "instrument_id": "XMU-USDT", "state": "live", "quantity": 1}], "fills": [{"fill_id": "f-*", "order_id": "o-*", "instrument_id": "XMU-USDT", "side": "buy", "quantity": 1, "price": 100, "fill_ts": "RFC3339"}]},
  "local_state": {"positions": [{"instrument_id": "XMU-USDT", "quantity": 1}], "open_orders": [{"order_id": "o-*", "instrument_id": "XMU-USDT", "state": "live", "quantity": 1}], "fills": [{"fill_id": "f-*", "order_id": "o-*", "instrument_id": "XMU-USDT", "side": "buy", "quantity": 1, "price": 100, "fill_ts": "RFC3339"}]},
  "execution_results": [],
  "risk": {"account_hard_redline": false},
  "policy": {"position_tolerance": 0, "max_spread_bps": 50},
  "entry_score": {"schema_version": "entry_score_compilation.v1", "status": "complete | insufficient_data", "entry_score_100": 72, "factor_coverage": 1, "confidence": "high", "unresolved_conflict": false, "entry_permission_ceiling": null, "factors": {"technical": {"score": 4, "contribution_points_100": 16}, "capital_flow": {"score": 3, "contribution_points_100": 12}, "sentiment": {"score": 3.5, "contribution_points_100": 14}, "fundamentals": {"score": 4, "contribution_points_100": 20}, "macro": {"score": 3.3333333333, "contribution_points_100": 10}}},
  "live_controls": {"approval_verified": true, "ip_allowlist_verified": true, "withdrawal_enabled": false, "credentials_rotated": true}
}
```

监督输出 `okx_execution_supervision.v1` 必须分开：

- `pause_required`：仅在 `demo/live` 表示外部策略必须暂停新仓；`public/read_only` 没有控制面，只能通过 `new_entries_allowed=false` 表达分析层阻断，不得声称需要或已执行暂停；
- `pause_effective`：外部策略已实际处于 stop/paused；
- `new_entries_allowed`：只有 `mode=demo|live`、权限边界合法，且身份、freshness、heartbeat、私有长连接、REST baseline、账实对账、流动性、账户风险与 fixed-weight EntryScore 全部过门才可为 true；EntryScore 必须是完整五因子、coverage=1、confidence=high、贡献重算一致、无冲突且 `entry_permission_ceiling=null`；
- `market_analysis_allowed` 与 `analysis_only`：public/read_only 模式可研究但不能形成账户级执行许可；public 下 `region_eligible=null` 不阻断市场分析，但仍禁止账户级许可；
- `position_reconciliation_status=matched|drift|pending`；
- `order_state_status=known|unknown|partial_failure`；
- `reconciliation.order_detail_mismatches`：同一 `order_id` 的 `instrument_id/state/quantity` 任一不一致即记录并阻断；
- `reconciliation.fill_detail_mismatches`：同一 `fill_id` 的 `order_id/instrument_id/side/quantity/price/fill_ts` 或可选 fee 明细任一不一致即记录并阻断；只比较 ID 不算全量对账；
- `connection.private_ws_connected/rest_baseline_complete` 与经过字段白名单投影的 `entry_score`；
- 健康状态 `module_signals=[]`；只有阻断项才复用 `data_quality/conflict_ledger/account/liquidity/execution_window`，且只能收紧；
- `no_order_execution=true`。

CEX identity 使用 `inst_id/inst_type/inst_category/mapping_scope/state/tick_size/lot_size/min_size`；Wallet/DEX identity 使用 `chain_id/token_contract/provider/underlying_symbol/mapping_scope`。两类字段混用、私有模式地区资格未知、未来或 stale 时间、feed/策略/连接异常、REST baseline 缺失、订单未知、畸形对账行、同 ID 订单或成交明细不一致、账实漂移或 item-level 部分失败时 fail-closed。`position_tolerance` 只接受非负显式策略值且必须严格小于 instrument `lot_size`；大于或等于一个 lot 时策略无效并阻断，不能隐藏最小可交易单位漂移。`last_good_value` 不仅按 feed 字段白名单投影，还按字段类型限定为有限数值、非负计数、RFC3339 时间或短币种码；非法值只记录字段名并阻断，不回显原值。Dashboard 写盘前再次投影为页面实际消费的 canonical 子集，保留足够重验 CEX/Wallet identity 与 EntryScore ceiling/五因子公式的字段，未知字段不得落盘，并强制 `public/read_only/demo/live` 与 `credential_scope/execution_permission/new_entries_allowed/private state/strategy state` 的语义矩阵；`public` 的 private WS 为 `null`、strategy 为 `not_available`。若声明 `new_entries_allowed=true`，面板发布前还必须验证 identity、三类 feed fresh、orders REST baseline、EntryScore complete/全覆盖/high confidence/公式一致/无冲突/无 BLOCK ceiling、对账/策略/连接状态及 live 四项 safety attestation；attestation 只用于发布门，不持久化到页面 JSON。`mode=live` 的四项确证由外部提供；本 Skill 不读取或验证凭据值。
