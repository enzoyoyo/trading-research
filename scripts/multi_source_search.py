#!/usr/bin/env python3
"""Stateless multi-source search fallback for trading-research.

Search results are discovery candidates, never verified evidence. The script does
not read credentials/cookies, persist state, create alerts, or execute orders.
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable

USER_AGENT = "Mozilla/5.0 (compatible; trading-research-discovery/2.38; +local-readonly)"
PROFILES = {"general", "news", "filing", "rumor", "hot", "dividend"}
MARKETS = {"A", "HK", "US", "unknown"}
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "spm"}
NAV_TITLE_PATTERNS = ("看看元宝怎么说", "元宝电脑版", "百度一下", "相关搜索", "search settings", "privacy policy")
SENSITIVE_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{10,})"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|secret)\s*[:=]"),
    re.compile(r"(?i)\b(localhost|127\.0\.0\.1|::1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b"),
    re.compile(r"(?i)(\.internal\b|\.local\b|file://|/Users/|/home/|confidential|restricted material|未公开|未披露|内部交易计划|内部主机|身份证号|银行卡号)"),
    re.compile(r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])"),
    re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
]


@dataclass(frozen=True)
class EngineSpec:
    name: str
    family: str
    region: str
    parser: str
    url_template: str
    method: str = "GET"
    # Keyed providers are opt-in only: they cost quota and require an operator-supplied
    # credential, so they never appear in default_engines().
    auth_env: str | None = None
    # Doubao Global rejects multi-word/operator syntax and truncates past 100 chars, so a
    # keyed provider can declare that it must receive the raw user query, not the
    # operator-expanded one. Sending the expanded query would silently degrade recall.
    query_mode: str = "expanded"
    max_query_chars: int | None = None


ENGINES: dict[str, EngineSpec] = {
    "google_news": EngineSpec("google_news", "google", "global", "rss", "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"),
    "google_news_zh": EngineSpec("google_news_zh", "google", "cn", "rss", "https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    "bing_news": EngineSpec("bing_news", "bing", "global", "rss", "https://www.bing.com/news/search?q={query}&format=rss"),
    "bing_web": EngineSpec("bing_web", "bing", "global", "rss", "https://www.bing.com/search?q={query}&format=rss"),
    "duckduckgo": EngineSpec("duckduckgo", "duckduckgo", "global", "html", "https://html.duckduckgo.com/html/?q={query}"),
    "brave": EngineSpec("brave", "brave", "global", "html", "https://search.brave.com/search?q={query}&source=web"),
    "baidu": EngineSpec("baidu", "baidu", "cn", "html", "https://www.baidu.com/s?wd={query}"),
    "sogou": EngineSpec("sogou", "sogou", "cn", "html", "https://www.sogou.com/web?query={query}"),
    "sogou_wechat": EngineSpec("sogou_wechat", "sogou", "cn", "html", "https://wx.sogou.com/weixin?type=2&query={query}"),
    "so360": EngineSpec("so360", "so360", "cn", "html", "https://www.so.com/s?q={query}"),
    "doubao": EngineSpec(
        "doubao",
        "volcengine",
        "global",
        "doubao_json",
        "https://open.feedcoopapi.com/search_api/global_search",
        method="POST",
        auth_env="VOLC_DOUBAO_SEARCH_API_KEY",
        query_mode="raw",
        max_query_chars=100,
    ),
}

KEYED_ENGINES = {name for name, spec in ENGINES.items() if spec.auth_env}
# Only search credentials belong here. The layer must never be able to read broker,
# exchange, messaging or model credentials, so the loader refuses every other key name.
ALLOWED_CREDENTIAL_ENV = {spec.auth_env for spec in ENGINES.values() if spec.auth_env}
CREDENTIAL_FILE = Path(
    os.environ.get("TRADING_RESEARCH_SEARCH_ENV") or (Path.home() / ".config" / "trading-research" / "search.env")
)
DOUBAO_ERROR_HINTS = {
    10400: "invalid_request_parameter",
    10403: "account_or_permission_error",
    10408: "feature_unavailable_for_account",
    10409: "billing_mode_unsupported_post_paid_only",
    10410: "search_plan_not_enabled",
    10412: "search_quota_exhausted",
    10500: "provider_internal_error",
    10501: "provider_free_quota_dependency_failed",
    700429: "rate_limited_5qps",
    700901: "invalid_api_key",
}
# Errors that will not clear on retry within a run; distinguishing them from transient
# faults keeps DataGaps honest instead of blaming the network for a config problem.
DOUBAO_CONFIG_ERRORS = {10403, 10408, 10409, 10410, 10412, 700901}


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._parts: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            title = normalize_text(" ".join(self._parts))
            self.anchors.append((self._href, title))
            self._href = None
            self._parts = []


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def strip_tags(value: str) -> str:
    return normalize_text(re.sub(r"<[^>]+>", " ", value or ""))


def detect_language(query: str) -> str:
    cjk = len(re.findall(r"[\u3400-\u9fff]", query))
    letters = len(re.findall(r"[A-Za-z]", query))
    return "zh" if cjk >= max(1, letters // 3) else "en"


def query_is_sensitive(query: str) -> tuple[bool, list[str]]:
    reasons = [pattern.pattern for pattern in SENSITIVE_PATTERNS if pattern.search(query)]
    return bool(reasons), reasons


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse only `KEY=value` lines whose KEY is an allow-listed search credential.

    Deliberately not a general dotenv parser: unknown keys are dropped rather than
    returned, so a shared env file cannot leak broker/exchange secrets into this layer.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key not in ALLOWED_CREDENTIAL_ENV:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            values[key] = value
    return values


def load_search_credential(env_name: str, credential_file: Path | None = None) -> tuple[str | None, dict[str, Any]]:
    """Resolve one allow-listed search credential; never returns the secret in diagnostics."""
    if env_name not in ALLOWED_CREDENTIAL_ENV:
        return None, {"source": "rejected", "reason": "env_name_not_allow_listed", "env_name": env_name}
    from_process = os.environ.get(env_name)
    if from_process:
        return from_process, {"source": "process_env", "env_name": env_name}
    path = credential_file if credential_file is not None else CREDENTIAL_FILE
    try:
        info = path.stat()
    except OSError:
        return None, {"source": "missing", "reason": "credential_file_absent", "env_name": env_name, "path": str(path)}
    # World/group-readable secrets are a real leak, not a style nit: fail closed and tell
    # the operator the exact fix instead of silently using the key.
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return None, {
            "source": "blocked",
            "reason": "credential_file_permissions_too_open",
            "env_name": env_name,
            "path": str(path),
            "mode": oct(info.st_mode & 0o777),
            "remediation": f"chmod 600 {path}",
        }
    try:
        parsed = _parse_env_file(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return None, {"source": "error", "reason": type(exc).__name__, "env_name": env_name, "path": str(path)}
    value = parsed.get(env_name)
    if not value:
        return None, {"source": "missing", "reason": "key_not_set_in_credential_file", "env_name": env_name, "path": str(path)}
    return value, {"source": "credential_file", "env_name": env_name, "path": str(path)}


def parse_publication_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def freshness_state(published_at: str | None, observed_at: str, freshness_hours: int | None) -> str:
    if freshness_hours is None:
        return "not_required"
    published = parse_publication_time(published_at)
    observed = parse_publication_time(observed_at)
    if published is None or observed is None:
        return "unknown"
    if published > observed:
        return "future_timestamp"
    return "within_window" if published >= observed - timedelta(hours=freshness_hours) else "stale"


def normalize_market(value: str | None) -> str:
    raw = (value or "unknown").upper()
    aliases = {"CN": "A", "ASHARE": "A", "A-SHARE": "A", "HKG": "HK", "USA": "US"}
    normalized = aliases.get(raw, raw)
    return normalized if normalized in MARKETS else "unknown"


def expand_query(query: str, profile: str, market: str, language: str) -> str:
    if profile == "general":
        return query
    if profile == "news":
        return f"{query} {'最新 新闻 催化 风险' if language == 'zh' else 'latest news catalyst risk'}"
    if profile == "rumor":
        terms = "并购 传闻 内部人 评级 调查 融资" if language == "zh" else 'acquisition rumor insider "analyst action" investigation financing'
        return f"{query} {terms}"
    if profile == "hot":
        terms = "异动 放量 热点 活跃" if language == "zh" else 'movers "unusual volume" trending "most active"'
        return f"{query} {terms}"
    if profile == "dividend":
        terms = "股息 分红率 自由现金流 覆盖 除息" if language == "zh" else 'dividend payout "free cash flow" coverage ex-date'
        return f"{query} {terms}"
    if market == "US":
        return f"{query} (site:sec.gov OR site:investor.*) (10-K OR 10-Q OR 8-K OR filing)"
    if market == "HK":
        return f"{query} (site:hkexnews.hk OR site:hkex.com.hk) 公告 filing"
    if market == "A":
        return f"{query} (site:cninfo.com.cn OR site:sse.com.cn OR site:szse.cn) 公告"
    return f"{query} official filing announcement"


def default_engines(market: str, language: str, profile: str) -> list[str]:
    if language == "zh" or market in {"A", "HK"}:
        if profile == "filing":
            return ["bing_web", "google_news_zh", "baidu", "sogou"]
        return ["google_news_zh", "bing_news", "bing_web", "baidu", "sogou_wechat"]
    if profile == "filing":
        return ["bing_web", "google_news", "duckduckgo", "brave"]
    return ["google_news", "bing_news", "bing_web", "duckduckgo", "brave"]


def build_plan(
    query: str,
    profile: str,
    market: str,
    engines: list[str] | None = None,
    freshness_hours: int | None = None,
    fallback_engines: list[str] | None = None,
    min_candidates: int = 3,
) -> dict[str, Any]:
    if freshness_hours is not None and not 1 <= freshness_hours <= 8760:
        raise ValueError("freshness_hours must be within 1..8760")
    if min_candidates < 0:
        raise ValueError("min_candidates must be >= 0")
    language = detect_language(query)
    selected = engines or default_engines(market, language, profile)
    # Keyed providers consume paid quota, so they stay out of the primary tier unless the
    # operator names them explicitly; otherwise they are reserved for the fallback tier.
    fallback = sorted(KEYED_ENGINES) if fallback_engines is None else list(fallback_engines)
    unknown = [name for name in list(selected) + list(fallback) if name not in ENGINES]
    if unknown:
        raise ValueError(f"unknown_engine:{','.join(unknown)}")
    # Validate before de-duplicating against the primary tier, otherwise an invalid
    # non-keyed fallback engine would be silently dropped instead of rejected.
    non_keyed_fallback = [name for name in fallback if name not in KEYED_ENGINES]
    if non_keyed_fallback:
        raise ValueError(f"fallback_requires_keyed_engine:{','.join(non_keyed_fallback)}")
    fallback = [name for name in fallback if name not in selected]
    expanded = expand_query(query, profile, market, language)
    sensitive, reasons = query_is_sensitive(query)
    effective_freshness = 72 if freshness_hours is None and profile == "news" else freshness_hours
    return {
        "query": query,
        "expanded_query": expanded,
        "profile": profile,
        "market": market,
        "language": language,
        "selected_engines": selected,
        "fallback_engines": fallback,
        "min_candidates": min_candidates,
        "provider_families": sorted({ENGINES[name].family for name in selected}),
        "fallback_provider_families": sorted({ENGINES[name].family for name in fallback}),
        "sensitive_query": sensitive,
        "sensitive_reasons": reasons,
        "freshness_hours": effective_freshness,
        "external_transmission": "blocked_sensitive" if sensitive else "requires_explicit_allow",
    }


def decode_duckduckgo_redirect(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    target = params.get("uddg", [None])[0]
    return urllib.parse.unquote(target) if target else url


def canonicalize_url(url: str, base_url: str = "") -> str:
    value = html.unescape((url or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("/") and base_url:
        value = urllib.parse.urljoin(base_url, value)
    if "duckduckgo.com/l/" in value or "duckduckgo.com/l/?" in value:
        value = decode_duckduckgo_redirect(value)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    kept = []
    for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_KEYS:
            continue
        kept.append((key, val))
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", urllib.parse.urlencode(kept), ""))


def redirect_unresolved(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    return (
        host.endswith("news.google.com")
        or ("bing.com" in host and "apiclick" in path)
        or ("baidu.com" in host and "link" in path)
        or ("sogou.com" in host and ("link" in path or "weixin" in path))
        or (host.endswith("so.com") and "link" in path)
    )


def parse_rss(payload: bytes, engine: EngineSpec, limit: int) -> list[dict[str, Any]]:
    root = ET.fromstring(payload)
    items: list[dict[str, Any]] = []
    for node in root.findall(".//item")[:limit]:
        title = normalize_text(node.findtext("title") or "")
        link = canonicalize_url(node.findtext("link") or "")
        if not title or not link:
            continue
        items.append({
            "title": title,
            "url": link,
            "snippet": strip_tags(node.findtext("description") or ""),
            "published_at": normalize_text(node.findtext("pubDate") or "") or None,
            "redirect_unresolved": redirect_unresolved(link),
        })
    return items


def own_host_result_allowed(engine: EngineSpec, url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    if engine.name == "baidu":
        return "baidu.com" not in host or "link" in path
    if engine.name in {"sogou", "sogou_wechat"}:
        return "sogou.com" not in host or "link" in path or "weixin" in path
    if engine.name == "so360":
        return not host.endswith("so.com") or "link" in path
    blocked = ("duckduckgo.com", "brave.com")
    return not any(domain in host for domain in blocked)


def parse_html(payload: bytes, engine: EngineSpec, limit: int) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="replace")
    parser = AnchorParser()
    parser.feed(text)
    base = engine.url_template.split("?", 1)[0]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, title in parser.anchors:
        if len(title) < 8 or any(pattern in title.lower() for pattern in NAV_TITLE_PATTERNS):
            continue
        url = canonicalize_url(href, base)
        if not url or url in seen or not own_host_result_allowed(engine, url):
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "snippet": "", "published_at": None, "redirect_unresolved": redirect_unresolved(url)})
        if len(items) >= limit:
            break
    return items


def parse_doubao_publish_time(value: str | None) -> str | None:
    """Normalize Doubao PublishTime into an RFC/ISO string the freshness gate understands."""
    raw = normalize_text(value or "")
    if not raw:
        return None
    if raw.isdigit():
        # Epoch seconds/milliseconds are the only numeric forms the API is documented to
        # emit; anything else is left unparsed so freshness stays `unknown` rather than wrong.
        try:
            number = int(raw)
        except ValueError:
            return None
        if len(raw) == 13:
            number //= 1000
        elif len(raw) != 10:
            return None
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    normalized = raw.replace("/", "-")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(normalized, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return parsed.isoformat().replace("+00:00", "Z")
    return raw


class DoubaoProviderError(RuntimeError):
    """Provider-reported failure carrying the documented Doubao error code."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = DOUBAO_ERROR_HINTS.get(code or -1, "provider_error")
        self.config_error = code in DOUBAO_CONFIG_ERRORS


def parse_doubao(payload: bytes, engine: EngineSpec, limit: int) -> list[dict[str, Any]]:
    try:
        body = json.loads(payload.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise DoubaoProviderError(None, f"invalid_json:{exc.msg}") from exc
    if not isinstance(body, dict):
        raise DoubaoProviderError(None, "invalid_payload_shape")
    metadata = body.get("ResponseMetadata")
    if isinstance(metadata, dict):
        error = metadata.get("Error")
        if isinstance(error, dict) and (error.get("Code") or error.get("CodeN") or error.get("Message")):
            code = error.get("CodeN") if isinstance(error.get("CodeN"), int) else None
            if code is None:
                try:
                    code = int(str(error.get("Code")))
                except (TypeError, ValueError):
                    code = None
            raise DoubaoProviderError(code, normalize_text(str(error.get("Message") or "provider_error")))
    result = body.get("Result")
    if not isinstance(result, dict):
        # HTTP 200 with Result=null means the provider refused; never report it as success.
        raise DoubaoProviderError(None, "result_missing")
    error_code = result.get("ErrorCode")
    if isinstance(error_code, int) and error_code != 0:
        raise DoubaoProviderError(error_code, normalize_text(str(result.get("ErrorMsg") or "provider_error")))
    documents = result.get("Documents")
    if not isinstance(documents, list):
        return []
    items: list[dict[str, Any]] = []
    for document in documents[:limit]:
        if not isinstance(document, dict):
            continue
        title = normalize_text(str(document.get("Title") or ""))
        url = canonicalize_url(str(document.get("Url") or ""))
        if not title or not url:
            continue
        snippets: list[str] = []
        raw_snippets = document.get("Snippet")
        if isinstance(raw_snippets, list):
            for snippet in raw_snippets:
                # Image snippets carry no verifiable claim text; discovery only consumes text.
                if isinstance(snippet, dict) and snippet.get("Type") == "text":
                    text = normalize_text(str(snippet.get("Text") or ""))
                    if text:
                        snippets.append(text)
        document_info = document.get("DocumentInfo")
        if not isinstance(document_info, dict):
            document_info = {}
        host_info = document.get("HostInfo")
        if not isinstance(host_info, dict):
            host_info = {}
        items.append({
            "title": title,
            "url": url,
            "snippet": " ".join(snippets)[:2000],
            "published_at": parse_doubao_publish_time(document_info.get("PublishTime")),
            "redirect_unresolved": redirect_unresolved(url),
            # HostInfo.Hostname is a human-readable site label (e.g. 抖音百科), not a domain,
            # so it is kept as provider metadata and never used for host-based trust checks.
            "provider_site_label": normalize_text(str(host_info.get("Hostname") or "")) or None,
            "filetype": normalize_text(str(document_info.get("Filetype") or "")) or None,
        })
    return items


def fetch_json_post(url: str, timeout: float, attempts: int, body: dict[str, Any], token: str) -> tuple[bytes, int]:
    """POST a JSON search request with a Bearer credential.

    The credential is only ever placed in the Authorization header of this one request:
    it is not logged, echoed into diagnostics, or persisted anywhere.
    """
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(4_000_000), int(response.status)
        except urllib.error.HTTPError as exc:
            # 401/403/429 are credential/quota states, not transient faults: never retry
            # them, so the layer cannot look like it is hammering a rate limit.
            if exc.code in {401, 403, 429} or exc.code < 500 or attempt + 1 >= attempts:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
        time.sleep(1.0)
    raise last_error or RuntimeError("fetch_failed")


def fetch_bytes(url: str, timeout: float, attempts: int = 2) -> tuple[bytes, int]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,text/html;q=0.9,*/*;q=0.5"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(2_000_000), int(response.status)
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429} or exc.code < 500 or attempt + 1 >= attempts:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
        time.sleep(1.0)
    raise last_error or RuntimeError("fetch_failed")


def engine_query(engine: EngineSpec, plan: dict[str, Any]) -> str:
    """Pick the query form a provider can actually consume.

    Doubao Global rejects operator syntax and truncates past 100 chars, so feeding it the
    expanded query would quietly return junk. Raw-mode providers get the user's query.
    """
    query = str(plan["query"] if engine.query_mode == "raw" else plan["expanded_query"])
    if engine.max_query_chars:
        query = query[: engine.max_query_chars]
    return query


def fetch_keyed_engine(
    engine: EngineSpec,
    query: str,
    timeout: float,
    limit: int,
    token: str,
    poster: Callable[[str, float, int, dict[str, Any], str], tuple[bytes, int]] = fetch_json_post,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.monotonic()
    base = {"engine": engine.name, "family": engine.family, "keyed": True}
    body = {
        "Query": query,
        "DocCount": max(1, min(limit, 20)),
        "MaxSnippetLength": 1000,
        "MaxImageCountPerDoc": 0,
    }
    try:
        payload, status = poster(engine.url_template, timeout, 2, body, token)
        candidates = parse_doubao(payload, engine, limit)
        return {
            **base,
            "status": "ok" if candidates else "fail",
            "http_status": status,
            "error": None if candidates else "parse_empty",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "candidate_count": len(candidates),
        }, candidates
    except DoubaoProviderError as exc:
        return {
            **base,
            "status": "blocked" if exc.config_error else "fail",
            "http_status": 200,
            "error": f"provider_error_{exc.code}" if exc.code is not None else "provider_error",
            "error_hint": exc.hint,
            "config_error": exc.config_error,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "candidate_count": 0,
        }, []
    except urllib.error.HTTPError as exc:
        return {
            **base,
            "status": "blocked" if exc.code in {401, 403, 429} else "fail",
            "http_status": exc.code,
            "error": f"http_{exc.code}",
            "error_hint": "invalid_or_unauthorized_api_key" if exc.code in {401, 403} else ("rate_limited_5qps" if exc.code == 429 else "provider_http_error"),
            "config_error": exc.code in {401, 403},
            "latency_ms": round((time.monotonic() - started) * 1000),
            "candidate_count": 0,
        }, []
    except Exception as exc:
        return {
            **base,
            "status": "fail",
            "http_status": None,
            "error": type(exc).__name__,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "candidate_count": 0,
        }, []


def fetch_engine(engine: EngineSpec, query: str, timeout: float, limit: int, fetcher: Callable[[str, float, int], tuple[bytes, int]] = fetch_bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    encoded = urllib.parse.quote_plus(query)
    url = engine.url_template.format(query=encoded)
    started = time.monotonic()
    try:
        payload, status = fetcher(url, timeout, 2)
        candidates = parse_rss(payload, engine, limit) if engine.parser == "rss" else parse_html(payload, engine, limit)
        health = {
            "engine": engine.name,
            "family": engine.family,
            "status": "ok" if candidates else "fail",
            "http_status": status,
            "error": None if candidates else "parse_empty",
            "latency_ms": round((time.monotonic() - started) * 1000),
            "candidate_count": len(candidates),
        }
        return health, candidates
    except urllib.error.HTTPError as exc:
        status = "blocked" if exc.code in {403, 429} else "fail"
        return {"engine": engine.name, "family": engine.family, "status": status, "http_status": exc.code, "error": f"http_{exc.code}", "latency_ms": round((time.monotonic() - started) * 1000), "candidate_count": 0}, []
    except Exception as exc:
        return {"engine": engine.name, "family": engine.family, "status": "fail", "http_status": None, "error": type(exc).__name__, "latency_ms": round((time.monotonic() - started) * 1000), "candidate_count": 0}, []


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", title.lower())


def query_terms(query: str) -> list[str]:
    stop = {"latest", "news", "the", "and", "or", "what", "is", "about", "official", "最新", "新闻"}
    english = re.findall(r"[a-z0-9][a-z0-9._-]{1,}", query.lower())
    chinese = re.findall(r"[\u3400-\u9fff]{2,}", query)
    common_cn = [term for term in ("公告", "分红", "股息", "传闻", "并购", "减持", "增持", "融资", "评级", "调查", "财报") if term in query]
    return list(dict.fromkeys(term for term in english + chinese + common_cn if term not in stop and len(term) >= 2))


def identity_terms(query: str) -> list[str]:
    tickers = [value.lower() for value in re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{2})?\b", query) if value not in {"OR", "AND", "SEC", "IPO", "FCF", "EPS"}]
    generic_cn = {"最新公告", "公告", "分红", "股息", "传闻", "并购", "减持", "增持", "融资", "评级", "调查", "财报", "最新新闻"}
    chinese = [value for value in re.findall(r"[\u3400-\u9fff]{3,}", query) if value not in generic_cn]
    return list(dict.fromkeys(tickers + chinese[:1]))


def candidate_relevance(query: str, title: str, snippet: str, url: str) -> tuple[bool, list[str], float]:
    terms = query_terms(query)
    if not terms:
        return True, [], 1.0
    haystack = f"{title} {snippet} {urllib.parse.unquote(url)}".lower()
    matched: list[str] = []
    for term in terms:
        if re.search(r"[\u3400-\u9fff]", term):
            if term in haystack:
                matched.append(term)
        elif re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack):
            matched.append(term)
    ascii_terms = [term for term in terms if re.fullmatch(r"[a-z0-9._-]+", term)]
    required = 2 if len(ascii_terms) >= 3 else 1
    identities = identity_terms(query)
    identity_ok = not identities or any(
        (identity in haystack if re.search(r"[\u3400-\u9fff]", identity) else bool(re.search(rf"(?<![a-z0-9]){re.escape(identity)}(?![a-z0-9])", haystack)))
        for identity in identities
    )
    return identity_ok and len(matched) >= required, matched, round(len(matched) / len(terms), 3)


def aggregate_candidates(query: str, profile: str, market: str, observed_at: str, raw: Iterable[tuple[EngineSpec, dict[str, Any]]], freshness_hours: int | None = None, diagnostics: dict[str, int] | None = None) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    title_index: dict[str, str] = {}
    freshness_rank = {"within_window": 0, "not_required": 0, "unknown": 1}
    stats = diagnostics if diagnostics is not None else {}
    for key in ("raw_candidates", "irrelevant_or_invalid", "stale_filtered", "future_timestamp_filtered", "accepted_raw"):
        stats.setdefault(key, 0)
    for engine, item in raw:
        stats["raw_candidates"] += 1
        url = canonicalize_url(str(item.get("url") or ""))
        title = normalize_text(str(item.get("title") or ""))
        snippet = normalize_text(str(item.get("snippet") or ""))
        published_at = item.get("published_at")
        freshness = freshness_state(published_at, observed_at, freshness_hours)
        relevant, match_terms, relevance = candidate_relevance(query, title, snippet, url)
        invalid = not url or not title or not relevant
        if invalid:
            stats["irrelevant_or_invalid"] += 1
        if freshness == "stale":
            stats["stale_filtered"] += 1
        elif freshness == "future_timestamp":
            stats["future_timestamp_filtered"] += 1
        if invalid or freshness in {"stale", "future_timestamp"}:
            continue
        stats["accepted_raw"] += 1
        title_key = normalize_title(title)
        key = url
        if title_key and len(title_key) >= 12 and title_key in title_index:
            key = title_index[title_key]
        if key not in merged:
            merged[key] = {
                "candidate_id": "SDC-" + hashlib.sha256(f"{query}|{url}".encode()).hexdigest()[:12],
                "query": query,
                "profile": profile,
                "market": market,
                "title": title,
                "url": url,
                "snippet": snippet,
                "published_at": published_at,
                "freshness_state": freshness,
                "freshness_hours": freshness_hours,
                "freshness_eligible": freshness in {"within_window", "not_required"},
                "query_match_terms": match_terms,
                "discovery_relevance": relevance,
                "engines": [],
                "provider_families": [],
                "observed_at": observed_at,
                "discovery_only": True,
                "redirect_unresolved": bool(item.get("redirect_unresolved")),
                "claim_type": "rumor_signal" if profile == "rumor" else "assumption",
                "verification_status": "unverified",
                "credibility_state": "unverified",
                "readiness_impact": "monitoring_only",
                "allowed_use": ["evidence_collection_plan", "watch_priority", "source_discovery"],
                "forbidden_use": ["verified_fact", "position_sizing", "order_execution"],
            }
            if title_key:
                title_index[title_key] = key
        candidate = merged[key]
        candidate["engines"] = sorted(set(candidate["engines"] + [engine.name]))
        candidate["provider_families"] = sorted(set(candidate["provider_families"] + [engine.family]))
        candidate["independent_provider_count"] = len(candidate["provider_families"])
        count = candidate["independent_provider_count"]
        candidate["attention_state"] = "broad" if count >= 3 else "emerging" if count >= 2 else "quiet"
        candidate["redirect_unresolved"] = bool(candidate["redirect_unresolved"] or item.get("redirect_unresolved"))
        if freshness_rank.get(freshness, 2) < freshness_rank.get(candidate["freshness_state"], 2):
            candidate["published_at"] = published_at
            candidate["freshness_state"] = freshness
            candidate["freshness_eligible"] = freshness in {"within_window", "not_required"}
        if not candidate["snippet"] and item.get("snippet"):
            candidate["snippet"] = normalize_text(str(item["snippet"]))
    return sorted(merged.values(), key=lambda row: (freshness_rank.get(row["freshness_state"], 2), -row["discovery_relevance"], -row["independent_provider_count"], row["title"].lower()))


def fallback_trigger(health: list[dict[str, Any]], candidate_count: int, min_candidates: int) -> tuple[bool, str]:
    """Decide whether the keyed backup tier is warranted, and say why in one word."""
    usable = [row for row in health if row["status"] == "ok"]
    if not usable:
        return True, "all_primary_providers_failed"
    if candidate_count < min_candidates:
        return True, "insufficient_primary_candidates"
    blocked = [row for row in health if row["status"] == "blocked"]
    if blocked and len(blocked) >= max(1, len(health) // 2):
        return True, "majority_primary_providers_blocked"
    return False, "primary_coverage_sufficient"


def run_engine_tier(
    plan: dict[str, Any],
    engine_names: list[str],
    timeout: float,
    per_engine_limit: int,
    max_workers: int,
) -> tuple[list[dict[str, Any]], list[tuple[EngineSpec, dict[str, Any]]]]:
    """Run one tier of providers, resolving credentials only for keyed engines."""
    health: list[dict[str, Any]] = []
    raw: list[tuple[EngineSpec, dict[str, Any]]] = []
    runnable: list[tuple[EngineSpec, str, str | None]] = []
    for name in engine_names:
        engine = ENGINES[name]
        query = engine_query(engine, plan)
        if not engine.auth_env:
            runnable.append((engine, query, None))
            continue
        token, diagnostics = load_search_credential(engine.auth_env)
        if not token:
            # No key is a configuration state, never a "no news" finding: report it as a
            # skip with the exact remediation and keep the rest of the tier running.
            health.append({
                "engine": engine.name,
                "family": engine.family,
                "keyed": True,
                "status": "skipped",
                "http_status": None,
                "error": f"auth_{diagnostics.get('reason', 'missing')}",
                "error_hint": diagnostics.get("remediation") or f"set {engine.auth_env} in {CREDENTIAL_FILE}",
                "credential_source": diagnostics.get("source"),
                "latency_ms": 0,
                "candidate_count": 0,
            })
            continue
        runnable.append((engine, query, token))
    if not runnable:
        return health, raw
    with ThreadPoolExecutor(max_workers=max(1, min(3, max_workers))) as pool:
        futures = {}
        for engine, query, token in runnable:
            if token:
                futures[pool.submit(fetch_keyed_engine, engine, query, timeout, per_engine_limit, token)] = engine
            else:
                futures[pool.submit(fetch_engine, engine, query, timeout, per_engine_limit)] = engine
        for future in as_completed(futures):
            engine = futures[future]
            engine_health, items = future.result()
            health.append(engine_health)
            raw.extend((engine, item) for item in items)
    return health, raw


def execute_search(plan: dict[str, Any], timeout: float, per_engine_limit: int, max_workers: int) -> dict[str, Any]:
    observed_at = utc_now()
    health, raw = run_engine_tier(plan, list(plan["selected_engines"]), timeout, per_engine_limit, max_workers)
    health.sort(key=lambda row: plan["selected_engines"].index(row["engine"]))

    primary_diagnostics: dict[str, int] = {}
    primary_candidates = aggregate_candidates(
        plan["query"], plan["profile"], plan["market"], observed_at, raw, plan.get("freshness_hours"), primary_diagnostics
    )
    fallback_engines = list(plan.get("fallback_engines") or [])
    min_candidates = int(plan.get("min_candidates", 3))
    triggered, reason = fallback_trigger(health, len(primary_candidates), min_candidates)
    fallback_state: dict[str, Any] = {
        "engines": fallback_engines,
        "min_candidates": min_candidates,
        "triggered": bool(triggered and fallback_engines),
        "reason": reason if fallback_engines else "no_fallback_provider_configured",
        "primary_candidate_count": len(primary_candidates),
    }
    if triggered and fallback_engines:
        fallback_health, fallback_raw = run_engine_tier(plan, fallback_engines, timeout, per_engine_limit, max_workers)
        fallback_health.sort(key=lambda row: fallback_engines.index(row["engine"]))
        for row in fallback_health:
            row["tier"] = "fallback"
        health.extend(fallback_health)
        raw.extend(fallback_raw)
        fallback_state["candidates_added"] = len(fallback_raw)
        fallback_state["engines_ok"] = [row["engine"] for row in fallback_health if row["status"] == "ok"]
    for row in health:
        row.setdefault("tier", "primary")

    # Re-aggregate over both tiers so dedup, provider-family independence and freshness
    # ranking stay correct instead of naively concatenating two candidate lists.
    filter_diagnostics: dict[str, int] = {}
    candidates = aggregate_candidates(
        plan["query"], plan["profile"], plan["market"], observed_at, raw, plan.get("freshness_hours"), filter_diagnostics
    )
    gaps = []
    for row in health:
        if row["status"] not in {"ok", "skipped"}:
            gaps.append({"source": row["engine"], "gap": row["error"], "impact": "reduced_discovery_coverage"})
        elif row["status"] == "skipped":
            gaps.append({
                "source": row["engine"],
                "gap": row.get("error") or "provider_skipped",
                "impact": "backup_provider_unavailable_not_an_absence_of_news",
                "remediation": row.get("error_hint"),
            })
    if not candidates:
        gaps.append({"source": "multi_source_search", "gap": "no_candidates", "impact": "must_use_other_sources_or_report_DataGap"})
    unknown_freshness = sum(1 for row in candidates if row["freshness_state"] == "unknown")
    if unknown_freshness:
        gaps.append({"source": "multi_source_search", "gap": "published_at_unknown", "candidate_count": unknown_freshness, "impact": "cannot_claim_freshness_without_original_timestamp"})
    if filter_diagnostics["stale_filtered"]:
        gaps.append({"source": "multi_source_search", "gap": "stale_candidates_filtered", "candidate_count": filter_diagnostics["stale_filtered"], "impact": "excluded_from_fresh_discovery"})
    if filter_diagnostics["future_timestamp_filtered"]:
        gaps.append({"source": "multi_source_search", "gap": "future_timestamp_candidates_filtered", "candidate_count": filter_diagnostics["future_timestamp_filtered"], "impact": "excluded_to_prevent_lookahead"})
    keyed_used = sorted({row["engine"] for row in health if row.get("keyed") and row["status"] == "ok"})
    return {
        "ok": bool(candidates),
        "mode": "live_discovery",
        "generated_at": observed_at,
        "plan": {**plan, "external_transmission": "allowed_for_selected_engines"},
        "source_health": health,
        "fallback": fallback_state,
        "filter_diagnostics": filter_diagnostics,
        "candidates": candidates,
        "data_gaps": gaps,
        "privacy": {
            "query_sent_to_selected_engines": True,
            "cookies_read_or_written": False,
            "environment_secrets_read": False,
            "search_api_credentials_used": keyed_used,
            "credential_scope": sorted(ALLOWED_CREDENTIAL_ENV),
        },
        "no_order_execution": True,
    }


def self_test() -> dict[str, Any]:
    assert detect_language("贵州茅台 最新公告") == "zh"
    assert detect_language("NVDA latest filing") == "en"
    assert query_is_sensitive("token=secret")[0]
    assert query_is_sensitive("contact analyst@example.com")[0]
    assert query_is_sensitive("手机号 13800138000")[0]
    assert not query_is_sensitive("insider buying NVDA")[0]
    plan = build_plan("NVDA latest filing", "filing", "US")
    assert plan["selected_engines"] == ["bing_web", "google_news", "duckduckgo", "brave"]
    assert build_plan("NVDA news", "news", "US")["freshness_hours"] == 72
    assert freshness_state("Sat, 11 Jul 2026 23:00:00 GMT", "2026-07-12T00:00:00Z", 72) == "within_window"
    assert freshness_state("Wed, 01 Jul 2026 00:00:00 GMT", "2026-07-12T00:00:00Z", 72) == "stale"
    decoded = canonicalize_url("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Futm_source%3Dx")
    assert decoded == "https://example.com/a"
    engine_a = ENGINES["bing_web"]
    engine_b = ENGINES["bing_news"]
    rows = aggregate_candidates(
        "q", "rumor", "US", "2026-01-01T00:00:00Z",
        [(engine_a, {"title": "Example acquisition rumor report", "url": "https://example.com/x", "snippet": "", "redirect_unresolved": False}),
         (engine_b, {"title": "Example acquisition rumor report", "url": "https://example.com/x?utm_source=b", "snippet": "", "redirect_unresolved": False})],
    )
    assert len(rows) == 1 and rows[0]["independent_provider_count"] == 1
    assert rows[0]["claim_type"] == "rumor_signal" and rows[0]["forbidden_use"][-1] == "order_execution"
    # Keyed backup provider contract.
    assert "doubao" in KEYED_ENGINES and ENGINES["doubao"].auth_env == "VOLC_DOUBAO_SEARCH_API_KEY"
    assert "doubao" not in plan["selected_engines"], "keyed provider must never join the default primary tier"
    assert plan["fallback_engines"] == ["doubao"]
    assert _parse_env_file("OKX_DEMO_API_KEY=abc\nVOLC_DOUBAO_SEARCH_API_KEY=xyz\n") == {"VOLC_DOUBAO_SEARCH_API_KEY": "xyz"}
    assert load_search_credential("OKX_DEMO_API_KEY")[0] is None
    assert engine_query(ENGINES["doubao"], build_plan("NVDA earnings", "filing", "US")) == "NVDA earnings"
    assert len(engine_query(ENGINES["doubao"], build_plan("试" * 400, "general", "A"))) == 100
    assert fallback_trigger([{"status": "ok"}], 0, 3)[0] is True
    assert fallback_trigger([{"status": "ok"}], 5, 3)[0] is False
    assert fallback_trigger([{"status": "fail"}], 9, 3)[1] == "all_primary_providers_failed"
    doubao_rows = parse_doubao(
        json.dumps({"ResponseMetadata": {"RequestId": "r"}, "Result": {"TotalDocCount": 1, "ErrorCode": 0, "Documents": [
            {"Rank": 0, "Url": "https://example.com/a", "Title": "Example report title",
             "Snippet": [{"Type": "text", "Text": "body"}, {"Type": "image", "Image": {"ImageUrl": "https://img"}}],
             "DocumentInfo": {"Filetype": "webpage", "PublishTime": "2026-07-12 08:00:00"},
             "HostInfo": {"Hostname": "示例站"}}]}}).encode(),
        ENGINES["doubao"], 5,
    )
    assert len(doubao_rows) == 1 and doubao_rows[0]["snippet"] == "body"
    assert doubao_rows[0]["published_at"] == "2026-07-12T08:00:00Z"
    try:
        parse_doubao(json.dumps({"ResponseMetadata": {"Error": {"CodeN": 700901, "Code": "700901", "Message": "invalid"}}, "Result": None}).encode(), ENGINES["doubao"], 5)
        raise AssertionError("provider error must not be reported as success")
    except DoubaoProviderError as exc:
        assert exc.code == 700901 and exc.config_error and exc.hint == "invalid_api_key"
    return {
        "ok": True,
        "self_test": "passed",
        "engines": len(ENGINES),
        "keyed_engines": sorted(KEYED_ENGINES),
        "credential_file": str(CREDENTIAL_FILE),
        "profiles": sorted(PROFILES),
        "no_order_execution": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stateless multi-source discovery fallback; results are not evidence.")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="general")
    parser.add_argument("--market", default="unknown")
    parser.add_argument("--engines", help="Comma-separated explicit engine names")
    parser.add_argument("--fallback-engines", help=f"Comma-separated keyed backup engines; default {','.join(sorted(KEYED_ENGINES))}")
    parser.add_argument("--no-fallback", action="store_true", help="Disable the keyed backup tier entirely")
    parser.add_argument("--min-candidates", type=int, default=3, help="Below this many primary candidates the keyed backup tier runs")
    parser.add_argument("--allow-external-search", action="store_true")
    parser.add_argument("--freshness-hours", type=int, help="Freshness window for dated candidates; news defaults to 72 hours")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--per-engine-limit", type=int, default=8)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--check-credentials", action="store_true", help="Report keyed provider credential status without searching")
    return parser.parse_args()


def check_credentials() -> dict[str, Any]:
    """Report configuration state for keyed providers without printing any secret."""
    providers = []
    for name in sorted(KEYED_ENGINES):
        engine = ENGINES[name]
        assert engine.auth_env
        token, diagnostics = load_search_credential(engine.auth_env)
        providers.append({
            "engine": name,
            "family": engine.family,
            "env_name": engine.auth_env,
            "configured": bool(token),
            "credential_source": diagnostics.get("source") if not token else diagnostics.get("source"),
            "reason": diagnostics.get("reason"),
            "remediation": diagnostics.get("remediation") or (None if token else f"write {engine.auth_env}=<key> to {CREDENTIAL_FILE} (chmod 600)"),
        })
    return {
        "ok": True,
        "mode": "credential_check",
        "generated_at": utc_now(),
        "credential_file": str(CREDENTIAL_FILE),
        "credential_scope": sorted(ALLOWED_CREDENTIAL_ENV),
        "providers": providers,
        "secret_values_printed": False,
        "no_order_execution": True,
    }


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print("DISCOVERY_ONLY: titles/snippets are not evidence; fetch original URLs before grading")
    print(f"mode={payload.get('mode')} ok={payload.get('ok')} no_order_execution={payload.get('no_order_execution')}")
    plan = payload.get("plan", {})
    print(f"profile={plan.get('profile')} market={plan.get('market')} engines={','.join(plan.get('selected_engines', []))}")
    fallback = payload.get("fallback")
    if fallback:
        print(f"fallback={','.join(fallback.get('engines', [])) or 'none'} triggered={fallback.get('triggered')} reason={fallback.get('reason')}")
    for item in payload.get("candidates", [])[:10]:
        print(f"- [{item['independent_provider_count']}] {item['title']}\n  {item['url']}")
    for gap in payload.get("data_gaps", []):
        print(f"gap: {gap}")


def main() -> int:
    args = parse_args()
    if args.self_test:
        emit(self_test(), True)
        return 0
    if args.check_credentials:
        emit(check_credentials(), True)
        return 0
    if not args.query:
        raise SystemExit("query is required unless --self-test or --check-credentials is used")
    market = normalize_market(args.market)
    engines = [value.strip() for value in args.engines.split(",") if value.strip()] if args.engines else None
    if args.no_fallback:
        fallback_engines = []
    elif args.fallback_engines:
        fallback_engines = [value.strip() for value in args.fallback_engines.split(",") if value.strip()]
    else:
        fallback_engines = None
    try:
        plan = build_plan(args.query, args.profile, market, engines, args.freshness_hours, fallback_engines, max(0, args.min_candidates))
    except ValueError as exc:
        emit({"ok": False, "mode": "plan_error", "error": str(exc), "no_order_execution": True}, True)
        return 2
    if plan["sensitive_query"]:
        payload = {
            "ok": False,
            "mode": "blocked_sensitive_query",
            "generated_at": utc_now(),
            "plan": plan,
            "source_health": [],
            "candidates": [],
            "data_gaps": [{"source": "privacy_gate", "gap": "sensitive_query", "impact": "external_search_blocked"}],
            "privacy": {"query_sent_to_selected_engines": False, "cookies_read_or_written": False, "environment_secrets_read": False, "search_api_credentials_used": []},
            "no_order_execution": True,
        }
        emit(payload, True)
        return 3
    if not args.allow_external_search:
        payload = {
            "ok": True,
            "mode": "plan_only",
            "generated_at": utc_now(),
            "plan": plan,
            "source_health": [{"engine": name, "family": ENGINES[name].family, "status": "skipped", "error": "external_search_not_allowed", "tier": "fallback" if name in plan["fallback_engines"] else "primary"} for name in list(plan["selected_engines"]) + list(plan["fallback_engines"])],
            "candidates": [],
            "data_gaps": [],
            "privacy": {"query_sent_to_selected_engines": False, "cookies_read_or_written": False, "environment_secrets_read": False, "search_api_credentials_used": []},
            "no_order_execution": True,
        }
        emit(payload, args.json)
        return 0
    payload = execute_search(plan, max(1.0, min(args.timeout, 30.0)), max(1, min(args.per_engine_limit, 20)), args.max_workers)
    emit(payload, args.json)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
