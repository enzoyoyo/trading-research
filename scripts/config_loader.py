#!/usr/bin/env python3
"""Load public example / local operator config without leaking secrets.

- Reads optional YAML config from TRADING_RESEARCH_CONFIG or a provided path.
- Reads optional dotenv-style files for allow-listed keys only.
- Never prints secret values; missing required keys fail closed.
- Live trading is disabled unless explicitly enabled via env + config.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|secret|password|passphrase|private[_-]?key|cookie|authorization)"
)

ALLOWLISTED_ENV = {
    "LONGBRIDGE_BIN",
    "HERMES_BIN",
    "AKSHARE_PYTHON",
    "VOLC_DOUBAO_SEARCH_API_KEY",
    "TRADING_RESEARCH_SEC_IDENTITY",
    "SEC_EDGAR_IDENTITY",
    "TRADING_RESEARCH_SEC_CACHE_DIR",
    "TRADING_RESEARCH_STATE_DIR",
    "TRADING_RESEARCH_SEARCH_ENV",
    "TRADING_RESEARCH_CONFIG",
    "TRADING_RESEARCH_ALLOW_LIVE",
    "LONGPORT_APP_KEY",
    "LONGPORT_APP_SECRET",
    "LONGPORT_ACCESS_TOKEN",
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
    "OKX_MODE",
}


def _expand(path: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path)))).resolve()


def default_config_candidates() -> list[Path]:
    home = Path.home()
    return [
        _expand(os.environ["TRADING_RESEARCH_CONFIG"]) if os.environ.get("TRADING_RESEARCH_CONFIG") else None,
        Path.cwd() / "config.yaml",
        home / ".config" / "trading-research" / "config.yaml",
    ]


def load_yaml_config(path: str | Path | None = None) -> dict[str, Any]:
    candidates: list[Path] = []
    if path is not None:
        candidates.append(_expand(path))
    else:
        candidates.extend([p for p in default_config_candidates() if p is not None])
    for candidate in candidates:
        if candidate.is_file():
            if yaml is None:
                raise RuntimeError("PyYAML is required to load config.yaml; pip install pyyaml")
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise RuntimeError(f"config root must be a mapping: {candidate}")
            return data
    return {}


def parse_env_file(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key in ALLOWLISTED_ENV:
            out[key] = value
    return out


def load_env_file(path: str | Path) -> dict[str, str]:
    p = _expand(path)
    if not p.is_file():
        return {}
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError(f"refusing to load group/world-readable env file: {p} (chmod 600 required)")
    return parse_env_file(p.read_text(encoding="utf-8"))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required configuration: {name}")
    return value


def mask_secret(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return "***"
    return value[:2] + "***" + value[-2:]


_SECRET_ASSIGN = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|secret|password|passphrase)\s*[=:]\s*)([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9\-._~+/]+=*)")


def scrub_secrets(text: str) -> str:
    text = text or ""
    text = _SECRET_ASSIGN.sub(r"\1<redacted>", text)
    text = _BEARER.sub(r"\1 <redacted>", text)
    return text


def live_trading_enabled(config: dict[str, Any] | None = None) -> bool:
    """Live trading is OFF by default. Requires both env and config affirmatives."""
    cfg = config if config is not None else load_yaml_config()
    env_ok = os.environ.get("TRADING_RESEARCH_ALLOW_LIVE", "").strip().lower() in {"1", "true", "yes"}
    cfg_ok = bool(((cfg.get("safety") or {}).get("allow_live_trading")) is True)
    return env_ok and cfg_ok


def assert_research_only(config: dict[str, Any] | None = None) -> None:
    if live_trading_enabled(config):
        # Still do not place orders from this skill; research-only surface.
        raise RuntimeError(
            "live trading flags are set, but this open-source skill never submits brokerage orders; "
            "unset TRADING_RESEARCH_ALLOW_LIVE and safety.allow_live_trading"
        )


def public_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_yaml_config()
    keyed = [k for k in ("VOLC_DOUBAO_SEARCH_API_KEY", "LONGPORT_APP_KEY", "OKX_API_KEY") if os.environ.get(k)]
    return {
        "live_trading_enabled": False,
        "orders_allowed": False,
        "config_keys_present": sorted(cfg.keys()),
        "credential_env_configured": keyed,
        "secret_values_printed": False,
    }


if __name__ == "__main__":
    assert_research_only()
    status = public_status()
    assert status["orders_allowed"] is False
    assert status["secret_values_printed"] is False
    print("config_loader: ok")
