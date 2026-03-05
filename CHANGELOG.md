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
- `Dockerfile` updated to install dependencies from `pyproject.toml` at build time and to copy `locale/` into the image.
- `.env.example` updated with `BOT_LOCALE` variable.
- Docker Compose stack with `bot` and `glances` services.
- CI workflows: lint (`ruff` + `mypy`) and Docker image publish to GHCR.
- `app/main.py` — `Application` bootstrap with polling, `setMyCommands` registration and global error handler.
- `app/handlers/start.py` — `/start` command with personalised greeting (Telegram first name) and persistent `ReplyKeyboardMarkup` (Status, Alerts, Models, Help).
- `app/__init__.py` — added to fix mypy module resolution.
- `pyproject.toml` — added `[tool.ruff.lint.isort] known-first-party` and `[tool.setuptools.packages.find]`.
- `.github/workflows/lint.yml` — install project dependencies before running mypy.
- `.gitignore` / `.dockerignore` — exclude packaging artefacts (`*.egg-info`, `dist`, `build`).

### Added — Ollama model management
- `app/services/ollama.py` — async httpx client: `list_models()` fetches installed model names from `GET /api/tags`.
- `app/core/store.py` — SQLite persistence layer via `aiosqlite`: `init_db()` creates schema and seeds `active_model` from `OLLAMA_MODEL` on first run; `get_active_model()` and `set_active_model()` typed helpers.
- `app/handlers/models.py` — `/models` command and 🤖 Models button: lists all installed models with the active one marked ✅; inline buttons for each inactive model trigger a Confirm / Cancel flow before persisting the change.
- `locale/en.json` — added model management keys: `confirm_change`, `confirm_button`, `cancel_button`, `updated`, `cancelled`, `already_active`.
- `app/main.py` — calls `store.init_db()` in `post_init` (runs before polling starts); registers models handler via `models_handler.register(app)`.