# anishelf-cli

Read-only AniShelf library inspection CLI.

## Status

The CLI implements CloudKit auth, effective configuration display, direct
library entry lookup, and cached library list/search/export. Some surfaces, such
as low-level diagnostics, metadata hydration depth, `library changes`, and
top-level `tmdb search`, are still placeholders while implementation continues
in small vertical slices.

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

`library list` and `library export` read from a rebuildable SQLite cache. By
default they refresh the authenticated user's CloudKit zone changes first; use
`--offline` to read the existing cache without network access.

```bash
uv run ani library list --json | jq '.entries[] | {identity, watch_status}'
uv run ani library export --offline --json | jq '.summary.cache.mode'
uv run ani library export --include-tombstones --json | jq '.entries[] | select(.kind != "snapshot")'
```

`library search --title` refreshes the cache, searches TMDb movie and TV titles
with the configured API key, intersects the returned TMDb IDs with cached
movie/series entries, and includes seasons whose parent series matched.

```bash
uv run ani library search --title "Alien" --json | jq '.entries[].identity'
```

Library commands accept optional `--metadata` parsing for future TMDb enrichment.
Bare `--metadata` selects the default summary level; explicit levels use `none`,
`summary`, `details`, or `full`. Full metadata hydration is still mostly inert in
the current implementation.
