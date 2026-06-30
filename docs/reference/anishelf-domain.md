# AniShelf Domain Reference

Developer reference for decoding AniShelf library data and shaping read-only
library commands. This is not a full command specification.

## CloudKit Schema

Current reference constants:

- Container: `iCloud.com.samuelhe.MyAnimeList`
- Custom zone: `AniShelfLibrary`
- Library entry record type: `LibraryEntry`
- Settings record type: `LibrarySettings`
- Settings record name: `userDefaults`

Stable library identities are semantic record names:

- `movie:<tmdbID>`
- `series:<tmdbID>`
- `season:<parentSeriesID>:<seasonNumber>:<tmdbID>`

`LibraryEntry` live snapshots should decode identity, TMDb IDs, entry type,
display state, saved date, watch status, dates, score, favorite, notes, custom
poster path, episode progress, and update clocks. Tombstones should decode from
valid identity fields plus `deletedAt`.

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
currently keeps kind-scoped sort and lookup indexes for saved-date ordering,
direct movie/series TMDb matches, and season parent-series matches.

## Public Commands

Normal user commands should stay library-first. Useful read-only surfaces
include:

- `library get <identity...> [--metadata[=none|summary|details|full]]`
- `library list [--offline] [--metadata[=none|summary|details|full]]`
- `library search --title` with optional `--metadata`
- `library export [--offline] [--include-tombstones]` with optional `--metadata`
- `tmdb search --title`

`library list` and `library export` auto-refresh the authenticated user's cache
before reading unless `--offline` is passed. Default list/export output excludes
tombstones; export can include tombstones explicitly.

`library search --title` auto-refreshes the cache, searches TMDb movie and TV
titles using API-key auth, intersects returned TMDb IDs with cached movie/series
entries, and includes seasons whose `parent_series_id` matches a matched series.

Low-level CloudKit zone, record, change, settings, and schema-check commands
are diagnostics. Keep them out of the normal user command tree unless a future
dev-only entry point is intentionally added.

## Batch And Output

Commands that naturally accept one identity should usually accept many. Batch
input can grow from positional arguments first, then stdin/file/JSONL when a
real workflow needs it.

Batch output should preserve caller order, keep item-level errors, and keep
progress or diagnostics on stderr. Partial-success exit behavior should be
defined when the first batch command needs it.

## Metadata Hydration

CloudKit records do not contain rich TMDb metadata such as localized titles,
overviews, posters for normal TMDb items, runtime, credits, or season detail.
Hydration should be explicit and optional.

The CLI decision is to keep metadata on library commands instead of exposing a
separate top-level hydration pass. Bare `--metadata` should request the default
summary level, while explicit `none`, `summary`, `details`, and `full` values
should control the TMDb depth as implemented. `none` means no TMDb request.

Current title search uses TMDb only to find candidate movie and TV IDs for cache
intersection. Exact hydrated metadata fields for each depth should be finalized
alongside the first implemented metadata-enrichment path.
