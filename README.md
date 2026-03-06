# 🤖 ServerWatch AI Bot

<p align="left">
  <img src="https://github.com/ArtCC/serverwatch-ai-bot/blob/main/assets/serverwatch-ai-bot.png" alt="ServerWatch AI Bot" width="175"/>
</p>

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](Dockerfile)

Single-user Telegram bot for server monitoring plus AI chat.
Supports local Ollama models and optional cloud providers (OpenAI, Anthropic, DeepSeek).

## How it works

1. You send a command or free-text message from Telegram.
2. The bot fetches live server metrics from Glances.
3. Metrics are injected into the prompt.
4. The prompt is sent to the currently active model option (`ollama`, `openai`, `anthropic`, or `deepseek`).
5. The reply is sent back in Telegram.

## Commands

| Command | Description |
|---|---|
| `/start` | Start the bot and show the persistent keyboard |
| `/status` | Current server metrics snapshot |
| `/alerts` | View and configure alert thresholds |
| `/models` | List model options and select the active one |
| `/help` | Show help message |

## Persistent keyboard

```
[ 📊 Status  ] [ 🔔 Alerts ]
[ 🤖 Models  ] [ ❓ Help   ]
```

## Inline flows

- `Model selection` (`/models`): lists all local Ollama models and optionally cloud provider options (`OpenAI`, `Anthropic`, `DeepSeek`) when API key and model are configured. Active option is marked with `✅`.
- `Alert thresholds` (`/alerts`): edit CPU/RAM/Disk thresholds with confirmation.

## Architecture tree

```text
.
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── auth.py
│   │   ├── config.py
│   │   └── store.py
│   ├── handlers/
│   │   ├── alerts.py
│   │   ├── chat.py
│   │   ├── help.py
│   │   ├── models.py
│   │   ├── start.py
│   │   └── status.py
│   ├── services/
│   │   ├── glances.py
│   │   ├── llm_router.py
│   │   └── ollama.py
│   └── utils/
│       ├── formatting.py
│       └── i18n.py
├── locale/
│   ├── en.json
│   └── es.json
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Token from BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | — | Authorized chat ID |
| `GLANCES_BASE_URL` | | `http://glances:61208/api/4` | Glances API base URL |
| `OLLAMA_BASE_URL` | | `http://host.docker.internal:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | | `llama3.2:3b` | Default Ollama model |
| `OPENAI_API_KEY` | | — | Optional OpenAI API key |
| `OPENAI_MODEL` | | — | Optional fixed OpenAI model |
| `ANTHROPIC_API_KEY` | | — | Optional Anthropic API key |
| `ANTHROPIC_MODEL` | | — | Optional fixed Anthropic model |
| `DEEPSEEK_API_KEY` | | — | Optional DeepSeek API key |
| `DEEPSEEK_MODEL` | | — | Optional fixed DeepSeek model |
| `BOT_LOG_LEVEL` | | `INFO` | Logging level |
| `BOT_LOCALE` | | `en` | Fallback locale |
| `TZ` | | `UTC` | Timezone |
| `SQLITE_PATH` | | `/app/data/serverwatch.db` | SQLite path |
| `DATA_PATH` | | `/opt/docker/serverwatch-ai-bot/data` | Host path mounted into `/app/data` |
| `ALERT_CHECK_INTERVAL_SECONDS` | | `60` | Alert scan interval |
| `ALERT_COOLDOWN_SECONDS` | | `300` | Alert cooldown |
| `ALERT_DEFAULT_CPU_THRESHOLD` | | `85` | Default CPU threshold |
| `ALERT_DEFAULT_RAM_THRESHOLD` | | `85` | Default RAM threshold |
| `ALERT_DEFAULT_DISK_THRESHOLD` | | `90` | Default Disk threshold |

## Deployment

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
```

## Dev checks

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m ruff format --check .
./.venv/bin/python -m mypy app
./.venv/bin/python -m pytest -q
```

## Contributing

See `CONTRIBUTING.md`.
