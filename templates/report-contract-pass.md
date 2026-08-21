# TSLA 简洁投研报告（pass 样本）

用户，已完成。核心结论：

TSLA：行动等级 L1 试错/观察；新钱不追；若已持有，保留核心但不加。

原因很简单：基本面仍有支撑，但宏观流动性和拥挤度要求仓位打折。

关键依据：
- [E1] LongBridge quote 显示价格仍在关键均线上方，趋势未破。
- [E2] fundamental_snapshot 显示营收仍增长，基本面不是主杀因。
- [E3] macro dashboard 显示流动性偏紧，追高胜率下降。
- [E4] endogenous structure 显示 consensus premium 偏高，仓位需要打折。

行动线：
- 空仓：不追，等回踩关键均线后再评估。
- 低吸：只有 E1/E2 继续确认且 E3 不恶化，才允许小仓试错。
- 已持有：保留核心，不加；跌破关键位未收回则降风险。
- 转强复核：放量站回压力位并连续 2 个交易日收稳。
- falsifier: 下季度营收增速低于 15%，或跌破关键价位后 2 个交易日无法收回。
- 冲突处理：基本面偏多 vs 宏观偏弱，按 Decision Compiler 降到 L1。

数据缺口：
- X/Grok 未发现可复核新增一线信号，只作线索，不提高仓位。
- 账户/真实持仓未接入，不给账户级数量。

验证结果：
- validate_report.py → OK。
- Decision Compiler → L1 / hard_veto=false / final_position_multiplier=0.5。
- Decision Memory → preflight pass，未发现同标的负面历史样本。
- write_status: recorded；completeness: full。

质量门：
- readiness_level: actionable_with_caveats
- intelligence coverage: 1.0；critical gaps=0；research watch triggers: none；
- stale_after: 2026-06-12 收盘后
- must_refresh_if: 财报/指引更新；跌破关键位未收回；宏观四象限转 defend；核心 falsifier 出现
- review clock: 每周收盘后复盘

非下单指令，不构成投资建议；我没有操作券商，也不代表你的真实持仓。
