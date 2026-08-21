# WeChat article → A-share inference workflow

Use this when 用户 gives a WeChat/公众号 article and asks “说的是哪些 A 股票 / 图片要完全理解 / 深度分析”.

## Goal
Turn a narrative article plus embedded images into a bounded A-share inference: explicit evidence, inferred tickers, confidence, and watch/decision limits. This is research only; no order execution.

## Workflow
1. **Extract article evidence first**
   - Use `web_extract` for clean text.
   - Open the URL in browser and extract DOM fields (`#js_content`, `#publish_time`, `#js_name`) because WeChat pages may show different publish time / lazy image URLs than static extraction.
   - Save a `task_plan.md` and `findings.md` under a task-specific work directory for complex multi-source analysis.
2. **Resolve WeChat lazy images**
   - `browser_get_images` may only return loaded images.
   - Use browser JS to collect `img.currentSrc || img.src` and `img.getAttribute('data-src')` from `#js_content img`.
   - Analyze each article image URL directly with vision/OCR. Do not rely on article text alone when user explicitly says images must be understood.
3. **Separate evidence layers**
   - `article_explicit`: stocks/terms directly named in text or image OCR.
   - `article_inferred`: metaphor/code-word mapping, e.g. “晶莹剔透” → glass substrate/TGV; “闪闪发光/钻石” → diamond/金刚石散热.
   - `market_cross_check`: web/LongBridge/a-stock bridge quote/news cross-check.
   - `watch_only`: unsupported or early industrialization clues.
4. **For A-share candidate mapping**
   - First list directly named tickers from text/images.
   - Then map sector phrases to a concentrated candidate set, not a scattered 20+ name dump.
   - Prefer 2–4 core + 2–4 watchlist per theme when possible; if the user asks “哪些票” still label confidence rather than implying recommendation.
5. **Participant-flow first**
   - Before any action framing, describe marginal buyers/sellers: e.g. momentum/quant/retail after a vertical monthly move vs profit-taking holders.
   - If the article itself says a prior theme is already “学明白了/疯狗浪/涨幅大”, treat that as a chasing-risk flag.
6. **Data-source discipline**
   - For A-shares, use LongBridge if available; if unavailable or missing BJ names, use `scripts/a_stock_data_bridge.py quote ... --json` and mark `longbridge_gap` / `a_stock_bridge`.
   - If auth is expired, follow the existing data-source auth renewal rule; do not silently skip.
7. **Output shape**
   - Conclusion first: “最可能不是一只票，而是 X/Y themes”.
   - Then tables: explicit evidence, inferred themes, candidate tickers, confidence, risk boundary.
   - Keep action at L0/L1 watch unless there is fresh verifiable fundamental/price evidence and the user asked for a trading decision.

## Pitfalls
- Do not infer tickers from metaphors without labeling the inference path.
- Do not treat a monthly gainers screenshot as a buy list; it often proves the *previous* mainline and chasing risk.
- Do not ignore small screenshots that reference old posts; they may encode the author’s method rather than a ticker.
- Do not let WeChat lazy placeholders (`data:image/svg+xml`) count as image evidence; fetch `data-src`.

## Example mapping from 2026-07 WeChat article case
- “晶莹剔透” → 玻璃基板/TGV: 京东方A、沃格光电、彩虹股份、凯盛科技、戈碧迦.
- “闪闪发光/钻石” → 金刚石散热/培育钻石: 惠丰钻石、黄河旋风、四方达、力量钻石、沃尔德.
- “存储、大光、大PCB，中报确定性” → separate earnings-anchor bucket: storage, optical modules/interconnect, PCB/CCL.
