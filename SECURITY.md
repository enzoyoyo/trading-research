# Security Policy

## Supported use

This repository is a **research / analysis skill**. It must not place live brokerage orders.

- Default mode is research-only.
- Missing credentials must fail closed.
- Logs and diagnostics must never print secret values.
- `TRADING_RESEARCH_ALLOW_LIVE` and `safety.allow_live_trading` are denied by the open-source safety layer even if set.

## Reporting a vulnerability

Please open a GitHub security advisory or private issue describing:

1. Affected file paths and versions (commit SHA)
2. Impact (secret leak, unintended order path, path traversal, etc.)
3. Reproduction without including real secrets

Do **not** attach live API keys, account statements, or personal data.

## Operator checklist

1. Copy `.env.example` values into a chmod-600 env file outside the repo.
2. Copy `config.example.yaml` to `~/.config/trading-research/config.yaml`.
3. Keep real `.env`, logs, databases, cookies, and session files out of git (see `.gitignore`).
4. Rotate any credential that may have been exposed in chat, screenshots, or prior private clones.
