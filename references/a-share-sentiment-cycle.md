# A-Share Sentiment Cycle · A股情绪周期专项模块

> **按需加载**：仅在用户明确要求 A 股短线、情绪周期、涨跌停生态、连板梯队、市场复盘时使用。不得污染港股、美股、宏观-only 或长线基本面研究。

## 定位

本模块把 A 股特有的涨跌停生态量化为结构化快照，并给出可验证的情绪阶段标签。它是 **A 股专项分析器**，不是通用核心，也不替代 `Decision Compiler`。

与 `a-share-short-term-layer.md` 的分工：

| 文件 | 职责 |
|---|---|
| 本模块 | 全市场量化快照：涨停/炸板/跌停、连板高度、板块扩散、情绪阶段、资金流状态 |
| `a-share-short-term-layer.md` | 执行纪律：右侧买点、仓位、止盈、做T、复盘问题清单 |
| `a-share-post-close-review.md` | 仅盘后/情绪复盘加载：同源三窗口资金、五日涨停生态、阈值敏感性与一字板参与度审查；三窗口累计符号不替代本模块的日级连续流入分类 |

## 触发条件

满足任一即可加载：

- 用户提到 A 股情绪周期、涨跌停、连板、炸板率、龙头梯队、短线复盘
- `method_router.py` 路由到 `A_short`
- 需要写入 A 股短线判断并做次日验证

**禁止触发**：港股/美股个股、ETF 轮动、宏观四象限、长线 Serenity/基本面框架。

## 数据接口

```bash
python3 scripts/a_share_sentiment_cycle.py --trade-date 20260819 --json
python3 scripts/a_share_sentiment_cycle.py --trade-date 20260819 --symbol 600519 --json
python3 scripts/a_share_sentiment_cycle.py --self-test
```

数据源优先级（只读、可降级）：

1. AkShare 涨停池 / 炸板池 / 跌停池（eastmoney 推送，需绕代理）
2. 可选 `--symbol` 时追加同源日级主力净流入历史（不得与分钟快照混算累计）
3. `scripts/data_freshness_guard.py` 校验目标交易日与 as-of 对齐

## 情绪阶段（结构化）

| phase | 含义 | 主要量化线索 |
|---|---|---|
| `ice` | 冰点 | 涨停稀少、高度打不开、昨日溢价弱 |
| `start` | 启动/酝酿 | 涨停与高度从低位抬升 |
| `ferment` | 发酵/主升 | 高度≥3 且涨停扩散 |
| `climax` | 高潮 | 涨停数量与连板高度同时高位 |
| `divergence` | 分歧 | 涨停多但炸板率高 |
| `retreat` | 退潮 | 涨停收缩、跌停抬升 |

阶段标签是 **研究辅助**，不是动作等级；最终动作仍由 `Decision Compiler` 裁决。

## 板块与龙头结构

输出 `sector_ladder.top_sectors[]`：

- 板块涨停数
- 板块最高连板
- 板块内前 3 龙头候选（按高度与成交额排序）

不得把「涨停最多板块」直接写成买入指令。

## 资金状态分类

`fund_flow_state.state` 只基于同源日级 `main_net` 序列：

- `first_inflow`：前期非流入，最新日转正
- `continuous_inflow`：连续 3 日以上为净流入
- `recovery_inflow`：前期流出后最新日转正
- `outflow`：前期流入后最新日转负
- `mixed` / `unknown`：样本不足或方向混杂

**口径隔离**：当日分钟快照不得冒充 5 日/20 日累计。

## 次日回头看（复用通用核心）

不新建第二套记忆系统。A 股短线判断应：

1. 用 `scripts/prediction_ledger.py register` 登记可结算预测，`regime` 写入情绪阶段（如 `A_sentiment:ferment`）
2. 用 `scripts/record_due_results.py` 在到期后结算
3. 用 `scripts/trading_memory.py record-decision/record-result` 保留 append-only 因果链
4. 用 `scripts/calibration_scorecard.py` 按 `regime` 分桶统计，禁止混合样本

原判断一旦写入，后续只能追加验证事件，不得事后修改。

## 数据质量护栏

必须通过 `data_freshness_guard`：

- 目标交易日无效 → 阻断
- as-of 与目标交易日不一致 → 阻断
- 观测时间早于目标交易日 → 阻断
- 周末日期 → 降级并标注

数据不足时：

- 输出 `data_gaps`
- `may_write_formal_conclusion=false`
- 最高 `monitoring_only`，禁止编造涨停数或情绪阶段

## 硬边界

- 不输出「明天买什么」
- 不生成自动交易指令
- 不把 A 股短线逻辑套用到其他市场
- 不替代 `a_stock_data_bridge.py` 个股快照
- 不替代 `market_structure.py` 单票涨跌停/龙虎榜查询

## 相关文件

| 文件 | 关系 |
|---|---|
| `scripts/data_freshness_guard.py` | 通用日期/新鲜度护栏 |
| `scripts/a_share_sentiment_cycle.py` | 本模块执行器 |
| `references/a-share-short-term-layer.md` | A 股短线执行纪律 |
| `references/trading-decision-memory.md` | 次日验证与 append-only 记忆 |
| `references/trading-laws.md` | 情绪周期纪律（定性） |
| `scripts/market_structure.py` | 单票结构，与本模块互补 |
