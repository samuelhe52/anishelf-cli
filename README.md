# anishelf-cli

Read-only AniShelf library inspection CLI. `ani` signs in to your AniShelf
CloudKit library, keeps a local cache, and lets you inspect, search, and export
that cached library from the terminal.

## Quick start

```bash
uv sync
uv run ani auth login
uv run ani config set-tmdb-api-key
uv run ani library init
uv run ani library list
```

`ani library init` is the required first library step. It fetches the full
library into the local SQLite cache and hydrates TMDb summary metadata when a
TMDb key is available. Later reads use the local cache by default.

## Authentication

`ani auth login` stores the user-scoped CloudKit web auth token in the OS secure
credential store. This token authorizes access to the signed-in user's private
CloudKit data. `ani auth logout` removes that token and clears all local library
cache files. Use `ani auth status` to verify the current login and `ani auth
refresh` to roll forward stored auth state when CloudKit returns a successor
token.

TMDb API keys can be stored in Keychain with `ani config set-tmdb-api-key`.

## Library Commands

Use `ani library sync` when you want to refresh the initialized cache from
CloudKit. Read commands also accept `--sync` for an explicit one-shot refresh
before reading:

```bash
uv run ani library sync
uv run ani library list --sync
```

Common read commands:

```bash
uv run ani library status
uv run ani library list
uv run ani library get movie:55
uv run ani library search --title "Alien"
uv run ani library export --json
```

`library get` looks up entries by identity. Accepted identity forms are
`movie:<tmdbID>`, `series:<tmdbID>`, and
`season:<parentSeriesID>:<seasonNumber>:<tmdbID>`.

`library search --title` searches the initialized local cache by title. Use
`ani tmdb search --title "Alien"` for global TMDb discovery, or omit `--title`
to discover popular TMDb titles.

Use `ani library refresh-meta` to refetch TMDb summary metadata for the full
local library cache. Use `--live-meta` on `library get` to refetch TMDb summary
metadata only for the requested entries and write the refreshed summaries back
to the cache.

Use `ani library clear-cache` to remove all local library cache files after an
interactive confirmation. Pass `--yes` to skip the prompt.

## Metadata

Library read commands include cached TMDb summary metadata by default. Pass
`--metadata none` to omit metadata from the output without making TMDb requests.
Bare `--metadata` selects the default `summary` level.

`details` and `full` are reserved for future TMDb detail caching. Passing either
level currently fails with a clear error.

## JSON Output

Commands that support JSON accept `--json` either globally or on the command:

```bash
uv run ani --json library get movie:55
uv run ani library get movie:55 --json
```

`library get` emits an ordered envelope designed for `jq`: `.summary` contains
counts, and `.items[]` contains either `.entry` or `.error`.

For mixed `library get` batches, the command can exit `0` while individual
items contain errors. Check `.summary.errors` or `.items[] | select(.status ==
"error")` when automating batch lookups.

```bash
uv run ani library get movie:55 --json | jq '.items[].entry.watch_status'
uv run ani library get movie:55 --json | jq '.items[] | {identity, score: .entry.score}'
uv run ani library get movie:55 --json | jq '.items[] | select(.status == "error")'
uv run ani library init --json | jq '.summary.cache.records'
uv run ani library sync --json | jq '.summary.cache.records'
uv run ani library status --json | jq '.summary'
uv run ani library list --json | jq '.entries[] | {identity, watch_status}'
uv run ani library list --sync --json | jq '.summary.cache.mode'
uv run ani library export --json | jq '.entries[] | {identity, watch_status}'
uv run ani library search --title "Alien" --json | jq '.entries[].identity'
```
