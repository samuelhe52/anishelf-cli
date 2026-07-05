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
library into the local SQLite cache and hydrates TMDb metadata when a
TMDb key is available. Later reads use the local cache by default.

TMDb metadata is fetched in the configured preferred language, `en` by
default. Change it with:

```bash
uv run ani config set-defaults --tmdb-language ja
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
uv run ani lib search "Alien" --limit 5
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
`--fields` to choose which base library fields appear in either human style.
When metadata is requested, list-style human output appends bounded metadata
rows after those base fields; table output stays compact:

```bash
uv run ani lib list --style list --fields title,id,status
uv run ani config set-defaults --style list
```

`lib get` looks up entries by id. Accepted id forms are
`movie:<tmdbID>`, `series:<tmdbID>`, and
`season:<parentSeriesID>:<seasonNumber>:<tmdbID>`.

`lib search <query>` searches the initialized local cache with the same broad
library search shape used by AniShelf: titles, translations, parent-series
metadata, overviews, notes, and on-air dates. Pass `--limit <n>` to cap search
results. Use `ani tmdb search "Alien"` or `ani tmdb search --title "Alien"` for
global TMDb title search, and pass `--limit <n>` there too. Omit the title to
discover popular TMDb titles.

Use `ani lib refresh-meta` to refetch TMDb metadata for the full local library
cache at the configured hydration depth (`details` unless changed). Use
`ani config set-defaults --hydration-depth full` to change the persisted refresh
depth. Use `--live-meta` on `lib get` to refetch TMDb metadata only for the
requested entries at the configured hydration depth, or at `full` when full
output is requested, and write the refreshed rows back to the cache.

Use `--tmdb-language <code>` on `lib get`, `lib list`, `lib search`, `lib export`,
or `tmdb search` for a one-off language override. For library commands, a
language that differs from the configured default is fetched live for that
request and is not written back to the metadata cache. Supported public codes
are `en`, `ja`, and `zh`.

Use `ani lib clear-cache` to remove all local library cache files after an
interactive confirmation. Pass `--yes` or `-y` to skip the prompt.

## Metadata

Library read commands include cached TMDb summary metadata by default. Pass
`--metadata none` to omit metadata from the output without making TMDb requests.
Pass `--metadata summary`, `--metadata details`, or `--metadata full` to choose
another projection. `--metadata` requires a separated value; `--metadata=...`
is not supported.

For human output, `lib get` and `lib list --style list` / `lib search --style
list` render metadata rows according to the requested depth. `--metadata none`
suppresses those rows, although cached TMDb titles may still be used as display
titles for readability. Human table output remains a compact base-field table.

Summary output includes compact fields only: `name`, `overview`, type-specific
counts or runtime, `on_air_date`, and derived `parent_series_title` for seasons
when cached. Details output adds image paths, original language, link, genres,
ratings/counts, popularity, status/date fields, runtime/count fields, and
tagline/subtitle. Full output adds translations plus season and episode
summaries.

Hydration depth is separate from output depth. `lib init` and `lib sync` hydrate
the configured default depth, which is `details` by default and can be changed
to `full` with `ani config set-defaults --hydration-depth full`. The same
configured depth applies to `lib refresh-meta` and targeted `lib get --live-meta`,
so a user-configured `full` default can perform full hydration even when the
command projects `--metadata details`.

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
uv run ani lib search "Alien" --limit 5 --json | jq '.entries[].id'
```
