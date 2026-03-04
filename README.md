# ServerWatch AI Bot

## Bot specification

- [.github/bot_spec.md](.github/bot_spec.md)

## Deployment base (single-user MVP)

This repository is configured for a single-user deployment:

- `glances` runs inside the same Docker Compose stack.
- `ollama` is external and consumed via API URL.
- no Glances username/password is used in this MVP.

## Environment variables

Create your local env file from the example:

```bash
cp .env.example .env
```

Required values to set:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `OLLAMA_BASE_URL`
- `OLLAMA_MODEL`

Default internal Glances API URL in stack:

- `GLANCES_BASE_URL=http://glances:61208/api/4`

## Run with Docker Compose

```bash
docker compose up -d --build
```

Check service status:

```bash
docker compose ps
docker compose logs -f bot
```

Glances web UI is bound to localhost:

- http://127.0.0.1:61208

## Package publishing (GitHub)

Workflow file:

- `.github/workflows/package.yml`

On every push to `main`, GitHub Actions builds and publishes the Docker image to:

- `ghcr.io/artcc/serverwatch-ai-bot:latest`
- `ghcr.io/artcc/serverwatch-ai-bot:sha-<commit>`

## Quick package validation

After a push to `main` and successful workflow:

```bash
docker pull ghcr.io/artcc/serverwatch-ai-bot:latest
docker run --rm --env-file .env ghcr.io/artcc/serverwatch-ai-bot:latest
```

## Lint

Local run (inside this project):

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m ruff format --check .
./.venv/bin/python -m mypy app
```

Optional first-time setup (if `.venv` does not exist yet):

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip ruff mypy
```

CI run:

- `.github/workflows/lint.yml`
- Runs real code quality checks with `ruff` and `mypy` on GitHub Actions.