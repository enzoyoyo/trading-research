# Market Post Research

## Goal

Turn a market author's post, Substack/newsletter, X thread, screenshot, or trade recap into an auditable Chinese trading-research report. The post is treated as a **hypothesis source**, not truth. Every important claim must be separated into:

1. what the author claims;
2. what text was actually accessible;
3. what independent data verifies;
4. what remains a gap or contradiction.

When the post contains tradable instruments, macro, options/Gamma, crowding, or regime calls, also follow the main SKILL.md fixed flow and its Decision Compiler.

## Trigger examples

- "deeply research this post with Chinese"
- "分析这篇 Substack / newsletter / X thread 的交易观点"
- "帮我核验这个交易复盘有没有道理"
- "把这篇市场文章转成中文投研报告"

## Standard workflow

1. **Access and extraction**
   - Try structured extraction first.
   - If incomplete, use a real browser and extract article text from the page DOM.
   - If the article is paywalled or only a preview is accessible, explicitly label `paywall_gap`; never infer hidden sections.
   - If a "free claim" button exists, click only when it does not require payment or credential sharing. If it does not unlock, stop and mark the gap.

2. **Author-claim ledger**
   - Convert every material claim into a checkable item, and classify it by type: fact, opinion, forecast, price anchor, catalyst, technical trigger, or risk control.
   - Preserve exact-number claims separately from directional claims.
   - Example: "hike odds rose" may verify while "63%" remains exact-number unverified.

3. **Independent verification**
   - Map each claim to required evidence: company filing, exchange/regulator material, LongBridge quote/fundamentals, option data, macro data, or X/frontline source.
   - Prices/K-lines: LongBridge CLI/MCP/SDK; label the data tier.
   - Company numbers: official IR, SEC, exchange filings.
   - Macro: BLS, FRED, EIA, CME/CBOE, central bank/official releases.
   - Options/structure: `dispersion_crowding.py`, `risk_regime_snapshot.py`, `options_gamma.py`.
   - X/Grok: only a C-tier `frontline_clue`; never as sole proof or as a reason to raise position.

4. **Conflict handling**
   - Classify each major claim as: `verified`, `partially_verified`, `direction_verified_exact_unverified`, `not_current`, `unverified`, or `contradicted`.
   - Mark unverifiable post content as `weak_signal` or `modeled_scenario` where relevant.
   - For time-sensitive claims, distinguish "was true during the author's reference window" from "is true at verification time."

5. **Decision compilation**
   - Do not stop at summary. Produce action boundaries: short-term, swing, medium-term, invalidation, repair signal.
   - Run Decision Compiler only after unresolved conflicts and missing triggers are explicit.
   - If a tradable stance is produced, run Decision Memory preflight and record the AI decision.

## Minimum report shape / output contract

- Start with the practical decision, not a post summary — 中文一行结论。
- Source access status: full / preview / paywall_gap.
- A compact claim table: `claim / source / verification / decision impact` (author claim map + evidence ledger with source tier).
- Conflict / 反证 ledger.
- Risk regime and Gamma/structure snapshot if relevant.
- Decision Compiler: short-term / swing / medium-term.
- Repair signals and invalidation triggers.
- Decision Memory status when applicable.
- Do not expose paid-post text beyond what is necessary for private analysis.

## Pitfalls

- Do not treat a paid-post preview as the full post.
- Do not copy the author's exact numbers unless independently verified or clearly labeled as the author's claim.
- Do not treat a high-conviction author as evidence.
- Do not turn screenshots into verified facts without source recovery.
- Do not let a correct trade recap become an automatic forward trade signal. After a successful flush, re-check whether the structure has already repaired or remains unstable.
- Do not let a post's target price override risk regime, gamma, account risk, or data gaps.
- For HK or non-US symbols from LongBridge, timestamps may be UTC session timestamps; label date mapping carefully instead of assuming the displayed date equals local exchange date.
