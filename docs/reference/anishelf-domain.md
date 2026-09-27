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
- `lib get <id...> [--sync] [--live-meta] [--tmdb-language en|ja|zh] [--metadata none|summary|details|full]`
- `lib list [--sync] [--tmdb-language en|ja|zh] [--metadata none|summary|details|full] [--style table|list]`
- `lib search <query>` with optional `--sync`, `--tmdb-language`,
  `--metadata`, `--style table|list`, and `--limit`
- `lib export` with optional `--sync`, `--tmdb-language`, `--metadata`,
  `--format json|jsonl|csv`, and `--output <path>` (written atomically with
  owner-only permissions)
- `tmdb search [title] [--title] [--tmdb-language] [--limit]`

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
metadata, overviews, notes, and on-air dates. If none of the entries a command
considers has current cached metadata, the command should fail explicitly and
tell the user how to hydrate metadata first. If only some of them lack it (for
example after a transient TMDb failure, or for a TMDb id removed upstream), the
command still runs, prints one stderr warning that results may be incomplete,
and reports `summary.metadata_missing` in JSON. The same rule applies to
metadata-backed `lib list` sorts and to `--metadata details|full` attachment.
Coverage counts only entries in scope: hidden entries are ignored unless
`--show-hidden` is set, list filters apply, and attachment checks cover the
returned entries. `summary.metadata_missing` is absent when a command does not
depend on metadata, `0` when coverage is complete, and otherwise the number of
entries without metadata. Title and air-date sorting place entries without
metadata last, including under `lib list --reverse`.
`lib sync` retries entries with missing metadata, so transient gaps heal on the
next sync. Use `tmdb search <title>` or `tmdb search --title <title>` for
global TMDb anime title search, or omit a title for popular anime discovery.
`tmdb search <title>` is equivalent to `tmdb search --title <title>`. `--limit`
on `lib search` caps visible local matches after filtering; `--limit` on
`tmdb search` caps total grouped rows returned to the user.

Low-level CloudKit zone, record, change, and schema-check commands are
diagnostics. Keep them out of the normal user command tree unless a future
dev-only entry point is intentionally added.

## Batch And Output

Commands that naturally accept one id should usually accept many. `lib get`
accepts positional ids and `-` for whitespace-separated ids on stdin (so JSON
output piped through `jq -r '.entries[].id'` feeds it directly). File or JSONL
input can follow when a real workflow needs it.

Batch output should preserve caller order, keep item-level errors, and keep
progress or diagnostics on stderr. `lib get` exits nonzero only when no
requested item is found; partial failures remain item-level errors in the output
envelope, so agents should inspect `summary.errors`, or pass `--strict` to exit 1
on any item error.
`lib get` uses an ordered `items` collection because each requested id can
resolve to either an entry or an item-level error. Homogeneous library
collection commands (`lib list`, `lib search`, and `lib export`) use `entries`.

Human collection tables should keep inflexible fields readable, shrink the title
first and truncate ids only as a last resort, use compact dates, and omit the
display column unless hidden entries are included or the user explicitly asks
for that field.

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
separate top-level hydration pass. Explicit `none`, `summary`, `details`, and
`full` are implemented as separated option values, such as `--metadata none`.
Bare `--metadata` and equals-form values such as `--metadata=none` are rejected.
`none` means no TMDb request for output attachment.

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

`config set-defaults --tmdb-language en|ja|zh` changes the preferred persisted
metadata language. The CLI maps those public codes to TMDb HTTP tags only when
building requests (`en-US`, `ja-JP`, or `zh-CN`). Changing the preferred
language should prompt the user to clear and rebuild the cache; the cache does
not rewrite old metadata rows in place. A one-off
`--tmdb-language` on a library read command is ad-hoc: if it differs from the
preferred language, fetch the requested metadata live for that command and do
not upsert it into `tmdb_metadata_items`. `tmdb search --tmdb-language`
uses the override only for that search request.
