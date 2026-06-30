# anishelf-cli

Read-only AniShelf library inspection CLI.

## Status

The CLI implements CloudKit auth, effective configuration display, direct
library entry lookup, and cached library list/search/export. Some surfaces, such
as low-level diagnostics, metadata hydration depth, and top-level `tmdb search`,
are still placeholders while implementation continues in small vertical slices.

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
CloudKit data. `ani auth logout` removes that token and clears all local library
cache files. Use `ani auth status` to verify the current login and `ani auth
refresh` to roll forward stored auth state when CloudKit returns a successor
token.

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

Run `ani library init` once before using the read commands. Initialization
fetches the full library into the local SQLite cache and hydrates TMDb summary
metadata for every entry when a TMDb key is available. Later CloudKit refreshes
use `ani library sync`, or pass `--sync` to a read command when you want an
explicit one-shot refresh first.

```bash
uv run ani library init --json | jq '.summary.cache.records'
uv run ani library sync --json | jq '.summary.cache.records'
uv run ani library status --json | jq '.summary'
uv run ani library list --json | jq '.entries[] | {identity, watch_status}'
uv run ani library list --sync --json | jq '.summary.cache.mode'
uv run ani library list --json | jq '.summary.cache.mode'
uv run ani library export --json | jq '.entries[] | {identity, watch_status}'
```

Use `ani library clear-cache` to remove all local library cache files after an
interactive confirmation. Pass `--yes` to skip the prompt.

`ani library status` also reports TMDb summary metadata readiness for the local
cache so you can see whether entries are fully hydrated.

`library search --title` searches the initialized local cache by title and
identity. Use `tmdb search` for global TMDb discovery.

```bash
uv run ani library search --title "Alien" --json | jq '.entries[].identity'
```

Use `ani library refresh-meta` to refetch TMDb summary metadata for the full
local library cache. Use `--live-meta` on `library get` to refetch TMDb summary
metadata only for the requested entries and write the refreshed summaries back
to the cache.

Library commands accept optional `--metadata` parsing for future TMDb enrichment.
Bare `--metadata` selects the default summary level; explicit levels use `none`,
`summary`, `details`, or `full`. Full metadata hydration is still mostly inert in
the current implementation.
