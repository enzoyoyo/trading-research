# Financial-API · 同花顺 A 股只读数据层

本层用同花顺官方结构化数据补充现有 A 股研究链。调用结果进入 `references/data-contracts.md` 的 Evidence/DataGap，仍由 Mira 和唯一 Decision Compiler 裁决；不生成 ModuleSignal，不新增下单入口、调仓规则或 cron。

实现为 `scripts/financial_api_bridge.py`，离线契约与安全测试为 `scripts/test_financial_api_bridge.py`。不安装第二个投研 Skill，不导入上游 Agent 指令。

## 官方来源与核验

2026-09-05 已回抓 [HiThink-Tech/Financial-API 官方仓库](https://github.com/HiThink-Tech/Financial-API)及 [REST 通用契约](https://github.com/HiThink-Tech/Financial-API/blob/765513c2616030803ad80915ed65b205f425a942/docs/api/README.md)，固定提交 `765513c2616030803ad80915ed65b205f425a942`，MIT。采用公开接口契约，适配器为本地实现。接口字段以官方契约为准。

- 官方主机：`https://fuyao.aicubes.cn`；认证头：`X-api-key`。
- 成功必须同时满足 HTTP 200、整数 `code=0`、符合端点约定的 `data.item` 数组。
- 数据信封包含 `code/message/request_id/data`；错误的 `data=null` 不是成功空结果。
- 仅支持固定白名单 GET，不接受自定义主机、URL 或端点，不跟随 HTTP 重定向，不读取交易凭据。
- 原始供应商 `message`、异常文本和请求头不进入输出，避免错误回显凭据。成功正文若回显当前 key，也进行脱敏。

## 命令与边界

| 命令 | 官方端点及契约 | 本地范围 |
|---|---|---|
| `search` | [`/api/meta/tickers/search`](https://github.com/HiThink-Tech/Financial-API/blob/765513c2616030803ad80915ed65b205f425a942/docs/api/endpoints-meta.md) | A 股身份候选，最多 50 条；不自行选首条 |
| `quote` | [`/api/a-share/prices/snapshot`](https://github.com/HiThink-Tech/Financial-API/blob/765513c2616030803ad80915ed65b205f425a942/docs/api/endpoints-prices.md) | 1–100 个显式完整股票代码，不拉全市场 |
| `history` | 同上 `/api/a-share/prices/historical` | 单只、日线 `1d`、窗口不超过 10 年，显式复权方式和 offset |
| `valuation` | [`/api/a-share/valuations/snapshot`](https://github.com/HiThink-Tech/Financial-API/blob/765513c2616030803ad80915ed65b205f425a942/docs/api/endpoints-valuations.md) | 最新 PE TTM/MRQ、PB MRQ、PS/PCF TTM；无历史估值 |
| `income/balance/cashflow` | [`/api/a-share/financials/*-statements`](https://github.com/HiThink-Tech/Financial-API/blob/765513c2616030803ad80915ed65b205f425a942/docs/api/endpoints-financials.md) | 单只、最近 1–20 个年/季报告期；保留报告期末和报告日期 |
| `calendar` | [`/api/a-share/calendar/trading-days`](https://github.com/HiThink-Tech/Financial-API/blob/765513c2616030803ad80915ed65b205f425a942/docs/api/endpoints-calendar.md) | 官方固定近一年、截至今天的交易日；不是未来日历 |

本版不实现指数、板块、基金、集合竞价、龙虎榜、涨停池和全市场下载等其他官方能力。这些能力只能在读取对应正式契约并增加适配/测试后接入。官方主 README 明确排除分钟 K、tick、海外行情、宏观、新闻公告原文和研报；本数据源不承担美股行情或 A 股 tick 微观结构输入。正式个股历史接口契约当前仅 `1d`，不能依据其他介绍页的日/周/月文字擅自发送周/月参数。

## 凭据与运行

此适配器唯一读取的环境变量是 `FINANCIAL_API_KEY`。由用户级秘密存储或运行器注入，不写到 Skill、示例、笔记、进程参数或 Git。上游推荐变量名 `HITHINK_FINANCE_API_KEY` 与此适配器的本地变量不同；调用方应显式映射，不静默扫描多个凭据文件。

调用方通过安全存储注入环境后，可运行 `python3 scripts/a_stock_data_bridge.py quote 600519.SH --source financial_api --json`。默认腾讯路径不变；无同花顺 key 时显式 auth_missing，不尝试假鉴权。

```bash
# 环境变量已由安全存储注入后运行；以下命令没有密钥。
python3 scripts/financial_api_bridge.py search 600519
python3 scripts/financial_api_bridge.py quote 600519.SH
python3 scripts/financial_api_bridge.py valuation 600519.SH 000001.SZ
python3 scripts/financial_api_bridge.py income 600519.SH --period quarterly --limit 4
python3 scripts/financial_api_bridge.py calendar

# 大结果写到调用任务的工作目录；--out 必须放在子命令前。
python3 scripts/financial_api_bridge.py --out work/ths-history.json history 600519.SH \
  --start 2026-08-01 --end 2026-09-04 --adjust none
python3 -m unittest scripts/test_financial_api_bridge.py -v
```

输入必须为已确认的 `600519.SH` 等完整身份，纯 6 位代码和名称先 `search`，多个有效候选不能随意拼交易所后缀。CLI 日期按 Asia/Shanghai 当日首尾转换，避免机器时区造成错日。`history` 默认 `adjust=none`，回测若选 `forward/backward` 必须保留复权参数和公司行动约束。

## 时间、数据缺口与证据映射

输出 `financial_api_snapshot.v1`，包含 `observed_at/as_of/data/items/data_gaps/request_id/response_sha256/source_url/request_parameters`。`data` 保留供应商字段；quote 的 `items` 映射成既有 A 股桥接使用的 `canonical_symbol/price/prev_close/open/high/low/change_amt/change_pct/volume/turnover`，并附 `field_map`。成交量单位为股、成交额为 CNY，涨跌幅 1.74 表示 1.74%，不除以 100 冒充同单位值。

`ok=true` 只表示取到了记录，退出码 0 也只证明本次请求取得记录；`partial` 仍可能有关键缺口。不能据此判定“鉴权、行情、可行动性全部通过”。所有包固定 `no_order_execution=true`、`may_write_formal_conclusion=false`、`suggested_module_signals=[]`。消费方把证据编入完整主流程后再单独裁决。

| 情况 | 处理 |
|---|---|
| 指定 thscodes 的行情快照 | 官方 `data.timestamp=null`，行也无报价时间。保留 `as_of=null`、`freshness=missing`，不能用抓取时间冒充行情时间；需与带报价时间的独立行情交叉核验 |
| 估值数据时间 | 仅代表本批指标中最新有效上游时间，不证明每只/每项同步；固定 `per_item_timestamp_verified=false`，负值和 null 原样保留 |
| 历史日线 | 标注 `coverage=page_only`、`complete_series_verified=false`。需另行验证分页、交易日覆盖、停牌/公司行动与无未来数据后才能做完整回测 |
| 财报 | 报告期末不等于披露日；缺失/未来 `report_date_ms` 写缺口。供应商返回的当前历史不证明历史修订版本在当时可得，不能自动变成 point-in-time 样本 |
| timestamp 缺失、秒/毫秒混淆、未来、过期 | 标为 `missing/future/stale`，抓取成功不能覆盖；阈值为保守经过时间阈值，不宣称交易所 session 已核验 |
| 请求多标的但漏行 | 在 coverage 中列出 missing_symbols，不给漏行补价格 0；多出/重复身份直接拒绝响应 |
| code 2001/2003 或 HTTP 401/403 | `auth_missing/auth_invalid`；保留缺口，调用方按主 Skill 鉴权规则处理 |
| code 3002、4001，HTTP 429，空数据、网络错误 | 分别 `not_ready/rate_limited/missing/network_error`；不转成“无事件/无资金/零价格” |

每次调用只请求一次，不在适配器内部重试；外层受主 Skill 的“同一源连续两次失败停止”约束，不嵌套重试。响应体上限 5 MiB，超限记 `oversized`。不自动保留或显示旧快照；需要 last-good 的调用方应同时存原 as_of 和 stale 状态。

EvidenceItem 的 `type/variable`：quote → `quote/trading_confirmation`；history → `kline/trend_structure`；valuation → `a_share_raw/valuation_snapshot`；财报 → `financial/financial_validation`；日历和代码表只作为数据质量与身份辅助。`provider_family=ths_financial_api`，`source_family=tonghuashun_aggregated_data`；REST/MCP/CLI 都是同一个供应商，不能算三票。财报是供应商整理的披露事实，若与交易所/巨潮命中同一报告，不新增独立原始事实源。不要把 API 能力介绍或返回成功单独登记为 verified_fact。

`response_sha256` 用于校验本次返回字节；完整分析应保存输出包，外部直接引文仍按 `references/source-grounded-research-provenance.md` 生成独立原文哈希和锚点，不能把本桥接 hash 当作全文引用证明。

## 验收状态说明

脚本测试使用明确标为 synthetic fixture 的内存响应，不是真行情、真实胜率或实盘结果。发布时必须另列“契约测试通过”和“用户 key 实测结果”；无凭据实测不能写“新数据源已打通”。真实 smoke test 建议先单代码 `search`，再 `quote` 或 `valuation`，记录业务 code、request_id、时间、行数和 DataGap；不得输出 key。本文件不把某次 smoke test 的状态固化成永久可用声明。
