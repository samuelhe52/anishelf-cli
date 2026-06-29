# anishelf-cli

Read-only AniShelf and CloudKit inspection CLI.

## Status

This repository is being scaffolded from the `cloudkit-private-db-agent-cli` OpenSpec change. The command surface exists, but most commands are still placeholders while the implementation is completed task-by-task.

## Tooling

- Python `>=3.13`
- `uv` for environment and dependency management
- `pytest` for tests
- `ruff` for linting
- `mypy` for type checking

## Quick start

```bash
uv sync
uv run ani --help
uv run pytest
uv run ruff check .
uv run mypy src
```

## Secret handling

Do not commit CloudKit or TMDb credentials to this repository. Use Keychain-backed storage or a local env file outside the repo such as `~/.secrets/env/anishelf-cli.env`.
