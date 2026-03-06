# MVP Task List — ServerWatch AI Bot

## Dependencies and configuration
- [x] Add dependencies to `pyproject.toml`: `python-telegram-bot`, `httpx`, `aiosqlite`
- [x] Update `Dockerfile` to install dependencies

## Base architecture
- [x] Create folder structure: `app/handlers/`, `app/services/`, `app/core/`, `app/utils/`
- [x] `app/core/config.py` — environment variable loading and validation
- [x] `app/utils/formatting.py` — message helpers with ℹ️ ✅ ⚠️ ❌
- [x] `app/core/auth.py` — middleware to block messages from users other than `TELEGRAM_CHAT_ID`
- [x] `locale/en.json` — centralise all bot texts
- [x] `app/utils/i18n.py` — locale loader and key accessor with interpolation support (multi-language)

## Bot bootstrap — first deployable milestone
- [x] `app/main.py` — refactor: initialise `Application` from `python-telegram-bot`
- [x] Register `setMyCommands`
- [x] Persistent `ReplyKeyboardMarkup` (buttons: Status, Alerts, Models, Help)
- [x] Global error handler with friendly response
- [x] `app/handlers/start.py` — `/start` → personalised greeting with Telegram username + persistent keyboard

## Service layer
- [x] `app/services/glances.py` — async client for Glances API (CPU, RAM, disk, network, processes, Docker)
- [x] `app/services/ollama.py` — async client for Ollama (list models, chat/completions with injected context)
- [x] `app/core/store.py` — SQLite with `aiosqlite` (conversation history, alert thresholds)

## Handlers
- [x] `app/handlers/chat.py` — free-text message → gather metrics → prompt with context → LLM response
- [x] `app/handlers/status.py` — `/status` / Status button → visual metrics summary
- [x] `app/handlers/alerts.py` — `/alerts` / Alerts button → view/edit thresholds with inline buttons + confirmation
- [x] `app/handlers/models.py` — `/models` / Models button → active model selection flow

## Ollama model management
- [x] `app/core/store.py` — persist the active model selected by the user in SQLite
- [x] On startup, initialise the active model from `OLLAMA_MODEL` if none is saved in the DB
- [x] `/models` shows the list of installed models with the active one marked (✅)
- [x] Inline buttons to select any model from the list
- [x] Confirmation before changing the active model
- [x] All Ollama prompts use the active model stored in the DB

## Optional cloud model providers (OpenAI, Anthropic, DeepSeek)
- [x] `app/core/config.py` — add optional env vars for API keys:
	`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`
- [x] `app/core/config.py` — add optional env vars for fixed models:
	`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `DEEPSEEK_MODEL`
- [x] `.env.example` and `README.md` — document the 6 new optional vars
- [x] `app/services/` — implement provider clients for OpenAI, Anthropic and DeepSeek chat APIs
- [x] Unified chat routing: use the currently active model/provider (Ollama or cloud)
- [x] `/models` flow — keep listing local Ollama installed models
- [x] `/models` flow — also append `OpenAI`, `Anthropic`, `DeepSeek` model options only when both API key and model are configured
- [x] Cloud provider options are fixed by env (single model per provider): no extra in-bot per-provider model selection
- [x] Persist active selection in store with provider metadata (e.g. `ollama:<name>`, `openai:<model>`, `anthropic:<model>`, `deepseek:<model>`)
- [x] Validate graceful fallback/errors when cloud provider is selected but unreachable (friendly user message)

## Alert engine
- [ ] `app/services/scheduler.py` — async loop that checks metrics every `ALERT_CHECK_INTERVAL_SECONDS`
- [ ] Cooldown logic (`ALERT_COOLDOWN_SECONDS`) to avoid spamming repeated alerts
- [ ] Proactive alert message to the chat when a threshold is exceeded

## Documentation (keep updated as we go)
- [x] `CHANGELOG.md`
- [x] `CONTRIBUTING.md`
- [x] `README.md`