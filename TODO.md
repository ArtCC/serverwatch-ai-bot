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
- [ ] `app/services/glances.py` — async client for Glances API (CPU, RAM, disk, network, processes, Docker)
- [ ] `app/services/ollama.py` — async client for Ollama (list models, chat/completions with injected context)
- [ ] `app/core/store.py` — SQLite with `aiosqlite` (conversation history, alert thresholds)

## Handlers
- [ ] `app/handlers/chat.py` — free-text message → gather metrics → prompt with context → LLM response
- [ ] `app/handlers/status.py` — `/status` / Status button → visual metrics summary
- [ ] `app/handlers/alerts.py` — `/alerts` / Alerts button → view/edit thresholds with inline buttons + confirmation
- [ ] `app/handlers/models.py` — `/models` / Models button → active model selection flow

## Ollama model management
- [ ] `app/core/store.py` — persist the active model selected by the user in SQLite
- [ ] On startup, initialise the active model from `OLLAMA_MODEL` if none is saved in the DB
- [ ] `/models` shows the list of installed models with the active one marked (✅)
- [ ] Inline buttons to select any model from the list
- [ ] Confirmation before changing the active model
- [ ] All Ollama prompts use the active model stored in the DB

## Alert engine
- [ ] `app/services/scheduler.py` — async loop that checks metrics every `ALERT_CHECK_INTERVAL_SECONDS`
- [ ] Cooldown logic (`ALERT_COOLDOWN_SECONDS`) to avoid spamming repeated alerts
- [ ] Proactive alert message to the chat when a threshold is exceeded

## Documentation (keep updated as we go)
- [ ] `CHANGELOG.md`
- [ ] `CONTRIBUTING.md`
- [ ] `README.md`