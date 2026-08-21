#!/usr/bin/env python3
"""LongBridge-first, read-only US company evidence with optional SEC fallback.

This module is intentionally stdlib-only. Importing it never reads credentials,
opens a network connection, creates a cache, or invokes a command. SEC access is
fail-closed unless the caller supplies an explicit identity containing a contact
address. LongBridge commands are constrained to a read-only allowlist.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, MutableMapping

SCHEMA_VERSION = "us_company_evidence.v1"
SEC_IDENTITY_ENV = "SEC_EDGAR_IDENTITY"
SEC_ALLOWED_HOSTS = {"data.sec.gov", "www.sec.gov"}
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_MAX_BYTES = 8 * 1024 * 1024
SEC_CACHE_TTL_SECONDS = 6 * 60 * 60
READ_ONLY_LONGBRIDGE_COMMANDS = frozenset(
    {"financial-report", "valuation", "filing", "insider-trades", "investors", "shareholder"}
)
WRITE_TOKENS = frozenset({"order", "submit", "buy", "sell", "cancel", "replace", "deposit", "withdraw"})
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
_ACCESSION_RE = re.compile(r"^(\d{10})-?(\d{2})-?(\d{6})$")
_ARCHIVE_URL_RE = re.compile(r"/Archives/edgar/data/(\d+)/(\d{18})/([^?#]+)", re.I)
_CIK_IN_NAME_RE = re.compile(r"\((\d{1,10})\)\s*\((?:Filer|Issuer|Reporting|Owner)[^)]*\)", re.I)
_FORM_RE = re.compile(r"^\s*([0-9A-Z-]+(?:/A)?)\s+-\s+", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_NOTICE_LINE_RE = re.compile(
    r"^(?:New version .+ is available(?:,.*)?|Release notes:\s+https?://\S+)$",
    re.I,
)
_LAST_SEC_REQUEST_MONOTONIC = 0.0


def resolve_longbridge_bin(explicit: str | None = None, *, runner: Callable[..., Any] = subprocess.run) -> str:
    candidate = explicit or os.environ.get("LONGBRIDGE_BIN") or shutil.which("longbridge")
    if candidate:
        return candidate
    if runner is not subprocess.run:
        return "longbridge"
    raise RuntimeError("LongBridge CLI not found; install `longbridge`, add it to PATH, or set LONGBRIDGE_BIN")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gap(gap: str, error_class: str, impact: str, severity: str = "medium") -> dict[str, Any]:
    return {"gap": gap, "error_class": error_class, "impact": impact, "severity": severity}


def _number(value: Any, *, percent: bool = False) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        if text.endswith("%"):
            percent = True
            text = text[:-1].strip()
        try:
            number = float(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if not (float("-inf") < number < float("inf")):
        return None
    if not percent and number.is_integer() and abs(number) <= 9_007_199_254_740_991:
        return int(number)
    return number


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = html.unescape(_TAG_RE.sub("", str(value))).strip()
    return re.sub(r"\s+", " ", cleaned) or None


def parse_longbridge_json(stdout: str) -> dict[str, Any]:
    """Parse one JSON payload with an optional known LongBridge upgrade notice.

    JSON must be the first non-whitespace output. Arbitrary diagnostic prefaces or
    unknown trailing text fail closed instead of searching for a convenient later
    object and silently accepting a corrupted stream.
    """
    text = stdout or ""
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "[{":
        return {"ok": False, "payload": None, "trailing_output_class": None, "error_class": "unparsable_output"}
    decoder = json.JSONDecoder()
    try:
        payload, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError:
        return {"ok": False, "payload": None, "trailing_output_class": None, "error_class": "unparsable_output"}
    trailing = stripped[end:].strip()
    if not trailing:
        return {"ok": True, "payload": payload, "trailing_output_class": None, "error_class": None}
    lines = [line.strip() for line in trailing.splitlines() if line.strip()]
    if lines and all(_NOTICE_LINE_RE.fullmatch(line) for line in lines):
        return {"ok": True, "payload": payload, "trailing_output_class": "version_notice", "error_class": None}
    return {
        "ok": False,
        "payload": None,
        "trailing_output_class": "unexpected_trailing_output",
        "error_class": "unexpected_trailing_output",
    }


def normalize_accession(value: Any) -> str | None:
    match = _ACCESSION_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    # An arbitrary 18-digit provider snowflake is syntactically indistinguishable
    # from an unhyphenated accession unless the embedded filing year is checked.
    # EDGAR-era accessions use 94-99 or 00..the near-current year; rejecting other
    # values prevents LongBridge's numeric provider id from becoming fake SEC
    # lineage.  One year of clock skew is tolerated for fixtures/pre-filing data.
    accession_year = int(match.group(2))
    current_year = datetime.now(timezone.utc).year % 100
    if not (accession_year >= 94 or accession_year <= current_year + 1):
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _archive_identity(url: str | None) -> tuple[str | None, str | None, str | None]:
    if not url:
        return None, None, None
    match = _ARCHIVE_URL_RE.search(url)
    if not match:
        return None, None, None
    path_cik = match.group(1).zfill(10)
    accession = normalize_accession(match.group(2))
    document_name = Path(match.group(3)).name or None
    return path_cik, accession, document_name


def _issuer_cik(file_name: str | None, fallback: str | None = None) -> str | None:
    match = _CIK_IN_NAME_RE.search(file_name or "")
    if match:
        return match.group(1).zfill(10)
    digits = re.sub(r"\D", "", fallback or "")
    return digits.zfill(10) if 1 <= len(digits) <= 10 else None


def normalize_filing_row(row: dict[str, Any], *, retrieval_path: str = "longbridge_cli") -> dict[str, Any]:
    file_name = _clean_text(row.get("file_name") or row.get("title")) or ""
    form_match = _FORM_RE.match(file_name)
    form_type = form_match.group(1).upper() if form_match else None
    amendment = bool(form_type and form_type.endswith("/A"))
    urls = [str(url) for url in row.get("file_urls", []) if isinstance(url, str) and url.startswith("http")]
    document_url = urls[0] if urls else None
    path_cik, accession, document_name = _archive_identity(document_url)
    issuer_cik = _issuer_cik(file_name, path_cik)
    gaps: list[dict[str, Any]] = []
    if document_url is None:
        gaps.append(_gap("filing document URL missing", "document_url_missing", "无法回源或派生 SEC accession", "high"))
    elif accession is None:
        gaps.append(_gap("SEC accession unavailable from document URL", "accession_missing", "不得把 provider id 当 accession", "high"))
    if form_type is None:
        gaps.append(_gap("filing form type unavailable", "form_type_missing", "无法区分 10-K/10-Q/8-K/Form 4 与修订表单"))
    if issuer_cik is None:
        gaps.append(_gap("issuer CIK unavailable", "issuer_cik_missing", "SEC fallback 无法绑定发行人"))
    underlying = f"sec:{accession}" if accession else None
    return {
        "provider": "longbridge",
        "provider_family": "longbridge",
        "source_family": "sec_edgar" if accession else "unknown_aggregator",
        "provider_id": str(row.get("id")) if row.get("id") not in (None, "") else None,
        "source_kind": "regulatory_filing_index",
        "form_type": form_type,
        "amendment": amendment,
        "issuer_cik": issuer_cik,
        "archive_path_cik": path_cik,
        "accession_number": accession,
        "document_name": document_name,
        "document_url": document_url,
        "document_urls": urls,
        "provider_published_at": row.get("publish_at"),
        "retrieved_at": utc_now(),
        "filed_at": None,
        "report_period": None,
        "underlying_fact_key": underlying,
        "retrieval_paths": [retrieval_path],
        "independent_source_count": 1 if underlying else 0,
        "data_gaps": gaps,
    }


def normalize_financial_report(payload: dict[str, Any]) -> dict[str, Any]:
    indicators: list[dict[str, Any]] = []
    for raw in payload.get("indicators", []) if isinstance(payload.get("indicators"), list) else []:
        if not isinstance(raw, dict) or not raw.get("field_name"):
            continue
        raw_value = raw.get("indicator_value")
        is_percent = isinstance(raw_value, str) and raw_value.strip().endswith("%")
        indicators.append(
            {
                "field_name": str(raw["field_name"]),
                "label": _clean_text(raw.get("indicator_name")),
                "value": _number(raw_value, percent=is_percent),
                "unit": "percent" if is_percent else payload.get("currency"),
                "yoy": _number(raw.get("yoy")),
            }
        )
    gaps = [
        _gap(
            "financial report period basis unavailable",
            "period_basis_unknown",
            "不得把累计财年数据默认为单季数据",
            "high",
        )
    ]
    if not indicators:
        gaps.append(_gap("financial report indicators empty", "empty_financial_report", "不得生成基本面 EID", "high"))
    return {
        "provider": "longbridge",
        "provider_family": "longbridge",
        "source_family": "unknown_aggregator",
        "source_kind": "aggregator_derived",
        "underlying_fact_key": None,
        "currency": payload.get("currency"),
        "report_label": payload.get("report"),
        "report_text": _clean_text(payload.get("report_txt")),
        "report_period": None,
        "period_basis": "unknown",
        "retrieved_at": utc_now(),
        "indicators": indicators,
        "data_gaps": gaps,
    }


def _count_peer_rows(value: Any) -> int:
    """Count omitted peer records without counting structural wrapper lists."""
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            peer_keys = {"counter_id", "ticker", "name", "value"}
            if any(peer_keys.intersection(item) for item in value):
                return len(value)
        return sum(_count_peer_rows(item) for item in value)
    if isinstance(value, dict):
        return sum(_count_peer_rows(item) for item in value.values())
    return 0


def normalize_valuation(payload: dict[str, Any]) -> dict[str, Any]:
    history = payload.get("history") if isinstance(payload.get("history"), dict) else {}
    raw_metrics = history.get("metrics") if isinstance(history.get("metrics"), dict) else {}
    metrics: dict[str, Any] = {}
    for name, raw in raw_metrics.items():
        if not isinstance(raw, dict):
            continue
        history_rows = raw.get("list") if isinstance(raw.get("list"), list) else []
        last = history_rows[-1] if history_rows and isinstance(history_rows[-1], dict) else {}
        metrics[str(name)] = {
            "current": _number(last.get("value")),
            "observed_at_epoch": str(last.get("timestamp")) if last.get("timestamp") not in (None, "") else None,
            "high": _number(raw.get("high")),
            "low": _number(raw.get("low")),
            "median": _number(raw.get("median")),
            "description": _clean_text(raw.get("desc")),
            "history_point_count": len(history_rows),
        }
    layouts = payload.get("layouts")
    return {
        "provider": "longbridge",
        "provider_family": "longbridge",
        "source_family": "longbridge_market_data",
        "source_kind": "aggregator_derived",
        "underlying_fact_key": None,
        "range_years": _number(history.get("range")),
        "retrieved_at": utc_now(),
        "metrics": metrics,
        "omitted_peer_rows": _count_peer_rows(layouts),
        "data_gaps": [] if metrics else [_gap("valuation metrics empty", "empty_valuation", "估值锚不可用", "high")],
    }


def normalize_shareholder_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_rows = payload.get("shareholder_list") if isinstance(payload.get("shareholder_list"), list) else []
    rows: list[dict[str, Any]] = []
    dates: set[str] = set()
    unattributed_count = 0
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        report_date = str(raw.get("report_date")) if raw.get("report_date") else None
        if report_date:
            dates.add(report_date)
        name = _clean_text(raw.get("shareholder_name"))
        unattributed = not bool(name) or str(raw.get("shareholder_id") or "") in {"", "0"} and not name
        unattributed_count += int(unattributed)
        rows.append(
            {
                "shareholder_name": name,
                "shareholder_id": str(raw.get("shareholder_id")) if raw.get("shareholder_id") not in (None, "") else None,
                "report_date": report_date,
                "percent_of_shares": _number(raw.get("percent_of_shares")),
                "shares_changed": _number(raw.get("shares_changed")),
                "institution_type": _clean_text(raw.get("institution_type")),
                "unattributed_holder": unattributed,
            }
        )
    as_of_dates = sorted(dates)
    mixed_period = len(as_of_dates) > 1
    gaps: list[dict[str, Any]] = []
    if mixed_period:
        gaps.append(_gap("shareholder rows mix report periods", "mixed_report_periods", "禁止跨期汇总持股比例", "high"))
    if unattributed_count:
        gaps.append(_gap("shareholder rows contain unattributed holders", "unattributed_holders", "匿名行不得进入聚合"))
    gaps.append(_gap("shareholder provider ordering semantics unknown", "ordering_unknown", "不得把 provider 顺序称为 Top holders"))
    return {
        "provider": "longbridge",
        "provider_family": "longbridge",
        "source_family": "unknown_aggregator",
        "source_kind": "aggregator_derived",
        "underlying_fact_key": None,
        "rows": rows,
        "as_of_dates": as_of_dates,
        "mixed_period": mixed_period,
        "unattributed_holder_count": unattributed_count,
        "aggregation_allowed": False,
        "aggregate_percent_of_shares": None,
        "ordering_semantics": "provider_order_unknown",
        "retrieved_at": utc_now(),
        "data_gaps": gaps,
    }


def normalize_13f_portfolio(payload: dict[str, Any]) -> dict[str, Any]:
    accession = normalize_accession(payload.get("accession_number"))
    holdings = payload.get("holdings") if isinstance(payload.get("holdings"), list) else []
    total = _number(payload.get("total_holdings"))
    total_int = int(total) if isinstance(total, (int, float)) else len(holdings)
    return {
        "provider": "longbridge",
        "provider_family": "longbridge",
        "source_family": "sec_edgar" if accession else "unknown_aggregator",
        "source_kind": "regulatory_filing_index",
        "semantic_role": "institutional_portfolio",
        "is_target_holder_lookup": False,
        "cik": _issuer_cik(None, str(payload.get("cik") or "")),
        "accession_number": accession,
        "filed_at": payload.get("filing_date"),
        "report_period": payload.get("period"),
        "retrieved_at": utc_now(),
        "investor": _clean_text(payload.get("investor") or payload.get("firm")),
        "holdings": holdings,
        "returned_holdings": len(holdings),
        "total_holdings": total_int,
        "truncated": total_int > len(holdings),
        "total_value_usd": _number(payload.get("total_value_usd")),
        "underlying_fact_key": f"sec:{accession}" if accession else None,
        "retrieval_paths": ["longbridge_cli"],
        "independent_source_count": 1 if accession else 0,
        "data_gaps": [] if accession else [_gap("13F accession missing", "accession_missing", "13F 无法与 SEC 原文去重", "high")],
    }


def normalize_insider_payload(payload: Any) -> dict[str, Any]:
    rows = payload if isinstance(payload, list) else payload.get("trades", []) if isinstance(payload, dict) else []
    normalized: list[dict[str, Any]] = []
    regulatory: list[dict[str, Any]] = []
    for raw in rows[:100] if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        url = next((str(raw.get(key)) for key in ("document_url", "filing_url", "url") if raw.get(key)), None)
        _, accession, document_name = _archive_identity(url)
        form = str(raw.get("form_type") or raw.get("form") or "4")
        item = {
            "owner_name": _clean_text(raw.get("owner_name") or raw.get("insider_name") or raw.get("name")),
            "transaction_type": _clean_text(raw.get("transaction_type") or raw.get("transaction_code") or raw.get("type")),
            "transaction_date": raw.get("transaction_date") or raw.get("date"),
            "shares": _number(raw.get("shares") or raw.get("quantity")),
            "price": _number(raw.get("price")),
            "document_url": url,
            "accession_number": accession,
        }
        normalized.append(item)
        if accession:
            regulatory.append(
                {
                    "provider": "longbridge",
                    "provider_family": "longbridge",
                    "source_family": "sec_edgar",
                    "source_kind": "regulatory_filing_index",
                    "form_type": form,
                    "amendment": form.upper().endswith("/A"),
                    "issuer_cik": None,
                    "accession_number": accession,
                    "document_name": document_name,
                    "document_url": url,
                    "document_urls": [url] if url else [],
                    "provider_published_at": raw.get("filing_date") or raw.get("filed_at"),
                    "retrieved_at": utc_now(),
                    "filed_at": None,
                    "report_period": None,
                    "underlying_fact_key": f"sec:{accession}",
                    "retrieval_paths": ["longbridge_cli"],
                    "independent_source_count": 1,
                    "data_gaps": [],
                }
            )
    return {
        "provider": "longbridge",
        "provider_family": "longbridge",
        "source_family": "sec_edgar_derived",
        "source_kind": "sec_form4_derived",
        "rows": normalized,
        "regulatory_evidence": regulatory,
        "data_gaps": [] if normalized else [_gap("insider trades empty", "empty_insider_trades", "不得解释为无内部人交易")],
    }


def normalize_sec_submission_row(row: dict[str, Any], cik: str) -> dict[str, Any]:
    accession = normalize_accession(row.get("accessionNumber"))
    primary = row.get("primaryDocument")
    cik_digits = re.sub(r"\D", "", cik).lstrip("0") or "0"
    accession_digits = re.sub(r"\D", "", accession or "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_digits}/{accession_digits}/{primary}"
        if accession and primary
        else None
    )
    form_type = str(row.get("form") or "").upper() or None
    return {
        "provider": "sec_edgar",
        "provider_family": "sec_edgar",
        "source_family": "sec_edgar",
        "provider_id": None,
        "source_kind": "regulatory_filing",
        "form_type": form_type,
        "amendment": bool(form_type and form_type.endswith("/A")),
        "issuer_cik": re.sub(r"\D", "", cik).zfill(10),
        "archive_path_cik": re.sub(r"\D", "", cik).zfill(10),
        "accession_number": accession,
        "document_name": primary,
        "document_url": url,
        "document_urls": [url] if url else [],
        "provider_published_at": None,
        "retrieved_at": utc_now(),
        "filed_at": row.get("acceptanceDateTime") or row.get("filingDate"),
        "report_period": row.get("reportDate"),
        "underlying_fact_key": f"sec:{accession}" if accession else None,
        "retrieval_paths": ["sec_edgar"],
        "independent_source_count": 1 if accession else 0,
        "data_gaps": [] if accession else [_gap("SEC submission accession missing", "accession_missing", "SEC row 无法稳定去重", "high")],
    }


def _submission_rows(payload: dict[str, Any], cik: str, limit: int = 40) -> list[dict[str, Any]]:
    filings = payload.get("filings") if isinstance(payload.get("filings"), dict) else {}
    recent = filings.get("recent") if isinstance(filings.get("recent"), dict) else {}
    keys = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "primaryDocument")
    columns = {key: value for key, value in recent.items() if key in keys and isinstance(value, list)}
    count = max((len(value) for value in columns.values()), default=0)
    rows = []
    for index in range(min(count, max(0, limit))):
        row = {key: values[index] if index < len(values) else None for key, values in columns.items()}
        rows.append(normalize_sec_submission_row(row, cik))
    return rows


def cik_from_company_tickers(payload: dict[str, Any], symbol: str) -> str | None:
    """Resolve one exact SEC ticker to one CIK; ambiguity fails closed."""
    base = str(symbol or "").upper().strip()
    if base.endswith(".US"):
        base = base[:-3]
    ticker_candidates = {base, base.replace(".", "-"), base.replace("/", "-")}
    matches: set[str] = set()
    for raw in payload.values():
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "").upper().strip()
        if ticker not in ticker_candidates:
            continue
        digits = re.sub(r"\D", "", str(raw.get("cik_str") or ""))
        if 1 <= len(digits) <= 10:
            matches.add(digits.zfill(10))
    return next(iter(matches)) if len(matches) == 1 else None


def dedupe_regulatory_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for source in items:
        item = dict(source)
        key = item.get("underlying_fact_key")
        if not key:
            merged.append(item)
            continue
        current = by_key.get(str(key))
        if current is None:
            item["retrieval_paths"] = list(dict.fromkeys(item.get("retrieval_paths") or []))
            item["independent_source_count"] = 1
            by_key[str(key)] = item
            merged.append(item)
            continue
        current["retrieval_paths"] = list(
            dict.fromkeys([*(current.get("retrieval_paths") or []), *(item.get("retrieval_paths") or [])])
        )
        current["independent_source_count"] = 1
        for field in ("filed_at", "report_period", "issuer_cik", "document_name", "document_url"):
            if current.get(field) in (None, "") and item.get(field) not in (None, ""):
                current[field] = item[field]
        current["document_urls"] = list(
            dict.fromkeys([*(current.get("document_urls") or []), *(item.get("document_urls") or [])])
        )
        current["data_gaps"] = list(current.get("data_gaps") or [])
    return merged


def valid_sec_identity(identity: str | None) -> bool:
    text = str(identity or "").strip()
    return 8 <= len(text) <= 240 and bool(_EMAIL_RE.search(text)) and "\n" not in text and "\r" not in text


def _decode_http_body(body: bytes, encoding: str | None) -> bytes:
    normalized = str(encoding or "").lower()
    if normalized == "gzip":
        return gzip.decompress(body)
    if normalized == "deflate":
        return zlib.decompress(body)
    return body


def _sec_rate_limit() -> None:
    global _LAST_SEC_REQUEST_MONOTONIC
    now_mono = time.monotonic()
    wait = 1.0 - (now_mono - _LAST_SEC_REQUEST_MONOTONIC)
    if wait > 0:
        time.sleep(wait)
    _LAST_SEC_REQUEST_MONOTONIC = time.monotonic()


def _urllib_sec_transport(url: str, *, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    _sec_rate_limit()
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_host = (urllib.parse.urlparse(response.geturl()).hostname or "").lower()
            if final_host not in SEC_ALLOWED_HOSTS:
                return {"status": 0, "body": b"", "headers": {}, "error_class": "redirect_host_blocked"}
            body = response.read(SEC_MAX_BYTES + 1)
            if len(body) > SEC_MAX_BYTES:
                return {"status": 0, "body": b"", "headers": {}, "error_class": "response_too_large"}
            return {"status": int(response.status), "body": body, "headers": dict(response.headers.items())}
    except urllib.error.HTTPError as exc:
        return {"status": int(exc.code), "body": exc.read(4096), "headers": dict(exc.headers.items()) if exc.headers else {}}


def fetch_sec_json(
    url: str,
    *,
    identity: str | None,
    transport: Callable[..., dict[str, Any]] = _urllib_sec_transport,
    cache: MutableMapping[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    """Fetch one official SEC JSON document with bounded, injectable transport."""
    if not valid_sec_identity(identity):
        return {"status": "unavailable", "error_class": "identity_missing", "from_cache": False, "payload": None}
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme != "https" or (parsed_url.hostname or "").lower() not in SEC_ALLOWED_HOSTS:
        return {"status": "fail", "error_class": "source_url_not_allowlisted", "from_cache": False, "payload": None}
    if cache is not None and url in cache:
        return {"status": "ok", "error_class": None, "from_cache": True, "payload": cache[url]}
    headers = {
        "User-Agent": str(identity).strip(),
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    response: dict[str, Any] | None = None
    for attempt in range(2):
        try:
            response = transport(url, headers=headers, timeout=max(1, min(int(timeout), 30)))
        except (OSError, TimeoutError, urllib.error.URLError):
            if attempt == 0:
                continue
            return {"status": "fail", "error_class": "source_unavailable", "from_cache": False, "payload": None}
        status = int(response.get("status") or 0)
        if status not in {500, 502, 503, 504} or attempt == 1:
            break
    assert response is not None
    status = int(response.get("status") or 0)
    error_map = {403: "blocked_identity", 404: "no_such_cik", 429: "rate_limited"}
    if status != 200:
        return {
            "status": "fail",
            "error_class": error_map.get(status, response.get("error_class") or ("source_unavailable" if status >= 500 or status == 0 else "http_error")),
            "http_status": status or None,
            "from_cache": False,
            "payload": None,
        }
    headers_raw = response.get("headers") if isinstance(response.get("headers"), dict) else {}
    encoding = next((value for key, value in headers_raw.items() if str(key).lower() == "content-encoding"), None)
    body = response.get("body")
    if not isinstance(body, (bytes, bytearray)):
        return {"status": "fail", "error_class": "invalid_response_body", "from_cache": False, "payload": None}
    if len(body) > SEC_MAX_BYTES:
        return {"status": "fail", "error_class": "response_too_large", "from_cache": False, "payload": None}
    try:
        payload = json.loads(_decode_http_body(bytes(body), encoding).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, zlib.error):
        return {"status": "fail", "error_class": "invalid_json", "from_cache": False, "payload": None}
    if cache is not None:
        cache[url] = payload
    return {"status": "ok", "error_class": None, "from_cache": False, "payload": payload}


def _cache_file(cache_dir: Path, url: str) -> Path:
    return cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def fetch_sec_json_cached(
    url: str,
    *,
    identity: str | None,
    transport: Callable[..., dict[str, Any]] = _urllib_sec_transport,
    cache_dir: Path | None = None,
    ttl_seconds: int = SEC_CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """Disk-cache wrapper; the cache is created only when this function runs."""
    if not valid_sec_identity(identity):
        return {"status": "unavailable", "error_class": "identity_missing", "from_cache": False, "payload": None}
    cache_root = cache_dir or Path(
        os.environ.get("TRADING_RESEARCH_SEC_CACHE_DIR")
        or Path.home() / ".cache" / "trading-research" / "sec-edgar"
    )
    path = _cache_file(cache_root, url)
    try:
        if path.is_file() and time.time() - path.stat().st_mtime <= max(0, ttl_seconds):
            saved = json.loads(path.read_text(encoding="utf-8"))
            return {"status": "ok", "error_class": None, "from_cache": True, "payload": saved.get("payload")}
    except (OSError, json.JSONDecodeError):
        pass
    result = fetch_sec_json(url, identity=identity, transport=transport, cache=None)
    if result.get("status") == "ok":
        try:
            cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps({"fetched_at": utc_now(), "payload": result["payload"]}, ensure_ascii=False), encoding="utf-8")
            os.chmod(temp, 0o600)
            temp.replace(path)
        except OSError:
            result = dict(result)
            result["cache_write_status"] = "fail"
    return result


def build_longbridge_commands(symbol: str) -> list[list[str]]:
    normalized = symbol.upper().strip()
    if not normalized.endswith(".US"):
        normalized = f"{normalized}.US"
    commands = [
        ["financial-report", normalized, "--latest", "--format", "json"],
        ["valuation", normalized, "--format", "json"],
        ["filing", normalized, "--count", "20", "--format", "json"],
        ["insider-trades", normalized, "--count", "20", "--format", "json"],
        ["shareholder", normalized, "--format", "json"],
    ]
    for command in commands:
        if command[0] not in READ_ONLY_LONGBRIDGE_COMMANDS or any(token.lower() in WRITE_TOKENS for token in command):
            raise ValueError("non-read-only LongBridge command rejected")
    return commands


def build_investor_commands(cik: str, *, top: int = 50) -> list[list[str]]:
    digits = re.sub(r"\D", "", cik)
    if not digits:
        raise ValueError("investor CIK is required")
    return [["investors", digits.zfill(10), "--top", str(max(1, min(top, 100))), "--format", "json"]]


def run_longbridge_json(
    args: list[str],
    *,
    longbridge_bin: str | None = None,
    timeout: int = 45,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if not args or args[0] not in READ_ONLY_LONGBRIDGE_COMMANDS or any(token.lower() in WRITE_TOKENS for token in args):
        return {"status": "fail", "error_class": "command_not_allowlisted", "payload": None}
    command = [resolve_longbridge_bin(longbridge_bin, runner=runner), *args]
    try:
        proc = runner(command, capture_output=True, text=True, timeout=max(1, min(timeout, 60)))
    except FileNotFoundError:
        return {"status": "fail", "error_class": "command_not_found", "payload": None}
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error_class": "timeout", "payload": None}
    except OSError:
        return {"status": "fail", "error_class": "source_unavailable", "payload": None}
    if int(proc.returncode) != 0:
        return {"status": "fail", "error_class": "subprocess_fail", "returncode": int(proc.returncode), "payload": None}
    parsed = parse_longbridge_json(proc.stdout or "")
    return {
        "status": "ok" if parsed["ok"] else "fail",
        "error_class": parsed.get("error_class"),
        "trailing_output_class": parsed.get("trailing_output_class"),
        "payload": parsed.get("payload"),
    }


def _source_health(source: str, result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("payload")
    if isinstance(payload, list):
        safe_summary: Any = {"payload_type": "list", "item_count": len(payload)}
    elif isinstance(payload, dict):
        safe_summary = {
            "payload_type": "object",
            "top_level_keys": sorted(str(key) for key in payload)[:12],
        }
    else:
        safe_summary = {"payload_type": type(payload).__name__ if payload is not None else "none"}
    return {
        "source": source,
        "provider_family": "longbridge",
        "source_family": "longbridge",
        "status": result.get("status"),
        "error_class": result.get("error_class"),
        "checked_at": utc_now(),
        "safe_summary": safe_summary,
    }


def sec_source_health(source: str, result: dict[str, Any]) -> dict[str, Any]:
    """Project SEC fetch state into SourceHealth without retaining raw payloads."""
    payload = result.get("payload")
    if isinstance(payload, list):
        safe_summary: Any = {"payload_type": "list", "item_count": len(payload)}
    elif isinstance(payload, dict):
        safe_summary = {
            "payload_type": "object",
            "top_level_keys": sorted(str(key) for key in payload)[:12],
        }
    else:
        safe_summary = {"payload_type": type(payload).__name__ if payload is not None else "none"}
    return {
        "source": source,
        "provider_family": "sec_edgar",
        "source_family": "sec_edgar",
        "status": result.get("status"),
        "error_class": result.get("error_class"),
        "checked_at": utc_now(),
        "from_cache": bool(result.get("from_cache", False)),
        "safe_summary": safe_summary,
    }


def collect_company_evidence(
    symbol: str,
    *,
    longbridge_bin: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
    sec_identity: str | None = None,
    sec_transport: Callable[..., dict[str, Any]] = _urllib_sec_transport,
    sec_cache_dir: Path | None = None,
    include_sec: bool = True,
    investor_cik: str | None = None,
) -> dict[str, Any]:
    """Collect bounded, read-only US company evidence.

    `investor_cik` is explicit because `longbridge investors <CIK>` describes an
    institution's 13F portfolio; it is not a target-company holder lookup.
    """
    normalized_symbol = symbol.upper().strip()
    if not normalized_symbol.endswith(".US"):
        normalized_symbol = f"{normalized_symbol}.US"
    parts: dict[str, Any] = {}
    gaps: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    regulatory: list[dict[str, Any]] = []
    for args in build_longbridge_commands(normalized_symbol):
        result = run_longbridge_json(args, longbridge_bin=longbridge_bin, timeout=45, runner=runner)
        endpoint = args[0]
        endpoint_health = _source_health(f"longbridge:{endpoint}", result)
        health.append(endpoint_health)
        if result.get("status") != "ok":
            gaps.append(_gap(f"LongBridge {endpoint} unavailable", str(result.get("error_class") or "source_unavailable"), "对应公司证据维度缺失", "high" if endpoint in {"financial-report", "filing"} else "medium"))
            continue
        payload = result.get("payload")
        if endpoint == "financial-report" and isinstance(payload, dict):
            normalized = normalize_financial_report(payload)
            gaps.extend(normalized.get("data_gaps") or [])
            if normalized.get("indicators"):
                parts["financial_report"] = normalized
            else:
                endpoint_health.update({"status": "fail", "error_class": "empty_payload"})
        elif endpoint == "valuation" and isinstance(payload, dict):
            normalized = normalize_valuation(payload)
            gaps.extend(normalized.get("data_gaps") or [])
            if normalized.get("metrics"):
                parts["valuation"] = normalized
            else:
                endpoint_health.update({"status": "fail", "error_class": "empty_payload"})
        elif endpoint == "filing" and isinstance(payload, list):
            rows = [normalize_filing_row(row) for row in payload if isinstance(row, dict)]
            for row in rows:
                gaps.extend(row.get("data_gaps") or [])
            if rows:
                parts["regulatory_filings"] = rows
                regulatory.extend(rows)
            else:
                endpoint_health.update({"status": "fail", "error_class": "empty_payload"})
                gaps.append(_gap("LongBridge filing payload empty", "empty_payload", "不得解释为无监管申报", "high"))
        elif endpoint == "insider-trades":
            normalized = normalize_insider_payload(payload)
            regulatory.extend(normalized.get("regulatory_evidence") or [])
            gaps.extend(normalized.get("data_gaps") or [])
            if normalized.get("rows"):
                parts["insider_trades"] = normalized
            else:
                endpoint_health.update({"status": "fail", "error_class": "empty_payload"})
        elif endpoint == "shareholder" and isinstance(payload, dict):
            normalized = normalize_shareholder_payload(payload)
            gaps.extend(normalized.get("data_gaps") or [])
            if normalized.get("rows"):
                parts["shareholder_snapshot"] = normalized
            else:
                endpoint_health.update({"status": "fail", "error_class": "empty_payload"})
                gaps.append(_gap("LongBridge shareholder payload empty", "empty_payload", "不得解释为无股东数据"))
        else:
            endpoint_health.update({"status": "fail", "error_class": "schema_mismatch"})
            gaps.append(_gap(f"LongBridge {endpoint} payload shape unsupported", "schema_mismatch", "对应维度不生成证据", "high"))
    if investor_cik:
        args = build_investor_commands(investor_cik)[0]
        result = run_longbridge_json(args, longbridge_bin=longbridge_bin, timeout=45, runner=runner)
        health.append(_source_health("longbridge:investors", result))
        if result.get("status") == "ok" and isinstance(result.get("payload"), dict):
            portfolio = normalize_13f_portfolio(result["payload"])
            parts["institutional_portfolio"] = portfolio
            gaps.extend(portfolio.get("data_gaps") or [])
            if portfolio.get("underlying_fact_key"):
                regulatory.append(portfolio)
        else:
            gaps.append(_gap("LongBridge investors unavailable", str(result.get("error_class") or "source_unavailable"), "指定机构 13F 组合缺失"))
    regulatory = dedupe_regulatory_evidence(regulatory)
    cik = next((row.get("issuer_cik") for row in regulatory if row.get("issuer_cik")), None)
    need_sec_filings = not regulatory or any(not row.get("filed_at") for row in regulatory)
    need_sec_financials = "financial_report" not in parts
    if include_sec and (need_sec_filings or need_sec_financials):
        identity = sec_identity if sec_identity is not None else os.environ.get(SEC_IDENTITY_ENV)
        if not valid_sec_identity(identity):
            health.append(sec_source_health("sec_edgar", {
                "status": "unavailable",
                "error_class": "identity_missing",
                "from_cache": False,
                "payload": None,
            }))
            gaps.append(_gap("SEC EDGAR fallback unavailable", "identity_missing", "不得匿名请求；现有 LongBridge 证据按已知元数据使用"))
        else:
            if not cik:
                ticker_result = fetch_sec_json_cached(
                    SEC_TICKER_MAP_URL,
                    identity=identity,
                    transport=sec_transport,
                    cache_dir=sec_cache_dir,
                )
                health.append(sec_source_health("sec_edgar:company_tickers", ticker_result))
                if ticker_result.get("status") == "ok" and isinstance(ticker_result.get("payload"), dict):
                    cik = cik_from_company_tickers(ticker_result["payload"], normalized_symbol)
                    if not cik:
                        gaps.append(_gap("SEC ticker identity unresolved", "issuer_cik_missing", "无法绑定 submissions/companyfacts", "high"))
                else:
                    gaps.append(_gap("SEC ticker map unavailable", str(ticker_result.get("error_class") or "source_unavailable"), "无法解析 issuer CIK", "high"))

            if cik and need_sec_filings:
                submissions_url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
                sec_result = fetch_sec_json_cached(
                    submissions_url,
                    identity=identity,
                    transport=sec_transport,
                    cache_dir=sec_cache_dir,
                )
                health.append(sec_source_health("sec_edgar:submissions", sec_result))
                if sec_result.get("status") == "ok" and isinstance(sec_result.get("payload"), dict):
                    sec_rows = _submission_rows(sec_result["payload"], str(cik))
                    regulatory = dedupe_regulatory_evidence([*regulatory, *sec_rows])
                    parts["sec_submissions"] = {"issuer_cik": str(cik), "filing_count": len(sec_rows)}
                else:
                    gaps.append(_gap("SEC EDGAR submissions fallback unavailable", str(sec_result.get("error_class") or "source_unavailable"), "filing/filed_at/修订状态未由 SEC 直连确认", "high"))

            if cik and need_sec_financials:
                facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json"
                facts_result = fetch_sec_json_cached(
                    facts_url,
                    identity=identity,
                    transport=sec_transport,
                    cache_dir=sec_cache_dir,
                )
                health.append(sec_source_health("sec_edgar:companyfacts", facts_result))
                if facts_result.get("status") == "ok" and isinstance(facts_result.get("payload"), dict):
                    normalized = normalize_sec_companyfacts(facts_result["payload"])
                    parts["sec_companyfacts"] = normalized
                    gaps.extend(normalized.get("data_gaps") or [])
                else:
                    gaps.append(_gap("SEC companyfacts fallback unavailable", str(facts_result.get("error_class") or "source_unavailable"), "XBRL 基本面 fallback 缺失", "high"))
    parts["regulatory_filings"] = regulatory
    material_parts = [key for key in parts if key not in {"regulatory_filings", "sec_submissions"}]
    status = "ok" if material_parts and not gaps else "partial" if material_parts or regulatory else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "market": "US",
        "symbol": normalized_symbol,
        "fetched_at": utc_now(),
        "status": status,
        "parts": parts,
        "regulatory_evidence": regulatory,
        "source_health": health,
        "gaps": gaps,
        "no_order_execution": True,
    }


def normalize_sec_companyfacts(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep a bounded set of latest SEC XBRL facts with accession-level lineage."""
    us_gaap = payload.get("facts", {}).get("us-gaap", {}) if isinstance(payload.get("facts"), dict) else {}
    tags = {
        "Revenues": "operating_revenue",
        "RevenueFromContractWithCustomerExcludingAssessedTax": "operating_revenue",
        "NetIncomeLoss": "net_income",
        "Assets": "total_assets",
        "Liabilities": "total_liabilities",
        "EarningsPerShareDiluted": "diluted_eps",
    }
    indicators: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for tag, field in tags.items():
        if field in seen_fields:
            continue
        fact = us_gaap.get(tag)
        if not isinstance(fact, dict) or not isinstance(fact.get("units"), dict):
            continue
        candidates = [row for rows in fact["units"].values() if isinstance(rows, list) for row in rows if isinstance(row, dict)]
        candidates = [row for row in candidates if normalize_accession(row.get("accn")) and row.get("val") is not None]
        if not candidates:
            continue
        latest = max(candidates, key=lambda row: str(row.get("filed") or ""))
        accession = normalize_accession(latest.get("accn"))
        indicators.append(
            {
                "field_name": field,
                "xbrl_tag": tag,
                "value": _number(latest.get("val")),
                "unit": next((unit for unit, rows in fact["units"].items() if latest in rows), None),
                "period_start": latest.get("start"),
                "period_end": latest.get("end"),
                "filed_at": latest.get("filed"),
                "form_type": latest.get("form"),
                "accession_number": accession,
                "underlying_fact_key": f"sec:{accession}#{tag}" if accession else None,
            }
        )
        seen_fields.add(field)
    return {
        "provider": "sec_edgar",
        "source_kind": "regulatory_xbrl",
        "issuer_cik": _issuer_cik(None, str(payload.get("cik") or "")),
        "entity_name": _clean_text(payload.get("entityName")),
        "indicators": indicators,
        "data_gaps": [] if indicators else [_gap("SEC companyfacts supported metrics empty", "empty_companyfacts", "XBRL fallback 不生成基本面 EID", "high")],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect read-only US company evidence from LongBridge with optional SEC fallback.")
    parser.add_argument("symbol")
    parser.add_argument("--investor-cik", help="Explicit institutional CIK; returns that institution's 13F portfolio, not target holders.")
    parser.add_argument("--no-sec", action="store_true", help="Disable SEC fallback even when a dimension is missing.")
    parser.add_argument("--json", action="store_true", help="Accepted for compatibility; output is always JSON.")
    args = parser.parse_args()
    result = collect_company_evidence(args.symbol, include_sec=not args.no_sec, investor_cik=args.investor_cik)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ok", "partial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
