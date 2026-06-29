# anishelf-cli

Read-only AniShelf library inspection CLI.

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

`ani auth login` stores the user-scoped CloudKit web auth token in the OS secure
credential store. This token authorizes access to the signed-in user's private
CloudKit data and is removed with `ani auth logout`. Use `ani auth status` to
verify the current login and `ani auth refresh` to roll forward stored auth state
when CloudKit returns a successor token.

TMDb API keys can be stored in Keychain with `ani config set-tmdb-api-key`.

## JSON output

Commands that support JSON accept `--json` either globally or on the command:

```bash
uv run ani --json library get movie:55
uv run ani library get movie:55 --json
```

`library get` emits an ordered envelope designed for `jq`: `.summary` contains
counts, and `.items[]` contains either `.entry` or `.error`.

```bash
uv run ani library get movie:55 --json | jq '.items[].entry.watch_status'
uv run ani library get movie:55 --json | jq '.items[] | {identity, score: .entry.score}'
uv run ani library get movie:55 --json | jq '.items[] | select(.status == "error")'
```
