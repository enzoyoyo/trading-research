# A-Stock Data Source Layer · A 股直连公开源补强

## 结论

`a-stock-data` 不是新的交易系统，也不替代 LongBridge / Decision Compiler。它在本 skill 里的位置是：**A 股公开数据直连补强层**，用于在 LongBridge/AkShare/WindClaw 出现缺口时，快速抓取可复现的 A 股行情、板块归属、资金流和公告证据。

- 来源：<https://github.com/simonlin1212/a-stock-data>
- 本次吸收版本：v3.2.2，commit `9379ab90d0219312b5f4845cd8c97502f40b0806`（2026-06-03）
- License：Apache-2.0
- 本地桥接脚本：`scripts/a_stock_data_bridge.py`

## 采用什么

| 机制 | 采用方式 | 为什么 |
|---|---|---|
| 数据源优先级 | mootdx/腾讯/新浪/巨潮/同花顺等低风控源优先；东财只用独有数据 | 降低 A 股批量查询被东财风控的概率 |
| 腾讯财经字段校准 | `scripts/a_stock_data_bridge.py quote` 读取价格、PE/PB、市值、换手、涨跌停；字段 43=振幅，46=PB | 直连、无需 key、可快速补足估值/交易结构快照 |
| 东财统一限流 | 所有 Eastmoney 请求串行、`EM_MIN_INTERVAL>=1s` + jitter；禁止并发 | 防止批量筛选封 IP，失败写缺口 |
| 东财 slist 概念板块 | `concept` 命令抓行业/概念/地域混合板块、BK码、涨跌幅、龙头股 | 替代已失效的百度 PAE 概念归属 |
| 巨潮 orgId 动态映射 | `announcements` 命令先查 `szse_stock.json`，再 fallback | 避免硬编码 `gssx0{code}` 导致 601xxx 查不到公告 |
| 失效源记录 | 财联社旧 API、百度 PAE 资金流/概念历史失效；东财住宅 IP 间歇风控 | 避免把“接口空”误判成“公司无信息/无公告” |

## 不采用什么

- 不把 `a-stock-data` 作为第二个独立 skill 注册；本 skill 仍是唯一投研入口。
- 不复制其 2000+ 行大文件到 `SKILL.md`，避免路由上下文膨胀。
- 不默认启用 iwencai / PDF 下载 / 大批量研报抓取；需要 key 或会放大风控。
- 不把任何数据源输出直接转成买卖动作；仍必须进入 Evidence Ledger → Mira Gate → Decision Compiler。
- 不新增真实下单或模拟下单能力。

## 运行入口

```bash
# 健康检查：腾讯行情 + 东财概念 + 巨潮 orgId map
python3 scripts/a_stock_data_bridge.py health --json

# A 股快速行情/估值/交易结构快照
python3 scripts/a_stock_data_bridge.py quote 600519.SH 300750.SZ --json

# 个股所属板块/概念/地域混合列表（东财 slist，限流）
python3 scripts/a_stock_data_bridge.py concept 600519.SH --json

# 巨潮公告，动态 orgId，避免 601xxx 查不到公告
python3 scripts/a_stock_data_bridge.py announcements 601318.SH --limit 10 --json

# 东财分钟级资金流（盘中更有意义；非交易时段可能为空）
python3 scripts/a_stock_data_bridge.py fund-flow 000858.SZ --limit 20 --json
```

默认直连中国公开源，不走本机代理；如网络环境要求代理，可加 `--use-proxy`。

## Evidence Ledger 映射

| 桥接命令 | Evidence type | variable | 可靠性上限 | 必须标注 |
|---|---|---|---|---|
| `quote` 腾讯财经 | `quote` / `valuation` | `trading_confirmation` / `valuation_snapshot` | A- | observed_at、字段口径、是否直连成功 |
| `concept` 东财 slist | `a_share_raw` / `sentiment` | `concept_membership` / `sector_rotation` | B+ | 东财混合行业/概念/地域，不精确分类 |
| `announcements` 巨潮 | `filing` | `official_disclosure` | S/A | orgId 来源、公告 URL、公告日期 |
| `fund-flow` 东财 push2 | `fund_flow` | `money_flow_confirmation` | B | 盘中/分钟口径、东财风控状态、是否为空 |

建议 EvidenceItem 示例：

```json
{
  "eid": "AS1",
  "type": "a_share_raw",
  "source": "a-stock-data bridge / Tencent Finance / Eastmoney / CNINFO",
  "timestamp": "2026-06-18T12:00:00Z",
  "raw_fact": "600519.SH quote+concept+announcements snapshot",
  "variable": "valuation_snapshot | concept_membership | official_disclosure | money_flow_confirmation",
  "reliability": 0.75,
  "freshness": 1.0,
  "cross_check": ["LongBridge/AkShare/WindClaw/Web EID"],
  "treatment": "use_normally | source_gap | cross_check_required"
}
```

## A 股数据源优先级（本 skill 内）

1. **LongBridge MCP**：优先拿行情、财报、估值、行业、组合和账户上下文。
2. **a-stock-data bridge**：A 股公开直连补强；尤其适合腾讯估值快照、东财板块/资金流、巨潮公告 orgId 修正。
3. **AkShare**：保留为广覆盖兜底，尤其涨停/炸板/龙虎榜/南北向等已有函数；但遇到 eastmoney 代理/版本问题时优先试直连 bridge。
4. **WindClaw**：机构语料与补证；健康检查通过后使用，不能一源定结论。
5. **Web/官方公告**：官方材料永远可作为最高可信锚点。

## 风控与缺口规则

- 东财请求不得并发；批量查询时设置 `EM_MIN_INTERVAL=1.5` 或更高。
- 东财返回 `HTTP 000`、空、403/429、超时：写 `eastmoney_rate_limit_or_network_gap`，不要解释成基本面事实。
- 腾讯财经字段口径固定：43=振幅，46=PB；任何 PE/PB/市值结论需标注来源为腾讯快照。
- 巨潮公告如果 orgId map 拉取失败，只能使用 fallback，并在 Evidence 里标注 `cninfo_orgid_fallback=true`。
- 桥接层只提供数据，不提高 action level；最终动作由 Decision Compiler 裁决。
