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
currently keeps kind-scoped sort and lookup indexes for saved-date ordering,
direct movie/series TMDb matches, and season parent-series matches.

## Public Commands

Normal user commands should stay library-first. Useful read-only surfaces
include:

- `lib init`
- `lib sync`
- `lib status`
- `lib clear-cache`
- `lib refresh-meta`
- `lib get <id...> [--sync] [--live-meta] [--metadata[=none|summary|details|full]]`
- `lib list [--sync] [--metadata[=none|summary|details|full]]`
- `lib search --title` with optional `--sync` and `--metadata`
- `lib export` with optional `--sync` and `--metadata`
- `tmdb search [--title]`

`lib init` is the explicit bootstrap entry point for the local cache.
`lib sync` is the explicit refresh entry point after bootstrap. Other
library read commands require an initialized cache and should fail closed until
init has been run. `--sync` on a library read command should perform that same
refresh step explicitly before reading from the local cache.
`lib status` should report whether the local cache is initialized and which
cached scopes exist, including TMDb summary metadata readiness.
`lib clear-cache` should explicitly clear all local library cache files
after confirmation.
`lib refresh-meta` should explicitly refresh cached TMDb summary metadata
for the full local library.
Tombstones are an internal sync concern and should not appear in public entry
counts or library list/export output.

`lib search --title` depends on cached TMDb summary metadata. If that
metadata is incomplete or unavailable, the command should fail explicitly and
tell the user how to hydrate metadata first. Use `tmdb search --title` for
global TMDb title search, or omit `--title` for popular-title discovery.

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

The CLI decision is to keep metadata on library commands instead of exposing a
separate top-level hydration pass. Bare `--metadata` should request the default
summary level. Explicit `none` and `summary` are implemented; `details` and
`full` are reserved and should fail clearly until detail metadata caching exists.
Both `--metadata none` and `--metadata=none` should behave the same. If a
positional id or title is literally `none`, `summary`, `details`, or
`full`, require `--` before that positional argument so it is not consumed as
the metadata level. `none` means no TMDb request.

`lib init` should fetch the full library and hydrate TMDb summary metadata
for every entry when a TMDb key is available. After that initialization pass,
`lib sync` should hydrate every newly added entry automatically.
`lib refresh-meta` should refetch TMDb summary metadata for the full local
cache on demand. `lib get --live-meta` should refetch TMDb summary metadata
only for the requested entries and update the cache without broad library
refresh.
