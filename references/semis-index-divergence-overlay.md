# 半导体与指数背离研究

> 主落点声明：编译进 `endogenous_structure`。
> 本模块是现有模块内的 tighten-only overlay，不新增 Decision Compiler 模块。

`scripts/semis_divergence.py` 比较 SOXX/SMH 与 SPY/QQQ 的当日方向、相对幅度及历史残差。公开版提供规则计算，不包含个人回测样本、预设胜率或已验证收益主张。

## 规则与边界

- 半导体上涨而指数下跌：记录广度差异与研究优先级，不单独提高动作等级或仓位上限。
- 半导体下跌而指数上涨：不能对称反推指数将下跌。
- 同向下跌而半导体相对跌幅较大：可标记异常弱势；倍数为研究参数，不构成已验证的离散阈值。
- 残差极端：只作 train_only 研究，不进入可执行概率或正向授权。

## 残差计算

`resid_t = soxx_ret_t - beta60 * index_ret_t`。beta、残差均值和标准差用截至 t-1 的窗口计算，当前观测不得进入自身标准化窗口。缺历史或非有限值时列明缺口。

输出保持 `tighten_only=true`、`no_order_execution=true`。任何机制表述仅限“高β/风险偏好板块广度未失速”，不声称半导体领先或预知大盘。

## 验证与登记

阈值和方向规则须通过匹配基准、样本外检验、成本与多重检验复核。未经验证的模式保持 train_only；假设注册标识由调用方在独立账本中生成。单次命中或重复运行不增加独立样本，也不授予仓位权限。

回归入口：`python3 -m unittest scripts/test_semis_divergence.py -v`。合成测试只验证规则和边界，不证明预测能力。
