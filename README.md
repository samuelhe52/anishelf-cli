# anishelf-cli

Read-only AniShelf and CloudKit inspection CLI.

## Status

The command surface exists, but most commands are still placeholders while the implementation is completed in small vertical slices.

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

`ani login` stores the user-scoped CloudKit web auth token in the OS secure
credential store. This token authorizes access to the signed-in user's private
CloudKit data and is removed with `ani logout`.

TMDb API keys can be stored in Keychain with `ani config set-tmdb-api-key`.
