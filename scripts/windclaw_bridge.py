#!/usr/bin/env python3
"""WindClaw bridge for trading-research.

Provides a safe, deterministic wrapper around the WindClaw/Wind financial
workflows available on the user's machine. The script reads the current WindClaw
runtime session from environment or WindClaw's local state file, but never
prints the session id.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

HOME = Path.home()
WINDCLAW_ROOT = HOME / ".openclaw-windclaw"
DEFAULT_WORKFLOW_URL = "https://m.wind.com.cn/wstock_share/ai/run_workflow"
DEFAULT_WEB_MCP_URL = "https://t.wind.com.cn/Wind.MCP.Server/vserver/vserver_windclaw/mcp"
DEFAULT_QUOTE_MCP_URL = "https://m.wind.com.cn/Wind.MCP.Server/vserver/vserver_windclaw_wx/mcp"
DEFAULT_DOCUMENT_DOCTYPE = "1,2,3,4,12"
DEFAULT_TIMEOUT = 30.0

APP_CODES = {
    "document": "6c31b10a-0224-4e14-b3ea-7bb2c37dc41e",
    "data": "abe7dbb7-e9ac-455c-9057-98d721d27299",
    "reference": "7f6e2c65-81eb-4365-aec4-fac5063c871c",
}

WEB_TOOLS = {
    "web": {
        "url": DEFAULT_WEB_MCP_URL,
        "name": "internet_search",
        "default_args": {"freshness": "最近一周", "count": "5"},
    },
    "quote_stock": {
        "url": DEFAULT_QUOTE_MCP_URL,
        "name": "quote_get_stock_realtime_performance",
        "default_args": {},
    },
    "quote_market": {
        "url": DEFAULT_QUOTE_MCP_URL,
        "name": "quote_get_market_realtime_performance",
        "default_args": {},
    },
    "quote_sector": {
        "url": DEFAULT_QUOTE_MCP_URL,
        "name": "quote_get_sector_realtime_performance",
        "default_args": {},
    },
}


def safe_session_preview(session: str) -> str:
    if not session:
        return "absent"
    return f"present:{len(session)}chars"


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_state_dirs() -> list[Path]:
    dirs: list[Path] = []
    active = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if active:
        dirs.append(Path(active).expanduser())
    if WINDCLAW_ROOT.exists():
        dirs.extend(p.parent for p in WINDCLAW_ROOT.rglob(".windclaw-aigw-session"))
    unique: dict[str, Path] = {}
    for d in dirs:
        unique[str(d)] = d
    return sorted(unique.values(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def resolve_session() -> tuple[str, str, Path | None]:
    env_session = os.environ.get("WIND_SESSION_ID", "").strip()
    if env_session:
        return env_session, "env", None
    for state_dir in discover_state_dirs():
        candidate = state_dir / ".windclaw-aigw-session"
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except Exception:
            value = ""
        if value:
            return value, "runtime-file", candidate
    return "", "none", None


def plugin_config(plugin_id: str) -> dict[str, Any]:
    for state_dir in discover_state_dirs():
        cfg = read_json(state_dir / "openclaw.json")
        entries = cfg.get("plugins", {}).get("entries", {}) if isinstance(cfg, dict) else {}
        entry = entries.get(plugin_id, {}) if isinstance(entries, dict) else {}
        conf = entry.get("config", {}) if isinstance(entry, dict) else {}
        if isinstance(conf, dict):
            return conf
    return {}


def request_id() -> str:
    return f"hermes-{int(time.time())}-{uuid.uuid4().hex[:12]}"


def parse_json_maybe(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw


def maybe_parse_nested(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
            try:
                return maybe_parse_nested(json.loads(stripped), depth + 1)
            except Exception:
                return value
        return value
    if isinstance(value, list):
        return [maybe_parse_nested(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {k: maybe_parse_nested(v, depth + 1) for k, v in value.items()}
    return value


def extract_sse_payload(raw: str) -> Any:
    events: list[str] = []
    current: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            if current:
                event = "\n".join(current).strip()
                if event and event != "[DONE]":
                    events.append(event)
                current = []
            continue
        if line.startswith("data:"):
            current.append(line[5:].strip())
    if current:
        event = "\n".join(current).strip()
        if event and event != "[DONE]":
            events.append(event)
    if not events:
        return parse_json_maybe(raw)
    return maybe_parse_nested(parse_json_maybe(events[-1]))


def normalize_response(raw: str, content_type: str = "") -> Any:
    if "text/event-stream" in content_type.lower() or raw.lstrip().startswith("data:") or "\ndata:" in raw:
        return extract_sse_payload(raw)
    return maybe_parse_nested(parse_json_maybe(raw))


def find_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        for item in value:
            text = find_text(item)
            if text:
                return text
        return None
    if not isinstance(value, dict):
        return str(value)

    preferred = [
        "result",
        "content",
        "text",
        "output",
        "answer",
        "response",
        "data",
        "outputs",
        "resultData",
        "message",
    ]
    for key in preferred:
        if key in value:
            text = find_text(value[key])
            if text and text.lower() not in {"success", "ok", "0", "1", "-1"}:
                return text
    for key, item in value.items():
        if key.lower() in {"status", "resultcode", "issuccess", "success", "code", "errorcode"}:
            continue
        text = find_text(item)
        if text and text.lower() not in {"success", "ok", "0", "1", "-1"}:
            return text
    return None


def headers_for(session: str, *, mcp: bool = False) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "wind.sessionid": session,
        "windsessionid": session,
        "x-wind-clientname": "WindClaw",
    }
    if mcp:
        headers["Accept"] = "text/event-stream,application/json"
    return headers


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("content-type", ""), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return exc.code, exc.headers.get("content-type", ""), raw


def workflow_call(kind: str, query: str, *, doctype: str, timeout: float) -> dict[str, Any]:
    session, source, _path = resolve_session()
    if not session:
        return {"ok": False, "tool": kind, "error": "WindClaw session not found; open/login WindClaw first.", "session_source": source}
    conf = plugin_config("wind_financial_data")
    url = str(conf.get("url") or DEFAULT_WORKFLOW_URL)
    inputs: dict[str, Any] = {"query": query, "sessionId": session, "requestId": request_id()}
    if kind == "document":
        inputs["doctype"] = doctype or DEFAULT_DOCUMENT_DOCTYPE
    body = {
        "appCode": APP_CODES[kind],
        "inputs": inputs,
        "responseMode": "blocking",
        "runWorkflowUser": "1",
        "type": 1,
    }
    status, content_type, raw = post_json(url, body, headers_for(session), timeout)
    payload = normalize_response(raw, content_type)
    text = find_text(payload) or ""
    ok = 200 <= status < 300 and bool(text) and not re.search(r"(?i)(invalid_param|auth failed|session.*expired|请先登录)", text)
    return {
        "ok": ok,
        "tool": kind,
        "source": "WindClaw/Wind workflow",
        "session_source": source,
        "status": status,
        "content_type": content_type,
        "text": text,
        "raw_length": len(raw),
    }


def mcp_call(kind: str, query: str, *, freshness: str, count: str, timeout: float) -> dict[str, Any]:
    session, source, _path = resolve_session()
    if not session:
        return {"ok": False, "tool": kind, "error": "WindClaw session not found; open/login WindClaw first.", "session_source": source}
    tool = WEB_TOOLS[kind]
    conf = plugin_config("wind_web_search") if kind == "web" else {}
    url = str(conf.get("url") or tool["url"])
    arguments: dict[str, Any] = {"query": query}
    if kind == "web":
        arguments.update({"freshness": freshness or "最近一周", "count": count or "5"})
    body = {"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": "tools/call", "params": {"name": tool["name"], "arguments": arguments}}
    status, content_type, raw = post_json(url, body, headers_for(session, mcp=True), timeout)
    payload = normalize_response(raw, content_type)
    text = find_text(payload) or ""
    return {
        "ok": 200 <= status < 300 and bool(text),
        "tool": kind,
        "source": "WindClaw/Wind MCP direct JSON-RPC",
        "session_source": source,
        "status": status,
        "content_type": content_type,
        "text": text,
        "raw_length": len(raw),
    }


def health() -> dict[str, Any]:
    session, source, path = resolve_session()
    return {
        "ok": bool(session),
        "session": safe_session_preview(session),
        "session_source": source,
        "session_path": str(path) if path else None,
        "workflow_url": str(plugin_config("wind_financial_data").get("url") or DEFAULT_WORKFLOW_URL),
        "web_mcp_url": str(plugin_config("wind_web_search").get("url") or DEFAULT_WEB_MCP_URL),
        "native_mcp_servers": ["windclaw-web", "windclaw-quote"],
        "tools": ["reference", "data", "document", "web", "quote_stock", "quote_market", "quote_sector"],
        "note": "Session id is intentionally not printed.",
    }


def print_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json or not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(result.get("text") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe WindClaw/Wind bridge for trading-research")
    parser.add_argument("tool", choices=["health", "reference", "data", "document", "web", "quote_stock", "quote_market", "quote_sector"])
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--doctype", default=DEFAULT_DOCUMENT_DOCTYPE)
    parser.add_argument("--freshness", default="最近一周")
    parser.add_argument("--count", default="5")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.tool == "health":
        out = health()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1

    query = args.query.strip()
    if not query:
        print(json.dumps({"ok": False, "error": "query is required"}, ensure_ascii=False, indent=2))
        return 2

    if args.tool in APP_CODES:
        out = workflow_call(args.tool, query, doctype=args.doctype, timeout=args.timeout)
    else:
        out = mcp_call(args.tool, query, freshness=args.freshness, count=args.count, timeout=args.timeout)
    print_result(out, args.json)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
