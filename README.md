# anishelf-cli

> [!WARNING]
> This project is a work in progress. The CLI is not yet feature-complete,
> and the API surface is not yet stable. Expect breaking changes.

Read-only AniShelf library inspection CLI. `ani` signs in to your AniShelf
CloudKit library, keeps a local cache, and lets you inspect, search, and export
that cached library from the terminal.

## Quick start

```bash
uv sync
uv run ani auth login
uv run ani config set-tmdb-api-key
uv run ani lib init
uv run ani lib list
```

`ani lib init` is the required first library step. It fetches the full
library into the local SQLite cache and hydrates TMDb summary metadata when a
TMDb key is available. Later reads use the local cache by default.

TMDb metadata is fetched in the configured preferred language, `en-US` by
default. Change it with:

```bash
uv run ani config set-defaults --tmdb-language ja-JP
uv run ani lib clear-cache --yes
uv run ani lib init
```

Changing the preferred language does not rewrite existing cache rows in place;
clear and rebuild the cache when you want persisted metadata in the new
language.

## Authentication

`ani auth login` stores the user-scoped CloudKit web auth token in the OS secure
credential store. This token authorizes access to the signed-in user's private
CloudKit data. `ani auth logout` removes that token and clears all local library
cache files. Use `ani auth status` to verify the current login and `ani auth
refresh` to roll forward stored auth state when CloudKit returns a successor
token.

TMDb API keys can be stored in Keychain with `ani config set-tmdb-api-key`.

## Library Commands

Use `ani lib sync` when you want to refresh the initialized cache from
CloudKit. Read commands also accept `--sync` for an explicit one-shot refresh
before reading:

```bash
uv run ani lib sync
uv run ani lib list --sync
```

Common read commands:

```bash
uv run ani lib status
uv run ani lib list
uv run ani lib get movie:55
uv run ani lib search --title "Alien"
uv run ani lib export --json
```

Hidden entries (`onDisplay = false` in AniShelf) are excluded from list,
search, and export output by default. Pass `--show-hidden` to include them for
one command, or persist that default with:

```bash
uv run ani config set-defaults --show-hidden
```

`lib list` and `lib search` use compact table output by default. Pass
`--style list` for a per-entry human layout similar to `lib get`, and use
`--fields` to choose which fields appear in either human style:

```bash
uv run ani lib list --style list --fields title,id,status
uv run ani config set-defaults --style list
```

`lib get` looks up entries by id. Accepted id forms are
`movie:<tmdbID>`, `series:<tmdbID>`, and
`season:<parentSeriesID>:<seasonNumber>:<tmdbID>`.

`lib search --title` searches the initialized local cache by title. Use
`ani tmdb search --title "Alien"` for global TMDb discovery, or omit `--title`
to discover popular TMDb titles.

Use `ani lib refresh-meta` to refetch TMDb summary metadata for the full
local library cache. Use `--live-meta` on `lib get` to refetch TMDb summary
metadata only for the requested entries and write the refreshed summaries back
to the cache.

Use `--tmdb-language <tag>` on `lib get`, `lib list`, `lib search`, `lib export`,
or `tmdb search` for a one-off language override. For library commands, a
language that differs from the configured default is fetched live for that
request and is not written back to the metadata cache.

Use `ani lib clear-cache` to remove all local library cache files after an
interactive confirmation. Pass `--yes` to skip the prompt.

## Metadata

Library read commands include cached TMDb summary metadata by default. Pass
`--metadata none` to omit metadata from the output without making TMDb requests.
Bare `--metadata` selects the default `summary` level.

Summary metadata is intentionally AniShelf-aligned: requested `language`,
localized `name` and `overview`, `name_translations`, `overview_translations`,
poster/backdrop/logo paths, `original_language_code`, `on_air_date`,
homepage-backed `link_to_details`, and internal TMDb identity fields. Fields
such as runtime, genres, vote counts, popularity, credits, seasons, and episodes
belong to future detail caching, not the summary payload.

`details` and `full` are reserved for future TMDb detail caching. Passing either
level currently fails with a clear error.

## JSON Output

Commands that support JSON accept `--json` either globally or on the command:

```bash
uv run ani --json lib get movie:55
uv run ani lib get movie:55 --json
```

`lib get` emits an ordered envelope designed for `jq`: `.summary` contains
counts, and `.items[]` contains either `.entry` or `.error`.

For mixed `lib get` batches, the command can exit `0` while individual
items contain errors. Check `.summary.errors` or `.items[] | select(.status ==
"error")` when automating batch lookups.

```bash
uv run ani lib get movie:55 --json | jq '.items[].entry.watch_status'
uv run ani lib get movie:55 --json | jq '.items[] | {id, score: .entry.score}'
uv run ani lib get movie:55 --json | jq '.items[] | select(.status == "error")'
uv run ani lib init --json | jq '.summary.cache.records'
uv run ani lib sync --json | jq '.summary.cache.records'
uv run ani lib status --json | jq '.summary'
uv run ani lib list --json | jq '.entries[] | {id, watch_status}'
uv run ani lib list --sync --json | jq '.summary.cache.mode'
uv run ani lib export --json | jq '.entries[] | {id, watch_status}'
uv run ani lib search --title "Alien" --json | jq '.entries[].id'
```
