# Factor Validation Strict Gate · 严格因子验证门

## 目的

`open-source-quant-research-patterns.md` 的 Quant Robustness Gate 只查 walk-forward / 成本 / 容量 / 回撤 / no-lookahead，没有回答一个更基础的问题：这个因子的 IC 是不是共享横截面 beta（市值/市场）伪装出来的？本文档补这一层——任何因子/信号的 IC 类证据，必须先过**同宇宙随机对照零假设**，再谈稳健性检查。

来源：`HKUDS/Vibe-Trading`（`agent/src/factors/bench_runner_strict.py`），commit `9387c605cae45f5b05136c31bf1e18d60c63ee22`，MIT License，方法论级吸收，未复制因子代码或运行时依赖；详见 `source-map.md`。

## 1. 同宇宙随机对照零假设

任何因子/信号的 IC（Information Coefficient）类证据，必须与「同一宇宙、同一日期截面内随机置换」（cross-sectional shuffle within rows，不是把某个有限值原位钉住）产生的 null 因子对比。只对零基准检验（裸 IC>0.02、t>2）不构成 alpha 证据——共享横截面 beta（市值/市场）会让裸 IC 稳定通过随机对照之外的任何单变量门槛。

产出字段：

```yaml
factor_validation:
  random_ic_mean: 0.0        # 同宇宙随机置换因子的平均 IC，作为零基准
  alpha_t: 0.0                # 真实因子 IC 相对随机对照分布的 t 值，不是相对 0 的 t 值
```

裸 IC 通过但未声明 `random_ic_mean` / `alpha_t`，视为未过零假设检验，不构成 alpha 证据。

## 2. 多重检验校正

一次扫描多个因子/参数时，必须引用 Harvey-Liu-Zhu (2016) 的结论：单因子 t≥2 在 factor zoo 语境下失效，门槛应升至 t≈3.5 或等效的随机对照检验。

声明字段：

```yaml
factor_validation:
  n_factors_scanned: 0        # 本次扫描涉及的因子/参数组合数
```

未声明扫描规模的批量因子结论，`readiness_level` 封顶 `research_hypothesis`（对应 Mira Quality Gate 的 draft/not_actionable 档）。

## 3. OOS train/test 分割

任何因子结论必须给出 train/test（或 walk-forward）分割结果。train 通过、test 失败 = `train_only`，**这不是部分成功，是过拟合证据**，不能降级为「弱一点的 alpha」。

## 4. 四态分类税则

替代模糊的「有效/无效」二分：

| 状态 | 定义 | 允许用途 |
|---|---|---|
| `confirmed_alive` | 全样本 + OOS 都跑赢随机对照 | 可作为 `module_signal` 输入参与 Decision Compiler 裁决 |
| `train_only` | 仅 train 集跑赢随机对照，test 集未跑赢 | 最高 hypothesis/watch；过拟合证据，不得升级 |
| `reversed_strict` | 显著负 `alpha_t`（跑赢随机对照但方向反转） | 最高 hypothesis/watch；先查数据是否有错，再考虑反向信号解释 |
| `noise` | 与随机对照统计不可区分 | 最高 hypothesis/watch；不得作为排序或仓位输入 |

只有 `confirmed_alive` 允许作为 `module_signal` 输入喂给 Decision Compiler；`train_only` / `reversed_strict` / `noise` 一律最高 hypothesis/watch，不得作为加分信号。

## 5. 回测失败诊断分类法

跑回测出坏结果时，先分类再动手改代码/改参数：

| 症状 | 分类 | 处理方向 |
|---|---|---|
| 零交易 | 信号逻辑过严 | 检查信号阈值/过滤条件，不是换因子 |
| 首笔交易过晚 | lookback 窗口或 `dropna` 过度 | 检查数据对齐与窗口长度 |
| 资金利用率 <50% | sizing 逻辑问题 | 检查仓位分配规则，不是策略无效 |
| 期末持仓未平 | 退出逻辑未覆盖尾段 | 检查退出条件是否覆盖回测窗口边界 |
| provider 侧错误（rate limit / no data / 额度耗尽） | 数据源问题，非代码逻辑问题 | 换源或等待，不改代码掩盖 |

provider 侧错误不改代码；把限流/缺数据误判为策略失效并调整参数，是典型的伪优化。

## 6. Decision Compiler 映射（tighten-only）

`quant_robustness` 模块在 `decision-compiler.md` 的映射表新增 v2.33 行，封顶值写入该文件的 Cap & Tighten-Only Registry（本文档不重复维护数值，只描述规则方向）：

- 缺 `random_ic_mean` / `alpha_t` 或缺 `n_factors_scanned` 的因子类结论 → `readiness_level` 封顶 `research_hypothesis`，`position_multiplier=0.0`。
- `train_only` / `noise` / `reversed_strict` 不得作为加分信号，只能进入研究假设账本。
- 只收紧，不放宽：本门槛不改变已通过既有 Quant Robustness Gate（walk-forward/成本/容量/回撤/no-lookahead）结论的动作上限，只在其基础上叠加零假设检验要求。

## 与既有链路的融合规则

- 本门槛是 Quant Robustness Gate 的前置补强，不是替代——两者都必须过。
- `confirmed_alive` 状态本身不豁免 walk-forward/成本/容量/回撤/no-lookahead 检查，仍需通过 `open-source-quant-research-patterns.md` 的既有红线。
- 假设从 `train_only`/`noise` 候选进入持续跟踪时，同步登记到假设注册表（见 `references/hypothesis-lifecycle.md`），不在报告死亡后失踪。
