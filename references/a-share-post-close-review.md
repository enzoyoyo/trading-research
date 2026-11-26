# A 股盘后复盘：确定性取数与口径审查

> **仅 A 股 + 市场盘后复盘 / 情绪复盘按需加载。** 这是只读研究分支，不是新决策引擎；港股、美股、OKX、宏观-only、A 股长线基本面及单票买卖不加载。混合市场请求拆出 A 股复盘部分，不能将其他部分输入此分支。


## 入口与分工

- 原 `references/a-share-sentiment-cycle.md` / `scripts/a_share_sentiment_cycle.py` 保持情绪阶段和**日级连续段**逻辑不变。
- 本分支新增 `scripts/a_share_post_close_review.py`：本地 JSON → 资金三窗口符号、5 日涨停生态、阈值敏感性、一字板参与度提醒及数据缺口。只有 `market="A"` 且 `intent="post_close_review"|"sentiment_review"` 生效；其他输入 `not_applicable`。
- 主 Agent 先按既有数据源手册取得原始数据，在任务目录生成输入；不能要求用户手填 JSON。脚本**不联网、不自动装依赖、不建 cron、不写数据库、不生成订单**。stdout 是可保存的审查工件，既有 provider 和全市场路由不改。
- `data_contract_complete=true` 仅代表所声明输入的结构/计算合同完整，不证明来源正确、交易日历权威、全市场覆盖或报告可交易。恒定 `module_signals=[]`、`may_write_formal_conclusion=false`、`may_raise_action_or_position=false`、`no_order_execution=true`；正式结论仍走原 Evidence/Mira/Compiler。

## 执行流程

### 1. 固定复盘问题和时间

确定目标市场、交易日、所复盘会话、实际源日期、采集时钟。周末/节假日回看最近实际交易日，不能把服务器今天当行情日期。交易日来自当前交易所日历；按**交易所 + 日期 + 会话**确认收盘时钟 `session_end_at`，不把模板的 15:00 当全 A 股静态制度。盘中只出预览，收盘后重新取数；历史日期回看须明示，不冒充当日可用。

快照至少留 `source/source_ref/data_date/taxonomy/universe_id/unit`。源日期落后即 `stale`，重新计算不能洗新；未知/失败不等于零。只剩行情数量而缺逐股名单时，能显示原值但不能伪造连板、板块生态或昨日溢价的统计基础。

### 2. 一次采集、本地归约、按能力降级

1. 沿用 `references/data-source-playbook.md` 与 A 股来源层。先发现运行时工具及字段能力，核实接口可用性、返回限制与批量容量；可选数据源只在当前只读能力已核验时启用，不替换既有主源。
2. 同一 `源+接口+参数+数据日期+会话阶段` 采集一次，原始 JSON 留任务目录；排序、单位换算、三周期表、完整性由脚本做。一次调用已包含涨幅与三周期资金，就本地派生，不按排序重复抓整榜。强制刷新产生新 revision，不覆盖旧证据。
3. 批量请求按供应商规则和实际失败码控制，增量只取缺失 code/date；覆盖率按身份集合校验，不能只看总行数或全库 `max(date)`。源只成功一部分要明确缺口；限流、配额、认证和产品不支持分开，不把所有 429 归因为平台额度。
4. 某类 K 线失败只降级该能力，不连带弃用健康资金接口。备用源按 `source/taxonomy/universe/date/revision/adjustment` 分库存放；恢复主源重新计算受影响段落，旧版本留档但不混进当前汇总。禁止删除旧证据以“清理口径”。
5. 不借其他服务的认证/context header；不读、复制或传播密钥。缓存加速只保留在任务/研究运行目录，不把动态数据写进 skill。
6. 若需协作，先共享固定快照，再分 A（大盘/前次计划对照）、B（板块/梯队/资金）；主 Agent 或 C 在依赖就绪后撰写。规模、预算按任务估算，不固定协作人数或上下文预算。子 Agent 回报主 Agent，不互相辩论；多模型一致不等于独立市场证据。简单任务直接串行。

### 3. 确定性计算与口径门

| 项目 | 本地合同 | 不得做 |
|---|---|---|
| 单位 | `CNY` 元 ×1e-8、`CNY_10K` 万元 ×1e-4、`CNY_100M` 亿元原值；计算保全精度，展示再舍入 | 给数值贴亿元标签却不换算；用所谓 ±600 亿“正常区间”自动缩放 |
| 三周期 | 每行 1d/5d/20d 均是同源同范围的有限数字，null/bool/NaN/缺行/重复 ID 拦截 | 缺值补 0；今日值充当累计；只抄 TOP 行其余空着 |
| 覆盖与总额 | `expected_sector_ids` 来自当前名册；集合同等、无重复且成分不重叠时才给 `scope_total_1d_yi` | 固定 124 家；把行业+概念重复相加；把声明范围总额叫全市场真实流入 |
| 涨停生态 | 交易日历中最近 5 个交易日、同一源/定义/行业分类/股票范围，逐日唯一股票 ID；分别输出 stock-days 与 distinct-stocks | “5 次涨停=5 只个股”；空响应/请求失败算零；全日触板当收盘封板 |
| 板块映射 | 明确 taxonomy 和可追溯 ID 映射；不能以简称碰巧匹配当证据 | 最长前缀猜行业；将西式/同花顺/申万行业视为一套 |
| 情绪阈值 | 输入当次研究阈值，检查 `margin=abs(value-threshold)`；同定义/日期/单位不同源的最大绝对差为 `source_spread` | 把例子的 60 家、0.8、1% 固化成市场真理 |
| 最高板 | 连板高度与换手、一字板、成交额、开板/封单信息并列；脚本只提醒 `height_not_participation` | 高度单独证明承接增强；硬编码换手<1%自动剔除/加仓 |

阈值对照中出现过线翻转，或 `margin<=source_spread`，输出 `boundary_sensitive`；无备源是 `unassessed`，定义不兼容/同源重复是 `not_comparable`。`stable_on_observed_sources` 只表示所提供的可比读数未翻档，不是情绪整体稳健。上游两个品牌共用同一底层数据应先归一 source family，不能伪装独立性；不同统计口径的读数应列为冲突，不能未经定义核验就比较。

**资金窗口标签与原有日级分类严格隔离：**

| 同源窗口符号 | `window_sign_state` | 表述 |
|---|---|---|
| 1d<0 | `outflow` | 当日净流出 |
| 1d=0 | `neutral` | 当日净额为零 |
| 1d>0，5d<=0 | `one_day_pulse` | 单日转正/脉冲待确认，不声称历史首次 |
| 1d>0，5d>0，20d<=0 | `rebound_5d` | 短窗为正、长窗仍非正 |
| 1d>0，5d>0，20d>0 | `positive_5d_20d` | 三周期全正，**不代表逐日连续流入** |
| 任一关键口径不完整 | `unknown` | 数据不足 |

### 4. 报告与次日闭环

报告使用 `templates/universal-equity-report.md` 的简洁主回复；完整 A 股复盘附录按需覆盖：时间/范围/缺口 → 大盘与近5日生态 → 前次计划逐项验证 → 板块/梯队和参与度 → 同源三窗口资金 → 右侧研究候选 → 共振/背离及冲突 → 下一次验证条件。不是强制照搬九个长章节。

- 右侧候选每项列 `definition/formula/input/evidence/as_of/state`；条件定义、输入或有效性缺失为 unknown，不用补叙事凑条件通过。本分支不提供自动低位右侧筛选器。概念/行业分开研究，候选只是 watch list，不是交易信号。
- 前次计划按**准确决策 ID、复盘交易日、生成 revision**查询，逐条记录触发/未触发/未能判定和证据；没有上一份就明写不存在。不得把当日验证写回前日预测，或把“前一日之前、不含当日”的历史接口当当日入库回读。
- 同一条件的结论、表格、限定和缺口保持一致：一处撤回就修订全部引用，不能正文确认而脚注称未确认；更换数据源后整份报告重算，不只换资金表。
- 源差异只陈述事实和工具/数值，原因需逐股名单/公式/外部证据；不凭 ST/次新/北交所常识给差异编原因。自报效果、多个 Agent 同意均非独立证据。
- 沿用 Decision Memory/Prediction Ledger。方向结论、情绪档位、候选观察和正式行动分账；旧候选池不能回答今天能否买，须刷新并经原 Compiler。缺概率不捏造，资金窗标签不制造仓位上限。
- 校准必须保留 false-positive/false-negative、样本选择、成本和样本外切分，不能仅按一个月触发应验率自动改阈值。本分支不修改全局阈值、记忆库、provider 或自动化。

## 输入/运行合同

填写起点：`templates/a-share-post-close-review-input.json`。其全部市场值为**合成 fixture，不是 2026-09-25 实盘**。真实任务先替换为独立取数后的 `data_kind="source_capture"`；不能仅改标签就当真实证据。

必填：schema_version、market、intent、trade_date、observed_at（带时区）、session_phase、session_end_at（对应会话结束）、calendar（source_ref + 覆盖最近五交易日的 trading_days）、flow_snapshot、limit_up_history。可选 threshold_checks/leader_observations 模板提供完整例子；不需要就留空，不能编造。

```bash
python3 scripts/a_share_post_close_review.py --input templates/a-share-post-close-review-input.json --pretty
python3 -m unittest scripts/test_a_share_post_close_review.py -v
```

真实输入也用同一 `--input` 参数和任务目录内的实际路径。输出绑定 `input_sha256/calculator_sha256`；0=输入合同完整，1=partial，2=输入不可解析，3=不适用。clock/schema 失败压低分项状态，不能泄漏看似有效的窗口标签。数据缺失可出透明诊断，不算全市场正式复盘通过。CLI不自动验证源 URL/文件内容、交易所日历真伪或是否全量返回；这些仍由证据采集与 provenance gate 完成。

## 路由与回归验收

正例：A股今天盘后复盘、指定交易日的A股涨停梯队复盘、A股三周期资金与情绪复盘。邻近负例：港股复盘、美股盘后、OKX情绪。禁止自动加载：A股长线基本面、单票买卖、宏观-only、混合市场未拆分。脚本负例、空值/混源/单位/重复/日期失败、阈值贴线、重复股票日、一字板和真实 CLI 路径见 `scripts/test_a_share_post_close_review.py`。既有全量 `validate_skill.py --all` 自动发现该测试，不需要改全局门。
