# 🤖 ServerWatch AI Bot

<p align="left">
  <img src="https://github.com/ArtCC/serverwatch-ai-bot/blob/main/assets/serverwatch-ai-bot.png" alt="ServerWatch AI Bot" width="175"/>
</p>

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](Dockerfile)

A single-user Telegram bot that monitors your server and answers questions about it using a local LLM via [Ollama](https://ollama.com). Metrics are sourced from [Glances](https://nicolargo.github.io/glances/) running in the same Docker Compose stack.

---

## Implementation status

| Area | Status |
|---|---|
| Project scaffold + Docker Compose | ✅ Done |
| Config, auth, formatting, i18n | ✅ Done |
| `/start` + persistent keyboard | ✅ Done |
| SQLite persistence layer (`store.py`) | ✅ Done |
| Ollama client + `/models` flow | ✅ Done |
| Glances client + `/status` | ✅ Done |
| `/alerts` threshold management | ✅ Done |
| Free-text chat with LLM context | ✅ Done |
| Alert engine (background scheduler) | 🔧 Pending |

---

## How it works

1. You send a message or command from Telegram.
2. The bot collects live metrics from Glances (CPU, RAM, disk, processes, Docker…).
3. That context is injected into a prompt sent to your local LLM via Ollama.
4. The LLM replies in natural language directly in your Telegram chat.

Commands and persistent keyboard buttons are shortcuts for common actions. Everything else is free-text conversation with the LLM.

---

## Commands

| Command | Description |
|---|---|
| `/start` | Start the bot and show the persistent keyboard |
| `/status` | Current server metrics snapshot |
| `/alerts` | View and configure alert thresholds |
| `/models` | List installed Ollama models and select the active one |
| `/help` | Show help message |

---

## Persistent keyboard

A `ReplyKeyboardMarkup` is always visible with four quick-access buttons:

```
[ 📊 Status  ] [ 🔔 Alerts ]
[ 🤖 Models  ] [ ❓ Help   ]
```

---

## Inline flows

- **Model selection** (`/models`): lists all models installed in Ollama. The active model is marked with ✅. Tap any model to switch — a confirmation step is required before the change takes effect.
- **Alert thresholds** (`/alerts`): shows current CPU / RAM / disk thresholds with inline Edit buttons. Every threshold change requires confirmation before saving.

---

**Folder structure**

```
.
├── app/
│   ├── main.py               # Bootstrap, polling, setMyCommands, error handler
│   ├── core/
│   │   ├── config.py         # Typed Config dataclass — env vars
│   │   ├── auth.py           # @restricted — single-user access control
│   │   └── store.py          # SQLite persistence — thresholds, active model
│   ├── handlers/
│   │   ├── start.py          # /start — greeting + persistent keyboard
│   │   ├── help.py           # /help — help message
│   │   ├── status.py         # /status — metrics snapshot
│   │   ├── alerts.py         # /alerts — threshold management + inline buttons
│   │   ├── models.py         # /models — model listing and selection
│   │   └── chat.py           # Free-text → live context → LLM
│   ├── services/
│   │   ├── glances.py        # Async Glances REST API client
│   │   └── ollama.py         # Async Ollama API client (list models + chat)
│   └── utils/
│       ├── formatting.py     # info() / success() / warning() / error()
│       └── i18n.py           # Locale loader and t() key accessor
└── locale/
    └── en.json               # All bot-facing strings
```

---

## Environment variables

Copy the example file and fill in the required values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | ✅ | — | Your personal chat ID (single-user) |
| `OLLAMA_BASE_URL` | | `http://host.docker.internal:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | | `llama3.2:3b` | Default model used on first start |
| `GLANCES_BASE_URL` | | `http://glances:61208/api/4` | Glances REST API base URL |
| `BOT_LOG_LEVEL` | | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `BOT_LOCALE` | | `en` | Bot UI locale (must match a file in `locale/`) |
| `TZ` | | `UTC` | Container timezone |
| `SQLITE_PATH` | | `/app/data/serverwatch.db` | SQLite database path (inside container) |
| `ALERT_CHECK_INTERVAL_SECONDS` | | `60` | How often the alert engine checks metrics |
| `ALERT_COOLDOWN_SECONDS` | | `300` | Minimum time between repeated alerts for the same metric |
| `ALERT_DEFAULT_CPU_THRESHOLD` | | `85` | Default CPU alert threshold (%) |
| `ALERT_DEFAULT_RAM_THRESHOLD` | | `85` | Default RAM alert threshold (%) |
| `ALERT_DEFAULT_DISK_THRESHOLD` | | `90` | Default disk alert threshold (%) |

---

## Deployment

### Local — Docker Compose

```bash
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
docker compose up -d --build
```

Check status:

```bash
docker compose ps
docker compose logs -f bot
```

Glances web UI (localhost only):

- http://127.0.0.1:61208

### Portainer

Deploy as a stack using the `docker-compose.yml` from this repository. Define all environment variables directly in the Portainer stack UI — no `.env` file is needed.

---

## Data persistence

| Host path | Container path | Contents |
|---|---|---|
| `./data` | `/app/data` | SQLite database |

---

## Development setup

Requirements: Python 3.12+, Docker, Docker Compose.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
pip install ruff mypy
```

### Lint and type-check

```bash
ruff check .
ruff format --check .
mypy app
```

CI runs the same checks automatically on every push via `.github/workflows/lint.yml`.

---

## CI / CD

| Workflow | Trigger | Action |
|---|---|---|
| `lint.yml` | Push / PR | `ruff` check + format + `mypy` |
| `package.yml` | Push to `main` | Build and publish Docker image to GHCR |

Published image tags:

- `ghcr.io/artcc/serverwatch-ai-bot:latest`
- `ghcr.io/artcc/serverwatch-ai-bot:sha-<commit>`

Quick validation after publish:

```bash
docker pull ghcr.io/artcc/serverwatch-ai-bot:latest
docker run --rm --env-file .env ghcr.io/artcc/serverwatch-ai-bot:latest
```

## 🎨 Bot Avatar

You can use the official bot avatar for your own instance:

<p align="left">
  <img src="https://github.com/ArtCC/serverwatch-ai-bot/blob/main/assets/serverwatch-ai-bot.png" alt="ServerWatch AI Bot" width="200"/>
</p>

**Download**: [serverwatch-ai-bot.png](https://github.com/ArtCC/serverwatch-ai-bot/blob/main/assets/serverwatch-ai-bot.png)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[Apache License 2.0](LICENSE)

## Author

Arturo Carretero Calvo — [@artcc](https://github.com/artcc)