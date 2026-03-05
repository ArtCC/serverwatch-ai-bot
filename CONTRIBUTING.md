# Contributing to ServerWatch AI Bot

Thank you for your interest in contributing! Please read this guide before opening issues or pull requests.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Making Changes](#making-changes)
- [Commit Convention](#commit-convention)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Requesting Features](#requesting-features)

---

## Code of Conduct

Be respectful, constructive, and inclusive. We follow the [Contributor Covenant](https://www.contributor-covenant.org/).

---

## Getting Started

1. Fork the repository and clone your fork.
2. Create a new branch from `main` for your change.
3. Make your changes, add tests if applicable, and open a pull request.

---

## Development Setup

**Requirements:** Python 3.12+, Docker, Docker Compose.

```bash
# Create virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

# Copy environment file and fill in your values
cp .env.example .env
```

**Run locally with Docker Compose:**

```bash
docker compose up --build
```

**Lint and type-check:**

```bash
ruff check .
ruff format --check .
mypy app
```

---

## Project Structure

```
app/
  core/        # Config, auth, store
  handlers/    # Telegram update handlers
  services/    # Glances, Ollama, scheduler clients
  utils/       # Formatting helpers, i18n
locale/        # JSON locale files (en.json, ...)
```

---

## Making Changes

- Keep changes small and focused — one concern per pull request.
- Follow the existing code style (enforced by `ruff`).
- Use type hints everywhere.
- Add or update entries in `locale/en.json` for any user-facing text.
- Update `CHANGELOG.md` under `[Unreleased]` for every notable change.

---

## Commit Convention

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>
```

Common types: `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `ci`.

Examples:

```
feat(handlers): add /status command
fix(scheduler): prevent duplicate alerts during cooldown
docs: update README deployment section
```

---

## Pull Request Process

1. Ensure `ruff` and `mypy` pass with no errors.
2. Update `CHANGELOG.md` under `[Unreleased]`.
3. Fill in the pull request template with a clear description.
4. Request a review — the PR will be merged once approved.

---

## Reporting Bugs

Open a [GitHub Issue](../../issues) with:

- A clear title and description.
- Steps to reproduce.
- Expected vs actual behaviour.
- Relevant logs or screenshots.

---

## Requesting Features

Open a [GitHub Issue](../../issues) with the `enhancement` label and describe:

- The problem it solves.
- The proposed solution or behaviour.
- Any alternatives considered.
