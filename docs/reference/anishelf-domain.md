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

Full-library commands should prefer zone changes over broad queries once cache
work starts. The cache should be rebuildable, live under the platform user cache
directory, and key state by CloudKit scope plus authenticated user.

Token advancement must be commit-after-apply: persist a durable change token
only after the matching record changes have been applied. If CloudKit reports an
expired change token, discard the affected cursor and rebuild.

## Public Commands

Normal user commands should stay library-first. Useful read-only surfaces
include:

- `library get <identity...> [--metadata[=none|summary|details|full]]`
- `library list` with filters and optional `--metadata`
- `library search --title` with optional `--metadata`
- `library export` with optional `--metadata`
- `tmdb search --title`

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

Exact field contents for each level should be finalized alongside the first
implemented metadata path.
