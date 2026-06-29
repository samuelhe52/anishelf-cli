# AniShelf Domain Reference

This document captures durable reference material for decoding and exporting the
AniShelf CloudKit library. It is not a complete command specification.

## CloudKit Sync Schema

AniShelf syncs a lean CloudKit schema rather than a rich SwiftData mirror.
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
poster path, episode progresses, and update clocks. Tombstones are represented
by entry records with valid identity fields and `deletedAt`; they should not
require live snapshot fields.

`LibrarySettings` should decode supported settings schema versions, updated
timestamp, and payload values as booleans, strings, or string arrays.

Unsupported future schema versions must fail explicitly rather than silently
dropping fields or guessing.

## Schema Drift

The CLI should use a committed schema snapshot for runtime decoding. Schema
drift detection is a manual developer-maintainer workflow that compares that
snapshot against an explicitly supplied local AniShelf checkout when needed,
not a value stored in user profiles or global CLI configuration. The source
authority is the `DataProvider/Sources/LibrarySync` area of the AniShelf repo.

Schema checks should compare:

- container identifier, zone name, record types, and settings record name;
- field names and required or optional status;
- supported schema versions;
- entry type and watch status values;
- identity formats;
- JSON-in-Data payload shapes such as episode progress and settings payloads.

If AniShelf changes the CloudKit record contract, the CLI decoder should be
updated in the same release window or the compatibility decision should be
documented. Changes limited to local rich models or fetched metadata should not
force a CLI schema update.

## Local Cache

Full-library commands should prefer CloudKit zone changes over broad queries.
The derived local cache should be rebuildable and live under the platform user
cache directory.

Cache keys should include profile, container, environment, database scope,
authenticated user, owner, and zone. Persisting the environment and database is
important so future development or shared-database support cannot accidentally
reuse a production private-database cursor.

The cache should store:

- zone sync token for `AniShelfLibrary`;
- decoded live `LibraryEntry` snapshots and AniShelf tombstones;
- `LibrarySettings`;
- raw CloudKit deletion markers used to remove deleted records from derived
  cache state;
- lightweight indexes by entry type, TMDb ID, parent series, season, watch
  status, score, and update clocks;
- optional TMDb metadata keyed by media type, TMDb ID, language, and depth.

Token advancement must be commit-after-apply. During pagination, use the
response token to request the next page, but only persist a durable cache token
after the matching record changes have been applied successfully. If CloudKit
reports an expired change token, discard the affected cache/token and rebuild
from the beginning.

## Batch Input And Output

Commands that naturally accept one identity should accept many identities.
Supported input shapes should include positional values, stdin, file input, and
JSONL jobs where useful.

Batch behavior should:

- ignore blank lines and comments in text inputs;
- preserve first-seen output order;
- deduplicate network work where inputs are equivalent;
- keep item-level validation and lookup errors;
- preserve caller correlation fields for JSONL jobs;
- write only structured data to stdout when `--json` or `--jsonl` is enabled;
- write progress, warnings, and diagnostics to stderr.

Partial failures are expected in large batches. A distinct partial-success exit
code should separate mixed success from complete success and complete failure.

## Domain Commands

Useful read-only domain surfaces include:

- `library get <identity...>`;
- `library list` with filters for entry type, watch status, favorite, score,
  display state, tombstone inclusion, and updated-after clocks;
- `library search --title`, scoped to the user's library;
- `library export --format json|jsonl`;
- `library changes` for explicit cursor diagnostics;
- `library get --title --fields notes,score` for unambiguous title lookup;
- `settings show`;
- `schema check`;
- `tmdb search --title`, separate from library search.

Library title search should not hydrate the whole library. It should resolve
TMDb candidates, intersect candidate identities with the user's library
identities, and enrich from cached metadata when available. Ambiguous title
lookups should return candidates instead of choosing one silently.

## Metadata Hydration

CloudKit records do not contain rich titles, overviews, posters for normal TMDb
items, runtime, credits, seasons metadata, or localized TMDb data. Metadata must
come from TMDb and should be explicit.

Depth levels:

- `none`: CloudKit user state only.
- `summary`: titles, original titles, overview, dates, status, runtime or
  episode counts, genres, poster/backdrop/logo paths, popularity/vote fields,
  and external IDs where available.
- `details`: summary plus credits, images, videos, keywords, content ratings or
  release dates, translations, and relevant season details.
- `full`: details plus full season detail calls for every relevant season,
  including episode lists and season credits where needed.

Series summary should not fetch every season. Details should fetch per-season
details only for season records present in the user's CloudKit library unless
the user explicitly requests all seasons. Full/all-season hydration should be an
explicit choice because it can multiply request counts.
