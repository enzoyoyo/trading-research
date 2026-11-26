# Gamma 模型语义与兼容边界

`options_gamma.py` 的 Gamma Flip 方法为 `hypothetical_spot_bs_gamma_v1`。

## 计算口径

- 只使用选定到期窗口/切片内的正 OI 合约。OI 不包含交易双方谁是 dealer 的身份；call 为正、put 为负只是 `assumed_dealer_sign_proxy_call_plus_put_minus`，不代表观测到做市商持仓。
- 每一个假设 spot S 都重新计算所有纳入合约的 Black–Scholes gamma：`phi(d1)/(S*sigma*sqrt(T))`，`d1=(ln(S/K)+(r+sigma²/2)T)/(sigma*sqrt(T))`。
- `GEX(S) = Σ sign * gamma(S) * OI * 100 * S² * 0.01`。代码中的 `100 * 0.01` 抵消为 1；金额单位仍为每 1% 标的波动的美元 gamma 代理。
- IV 按合约固定（sticky strike），OI 与剩余期限固定；无风险利率采用传入 `rate`，股息率假设为零，合约乘数假设为 100。未处理美式提前行权、分红跳跃、调整合约乘数、波动率曲面随 spot 移动、真实对冲行为。
- 搜索区间为原始 spot 的 50%–150%，400 个网格间隔；只对有正负变号的 bracket 二分细化，再线性插值零点。返回全部找到的根，并以距离原始 spot 最近的根作为兼容字段 `gamma_flip`。有限网格可能漏掉非常窄的双重变号；不声称找到全球所有根。
- 零函数、只有单一符号、切触但未变号、区间内没有找到变号，都返回 `gamma_flip=null`、`gamma_flip_status=unknown`，不编造价格。多根、反向根都可能出现，现价高于 flip 不必然代表正 gamma；查看 `gamma_profile.spot_net_gex` 的模型符号。
- 只要任一正 OI 纳入合约缺失有效 IV/strike/期限，全链根为 unknown，不静默删掉合约计算“完整链”。零 OI 无贡献；数据源缺失整个合约或错误报告零 OI 仍无法由该模型识别。
- 日期精度的 `T=days/365` 不足以可靠处理 0DTE；0DTE 正 OI 存在时根为 unknown。禁止用凭空指定的半天寿命冒充精确到期时间。

## 输出分离

`total_net_gex` 与 `regime` 延续原有 spot 快照口径（CBOE 提供的 gamma，或 yfinance 兜底 BS），不强行当成上述 BS profile 的同口径数值。

旧逻辑是按 strike 排序后累加各 strike 在当前 spot 的净 GEX，寻找首次累计变号并取两个 strike 的中点。该数值现在只叫 `legacy_strike_cumulative_crossing`，不再赋给 `gamma_flip`。`_find_walls` 保留原有三个返回值的私有接口，但第三项是这个 legacy 指标；`analyze` 明确另算模型零点。墙位仍是原有 strike 极值代理，并不保证构成支撑/阻力。

新增 JSON 字段：`gamma_flip_method`、`gamma_flip_status`、`gamma_profile`、`legacy_strike_cumulative_crossing`、`dealer_sign_assumption`、`observed_dealer_inventory=false`、`direction_authority=false`、`position_authority=false`。`gamma_flip` 维持 number/null 类型；GammaStructure 的新增字段有默认值，旧构造调用仍可工作。

## 下游兼容风险

本次同步迁移 `risk_regime_snapshot.py`；`market_structure.py`、`short_cycle_signals.py` 只读核查。

- market_structure 原样传递 JSON，兼容新增字段。
- risk_regime 的 aggregate/near 保存 method、status、dealer assumption、profile 与到期日。历史去重签名包含 method、flip、status、profile 等，不会将新算法输出吞成旧行。
- 只有方法、持仓方向假设、来源、利率/股息/乘数/IV/期限口径相同才比较历史；旧历史没有 method 时不产生 flip shift 或 confirmed flags。flip 比较还要求两端 status=modeled 且数值有效。保留原历史，不清库、不强行改标新方法。
- risk_regime 不再用现价相对 flip 的位置推断 gamma 正负。新方法以 `gamma_profile.spot_net_gex` 判断假设模型符号；没有根但模型值有效时仍能说明模型符号，缺模型值则 unknown。供应商 total/regime 与模型符号分离。
- 无新方法标识的旧供应商快照，以及因缺IV/0DTE精确时间而不能计算BS profile的快照，仍可独立保留有效供应商Gamma的 `vendor_snapshot_assumed_dealer_sign_proxy`。此时flip继续unknown，不能编造到期时刻或观测到的dealer持仓。跨快照比较要求同方法/假设/来源/到期窗口及有效先后采集时钟；报告实际间隔，标为 `snapshot_to_snapshot_gex_weakening_proxy`。20分钟变化不冒充日频历史优势，采集时钟也不是交易所行情钟；confirmed_flags仅表示代理变化。
- None/NaN 不转成零。旧无数据快照若 `regime=unknown,total=0`，在本消费边界将其恢复为 unknown/null。通用风险阈值、动作上限与 Decision Compiler 权限没有改动。
- short_cycle 的既有外部 vendor 契约保留；新 BS method 只应作为 profile 参考，不能把 flip 上下解释为固定正负区间。其 `put_wall <= gamma_flip <= call_wall` 校验可能使新根失效/降级，不得移动根以通过校验。本次未将新模型直接接入 short_cycle，也不声称完成独立消费者迁移。
- 本模型零方向/仓位授权；不新增真实账户、交易接口或订单路径，也不替代 Decision Compiler。

## 验证证据

执行 `python3 -m unittest scripts/test_gamma_model_semantics.py -v`。

数学测试明确标记 synthetic：等 IV/期限/OI 双 strike 的解析根、单一符号、完全抵消、缺 IV、0DTE、非有限值、反向根、legacy 分离。合成数据不是行情、收益或线上验收。

供应商缓存仅用于检查解析与缺口降级。当前实时数据质量与可用性须由调用方单独验证。

下游离线回归：`python3 -m unittest scripts/test_gamma_risk_semantics.py -v`。8 个 mock 测试覆盖旧/新方法、参数变化、反向根、None/unknown、供应商标签和历史去重；history 只写临时目录，没有生产快照写入。
