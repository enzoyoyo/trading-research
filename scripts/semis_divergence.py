#!/usr/bin/env python3
"""semis_divergence.py — 半导体（SOXX/SMH）与大盘（SPY/QQQ）背离 overlay。

研究规则：比较半导体与指数的方向差异、相对跌幅，以及滞后窗口的残差。
本包未提供独立可复现的有效性回测，所有规则均为 train_only；不输出概率或证据等级。
这些参数仅用于划分研究样本，不能证明交易优势，也不能授权开仓。

本脚本不新增 Decision Compiler module，映射到既有 `endogenous_structure`；只能收紧
（tighten_only），不得单独提高 action level 或仓位上限。

数据源：本地面板缓存（由 factor_panel.py 写入，本脚本纯 stdlib、不 import akshare/pandas）。
默认只读缓存，绝不静默联网；`--fetch` 显式给出时才 subprocess 调用 factor_panel.py。
纪律：取数失败/面板缺失一律写缺口，绝不补数字；缺口是合法输出，退出码恒为 0。

用法：
  python3 semis_divergence.py
  python3 semis_divergence.py --json
  python3 semis_divergence.py --fetch --json
  python3 semis_divergence.py --asof 2026-08-20 --json
  python3 semis_divergence.py --panel-root /tmp/synthetic_panels --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Research parameters are conventional sample labels, not validated thresholds.
MOVE_MIN_PCT = 0.25
BETA_WINDOW = 60
Z_STRONG = 1.5
SEMI_2X_RATIO = 2.0

SCHEMA_VERSION = "semis_divergence.v1"
MODULE_MAPPING = "endogenous_structure"
# Registry IDs are assigned by each operator; this package contains no local IDs.
HYPOTHESIS_IDS: dict[str, str] = {}


def default_panel_root() -> Path:
    """真实缓存路径：~/.cache/hermes/trading-research/factor-panels/US（由 factor_panel.py 写入）。"""
    return Path.home() / ".cache" / "hermes" / "trading-research" / "factor-panels" / "US"


def load_panel(path: Path) -> dict[str, Any] | None:
    """读取单个面板 json，返回按日期升序去重后的 {date: close}；失败/缺失返回 None。"""
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    rows = obj.get("rows") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        return None
    closes: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = row.get("date")
        c = row.get("close")
        if not isinstance(d, str) or c is None:
            continue
        try:
            closes[d] = float(c)
        except (TypeError, ValueError):
            continue
    if not closes:
        return None
    return closes


def read_symbol(panel_root: Path, symbol: str) -> dict[str, float] | None:
    return load_panel(panel_root / f"{symbol}.json")


def pct_returns(closes: dict[str, float]) -> dict[str, float]:
    """按日期升序算 pct 单位日收益：ret[d] = (close[d]-close[prev])/close[prev]*100。"""
    dates = sorted(closes)
    rets: dict[str, float] = {}
    for i in range(1, len(dates)):
        prev_c = closes[dates[i - 1]]
        cur_c = closes[dates[i]]
        if prev_c == 0:
            continue
        rets[dates[i]] = (cur_c - prev_c) / prev_c * 100.0
    return rets


def resolve_semis(panel_root: Path) -> tuple[dict[str, float] | None, str | None, str | None]:
    """SOXX 优先；缺失且 SMH 在 → 用 SMH 代理。返回 (closes, proxy_symbol, gap)。"""
    soxx = read_symbol(panel_root, "SOXX")
    if soxx is not None:
        return soxx, None, None
    smh = read_symbol(panel_root, "SMH")
    if smh is not None:
        return smh, "SMH", "SOXX 面板缺失，已用 SMH 代理"
    return None, None, "SOXX/SMH 面板均缺失"


def pick_asof_date(common_dates: list[str], asof: str | None) -> str | None:
    """common_dates 升序；--asof 给定时取 <= asof 的最后一个；否则取最新。"""
    if not common_dates:
        return None
    if asof is None:
        return common_dates[-1]
    eligible = [d for d in common_dates if d <= asof]
    return eligible[-1] if eligible else None


def aligned_pairs(ret_a: dict[str, float], ret_b: dict[str, float]) -> list[tuple[str, float, float]]:
    common = sorted(set(ret_a) & set(ret_b))
    return [(d, ret_a[d], ret_b[d]) for d in common]


def compute_beta_residual_z(
    ret_semis: dict[str, float], ret_spy: dict[str, float], t: str, window: int
) -> dict[str, Any] | None:
    """无前视：β60/残差 std 只用 t 之前（严格 < t）的最近 window 个交易日。"""
    pairs = aligned_pairs(ret_semis, ret_spy)
    before_t = [(d, a, b) for d, a, b in pairs if d < t]
    if len(before_t) < window:
        return None
    win = before_t[-window:]
    beta = _beta_from_window(win)
    if beta is None:
        return None
    resids = [a - beta * b for _, a, b in win]
    mean_r = sum(resids) / len(resids)
    var_r = sum((r - mean_r) ** 2 for r in resids) / len(resids)
    std_r = var_r ** 0.5
    if std_r == 0:
        return None
    semis_t = ret_semis.get(t)
    spy_t = ret_spy.get(t)
    if semis_t is None or spy_t is None:
        return None
    resid_t = semis_t - beta * spy_t
    z = (resid_t - mean_r) / std_r
    return {
        "beta60": round(beta, 6),
        "window_size": len(win),
        "residual_t_pct": round(resid_t, 6),
        "window_mean_resid": round(mean_r, 6),
        "window_std_resid": round(std_r, 6),
        "z": round(z, 6),
    }


def _beta_from_window(win: list[tuple[str, float, float]]) -> float | None:
    n = len(win)
    if n == 0:
        return None
    mean_a = sum(a for _, a, _ in win) / n
    mean_b = sum(b for _, _, b in win) / n
    cov = sum((a - mean_a) * (b - mean_b) for _, a, b in win) / n
    var_b = sum((b - mean_b) ** 2 for _, _, b in win) / n
    if var_b == 0:
        return None
    return cov / var_b


def classify_primary_state(semis_t: float, spy_t: float, qqq_t: float) -> tuple[str, str | None]:
    """先判两腿方向：semis_strong_index_down > semis_strong_qqq_down > semis_weak_index_up。"""
    if semis_t >= MOVE_MIN_PCT and spy_t <= -MOVE_MIN_PCT:
        return "semis_strong_index_down", None
    if semis_t >= MOVE_MIN_PCT and qqq_t <= -MOVE_MIN_PCT:
        return "semis_strong_qqq_down", None
    if semis_t <= -MOVE_MIN_PCT and (spy_t >= MOVE_MIN_PCT or qqq_t >= MOVE_MIN_PCT):
        return "semis_weak_index_up", None
    return "no_divergence", None


def is_abnormal_weakness(semis_t: float, qqq_t: float) -> bool:
    """双腿皆跌、QQQ 至少跌 MOVE_MIN_PCT、且 |SOXX 跌幅| ≥ SEMI_2X_RATIO×|QQQ 跌幅|。"""
    if semis_t > -MOVE_MIN_PCT or qqq_t > -MOVE_MIN_PCT:
        return False
    return abs(semis_t) >= SEMI_2X_RATIO * abs(qqq_t)


def build_iwm_control(panel_root: Path, ret_spy: dict[str, float], t: str) -> tuple[dict[str, Any] | None, str | None]:
    iwm = read_symbol(panel_root, "IWM")
    if iwm is None:
        return None, "IWM 面板缺失，对照块降级为缺口（不影响主判定）"
    ret_iwm = pct_returns(iwm)
    iwm_t = ret_iwm.get(t)
    spy_t = ret_spy.get(t)
    if iwm_t is None or spy_t is None:
        return None, "IWM/SPY 在 t 日收益缺失，对照块降级为缺口"
    matches = iwm_t >= MOVE_MIN_PCT and spy_t <= -MOVE_MIN_PCT
    return {
        "iwm_ret_pct": round(iwm_t, 6),
        "matches_semis_bounce_pattern": matches,
        "mechanism": "待检验：高 beta / 风险偏好板块广度是否解释共同变化",
        "validation_posture": "train_only",
    }, None


def _unavailable_snapshot(asof_arg: str | None, gaps: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "no_order_execution": True,
        "validation_posture": "train_only",
        "usable_for_action": False,
        "performance_validation": "not_provided",
        "tighten_only": True,
        "module_mapping": MODULE_MAPPING,
        "asof": asof_arg,
        "state": "unavailable",
        "tags": [],
        "grades": {"state": None, "semis_abnormal_weakness": None},
        "forbidden_inference": None,
        "residual_z": None,
        "iwm_control": None,
        "proxy_symbol": None,
        "gaps": gaps,
        "hypothesis_ids": HYPOTHESIS_IDS,
    }


def _load_required_panels(panel_root: Path) -> tuple[dict[str, float] | None, dict[str, float] | None, dict[str, float] | None, str | None, list[str]]:
    """读齐 semis(SOXX/SMH)/SPY/QQQ 三腿，缺哪个记哪个缺口。"""
    gaps: list[str] = []
    semis_closes, proxy_symbol, semis_gap = resolve_semis(panel_root)
    if semis_gap:
        gaps.append(semis_gap)
    spy_closes = read_symbol(panel_root, "SPY")
    qqq_closes = read_symbol(panel_root, "QQQ")
    if spy_closes is None:
        gaps.append("SPY 面板缺失")
    if qqq_closes is None:
        gaps.append("QQQ 面板缺失")
    return semis_closes, spy_closes, qqq_closes, proxy_symbol, gaps


def _resolve_t(
    semis_closes: dict[str, float], spy_closes: dict[str, float], qqq_closes: dict[str, float], asof_arg: str | None
) -> tuple[str | None, dict[str, float], dict[str, float], dict[str, float]]:
    common_dates = sorted(set(semis_closes) & set(spy_closes) & set(qqq_closes))
    t = pick_asof_date(common_dates, asof_arg)
    ret_semis, ret_spy, ret_qqq = pct_returns(semis_closes), pct_returns(spy_closes), pct_returns(qqq_closes)
    if t is None or t not in ret_semis or t not in ret_spy or t not in ret_qqq:
        return None, ret_semis, ret_spy, ret_qqq
    return t, ret_semis, ret_spy, ret_qqq


def _assemble_residual_block(
    ret_semis: dict[str, float], ret_spy: dict[str, float], t: str, spy_t: float, beta_window: int
) -> tuple[dict[str, Any] | None, str | None]:
    residual_z = compute_beta_residual_z(ret_semis, ret_spy, t, beta_window)
    if residual_z is None:
        gap = f"β{beta_window}/残差窗口不足（需 t 前 {beta_window} 个交易日）或方差为零，已省略 Z 块"
        return None, gap
    extreme = residual_z["z"] >= Z_STRONG and spy_t <= -MOVE_MIN_PCT
    residual_z["extreme"] = extreme
    residual_z["train_only_block"] = (
        {
            "in_sample_only": True,
            "usable_for_action": False,
            "hypothesis_id": None,
        }
        if extreme
        else None
    )
    return residual_z, None


def build_snapshot(panel_root: Path, asof_arg: str | None, beta_window: int) -> dict[str, Any]:
    semis_closes, spy_closes, qqq_closes, proxy_symbol, gaps = _load_required_panels(panel_root)
    if semis_closes is None or spy_closes is None or qqq_closes is None:
        return _unavailable_snapshot(asof_arg, gaps)

    t, ret_semis, ret_spy, ret_qqq = _resolve_t(semis_closes, spy_closes, qqq_closes, asof_arg)
    if t is None:
        gaps.append("找不到 semis/SPY/QQQ 共同的可判定交易日（t 或其前一日缺失）")
        return _unavailable_snapshot(asof_arg, gaps)

    semis_t, spy_t, qqq_t = ret_semis[t], ret_spy[t], ret_qqq[t]
    state, state_grade = classify_primary_state(semis_t, spy_t, qqq_t)
    tags = [] if state == "no_divergence" else [state]
    abnormal = is_abnormal_weakness(semis_t, qqq_t)
    if abnormal:
        tags.append("semis_abnormal_weakness")

    residual_z, rz_gap = _assemble_residual_block(ret_semis, ret_spy, t, spy_t, beta_window)
    if rz_gap:
        gaps.append(rz_gap)
    iwm_control, iwm_gap = build_iwm_control(panel_root, ret_spy, t)
    if iwm_gap:
        gaps.append(iwm_gap)

    return {
        "schema_version": SCHEMA_VERSION,
        "no_order_execution": True,
        "validation_posture": "train_only",
        "usable_for_action": False,
        "performance_validation": "not_provided",
        "tighten_only": True,
        "module_mapping": MODULE_MAPPING,
        "asof": t,
        "state": state,
        "tags": tags,
        "grades": {"state": state_grade, "semis_abnormal_weakness": None},
        "forbidden_inference": "fake_rally_short" if state == "semis_weak_index_up" else None,
        "residual_z": residual_z,
        "iwm_control": iwm_control,
        "proxy_symbol": proxy_symbol,
        "gaps": gaps,
        "hypothesis_ids": HYPOTHESIS_IDS,
    }


def maybe_fetch(panel_root: Path) -> None:
    """--fetch 显式给出时才联网；默认路径零网络（保住 ACCEPT 门 network_disabled）。"""
    script = Path(__file__).resolve().with_name("factor_panel.py")
    cmd = [
        sys.executable, str(script), "fetch",
        "--market", "US", "--symbols", "SOXX", "SMH", "SPY", "QQQ", "IWM",
        "--window-days", "420", "--json",
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError):
        pass  # 取数失败也不报错——由后续读缓存环节写缺口


def render_human(snap: dict[str, Any]) -> str:
    lines = ["# 半导体-指数背离 overlay（endogenous_structure）", f"asof {snap.get('asof')}", ""]
    lines.append(f"状态：{snap['state']}")
    lines.append("验证状态：train_only；本包未提供有效性回测，不能据此授权交易。")
    if snap.get("proxy_symbol"):
        lines.append(f"代理符号：SOXX 缺失，已用 {snap['proxy_symbol']} 代理")
    if snap.get("tags"):
        lines.append(f"标签：{', '.join(snap['tags'])}")
    grades = snap.get("grades") or {}
    if grades.get("state"):
        lines.append(f"等级：{grades['state']}")
    if grades.get("semis_abnormal_weakness"):
        lines.append(f"异常弱势等级：{grades['semis_abnormal_weakness']}（tighten-only）")
    if snap.get("forbidden_inference"):
        lines.append(f"禁止推断：{snap['forbidden_inference']}（该方向差异不得单独当做空依据）")
    rz = snap.get("residual_z")
    if rz:
        lines.append(
            f"残差 Z：{rz['z']}（β60={rz['beta60']}，窗口={rz['window_size']}）"
            + ("｜train_only：样本内不可交易" if rz.get("extreme") else "")
        )
    iwm = snap.get("iwm_control")
    if iwm:
        lines.append(
            f"IWM 对照：ret={iwm['iwm_ret_pct']}%，同步反弹={iwm['matches_semis_bounce_pattern']}"
            f"｜研究假设：{iwm['mechanism']}"
        )
    if snap.get("gaps"):
        lines += ["", "缺口："] + [f"- {g}" for g in snap["gaps"]]
    lines += ["", "红线：本信号 tighten-only，不得单独提高 action level 或仓位上限。"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="半导体-指数背离 overlay（SOXX/SMH vs SPY/QQQ，IWM 对照）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--beta-window", type=int, default=BETA_WINDOW, help="β/残差窗口交易日数（默认 60）")
    parser.add_argument("--fetch", action="store_true", help="显式联网刷新面板缓存（默认零网络）")
    parser.add_argument("--asof", type=str, default=None, help="YYYY-MM-DD，取该日期（含）前最后一个交易日为 t")
    parser.add_argument("--panel-root", type=str, default=None, help="面板目录（测试注入合成面板用）")
    args = parser.parse_args()

    panel_root = Path(args.panel_root).expanduser() if args.panel_root else default_panel_root()
    if args.fetch:
        maybe_fetch(panel_root)

    snap = build_snapshot(panel_root, args.asof, max(2, args.beta_window))
    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(render_human(snap))


if __name__ == "__main__":
    main()
