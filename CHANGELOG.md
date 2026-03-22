# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.12] - 2026-03-22

### Fixed

- **Telegram flood control handling**: added `RetryAfter` exception handling in `streaming.py` (`_safe_edit`), `chat.py` (`_safe_edit_or_reply`), and the global `error_handler` in `main.py`. The bot now waits the required cooldown instead of silently dropping the operation or crashing with an unhandled exception.

### Changed

- **Streaming edit interval**: increased `DEFAULT_EDIT_INTERVAL` from 0.3 s to 1.0 s in `streaming.py` to reduce Telegram API pressure and avoid triggering rate limits during LLM streaming.

## [0.0.11] - 2026-03-22

### Added

- **LLM provider sub-package** (`app/services/providers/`): extracted OpenAI, Anthropic and DeepSeek implementations into dedicated modules (`openai_provider.py`, `anthropic_provider.py`, `deepseek_provider.py`) plus a shared `common.py` with HTTP client, SSE parsing, message builders and delta extractors.
- **Config validation**: `Config.__post_init__` now enforces range checks on all 14 numeric fields — invalid values raise `ValueError` at startup.
- **`ANTHROPIC_MAX_TOKENS`** environment variable (default `2048`) to control Anthropic response length. Added to config, Docker Compose, and Anthropic provider payloads.
- **Docker healthcheck** in `docker-compose.yml` (`python3 -c "import app"`, interval 30 s, 3 retries).
- **Multilingual Glances hints**: `_GLANCES_HINTS` in `chat.py` expanded from ~28 to ~53 keywords covering French, German and Italian.
- **SQLite WAL mode**: `PRAGMA journal_mode=WAL` in `init_db()` for better concurrent read performance.
- **32 new tests** (40 → 72): config validation (19 parametrized), `_trim_history_window` & `sanitize_history` (7), store CRUD with `:memory:` SQLite (6).
- **CONTRIBUTING.md**: documented singleton + lock pattern with table of singletons and testing rules.

### Changed

- **LLM router refactored**: `llm_router.py` reduced from 837 lines to ~200-line routing layer that delegates to provider sub-modules. Dead `_stream_with_fallback` removed; only `_stream_with_fallback_events` retained.
- **`_prepare_llm_payload` data-driven**: replaced ~120 lines of cascading `if/elif` with two lookup dicts (`_DICT_ENDPOINT_FIELDS`, `_LIST_ITEM_FIELDS`) in `glances_menu.py`.
- **`chat_handler` decomposed**: extracted `_resolve_system_prompt()` (Glances decision + prompt building) and `_persist_exchange()` (context persistence) helpers in `chat.py`.
- **Dockerfile build order**: `COPY app/__init__.py` before `pip install .` so setuptools can find the package during dependency resolution.
- **Ruff version pinned**: `ruff>=0.15` → `ruff>=0.15,<1.0` to avoid unexpected breaking changes.

### Removed

- `app/utils/formatting.py` — dead code (`info`/`success`/`warning`/`error` helpers were never imported).
- `get_threshold_cpu`, `get_threshold_ram`, `get_threshold_disk` individual getters from `store.py` (superseded by `get_thresholds()`).

## [0.0.10] - 2026-03-22

### Changed

- **Unified LLM streaming**: extracted the duplicated streaming loop from `chat.py` and `glances_menu.py` into a single `stream_to_telegram()` helper in `app/utils/streaming.py`. Edit interval reduced from 0.8 s to 0.3 s for a smoother *letter-by-letter* feel.
- `StreamChunk` dataclass moved from `app/services/llm_router` to `app/utils/streaming` (re-exported from `llm_router` for backward compatibility).
- **Glances data fetching**: replaced 21 individual endpoint requests with a single `GET /all` call plus `/all/limits` and 3 history endpoints. Reduces HTTP round-trips from 22 to 5 and guarantees temporal consistency across metrics.
- **Enriched LLM context** (`as_llm_context_json`): payload now includes all containers (name/status/CPU/RAM/IO), all filesystem mounts, all active network interfaces with RX/TX rates, all sensors (label/value/unit/warning/critical), disk I/O rates, system info (hostname/OS/distro), and top 10 processes with full detail.
- **ServerSnapshot** dataclass extended with `all_network_interfaces`, `all_containers`, `all_sensors`, `all_diskio`, and `system_info` fields.
- **Docker Glances service** is now optional and commented out in `docker-compose.yml`. Host-based Glances is the recommended setup.
- Top mounts and top processes limits increased from 3/5 to all mounts and top 10 processes respectively.

### Removed

- `_ENDPOINTS` tuple, `_CRITICAL_ENDPOINT_KEYS` frozenset, and `_max_concurrency()` function — no longer needed with `/all`.
- `_fetch_all()` semaphore-based parallel fetch function — replaced by direct `/all` request.
- `GLANCES_MAX_CONCURRENCY` environment variable from config, Docker Compose, `.env.example`, and README.
- `os` import dependency from `glances.py`.

## [0.0.9] - 2026-03-20

### Added

- Added missing runtime variables to Docker Compose bot service for full config parity:
	- `GLANCES_LOG_FULL_PAYLOAD`,
	- `CHAT_CONTEXT_MAX_TURNS`,
	- `CHAT_CONTEXT_MAX_CHARS`,
	- `CHAT_CONTEXT_RETENTION_MESSAGES`.
- Added missing chat context variables to `.env.example`:
	- `CHAT_CONTEXT_MAX_TURNS`,
	- `CHAT_CONTEXT_RETENTION_MESSAGES`.
- Added Glances GPU parsing coverage tests for nested payload variants and memory usage derivation.

### Changed

- Increased default `CHAT_CONTEXT_MAX_CHARS` from `6000` to `10000` in runtime config and docs.
- Hardened context clear callback to ignore Telegram `BadRequest: Message is not modified` when the context panel message is already up to date.
- Improved GPU data normalization in Glances integration to handle multiple payload shapes/field names and derive VRAM percent from used/total values when needed.
- Updated `docker-compose.yml` Glances service with GPU runtime access hints (`gpus: all` and `/dev/dri` mapping) to improve NVIDIA + Intel iGPU telemetry visibility.

## [0.0.8] - 2026-03-20

### Added

- Added GPU endpoint support in Glances aggregation and live detail flows:
	- `/gpu` is now fetched as part of the fixed endpoint bundle,
	- `/gpu` is now available in the `/glances` detail menu and callback allowlist.
- Added GPU operational summary fields to `ServerSnapshot`:
	- GPU availability and device count,
	- top GPU name,
	- top GPU utilization percent,
	- top GPU memory percent,
	- top GPU temperature.
- Added `status.gpu` locale key in `en`, `es`, `de`, `fr`, and `it`.
- Added Glances unit tests for GPU endpoint registration and top-GPU selection behavior.

### Changed

- `/status` now includes a GPU line (when GPU metrics are available) with utilization, VRAM usage, and temperature.
- Compact AI status context (`as_llm_context_json`) now includes a dedicated `gpu` object for downstream model reasoning.
- Operational scoring/watch-next logic now considers GPU utilization when GPU data is present.

## [0.0.7] - 2026-03-15

### Added

- Added inline cancel controls for free-text chat generation while the model is reasoning/streaming.
- Added per-request cancellation tokens and runtime cancellation events for active chat generations.
- Added localized chat cancellation strings in `en`, `es`, `de`, `fr`, and `it`:
	- cancel button label,
	- cancellation requested acknowledgement,
	- cancelled generation terminal message,
	- no-active-generation feedback.
- Added local Ollama model management actions inside `/models`:
	- install by model name from inline flow,
	- live pull progress bar (`█░`, percentage, MB transferred),
	- inline cancel button for active model downloads,
	- delete local model by selection with inline confirmation.
- Added Ollama local model lifecycle API helpers:
	- `pull_model()` streaming integration for `/api/pull`,
	- `delete_model()` integration for `DELETE /api/delete`.
- Added localized model-download cancellation strings in `en`, `es`, `de`, `fr`, and `it`:
	- cancel download button label,
	- cancellation requested acknowledgement,
	- cancelled download message,
	- no-active-download feedback.

### Changed

- Free-text chat placeholder now includes a `Cancel` inline button during streaming updates.
- Streaming loop now exits early when users request cancellation and finalizes the placeholder with a cancelled state.
- Registered a dedicated callback handler for chat cancellation (`chat_cancel:<token>`).
- Updated `/models` inline UX to include install and delete local-model actions, while keeping destructive operations protected by confirmation.

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