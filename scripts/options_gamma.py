#!/usr/bin/env python3
"""options_gamma.py — 免费自算美股期权 Gamma 结构。

输出 Put Wall / Call Wall / Gamma Flip / 总净 GEX / 多空 gamma 区间，
用于 trading-research skill 的「期权/Gamma 结构」执行风控层。

数据源（全部免费、无需 API key）：
  - 主源 CBOE delayed quotes JSON：
      https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json
    含 open_interest / iv / **gamma / delta**（CBOE 自算 Greeks）+ 标的现价，
    覆盖率高（实测 AVGO 约 80% 合约有非零 OI），延迟约 15 分钟。
  - 兜底 yfinance + Black-Scholes：Yahoo 免费 OI 常缺失（多为 0），仅在 CBOE
    取不到时降级使用，质量明显更差，会在 notes 标注。
  - 现价：CBOE JSON 自带；缺失时用 yfinance fast_info 兜底。

方法：每个 strike 的 GEX = gamma * OI * 100 * spot^2 * 1%，按 dealer 约定
（call +、put −）聚合每 1% 现价波动的美元 gamma 暴露。仅供研究，非实时交易级。

坑点：延迟约 15 分钟；dealer 持仓方向是行业约定假设，非真实 dealer 仓位。

用法：
  python options_gamma.py AVGO
  python options_gamma.py RKLB --json
  python options_gamma.py MRVL --max-days 30
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from typing import Any

SQRT_2PI = math.sqrt(2.0 * math.pi)
_OCC_RE = re.compile(r"^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$")
_CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"


def _clear_proxy_env() -> None:
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)


def _parse_occ(option: str) -> tuple[str, str, float] | None:
    """解析 OCC 合约代码 → (expiry YYYY-MM-DD, 'C'/'P', strike)。"""
    m = _OCC_RE.match(option)
    if not m:
        return None
    _root, ymd, cp, strike_raw = m.groups()
    try:
        exp = datetime.strptime(ymd, "%y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None
    return exp, cp, int(strike_raw) / 1000.0


# ── Black-Scholes 假设 spot profile 与 yfinance 兜底────────────────────────────
def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _bs_gamma(spot: float, strike: float, t_years: float, sigma: float, rate: float) -> float:
    if spot <= 0 or strike <= 0 or t_years <= 0 or sigma <= 0:
        return 0.0
    try:
        d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t_years) / (
            sigma * math.sqrt(t_years)
        )
    except ValueError:
        return 0.0
    return _norm_pdf(d1) / (spot * sigma * math.sqrt(t_years))


# ── 数据结构 ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StrikeGex:
    strike: float
    call_oi: int
    put_oi: int
    net_gex: float  # 美元 / 1% 现价波动


@dataclass(frozen=True)
class GammaStructure:
    symbol: str
    asof: str
    source: str
    spot: float
    spot_source: str
    expirations_used: list[str]
    total_net_gex: float
    regime: str  # positive_gamma / negative_gamma
    put_wall: float | None
    call_wall: float | None
    gamma_flip: float | None
    top_strikes: list[dict[str, Any]]
    notes: list[str]
    gamma_flip_method: str = "hypothetical_spot_bs_gamma_v1"
    gamma_flip_status: str = "unknown"
    gamma_profile: dict[str, Any] = field(default_factory=dict)
    legacy_strike_cumulative_crossing: float | None = None
    dealer_sign_assumption: str = "assumed_dealer_sign_proxy_call_plus_put_minus"
    observed_dealer_inventory: bool = False
    direction_authority: bool = False
    position_authority: bool = False


@dataclass(frozen=True)
class GammaContract:
    strike: float
    oi: float
    iv: float
    t_years: float
    sign: int


def _spot_gamma_profile(contracts: list[GammaContract], spot: float, rate: float) -> dict[str, Any]:
    """Frozen IV/OI, q=0, 100-share proxy; roots require actual sign brackets."""
    out: dict[str, Any] = {
        "method": "hypothetical_spot_bs_gamma_v1", "status": "unknown",
        "roots": [], "gamma_flip": None, "contract_count": len(contracts),
        "rate": rate, "dividend_yield": 0.0, "contract_multiplier": 100,
        "iv_assumption": "sticky_strike_frozen_iv", "time_basis": "calendar_days/365",
        "search_bounds": [spot * 0.5, spot * 1.5], "grid_intervals": 400,
        "root_selection": "nearest_to_observed_spot", "spot_net_gex": None,
    }
    valid = lambda x: isinstance(x, (int, float)) and math.isfinite(x) and x > 0
    if not valid(spot) or not math.isfinite(rate) or not contracts:
        out["reason"] = "missing_or_invalid_inputs"
        return out
    invalid = [c for c in contracts if not all(valid(v) for v in (c.strike, c.oi, c.iv, c.t_years)) or c.sign not in (-1, 1)]
    if invalid:
        out.update(reason="incomplete_chain_iv_or_expiry_time", invalid_contract_count=len(invalid))
        return out
    def value(x: float) -> float:
        return math.fsum(c.sign * _bs_gamma(x, c.strike, c.t_years, c.iv, rate) * c.oi * x * x for c in contracts)
    out["spot_net_gex"] = value(spot)
    lo, hi = out["search_bounds"]
    points = [(lo + (hi-lo)*i/400, value(lo + (hi-lo)*i/400)) for i in range(401)]
    # Skip exact-zero grid values: they are roots only if adjacent nonzero values reverse sign.
    nonzero = [(x,y) for x,y in points if y != 0]
    roots = []
    for (a, fa), (b, fb) in zip(nonzero, nonzero[1:]):
        if (fa > 0) == (fb > 0):
            continue
        for _ in range(50):
            mid = (a+b)/2
            fm = value(mid)
            if fm == 0:
                a = b = mid
                break
            if (fm > 0) == (fa > 0):
                a, fa = mid, fm
            else:
                b, fb = mid, fm
        root = a if a == b else a - fa*(b-a)/(fb-fa)
        roots.append(root)
    if not roots:
        out["reason"] = "no_sign_changing_root_in_search_range"
        return out
    out.update(status="modeled", roots=roots, gamma_flip=min(roots, key=lambda x: abs(x-spot)))
    return out


# ── 现价 ──────────────────────────────────────────────────────────────
def _spot_from_engine(symbol: str) -> tuple[float, str]:
    try:
        import yfinance as yf

        info = yf.Ticker(symbol).fast_info
        price = float(info.get("last_price") or info.get("lastPrice") or 0)
        if price > 0:
            return price, "yfinance_fast_info"
    except Exception:
        pass
    return 0.0, "unavailable"


# ── 到期日切片解析 ────────────────────────────────────────────────────
def _resolve_target_expiry(
    options: list[dict[str, Any]], today: date, max_days: int,
    expiry: str | None, near: bool,
) -> str | None:
    """expiry 指定则原样返回；near=True 则返回窗口内最近的到期日；否则 None（聚合）。"""
    if expiry:
        return expiry
    if not near:
        return None
    candidates: set[str] = set()
    for o in options:
        parsed = _parse_occ(str(o.get("option", "")))
        if not parsed:
            continue
        exp = parsed[0]
        try:
            days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if 0 <= days <= max_days:
            candidates.add(exp)
    return min(candidates) if candidates else None


# ── 主源：CBOE ────────────────────────────────────────────────────────
def _collect_from_cboe(
    symbol: str, max_days: int, expiry: str | None = None, near: bool = False,
    profile_contracts: list[GammaContract] | None = None,
) -> tuple[list[StrikeGex], list[str], float, list[str]] | None:
    """返回 (strikes, expirations, spot, notes)；取不到返回 None 让上层降级。
    expiry 指定时只聚合该到期日；near=True 时只聚合窗口内最近一个到期日（0DTE/近月视角）。"""
    _clear_proxy_env()
    url = _CBOE_URL.format(sym=symbol)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        payload = json.load(urllib.request.urlopen(req, timeout=25))
    except Exception:
        return None
    data = payload.get("data") or {}
    options = data.get("options") or []
    if not options:
        return None

    spot = float(data.get("close") or data.get("current_price") or 0)
    today = date.today()
    notes: list[str] = []
    used: set[str] = set()
    agg: dict[float, list[float]] = {}

    target = _resolve_target_expiry(options, today, max_days, expiry, near)
    if (expiry or near) and target is None:
        notes.append("数据缺口：窗口内无匹配到期日，回退聚合视角")

    for o in options:
        parsed = _parse_occ(str(o.get("option", "")))
        if not parsed:
            continue
        exp, cp, strike = parsed
        try:
            days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if days < 0 or days > max_days:
            continue
        if target is not None and exp != target:
            continue
        oi = float(o.get("open_interest") or 0)
        gamma = float(o.get("gamma") or 0)
        if oi > 0 and profile_contracts is not None:
            profile_contracts.append(GammaContract(strike, oi, float(o.get("iv") or 0), days / 365.0, 1 if cp == "C" else -1))
        if oi <= 0 or gamma <= 0 or strike <= 0:
            continue
        used.add(exp)
        # spot 缺失时用 ATM 兜底在外层处理；这里若 spot<=0 用 strike 近似不影响 gamma 已给
        ref = spot if spot > 0 else strike
        dollar_gex = gamma * oi * 100 * ref * ref * 0.01
        is_call = cp == "C"
        slot = agg.setdefault(strike, [0.0, 0.0, 0.0])
        if is_call:
            slot[0] += oi
            slot[2] += dollar_gex
        else:
            slot[1] += oi
            slot[2] -= dollar_gex

    strikes = [
        StrikeGex(strike=k, call_oi=int(v[0]), put_oi=int(v[1]), net_gex=v[2])
        for k, v in sorted(agg.items())
    ]
    if not strikes:
        return None
    return strikes, sorted(used), spot, notes


# ── 兜底：yfinance + BS ───────────────────────────────────────────────
def _collect_from_yfinance(
    symbol: str, spot: float, max_days: int, rate: float,
    expiry: str | None = None, near: bool = False,
    profile_contracts: list[GammaContract] | None = None,
) -> tuple[list[StrikeGex], list[str], list[str]]:
    import yfinance as yf

    notes = ["降级使用 yfinance+BS：Yahoo 免费 OI 常缺失，墙位可靠性低于 CBOE"]
    ticker = yf.Ticker(symbol)
    all_exps = list(ticker.options or [])
    if not all_exps:
        return [], [], notes + ["数据缺口：yfinance 无到期日"]
    today = date.today()
    in_window = [
        e for e in all_exps
        if (datetime.strptime(e, "%Y-%m-%d").date() - today).days >= 0
        and (datetime.strptime(e, "%Y-%m-%d").date() - today).days <= max_days
    ]
    target = expiry if expiry else (min(in_window) if (near and in_window) else None)
    used: list[str] = []
    agg: dict[float, list[float]] = {}
    for exp in all_exps:
        try:
            days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if days < 0:
            continue
        if days > max_days:
            break
        if target is not None and exp != target:
            continue
        t_years = max(days, 0.5) / 365.0
        try:
            chain = ticker.option_chain(exp)
        except Exception:
            continue
        used.append(exp)
        for df, is_call in ((chain.calls, True), (chain.puts, False)):
            for _, row in df.iterrows():
                strike = float(row.get("strike") or 0)
                oi = int(row.get("openInterest") or 0)
                iv = float(row.get("impliedVolatility") or 0)
                if oi > 0 and profile_contracts is not None:
                    profile_contracts.append(GammaContract(strike, oi, iv, days / 365.0, 1 if is_call else -1))
                if strike <= 0 or oi <= 0 or iv <= 0:
                    continue
                gamma = _bs_gamma(spot, strike, t_years, iv, rate)
                if gamma <= 0:
                    continue
                dollar_gex = gamma * oi * 100 * spot * spot * 0.01
                slot = agg.setdefault(strike, [0.0, 0.0, 0.0])
                if is_call:
                    slot[0] += oi
                    slot[2] += dollar_gex
                else:
                    slot[1] += oi
                    slot[2] -= dollar_gex
    strikes = [
        StrikeGex(strike=k, call_oi=int(v[0]), put_oi=int(v[1]), net_gex=v[2])
        for k, v in sorted(agg.items())
    ]
    if not strikes:
        notes.append("数据缺口：yfinance 窗口内无有效 OI/IV")
    return strikes, used, notes


# ── 墙位与 flip ───────────────────────────────────────────────────────
def _find_walls(
    strikes: list[StrikeGex], spot: float
) -> tuple[float | None, float | None, float | None]:
    """Return strike walls and legacy cumulative crossing; NOT a spot gamma root."""
    below = [s for s in strikes if s.strike < spot]
    above = [s for s in strikes if s.strike >= spot]
    put_wall = min(below, key=lambda s: s.net_gex).strike if below else None
    call_wall = max(above, key=lambda s: s.net_gex).strike if above else None

    legacy_strike_cumulative_crossing: float | None = None
    cumulative = 0.0
    prev_sign = 0
    prev_strike: float | None = None
    for s in strikes:
        cumulative += s.net_gex
        sign = 1 if cumulative > 0 else (-1 if cumulative < 0 else 0)
        if prev_sign != 0 and sign != 0 and sign != prev_sign and prev_strike is not None:
            legacy_strike_cumulative_crossing = round((prev_strike + s.strike) / 2.0, 2)
            break
        if sign != 0:
            prev_sign = sign
            prev_strike = s.strike
    return put_wall, call_wall, legacy_strike_cumulative_crossing


def analyze(
    symbol: str, max_days: int = 45, rate: float = 0.045,
    expiry: str | None = None, near: bool = False,
) -> GammaStructure:
    symbol = symbol.upper().strip()
    asof = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    notes: list[str] = []
    if expiry or near:
        notes.append(
            "视角：单一到期日 gamma（近月/0DTE 驱动日内 dealer 对冲），与聚合窗口的 flip 可能不同"
        )

    source = "cboe_delayed"
    strikes: list[StrikeGex] = []
    expirations: list[str] = []
    spot = 0.0
    spot_source = "cboe"

    profile_contracts: list[GammaContract] = []
    cboe = _collect_from_cboe(symbol, max_days, expiry=expiry, near=near, profile_contracts=profile_contracts)
    if cboe is not None:
        strikes, expirations, spot, cnotes = cboe
        notes.extend(cnotes)
    if not strikes:
        profile_contracts.clear()
        source = "yfinance_bs"
        spot, spot_source = _spot_from_engine(symbol)
        if spot > 0:
            strikes, expirations, ynotes = _collect_from_yfinance(
                symbol, spot, max_days, rate, expiry=expiry, near=near, profile_contracts=profile_contracts
            )
            notes.extend(ynotes)
        else:
            notes.append("数据缺口：CBOE 与现价源均不可用")

    if spot <= 0:
        spot, spot_source = _spot_from_engine(symbol)

    if not strikes or spot <= 0:
        if spot <= 0:
            notes.append("数据缺口：无法获取现价，墙位相对位置不可判定")
        return GammaStructure(
            symbol=symbol, asof=asof, source=source, spot=round(spot, 2),
            spot_source=spot_source, expirations_used=expirations, total_net_gex=0.0,
            regime="unknown", put_wall=None, call_wall=None, gamma_flip=None,
            top_strikes=[], notes=notes,
        )

    total = sum(s.net_gex for s in strikes)
    regime = "positive_gamma" if total >= 0 else "negative_gamma"
    put_wall, call_wall, legacy_crossing = _find_walls(strikes, spot)
    profile = _spot_gamma_profile(profile_contracts, spot, rate)
    gamma_flip = profile["gamma_flip"]
    notes.append("OI call+ / put− 仅 assumed dealer-sign proxy；未观测做市商持仓，不授予方向或仓位权限。")
    if profile["status"] == "unknown":
        notes.append("Gamma flip unknown: " + profile["reason"])
    top = sorted(strikes, key=lambda s: abs(s.net_gex), reverse=True)[:8]
    top_strikes = [
        {
            "strike": s.strike,
            "net_gex_musd": round(s.net_gex / 1e6, 2),
            "call_oi": s.call_oi,
            "put_oi": s.put_oi,
            "pos_vs_spot": "above" if s.strike >= spot else "below",
        }
        for s in top
    ]
    return GammaStructure(
        symbol=symbol, asof=asof, source=source, spot=round(spot, 2),
        spot_source=spot_source if source == "yfinance_bs" else "cboe",
        expirations_used=expirations, total_net_gex=round(total, 2), regime=regime,
        put_wall=put_wall, call_wall=call_wall, gamma_flip=gamma_flip,
        top_strikes=top_strikes, notes=notes, gamma_profile=profile,
        gamma_flip_status=profile["status"], legacy_strike_cumulative_crossing=legacy_crossing,
    )


def _render_text(s: GammaStructure) -> str:
    lines = [
        f"# {s.symbol} 期权 Gamma 结构  （{s.asof}｜源 {s.source}）",
        f"现价 {s.spot}（{s.spot_source}）｜到期窗口：{', '.join(s.expirations_used) or '无'}",
        "",
        f"总净 GEX：{s.total_net_gex/1e6:.1f} M$/1% ｜ 区间：{s.regime}",
    ]
    lines.append("  → GEX 符号为 OI 持仓方向假设下的代理值，不是观测到的做市商暴露。")
    lines += [
        "",
        f"Put Wall（下方 strike 代理）：{s.put_wall if s.put_wall is not None else '数据缺口'}",
        f"Call Wall（上方 strike 代理）：{s.call_wall if s.call_wall is not None else '数据缺口'}",
        f"Gamma Flip（假设 spot 全链 BS 零点）：{s.gamma_flip if s.gamma_flip is not None else '数据缺口'}",
    ]
    lines += ["", "Top GEX strikes（按 |净 GEX|）："]
    for t in s.top_strikes:
        lines.append(
            f"  {t['strike']:>8}  net {t['net_gex_musd']:>8} M$  "
            f"callOI {t['call_oi']:>8}  putOI {t['put_oi']:>8}  ({t['pos_vs_spot']})"
        )
    if s.notes:
        lines += ["", "备注/缺口："] + [f"  - {n}" for n in s.notes]
    lines.append("\n数据：CBOE delayed(~15min)+yfinance 现价兜底；dealer 方向为行业约定假设，非真实仓位。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="免费自算美股期权 Gamma 结构（CBOE 主源）")
    parser.add_argument("symbol", help="美股 ticker，如 AVGO/RKLB/MRVL")
    parser.add_argument("--max-days", type=int, default=45, help="纳入的到期日窗口（天），默认 45")
    parser.add_argument("--rate", type=float, default=0.045, help="无风险利率（假设 spot profile 与兜底 BS 用），默认 0.045")
    parser.add_argument("--expiry", type=str, default=None,
                        help="只看单一到期日 YYYY-MM-DD（日内/0DTE 视角，对应行情软件单到期日 Gamma 图）")
    parser.add_argument("--near", action="store_true",
                        help="只看窗口内最近一个到期日（近月/0DTE gamma，驱动日内 dealer 对冲）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    try:
        result = analyze(args.symbol, max_days=args.max_days, rate=args.rate,
                         expiry=args.expiry, near=args.near)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}",
                          "symbol": args.symbol.upper()}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2) if args.json
          else _render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
