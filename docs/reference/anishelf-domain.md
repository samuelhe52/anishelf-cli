# AniShelf Domain Reference

Developer reference for decoding AniShelf library data and shaping read-only
library commands. This is not a full command specification.

## CloudKit Schema

Current reference constants:

- Container: `iCloud.com.samuelhe.MyAnimeList`
- Custom zone: `AniShelfLibrary`
- Library entry record type: `LibraryEntry`
- `LibrarySettings` records are not supported or planned for this CLI.

Stable library ids are semantic record names:

- `movie:<tmdbID>`
- `series:<tmdbID>`
- `season:<parentSeriesID>:<seasonNumber>:<tmdbID>`

`LibraryEntry` live snapshots should decode the id, TMDb IDs, entry type,
display state, saved date, watch status, dates, score, favorite, notes, custom
poster path, episode progress, and update clocks. Tombstones should decode from
valid id fields plus `deletedAt`.

Unsupported future schema versions should fail explicitly instead of silently
dropping fields or guessing.

## Cache

Full-library commands use CloudKit zone changes instead of broad queries. The
cache is rebuildable, lives under the platform user cache directory, and keys
state by CloudKit container, environment, database, zone, and authenticated
`userRecordName`.

Token advancement must be commit-after-apply: persist a durable change token
only after the matching record changes have been applied. If CloudKit reports an
expired change token, discard the affected cursor and rebuild.

Rebuilds should not expose a mixed old/new cache. Fetch rebuilt rows into a
staging table and promote them only after the final page has been applied.

List and search reads should stay index-friendly for large libraries. The cache
currently keeps kind-scoped sort and lookup indexes for updated ordering,
direct movie/series TMDb matches, and season parent-series matches.

## Public Commands

Normal user commands should stay library-first. Useful read-only surfaces
include:

- `lib init`
- `lib sync`
- `lib status`
- `lib clear-cache`
- `lib refresh-meta`
- `lib get <id...> [--sync] [--live-meta] [--tmdb-language] [--metadata[=none|summary|details|full]]`
- `lib list [--sync] [--tmdb-language] [--metadata[=none|summary|details|full]] [--style table|list]`
- `lib search <query>` with optional `--sync`, `--tmdb-language`,
  `--metadata`, and `--style table|list`
- `lib export` with optional `--sync`, `--tmdb-language`, and `--metadata`
- `tmdb search [--title] [--tmdb-language]`

`lib init` is the explicit bootstrap entry point for the local cache.
`lib sync` is the explicit refresh entry point after bootstrap. Other
library read commands require an initialized cache and should fail closed until
init has been run. `--sync` on a library read command should perform that same
refresh step explicitly before reading from the local cache.
`lib status` should report whether the local cache is initialized, total
snapshot entries split into non-hidden and hidden counts, and which cached
scopes exist, including TMDb metadata readiness.
`lib clear-cache` should explicitly clear all local library cache files
after confirmation.
`lib refresh-meta` should explicitly refresh cached TMDb metadata for the full
local library.
Tombstones are an internal sync concern and should not appear in public entry
counts or library list/export output.

`lib search <query>` depends on cached TMDb metadata because it mirrors
AniShelf's smart library search across titles, translations, parent-series
metadata, overviews, notes, and on-air dates. If metadata is incomplete or
unavailable, the command should fail explicitly and tell the user how to hydrate
metadata first. Use `tmdb search --title` for global TMDb anime title search, or
omit `--title` for popular anime discovery.

Low-level CloudKit zone, record, change, and schema-check commands are
diagnostics. Keep them out of the normal user command tree unless a future
dev-only entry point is intentionally added.

## Batch And Output

Commands that naturally accept one id should usually accept many. Batch
input can grow from positional arguments first, then stdin/file/JSONL when a
real workflow needs it.

Batch output should preserve caller order, keep item-level errors, and keep
progress or diagnostics on stderr. `lib get` exits nonzero only when no
requested item is found; partial failures remain item-level errors in the output
envelope, so agents should inspect `summary.errors`.

## Metadata Hydration

CloudKit records do not contain rich TMDb metadata such as localized titles,
overviews, posters for normal TMDb items, runtime, credits, or season detail.
Hydration should be explicit and optional.

Metadata level is an output projection over one logical cache, not a separate
cache format. Summary output includes only compact display fields: localized
`name`, `overview`, type-specific facts (`runtime_minutes` for movies,
`number_of_seasons` / `number_of_episodes` for series, `number_of_episodes` for
seasons), `on_air_date`, and derived `parent_series_title` for seasons when the
parent title is cached.

Details output includes every summary field plus image paths, original
language, link, genres, vote average/count, popularity, status/date fields,
series episode runtime, and tagline/subtitle. Details must stay bounded to
item-level TMDb detail data and should not fan out into per-episode detail
endpoint requests.

Full output includes every details field plus translations, series season
summaries, season episode summaries, and episode summary fields such as episode
number, season number, name, air date, and still path. Full does not mean
fetching every TMDb episode detail endpoint. Translations are still fetched and
stored by details hydration because cached library search matches translated
titles and overviews; they are projected only in full output.

The CLI decision is to keep metadata on library commands instead of exposing a
separate top-level hydration pass. Bare `--metadata` should request the default
summary level. Explicit `none`, `summary`, `details`, and `full` are
implemented. Both `--metadata none` and `--metadata=none` should behave the
same. If a positional id or title is literally `none`, `summary`, `details`, or
`full`, require `--` before that positional argument so it is not consumed as
the metadata level. `none` means no TMDb request for output attachment.

`lib init` should fetch the full library and hydrate TMDb metadata at the
configured hydration depth for every entry in the configured preferred metadata
language when a TMDb key is available. The built-in hydration default is
`details`; users can configure `details` or `full`. `lib sync` should hydrate
new or insufficiently hydrated entries automatically in that same preferred
language and configured depth. `lib refresh-meta` should refetch metadata for
the full local cache on demand at the configured hydration depth; users should
change that depth with `config set-defaults --hydration-depth details|full`
rather than an ad-hoc refresh flag. `lib get --live-meta` should refetch
metadata only for the requested entries at the configured hydration depth, or
full when full output is requested, and then project the requested output depth
without broad library refresh. Because live refresh honors the configured
hydration depth, a user-configured `full` default can perform full hydration
for a targeted `--live-meta` request even when the requested output projection
is `details`.

`config set-defaults --tmdb-language <tag>` changes the preferred persisted
metadata language. Changing it should prompt the user to clear and rebuild the
cache; the cache does not rewrite old metadata rows in place. A one-off
`--tmdb-language` on a library read command is ad-hoc: if it differs from the
preferred language, fetch the requested metadata live for that command and do
not upsert it into `tmdb_metadata_items`. `tmdb search --tmdb-language`
uses the override only for that search request.
