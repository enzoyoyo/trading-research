#!/usr/bin/env python3
"""trading-research · fundamental_snapshot — 三市场基本面快照。

规则：空 parts 不是 ok；不得把空基本面结果生成 fundamentals EID。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

AKSHARE_PYTHON = os.environ.get("AKSHARE_PYTHON") or sys.executable
SCRIPTS = Path(__file__).resolve().parent
LONG_BRIDGE_BIN = os.environ.get("LONGBRIDGE_BIN") or shutil.which("longbridge")
DEFAULT_CONSENSUS_ROOT = (
    Path.home() / ".cache" / "hermes" / "trading-research"
    / "earnings-radar" / "consensus"
)
sys.path.insert(0, str(SCRIPTS))

try:
    from us_company_evidence import collect_company_evidence
except ImportError:
    collect_company_evidence = None  # type: ignore[assignment]


def require_longbridge_bin(*, runner: Callable[..., Any] = subprocess.run) -> str:
    if LONG_BRIDGE_BIN:
        return LONG_BRIDGE_BIN
    if runner is not subprocess.run:
        return "longbridge"
    raise RuntimeError("LongBridge CLI not found; install `longbridge`, add it to PATH, or set LONGBRIDGE_BIN")


def _no_proxy_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env[key] = ""
    env["no_proxy"] = "*"
    return env


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def consensus_cache_root() -> Path:
    return Path(os.environ.get("EARNINGS_RADAR_CONSENSUS_DIR", str(DEFAULT_CONSENSUS_ROOT))).expanduser()


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _aware_dt(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _candidate_dicts(payload: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 3:
        return []
    if isinstance(payload, list):
        return [row for item in payload for row in _candidate_dicts(item, depth=depth + 1)]
    if not isinstance(payload, dict):
        return []
    rows = [payload]
    for value in payload.values():
        if isinstance(value, (dict, list)):
            rows.extend(_candidate_dicts(value, depth=depth + 1))
    return rows


def normalize_consensus_payload(payload: Any) -> list[dict[str, Any]]:
    consensus_keys = ("eps_consensus", "consensus_eps", "forecast_eps", "mean_eps", "avg_eps")
    actual_keys = ("reported_eps", "actual_eps", "eps_actual")
    period_keys = ("fiscal_period", "report_period", "period", "quarter", "year")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float | None, str | None]] = set()
    for raw in _candidate_dicts(payload):
        consensus = next((_finite(raw.get(key)) for key in consensus_keys if _finite(raw.get(key)) is not None), None)
        if consensus is None:
            continue
        period = next((str(raw.get(key)).strip() for key in period_keys if str(raw.get(key) or "").strip()), "unknown")
        actual = next((_finite(raw.get(key)) for key in actual_keys if _finite(raw.get(key)) is not None), None)
        reported_at = next((
            str(raw.get(key)).strip() for key in ("reported_at", "earnings_release_ts", "published_at")
            if str(raw.get(key) or "").strip()
        ), None)
        identity = (period, consensus, actual, reported_at)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append({
            "fiscal_period": period,
            "eps_consensus": consensus,
            "reported_eps": actual,
            "reported_at": reported_at,
        })
    return rows


def fetch_eps_consensus(
    symbol: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    clean = str(symbol).strip().upper().replace(".US", "")
    failures: list[str] = []
    for endpoint in ("consensus", "forecast-eps"):
        command = [require_longbridge_bin(runner=runner), endpoint, f"{clean}.US", "--format", "json"]
        try:
            proc = runner(command, capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{endpoint}:{type(exc).__name__}")
            continue
        if int(proc.returncode) != 0:
            failures.append(f"{endpoint}:nonzero")
            continue
        try:
            payload = json.loads(proc.stdout or "null")
        except json.JSONDecodeError:
            failures.append(f"{endpoint}:invalid_json")
            continue
        rows = normalize_consensus_payload(payload)
        if rows:
            return rows, f"longbridge_{endpoint.replace('-', '_')}", []
        failures.append(f"{endpoint}:eps_consensus_missing")
    return [], None, [{
        "gap": "EPS consensus unavailable",
        "impact": "consensus part omitted; beat rate remains unavailable",
        "severity": "medium",
        "error_class": "source_unavailable",
        "attempts": failures,
    }]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def freeze_consensus_snapshot(
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    source: str,
    observed_at: str,
    root: Path,
) -> Path:
    observed = _aware_dt(observed_at)
    if observed is None:
        raise ValueError("consensus observed_at must be timezone-aware")
    clean = str(symbol).strip().upper().replace(".US", "")
    payload = {
        "schema_version": "eps_consensus_snapshot.v1",
        "symbol": clean,
        "observed_at": observed.isoformat(),
        "source": source,
        "rows": rows,
        "immutable_point_in_time": True,
        "no_order_execution": True,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:8]
    stamp = observed.strftime("%Y%m%dT%H%M%SZ")
    path = root / clean / f"{stamp}_{digest}.json"
    if not path.exists():
        _atomic_json(path, payload)
    return path


def load_consensus_snapshots(symbol: str, *, root: Path) -> list[dict[str, Any]]:
    clean = str(symbol).strip().upper().replace(".US", "")
    snapshots: list[dict[str, Any]] = []
    for path in sorted((root / clean).glob("*.json")) if (root / clean).exists() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and _aware_dt(payload.get("observed_at")) is not None:
            snapshots.append(payload)
    return snapshots


def compute_beat_rate_last_8(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        (row for row in snapshots if _aware_dt(row.get("observed_at")) is not None),
        key=lambda row: _aware_dt(row.get("observed_at")) or datetime.max.replace(tzinfo=timezone.utc),
    )
    frozen_before_result: dict[str, tuple[datetime, float]] = {}
    outcomes: dict[str, tuple[datetime, bool]] = {}
    for snapshot in ordered:
        observed = _aware_dt(snapshot.get("observed_at"))
        rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
        if observed is None:
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            period = str(row.get("fiscal_period") or "").strip()
            consensus = _finite(row.get("eps_consensus"))
            actual = _finite(row.get("reported_eps"))
            reported_at = _aware_dt(row.get("reported_at"))
            if not period or consensus is None:
                continue
            if actual is None:
                frozen_before_result.setdefault(period, (observed, consensus))
                continue
            prior = frozen_before_result.get(period)
            if prior is None or reported_at is None:
                continue
            if prior[0] < reported_at <= observed and period not in outcomes:
                outcomes[period] = (reported_at, actual > prior[1])
    paired = sorted(outcomes.values(), key=lambda item: item[0])[-8:]
    if len(paired) < 8:
        return {
            "consensus_history": "unavailable" if not paired else "insufficient",
            "beat_rate_last_8": None,
            "beat_rate_sample_count": len(paired),
        }
    return {
        "consensus_history": "available",
        "beat_rate_last_8": sum(1 for _, beat in paired if beat) / 8.0,
        "beat_rate_sample_count": 8,
    }


def collect_consensus_part(
    symbol: str,
    *,
    runner: Callable[..., Any],
    root: Path,
    observed_at: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    rows, source, gaps = fetch_eps_consensus(symbol, runner=runner)
    if not rows or source is None:
        return None, gaps
    path = freeze_consensus_snapshot(
        symbol, rows, source=source, observed_at=observed_at, root=root,
    )
    history = compute_beat_rate_last_8(load_consensus_snapshots(symbol, root=root))
    part = {
        "source": source,
        "observed_at": _aware_dt(observed_at).isoformat(),
        "fiscal_period": rows[0]["fiscal_period"],
        "eps_consensus": rows[0]["eps_consensus"],
        "rows": rows,
        "frozen_snapshot_path": str(path),
        **history,
        "no_retroactive_backfill": True,
    }
    if history["beat_rate_last_8"] is None:
        gaps.append({
            "gap": "consensus history unavailable",
            "impact": "beat_rate_last_8 is null until eight point-in-time pairs accumulate",
            "severity": "medium",
            "error_class": history["consensus_history"],
        })
    return part, gaps


def run_py(code: str, timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [AKSHARE_PYTHON, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_no_proxy_env(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error_class": "timeout", "stderr_tail": ""}
    except FileNotFoundError:
        return {"status": "fail", "error_class": "python_not_found", "stderr_tail": AKSHARE_PYTHON}
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def parse_stdout(res: dict[str, Any]) -> Any | None:
    if res.get("status") != "pass":
        return None
    text = (res.get("stdout") or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text[:800], "parse_error": True}


def latest_record(records: list[Any]) -> Any:
    if not records:
        return None
    if not all(isinstance(x, dict) for x in records):
        return records[-1]
    date_keys = ("REPORT_DATE", "公告日期", "报告期", "日期", "date", "Date", "TRADE_DATE")
    def key_fn(row: dict[str, Any]) -> str:
        for key in date_keys:
            val = row.get(key)
            if val not in (None, ""):
                return str(val)
        return ""
    return max(records, key=key_fn)


def add_part(result: dict[str, Any], key: str, res: dict[str, Any], *, list_tail: bool = True) -> None:
    parsed = parse_stdout(res)
    if parsed is None:
        result["gaps"].append({
            "gap": f"{key} unavailable",
            "impact": "基本面/估值验证缺失",
            "severity": "high" if key in {"financial_indicators", "us_financial_indicators"} else "medium",
            "error_class": res.get("error_class") or ("subprocess_fail" if res.get("status") != "pass" else "empty_or_parse_fail"),
            "stderr_tail": res.get("stderr_tail"),
        })
        return
    if isinstance(parsed, dict) and parsed.get("error"):
        result["gaps"].append({"gap": f"{key} error", "impact": "该基本面子源不可用", "severity": "medium", "error_class": "source_error", "safe_summary": parsed.get("error")})
        return
    if isinstance(parsed, list):
        if not parsed:
            result["gaps"].append({"gap": f"{key} empty", "impact": "该基本面子源无返回", "severity": "medium", "error_class": "empty"})
            return
        result["parts"][key] = {"count": len(parsed), "latest": latest_record(parsed) if list_tail else parsed[:5]}
        return
    if isinstance(parsed, dict):
        if parsed:
            result["parts"][key] = parsed
        else:
            result["gaps"].append({"gap": f"{key} empty", "impact": "该基本面子源无返回", "severity": "medium", "error_class": "empty"})


def finalize(result: dict[str, Any]) -> dict[str, Any]:
    if result["parts"]:
        result["status"] = "partial" if result["gaps"] else "ok"
    else:
        result["status"] = "fail"
        result["gaps"].append({
            "gap": "fundamental parts empty",
            "impact": "不得生成 fundamentals EID；估值/财报无锚",
            "severity": "high",
            "error_class": "empty_fundamentals",
        })
    return result


def a_share_fundamentals(code: str) -> dict[str, Any]:
    result = {"market": "A", "code": code, "fetched_at": now(), "status": "draft", "parts": {}, "gaps": []}
    fin_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_financial_analysis_indicator(symbol='{code}', start_year='2020')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "financial_indicators", run_py(fin_code, timeout=45))
    val_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_zh_valuation_baidu(symbol='{code}', indicator='市盈率(TTM)', period='近一年')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "valuation_pe_ttm", run_py(val_code, timeout=30))
    return finalize(result)


def hk_share_fundamentals(code: str) -> dict[str, Any]:
    hk = code.zfill(5) if code.isdigit() else code
    result = {"market": "HK", "code": hk, "fetched_at": now(), "status": "draft", "parts": {}, "gaps": []}
    fin_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_hk_financial_indicator_em(symbol='{hk}')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "financial_indicators", run_py(fin_code, timeout=45))
    val_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_hk_valuation_baidu(symbol='{hk}', indicator='市盈率(TTM)', period='近一年')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
    add_part(result, "valuation_pe_ttm", run_py(val_code, timeout=30))
    return finalize(result)


def us_share_fundamentals(
    code: str,
    *,
    consensus_runner: Callable[..., Any] = subprocess.run,
    consensus_root: Path | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    sym = code.upper().replace(".US", "")
    result = {
        "market": "US",
        "code": sym,
        "fetched_at": now(),
        "status": "draft",
        "parts": {},
        "regulatory_evidence": [],
        "source_health": [],
        "gaps": [],
        "no_order_execution": True,
    }
    consensus_observed_at = observed_at or result["fetched_at"]

    if collect_company_evidence is not None:
        collected = collect_company_evidence(f"{sym}.US")
        if isinstance(collected, dict):
            collected_parts = collected.get("parts") if isinstance(collected.get("parts"), dict) else {}
            for key in (
                "financial_report",
                "valuation",
                "insider_trades",
                "shareholder_snapshot",
                "institutional_portfolio",
                "sec_companyfacts",
            ):
                if key in collected_parts:
                    result["parts"][key] = collected_parts[key]
            result["regulatory_evidence"] = list(collected.get("regulatory_evidence") or [])
            result["source_health"] = list(collected.get("source_health") or [])
            result["gaps"].extend(collected.get("gaps") or [])
        else:
            result["gaps"].append({
                "gap": "US company evidence returned invalid payload",
                "impact": "LongBridge-first 基本面不可用",
                "severity": "high",
                "error_class": "schema_mismatch",
            })
    else:
        result["gaps"].append({
            "gap": "US company evidence adapter unavailable",
            "impact": "LongBridge-first 基本面不可用",
            "severity": "high",
            "error_class": "adapter_unavailable",
        })

    consensus_part, consensus_gaps = collect_consensus_part(
        sym,
        runner=consensus_runner,
        root=consensus_root or consensus_cache_root(),
        observed_at=consensus_observed_at,
    )
    if consensus_part is not None:
        result["parts"]["consensus"] = consensus_part
    result["gaps"].extend(consensus_gaps)

    # AkShare remains a per-dimension fallback. Do not call it when LongBridge
    # or SEC already supplied the corresponding core dimension.
    if "financial_report" not in result["parts"] and "sec_companyfacts" not in result["parts"]:
        fin_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_financial_us_analysis_indicator_em(symbol='{sym}', indicator='年报')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
        add_part(result, "us_financial_indicators", run_py(fin_code, timeout=45))
    if "valuation" not in result["parts"]:
        val_code = f"""import warnings, json; warnings.filterwarnings('ignore')
import akshare as ak
try:
    df = ak.stock_us_valuation_baidu(symbol='{sym}', indicator='市盈率(TTM)', period='近一年')
    print(df.to_json(orient='records', force_ascii=False, default_handler=str))
except Exception as e:
    print(json.dumps({{"error": str(e)}} , ensure_ascii=False))
"""
        add_part(result, "valuation_pe_ttm", run_py(val_code, timeout=30))
    return finalize(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate fundamental snapshot without false-positive empty EIDs.")
    parser.add_argument("--market", required=True, choices=["A", "HK", "US"])
    parser.add_argument("--code", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.market == "A":
        result = a_share_fundamentals(args.code)
    elif args.market == "HK":
        result = hk_share_fundamentals(args.code)
    else:
        result = us_share_fundamentals(args.code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
