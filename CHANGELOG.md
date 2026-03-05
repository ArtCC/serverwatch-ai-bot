# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - Unreleased

### Added
- Project scaffolding: `app/handlers/`, `app/services/`, `app/core/`, `app/utils/` package structure.
- `app/core/config.py` — typed `Config` dataclass loaded from environment variables with `get_config()` singleton.
- `app/core/auth.py` — `@restricted` decorator to block any chat ID other than `TELEGRAM_CHAT_ID`.
- `app/utils/formatting.py` — `info()`, `success()`, `warning()`, `error()` helpers for uniform response iconography (ℹ️ ✅ ⚠️ ❌).
- `app/utils/i18n.py` — locale loader (`load()`) and key accessor (`t()`) with interpolation support.
- `locale/en.json` — English locale file with all bot texts (start, help, status, alerts, models, chat, errors, alert notifications).
- `pyproject.toml` updated with runtime dependencies: `python-telegram-bot[job-queue]>=21.0`, `httpx>=0.27`, `aiosqlite>=0.20`.
- `Dockerfile` updated to install dependencies from `pyproject.toml` at build time.
- `.env.example` updated with `BOT_LOCALE` variable.
- Docker Compose stack with `bot` and `glances` services.
- CI workflows: lint (`ruff` + `mypy`) and Docker image publish to GHCR.