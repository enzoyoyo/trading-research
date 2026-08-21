# Adaptive Self-Optimization · 每日自检 / 自进化 / 自优化机制

## 目标

让 `trading-research` 每天自动检查是否需要吸收新信息、修正偏差、更新研究模板；但默认**保守更新**，没有必要就输出 `no_necessary_upgrade`，不为了“进化”制造冲突。

核心原则：
1. 证据驱动，不追热点。
2. 先融合，后新增；能映射到现有模块就不新建模块。
3. 每次改动必须通过验证；验证失败自动停止，不留半成品。
4. 不改变 `no_order_execution`、LongBridge 主数据源、Decision Compiler 唯一裁决、Mira Quality Gate、简洁主回复契约。

## Materiality Gate · 何时值得升级

只有满足至少一项，才允许修改 skill：

| 触发 | 可升级内容 |
|---|---|
| 数据源/API/工具发生实际变更，导致现有命令失败或更优路径出现 | data-source playbook / scripts / validator |
| 用户纠正过一次真实投研输出偏差，且属于可复用类型 | gotcha / scenario eval / template |
| 新市场结构或风险机制被多源验证，现有框架无法表达 | reference + Decision Compiler module mapping |
| 高质量开源项目/论文提供可复用 eval、risk gate、data contract | reference-first integration |
| 回归测试发现动作漂移、主回复变啰嗦、X/Grok 线索越权 | templates / validator / scenario regression |
| 监管/交易制度变化影响 A/H/美交易约束 | market-source / risk policy |
| 本 skill 自身 paper 战绩 / 决策记忆出现**已过门槛**的衰减因子或获得 sizing-change 资格（`performance_snapshot.performance_materiality != none`） | decision-compiler module mapping / template / scenario eval（不碰实盘） |
| 输出质量回归发现 posture 漂移（action band 放宽 / 必带 guard 掉失 / veto 丢失） | templates / decision-compiler / scenario regression |
| 校准记分卡 `calibration_materiality != none`：预测胜率 vs 实际出现显著且足量的系统性偏差（过度自信/过度保守） | 方法论复查 / template / scenario eval（只 advisory，最高 medium，不直接改 sizing） |

以下情况直接输出 `no_necessary_upgrade`：
- 只是 star 数变化、项目热度变化、KOL 新观点。
- 新项目只是旧机制换名，没有更强证据或更低维护成本。
- 需要新增大型依赖或自动交易能力，但本 skill 只是研究决策。
- 会与 LongBridge、Decision Compiler、Mira、no_order_execution 冲突。
- 无法验证或无法回滚。

## Daily Loop

每日 cron 执行以下流程：

1. **Preflight**
   - 加载 `trading-research`、`skill-quality-gate`。
   - 运行 `scripts/self_optimization_check.py --json --run-validators`。
   - 如需检查 Grok 状态，运行 `scripts/self_optimization_check.py --json --grok-health`，但不得输出 token。
   - 这一步内部会以 `--dry-run` 探活 `paper_outcome_calibration_feed.py`（`loop_liveness.calibration_liveness`），只读 paired/pending 样本数做健康提示，不写入。**喂数（写模式）本身不归本 Daily Loop 调度**：它的调度归属在 System A —— `~/.hermes/longbridge-paper-trading/scripts/run_paper_learning_cycle.py` 把它列为 `fill_reconcile` 之后的正式步骤（随 cron `LongBridge Demo Paper Learning Cycle` 每个交易时段跑一次）。2026-07-26 审计一度误判它「无任何调度归属」，实际是 `fill_reconcile` 因 LongBridge 429 崩溃导致该步骤当时从未被执行到；`run_paper_learning_cycle.py` 现已做步骤隔离，`fill_reconcile` 失败不再阻断这一步和其余下游步骤（详见该脚本 module docstring）。

2. **Performance Review（自身战绩 → 方法论）**
   - 读 `performance_snapshot`（已并入 `self_optimization_check.py` 输出）：System A 学习包 + System B 决策记忆 `trading_memory.py review` + 校准记分卡（loop B）。
   - `performance_materiality == none`：仅记录，不打补丁。
   - `!= none`：把过门的衰减因子/失败模式列为候选。**硬规则**：性能驱动补丁必须 (a) 有过门信号支撑（学习包 `enough_for_policy_sizing_change` 或决策记忆已过反过拟合门），(b) 同时新增/更新一条 scenario regression case 固化教训，(c) 通过 before/after baseline；任一不满足 → 记为 `watch`，不改。
   - 性能信号仅 advisory：永不直接移动实盘 sizing、永不让结构验证假绿。

2b. **Calibration Scorecard（loop B：预测准不准）**
   - 运行 `python3 scripts/calibration_scorecard.py --source skill --window 200`（只读决策记忆，已并入 `performance_snapshot.calibration`）。
   - 把每笔决策的预测胜率 `factors.estimated_win_rate` 与实际 `results.outcome`（success=1 / failure=0，mixed/neutral 排除）配对，算 **Brier** score、相对 base-rate 的 skill score、可靠性分箱、按 regime/方向/动作桶分层校准。
   - `calibration_materiality` 与全套反过拟合门一致：偏差大（`|gap|>=0.15` high，`[0.08,0.15)` medium）**且**样本足（默认 `min_samples=12`）才算 material；否则 `none`。
   - 校准信号只 advisory：最高把 `performance_materiality` 抬到 medium（提示方法论复查），永不单独抬到 high、永不直接改 sizing。过度自信 → 下手前给胜率打折；过度保守 → 该出手别缩。

2c. **Eval Candidate Generation（loop A：失败 → 护栏候选）**
   - 日常自检遇到 `performance_materiality != none` 时，先运行只读 triage：`python3 scripts/eval_candidate_generator.py --no-stage`。如果输出 `candidates=[]` 且只有 `manual_review`（常见于学习包只提示 “review closed_trade_reviews”），结论是 `watch/no_necessary_upgrade`，不要为了 medium materiality 硬打补丁。
   - 需要正式生成候选时运行 `python3 scripts/eval_candidate_generator.py --window 200`。
   - 把**已过反过拟合门**（默认 `min_count=3` 且 `min_share=0.20`）的复发失败标签翻成 Decision Compiler 可验证的 guard case，并立刻用 `decision_compiler.py` 自验：
     - `verified_guards_current_behavior`：编译器已强制该护栏，可作回归锁定（人工确认后才进 golden set）。
     - `exposes_compiler_gap`：失败暗示的护栏当前编译器**没**强制 = 真方法论漏洞，需人工改 compiler/方法论，绝不自动加测试。
   - 无法映射的失败标签 + 学习包 `skill_upgrade_candidates` → `manual_review`，不静默丢弃；但 `manual_review` 本身不是 patch 许可，必须等人审或等后续运行形成可验证 recurring failure guard。
   - 候选只**暂存**到 `~/.hermes/work/trading-research-autoevolve/eval-candidates/`，**绝不**自动写入 `templates/`——skill 不能拿自己偷偷写进 golden set 的测试给自己打分。

3. **External Refresh**
   - 用 GitHub API/web 搜索检查参考项目族：OpenBB、Qlib/RD-Agent、Freqtrade、Lean、vectorbt、FinRL/FinRL-X、PyPortfolioOpt/pyfolio。
   - 用 Hermes Grok 全网搜索检查是否有“投研框架/数据源/市场结构/监管制度”重大变化；Grok 结果只作发现层，必须抓原始链接。

4. **Conflict-Fusion Review**
   - 新机制必须映射到现有入口：`live_intelligence`、`data_quality`、`quant_robustness`、`portfolio_risk_budget`、`research_readiness`、`decision_compiler`。
   - 禁止新增平行 skill 或第二套动作等级。
   - 若冲突，优先 patch reference 说明边界，而不是改主流程。

5. **Patch Candidate**
   - 先写 findings/progress。
   - 小改用 `skill_manage(action='patch')` 或 `patch`。
   - 大改先新增/更新 `references/*`，再用 SKILL.md 一行引用。
   - 不把临时研究笔记、私有账户数据、付费源原文写入 skill。

6. **Validation Gate**
   - 改动前先跑基线：`python3 scripts/self_optimization_check.py --json --run-validators --skip-network --eval-snapshot-out /tmp/trading-research-baseline.json`
   - 改动后再跑：`python3 scripts/self_optimization_check.py --json --run-validators --skip-network --eval-baseline /tmp/trading-research-baseline.json`
   - 五套 eval（routing / market-router / method-router / scenario-regression / output-quality）任一从 pass 变 fail = regression，必须回滚本次 patch。
   - 输出质量回归（posture 漂移）：改动前 `python3 scripts/output_quality_regression.py --baseline /tmp/oq-baseline.json`，改动后 `python3 scripts/output_quality_regression.py --compare /tmp/oq-baseline.json`；任一 golden case posture 退化（action band 放宽 / 必带 guard 掉失 / veto 丢失）= regression，必须回滚。
   - 通过率持平但 materiality 不是 high，则默认不保留；只有 evidence quality、risk control、decision clarity 或真实用户纠正明显改善，才算值得升级。
   - `skill_view(name='trading-research')`
   - `skill_view` 新增/修改 reference
   - `python3 scripts/validate_skill.py`
   - `python "${HOME}/.hermes/skills/devops/skill-quality-gate/scripts/audit_skills.py" --root "${HOME}/.hermes/skills/trading-research" --format markdown`
   - 若验证/compile 生成 `__pycache__/` 或 `*.pyc`，验证后清理并复查 skill tree；这属于维护清洁，不是语义升级。

7. **Decision**
   - 验证通过且 materiality 成立：保留改动，输出改了什么、为什么、如何验证。
   - 验证失败：回滚本次改动或标注阻塞，不声称完成。
   - 无必要：输出 `no_necessary_upgrade`，说明检查过哪些来源与为什么不改。
   - **每日必做**：写完 findings 后，调用 `python3 scripts/self_optimization_ledger.py append --from <self_optimization_check JSON> --status <upgraded|no_necessary_upgrade|blocked> --materiality <high|medium|low|none>` 追加一行结构化台账（append-only，含 performance_materiality / eval 通过数 / 各来源状态）。静默失败巡检：`python3 scripts/self_optimization_ledger.py query --health`。

8. **Daily Journal**
   - 收尾跑 `python3 scripts/daily_journal.py --compose --days 3 --publish`：只读 System A + 校准桶，幂等重组最近 3 个交易日的 `~/.hermes/trading-journal/daily/*.md`，upsert `index.jsonl`、重生成 `README.md` 记分板，并在 journal 目录本地 `git commit`（不 push）。
   - 这是文档层挂接，不新增/不修改 cron 定义。

## Weekly Loop · Weekly Learning Digest（loop C：人话学习摘要）

每日循环吐的全是机器 JSON，没人天天读。每周一次（由 `self_optimization_check.py` 在周一或 digest 超过 7 天时自动触发，不新增 cron）把台账 + 校准 + 决策记忆复盘 + 暂存候选聚合成一份 用户 能直接读的中文摘要——这周 skill 学到了什么、是不是在自欺、还有什么要人工拍板。

- 运行 `python3 scripts/learning_digest.py --out ~/.hermes/work/trading-research-autoevolve/digests/weekly-<date>.md`（只读、不下单、不改 skill）。
- 摘要内容：一句话结论（校准是否诚实）→ 战绩（胜率/近端加权胜率/平均收益）→ 最常踩的坑（失败标签翻成人话）→ 拖后腿的票 → 待你拍板（loop A 的护栏候选 / compiler 漏洞 / 待人工映射数）→ 系统健康（台账 `query --health` 的静默失败信号）。
- 摘要里的所有候选都只是暂存，必须经 用户 确认 + 过 materiality 与 before/after eval 门才会真正进 skill。
- 同一次 digest 时机顺带跑 `python3 scripts/data_retention.py --dry-run --json`（只读统计，不落地任何文件改动）；待清理量超过 5000 个文件或 200MB 时在 digest 里提示，`--apply` 留给 用户 或主会话确认后再执行，本循环自身不调用 `--apply`。

## 输出格式

```markdown
trading-research daily self-optimization
status: upgraded | no_necessary_upgrade | blocked
materiality: high | medium | low | none
performance_materiality: high | medium | none
checked:
- local_validators: pass/fail
- grok_oauth: available/unavailable/skipped
- github_reference_projects: refreshed/partial/skipped
- performance_review: none/medium/high
- output_quality_regression: pass/fail/skipped
- user_corrections: none/found
changes:
- file: ...
  reason: ...
validation:
- command: ...
  result: ...
ledger: appended | skipped
conflicts_avoided:
- ...
next_watch:
- ...
```

## 决策约束

- 自优化任务不能自动创建真实交易、下单、券商 API 写操作。
- 自优化任务不能递归创建新的 cron job。
- 自优化任务不能因为单一 Grok/X/社媒线索修改交易框架。
- 如果新机制只会让主回复更长、更难用，默认拒绝。
- 如果新机制只能提高理论优雅度，不能提高证据质量、风险控制或用户决策清晰度，默认拒绝。


## Paper Calibration Bucket

System A paper-trading outcomes can be translated into an isolated calibration bucket via `scripts/paper_outcome_calibration_feed.py`. Boundary:

- Read-only: reads `~/.hermes/longbridge-paper-trading/journal/paper_outcomes.jsonl`, `paper_orders.jsonl`, plus read-only proposal/decision packet JSON for explicit prediction fields; never modifies System A.
- Separate bucket: samples are stored in `calibration_samples_paper` with `source='paper'`, not mixed into the skill decision/result chain.
- No invented probabilities: outcomes without an explicit predicted-probability field are skipped as `no_prediction_field`; open predicted trades are reported as `open_not_closed_yet` until a closed outcome exists.
- Reference only: `scripts/calibration_scorecard.py --source paper` emits Brier/reliability reads with `materiality_eligible=false`; paper samples never trigger sizing, position caps, or `calibration_materiality`.
- Skill bucket remains authoritative for method materiality: use `--source skill` for self-calibration gates.


### Pending Paper Predictions

`paper_outcome_calibration_feed.py` also records explicit predicted open proposals into `calibration_pending_paper_predictions`. These rows are not calibration samples; they are an audit queue showing which paper predictions have probabilities but are still waiting for a closed outcome. `calibration_scorecard.py --source paper` reports `paper_pending_predictions` so a zero-sample paper bucket can distinguish “no predictions exist” from “predictions exist but are not closed yet”.
