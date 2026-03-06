# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.1] - 2026-03-06

### Added

- Initial production-ready Telegram bot architecture under `app/` with separated layers:
	`core`, `handlers`, `services`, and `utils`.
- Single-user access control middleware using `TELEGRAM_CHAT_ID`.
- Internationalization support with locale files (`locale/en.json`, `locale/es.json`) and runtime locale resolution.
- Core Telegram UX features:
	- registered slash commands (`/start`, `/status`, `/alerts`, `/models`, `/help`),
	- persistent keyboard,
	- contextual inline flows for models, alerts, and status refresh,
	- friendly global error handling.
- `/status` metrics snapshot flow backed by Glances v4 API with a fixed endpoint bundle.
- `/alerts` threshold management flow (CPU/RAM/Disk) with inline Confirm/Cancel and persisted values.
- `/models` model selection flow with persisted active model.
- Unified LLM routing with support for:
	- local Ollama,
	- optional OpenAI,
	- optional Anthropic,
	- optional DeepSeek.
- Best-effort cloud web-search integration/fallback logic where supported.
- Proactive alert scheduler with configurable interval/cooldown.
- SQLite persistence for active model and alert thresholds.
- Dockerized deployment assets (`Dockerfile`, `docker-compose.yml`, `.env.example`).
- Baseline quality tooling and tests (`ruff`, `mypy`, `pytest`).

### Changed

- Glances endpoint requests are executed concurrently and aggregated in a best-effort payload.
- Added short-lived Glances snapshot caching to reduce redundant metric fetches.
- Introduced single-flight protection for Glances cache refresh under concurrency.
- Reused HTTP clients across services (`glances`, `ollama`, `llm_router`) and closed them on application shutdown.
- Switched SQLite access to a shared runtime connection with locking and explicit shutdown close.
- Optimized i18n lookup path with in-memory caches for supported locales, translation values, and regex patterns.
- Improved chat streaming robustness to avoid repeated fallback replies when placeholder edits fail.
- Escaped dynamic Markdown input in `/start` welcome flow to prevent Telegram formatting errors.

### Documentation

- Expanded README with architecture, command list, keyboard/inline flows, environment variables, deployment, and dev checks.
- Aligned README and runtime config for Glances logging behavior.
- Documented `GLANCES_REQUEST_TIMEOUT_SECONDS` and `GLANCES_LOG_FULL_PAYLOAD`.
- Added `GLANCES_LOG_FULL_PAYLOAD` to `.env.example` and `docker-compose.yml`.
- Added initial `CONTRIBUTING.md` guide.

### CI

- Added lint/type/test workflow for pushes and pull requests.
- Added container package workflow for GHCR publication.
- Optimized workflows with concurrency cancellation and dependency/build caching.

### Tests

- Added tests for key parsing and behavior in:
	- Glances aggregation timing behavior,
	- model name extraction and callback token sizing,
	- cloud model option configuration,
	- i18n locale/key matching,
	- alert confirmation payload parsing,
	- model selection normalization and splitting.