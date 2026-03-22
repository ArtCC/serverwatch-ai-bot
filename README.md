# 🤖 ServerWatch AI Bot

<p align="left">
  <img src="https://github.com/ArtCC/serverwatch-ai-bot/blob/main/assets/serverwatch-ai-bot.png" alt="ServerWatch AI Bot" width="175"/>
</p>

[![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](Dockerfile)

Single-user Telegram bot for server monitoring plus AI chat.
Supports local Ollama models and optional cloud providers (OpenAI, Anthropic, DeepSeek).

Supported locales: `en`, `es`, `it`, `de`, `fr`.

## How it works

1. You send a command or free-text message from Telegram.
2. The bot fetches live server metrics from Glances.
3. Metrics are injected into the prompt.
4. The prompt is sent to the currently active model option (`ollama`, `openai`, `anthropic`, or `deepseek`).
5. The reply is sent back in Telegram.

Free-text chat now uses a bounded multi-turn context window (turns + char budget)
so follow-up questions keep continuity without unbounded prompt growth.

## Glances usage

The bot uses the Glances REST API v4 (`GLANCES_BASE_URL`) and currently fetches
metrics through individual endpoints (not `/all`) to keep requests focused and
reduce prompt size.

The metrics pipeline now includes three layers:

- `Snapshot layer`: live endpoint fetch with short cache (`10s`) and fallback host resolution.
- `Operational layer`: health score (`0-100`), severity (`good|warning|critical`), key findings,
  and next-action hints built from thresholds + live values.
- `AI context layer`: compact JSON payload for chat prompts (high signal, low noise).

When metrics are requested (`/status`, refresh button, or free-text chat), the
bot queries this fixed bundle in parallel:

- `/status`
- `/cpu`
- `/load`
- `/mem`
- `/memswap`
- `/fs`
- `/processcount`
- `/uptime`
- `/diskio`
- `/network`
- `/containers`
- `/processlist/top/10`
- `/sensors`
- `/system`
- `/core`
- `/version`
- `/pluginslist`
- `/gpu`
- `/all/limits`
- `/cpu/total/history/3`
- `/mem/percent/history/3`
- `/load/min1/history/3`

Notes:

- Base URL example: `http://glances:61208/api/4`
- Glances auth is optional in this setup (works without auth if Glances is not
  started with `--password`)
- By default, the bot logs only aggregated payload keys at `DEBUG` level.
- Set `GLANCES_LOG_FULL_PAYLOAD=true` to log the full aggregated Glances JSON
  at `INFO` level for diagnostics (for example in container logs).
- For GPU telemetry in Docker, Glances must have runtime device access:
  `gpus: all` for NVIDIA and `/dev/dri` mapping for Intel iGPU.
- `/status` now prioritizes operational value: trends, top bottlenecks, recommended action,
  what to watch next, and top GPU usage when available.
- Free-text AI chat now uses compact status context (`as_llm_context_json`) instead of the
  full raw bundle to improve latency and reduce prompt noise.
- Scheduler alerts include classic metric threshold alarms plus a global health alert
  when the operational score degrades to warning/critical.

## Commands

| Command | Description |
|---|---|
| `/start` | Start the bot and show the persistent keyboard |
| `/status` | Current server metrics snapshot |
| `/alerts` | View and configure alert thresholds |
| `/glances` | Open live Glances per-endpoint detail menu |
| `/models` | Open model manager: switch active model, install local Ollama models, and delete local models |
| `/help` | Show help message |

## Persistent keyboard

```
[ 📊 Status  ] [ 🔔 Alerts ]
[ 🤖 Models  ] [ ❓ Help   ]
```

## Inline flows

- `Model manager` (`/models`): lists all local Ollama models and optionally cloud provider options (`OpenAI`, `Anthropic`, `DeepSeek`) when API key and model are configured. Active option is marked with `✅`.
- `Model install` (inside `/models`): tap `⬇️ Install model`, reply with an Ollama model name (for example `llama3.2:3b`), and the bot starts `POST /api/pull` with a live progress bar (`█░`, percentage, MB transferred).
- `Install cancel` (during model download): while a local model is downloading, an inline `⛔ Cancel download` button is shown; pressing it requests cancellation and stops the active pull safely.
- `Model delete` (inside `/models`): tap `🗑️ Delete local model`, select one local Ollama model from the inline list, then confirm with `✅ Confirm` before deletion (`DELETE /api/delete`).
- `Alert thresholds` (`/alerts`): edit CPU/RAM/Disk thresholds with confirmation.
- `Glances details` (`/glances` or inline button from `/status`): open a live menu and fetch one Glances endpoint on demand (CPU, RAM, disk, GPU, network, containers, top processes, etc.).
- `Chat context controls` (free-text replies): `ℹ️ Context` shows current context usage; panel includes `🧹 Clear` and `❌ Close`.

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
│   │   ├── glances_menu.py
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
│   ├── es.json
│   ├── it.json
│   ├── de.json
│   └── fr.json
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
| `GLANCES_REQUEST_TIMEOUT_SECONDS` | | `8.0` | Per-endpoint Glances request timeout |
| `GLANCES_MAX_CONCURRENCY` | | `4` | Max simultaneous Glances endpoint requests from the bot |
| `GLANCES_LOG_FULL_PAYLOAD` | | `false` | Log full aggregated Glances payload at `INFO` |
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
| `ALERT_CHECK_INTERVAL_SECONDS` | | `60` | Alert scan interval (`0` disables scheduler polling) |
| `ALERT_COOLDOWN_SECONDS` | | `300` | Alert cooldown |
| `ALERT_DEFAULT_CPU_THRESHOLD` | | `85` | Default CPU threshold |
| `ALERT_DEFAULT_RAM_THRESHOLD` | | `85` | Default RAM threshold |
| `ALERT_DEFAULT_DISK_THRESHOLD` | | `90` | Default Disk threshold |
| `ALERT_CONSECUTIVE_BREACHES` | | `2` | Consecutive checks required before firing metric alerts |
| `ALERT_RECOVERY_MARGIN_PERCENT` | | `5` | Hysteresis margin (threshold - margin) required to reset alert state |
| `ALERT_CONTEXT_WINDOW_SAMPLES` | | `3` | Number of recent samples used for alert context average |
| `CHAT_CONTEXT_MAX_TURNS` | | `8` | Max past user+assistant turns included in AI context |
| `CHAT_CONTEXT_MAX_CHARS` | | `10000` | Max total chars for stored context window sent to AI |
| `CHAT_CONTEXT_RETENTION_MESSAGES` | | `200` | Max persisted chat messages kept per chat |

## Deployment

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
```

## Dev checks

Recommended command (all-in-one):

```bash
./.venv/bin/python -m ruff check . && ./.venv/bin/python -m ruff format --check . && ./.venv/bin/python -m mypy app
```

You can also run them separately:

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m ruff format --check .
./.venv/bin/python -m mypy app
./.venv/bin/python -m pytest -q
```

## Contributing

See `CONTRIBUTING.md`.

## License

This project is licensed under the Apache 2.0 License. See `LICENSE`.

## Author

[ArtCC](https://github.com/ArtCC)

Built to keep server operations simple, clear, and always one message away.