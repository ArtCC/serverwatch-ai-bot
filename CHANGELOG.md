# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.7] - 2026-03-15

### Added

- Added inline cancel controls for free-text chat generation while the model is reasoning/streaming.
- Added per-request cancellation tokens and runtime cancellation events for active chat generations.
- Added localized chat cancellation strings in `en`, `es`, `de`, `fr`, and `it`:
	- cancel button label,
	- cancellation requested acknowledgement,
	- cancelled generation terminal message,
	- no-active-generation feedback.

### Changed

- Free-text chat placeholder now includes a `Cancel` inline button during streaming updates.
- Streaming loop now exits early when users request cancellation and finalizes the placeholder with a cancelled state.
- Registered a dedicated callback handler for chat cancellation (`chat_cancel:<token>`).

## [0.0.6] - 2026-03-13

### Added

- Added anti-noise controls for metric alerts with three new configuration variables:
	- `ALERT_CONSECUTIVE_BREACHES` (required consecutive breaches before firing),
	- `ALERT_RECOVERY_MARGIN_PERCENT` (hysteresis recovery band),
	- `ALERT_CONTEXT_WINDOW_SAMPLES` (rolling sample window for alert context).
- Added contextual alert details in metric notifications (rolling average and sustained duration).
- Added locale key `alerts_notification.context` in `en`, `es`, `de`, `fr`, and `it`.
- Added explicit streaming event channels (`thinking` and `answer`) in LLM routing to support richer Telegram progressive updates.
- Added Ollama streaming event support to surface model reasoning/thinking blocks when available.
- Added scheduler tests for alert stability behavior:
	- consecutive-breach triggering,
	- hysteresis anti-flapping,
	- re-trigger after full recovery,
	- contextual alert line rendering.

### Changed

- Refined scheduler alert state machine to reduce false positives and threshold flapping:
	- per-metric breach counters and rolling samples are tracked in bot runtime state,
	- recovery now requires dropping below `threshold - margin` before resetting alert state,
	- cooldown is now enforced only after at least one alert has actually been sent for a metric.
- Updated free-text chat UX: the initial `⏳ Thinking…` placeholder is now progressively edited with streamed model thinking blocks (when provided by the model/provider), then replaced by streamed final answer content.
- Updated `docker-compose.yml` and `.env.example` to include new alert scheduler variables with defaults aligned to runtime config.
- Updated README environment variable table with the new alert scheduler controls.

## [0.0.5] - 2026-03-09

### Added

- Added persistent bounded chat context storage in SQLite (`chat_context`) with per-chat retention.
- Added inline chat context controls in free-text replies: `ℹ️ Context`, `🧹 Clear`, and `❌ Close`.
- Added context usage panel showing used characters vs configured budget, estimated tokens, active-window message count, and stored message count.
- Added new configuration variables: `CHAT_CONTEXT_MAX_TURNS`, `CHAT_CONTEXT_MAX_CHARS`, and `CHAT_CONTEXT_RETENTION_MESSAGES`.
- Added locale strings for context controls and context usage panel in `en`, `es`, `it`, `de`, and `fr`.
- Added Glances limits and short-history endpoints to the snapshot bundle:
	- `/all/limits`
	- `/cpu/total/history/3`
	- `/mem/percent/history/3`
	- `/load/min1/history/3`
- Added operational health intelligence in `ServerSnapshot`:
	- global health score (`0-100`) and severity level (`good`, `warning`, `critical`),
	- trend labels for CPU/RAM/LOAD (`up`, `down`, `stable`),
	- key findings, recommended action, and watch-next hint,
	- enriched metrics for swap, process count, top network interface throughput, and top mounts.
- Added compact AI context serializer (`as_llm_context_json`) for high-signal, low-noise model prompts.
- Added global health alerting in scheduler (warning/critical) with cooldown-aware deduplication.
- Added Glances intelligence tests for trend detection, thresholds resolution, metric severity scoring, and top network interface selection.

### Changed

- Free-text chat now includes bounded multi-turn history when sending requests to LLM providers (Ollama, OpenAI, Anthropic, DeepSeek).
- Updated README to document bounded chat context behavior, context inline controls, and new environment variables.
- Free-text chat now uses compact operational Glances context instead of the full aggregated raw payload.
- `/status` output now prioritizes operational clarity with health scoring, trends, key findings, recommended action, and watch-next guidance.
- `/glances` endpoint summaries now extract more useful fields for real diagnostics:
	- network rates and interface state (`bytes_*_rate_per_sec`, `is_up`, `speed`),
	- container IO/network and memory limits,
	- process owner and thread count,
	- sensor warning/critical thresholds.
- README Glances section was expanded with the new 3-layer metrics pipeline (snapshot, operational, AI context) and scheduler health-alert behavior.

## [0.0.4] - 2026-03-08

### Changed

- Improved Glances API connectivity resilience: when `GLANCES_BASE_URL` points to `http://glances:...`, requests now retry automatically via `http://host.docker.internal:...` on DNS resolution failures (for example, `Name or service not known`).
- Hardened `/glances` detail callback flow against Telegram API races/timeouts while streaming summaries.
- Prevented close-button crashes in `/glances` detail view by safely handling callback answer, message edit, and message delete errors (`TimedOut`, stale/deleted message, and non-editable message scenarios).

## [0.0.3] - 2026-03-07

### Added

- Added `/glances` command to open a live per-endpoint Glances detail menu.
- Added new inline flow in `/status` to open Glances details directly from the status card.
- Added `app/handlers/glances_menu.py` to handle endpoint selection, live fetch, refresh, back, and close actions.
- Added localized `glances.*` UI strings and `commands.glances` descriptions for `en`, `es`, `it`, `de`, and `fr` locales.
- Added on-demand Glances service API `get_live_endpoint_detail(key)` with allowlisted endpoint keys.
- Added German and French locale files (`locale/de.json`, `locale/fr.json`) with full translations for commands, keyboard labels, handlers, errors, and alert notifications.
- Added `/author` command and handler to show the project author and GitHub profile.
- Added `commands.author` and `author.text` locale keys for `en`, `es`, `it`, `de`, and `fr`.
- Added `glances.loading` locale key for localized loading state messages in the Glances detail flow.

### Changed

- Added a soft status response template in chat prompts so status answers keep a consistent structure without becoming restrictive.
- Updated help text and README to document the new `/glances` command and inline details flow.
- Updated i18n tests to reflect the expanded supported locale set and current locale fallback behavior.
- Changed Glances detail output from raw JSON to LLM-generated summaries based on the selected endpoint payload.
- Enabled streaming responses for Glances detail summaries (progressive edit updates like free-text chat).
- Improved Glances detail robustness for Telegram callbacks and edits (`BadRequest` handling, stale callbacks, long message fallback, and not-modified edits).
- Optimized Glances detail summarization latency by reducing endpoint payloads before sending them to the LLM and adding fetch/summarize timing logs.
- Simplified Glances detail header wording to remove "live" phrasing and use cleaner labels across locales.

## [0.0.2] - 2026-03-06

### Added

- Added Italian locale file `locale/it.json` with full translations for commands, keyboard labels, handlers, errors, and alert notifications.

### Changed

- Localized Telegram `set_my_commands` descriptions via i18n keys instead of hardcoded English strings.
- Registered bot command descriptions per `language_code` for all supported locales discovered in `locale/*.json`.
- Added `commands.*` translation keys to locale files so command menu labels are language-aware.
- Updated README locale documentation to include Italian support.

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