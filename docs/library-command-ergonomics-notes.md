# Library Command Ergonomics Notes

Date: 2026-06-30

This note captures current findings from smoke-testing the implemented `ani
library` commands, plus the proposed direction for making those commands useful
for both humans and agents while keeping the CLI read-only.

## Current Problems

### 1. Library output is mostly not useful without TMDb metadata

The current cache stores decoded CloudKit `LibraryEntry` user-state rows, but
most commands expose only semantic identities and tracking fields:

- `series:253476`
- `watch_status`
- `score`
- `date_saved`
- `tracking_updated_at`

That is enough for low-level inspection, but not enough for practical browsing,
search, export review, or agent-facing question answering. Without hydrated
metadata, most output is missing the fields people actually recognize:

- localized title
- original title
- overview
- poster/backdrop/logo paths
- language
- air date

### 2. The current command surface forces users into full dumps and ad hoc `jq`

Simple questions such as:

- "what did I update most recently?"
- "what am I currently watching?"
- "show the latest five hidden entries"

should be answerable directly from `library` commands. Right now they usually
turn into `library export --json | jq ...` because the list/search surfaces are
too shallow and the output lacks recognizable metadata.

### 3. `--metadata` is currently advertised but inert

The CLI currently accepts `--metadata`, but the implemented library commands do
not yet change behavior based on it. That creates false affordance.

### 4. Human output does not scale well to real library data

The current list/table views are acceptable for basic proof-of-life checks, but
they are too raw for daily use:

- semantic identities dominate the table
- timestamps are noisy
- entries are not recognizable at a glance
- detailed `get` output can be overwhelmed by long notes

## Proposed Direction

The deeper fix is not just better formatting. The real fix is to store hydrated
TMDb metadata locally and join it into library reads by default.

This should follow the AniShelf app's architecture direction:

- CloudKit remains the source of user-owned library state.
- TMDb remains a separate metadata source.
- The CLI stays read-only.
- Local cache state can be enriched, but the CLI should not mutate CloudKit.

## Recommended Cache Shape

Keep library state and metadata as separate local concerns.

### Keep the existing CloudKit-derived cache

Continue storing decoded AniShelf `LibraryEntry` rows in the local SQLite cache
as the read-only library-state layer.

### Add separate local TMDb metadata cache tables

Do not rewrite the decoded CloudKit payload blob to embed hydrated TMDb data.
Instead, add dedicated metadata tables keyed by TMDb identity.

Suggested first slice:

- `tmdb_metadata_summary`

Possible later slice:

- `tmdb_metadata_detail`

Suggested summary-cache fields:

- `entry_type`
- `tmdb_id`
- `language`
- `name`
- `name_translations_json`
- `overview`
- `overview_translations_json`
- `poster_path`
- `backdrop_path`
- `logo_path`
- `original_language_code`
- `on_air_date`
- `link_to_details`
- `fetched_at`
- `source_version` or equivalent schema/version marker

This matches the main AniShelf app direction more closely than packing metadata
into the library-entry cache row.

## Hydration Policy

### Library commands stay read-only

No library command should write to CloudKit or modify user library state.

### Every library command should refresh CloudKit changes first

Before serving results, library commands should fetch zone changes for the
authenticated user and update the local library cache.

That means:

- `library list`
- `library search`
- `library export`
- `library get`

should all begin from the same "refresh local cache from CloudKit changes"
workflow unless an explicit offline mode is in effect.

### Hydrate metadata only when needed

When that changes fetch reveals newly seen library entries, the CLI should
hydrate TMDb summary metadata for those new entries and store it locally.

That means the normal background policy is:

- refresh CloudKit changes
- detect newly seen entries
- hydrate summary metadata on add
- serve results from the local cache + metadata join

This keeps the CLI read-only while still making new entries useful immediately.

### Support explicit metadata refresh on demand

Normal command execution should not re-fetch TMDb metadata for every entry every
time. After the initial hydrate-on-add flow, metadata should only refresh when
the user explicitly asks for it.

That explicit refresh can be command-scoped later, for example:

- refresh metadata for selected identities
- refresh metadata for the current result set
- refresh stale metadata only

## Command Behavior Implications

### `library list`

Default list output should include cached summary metadata so that entries are
recognizable by title rather than only by semantic identity.

At minimum, list results should become useful for:

- title
- entry type
- watch status
- score
- favorite/on-display
- recency timestamps

### `library search`

Search results should also carry cached metadata by default. Since this command
already depends on TMDb title search, the output should not stop at bare
identity rows.

### `library get`

Detailed lookup should merge:

- library-state fields from CloudKit-derived cache
- cached TMDb summary metadata

and later, when implemented, optional detail metadata.

### `library export`

Export should remain structurally stable and agent-friendly, but default export
should still be able to include cached metadata so the output is not just a raw
state dump.

## `--metadata` Recommendation

The existing `--metadata` surface is still the right place for this capability,
but it should describe cache behavior rather than a live-fetch-every-time model.

Recommended semantics:

- `--metadata none`: do not attach cached TMDb metadata
- `--metadata summary`: attach cached summary metadata
- `--metadata details`: reserved until detail cache exists
- `--metadata full`: reserved until detail/full cache behavior exists

Then add a separate explicit refresh control when needed instead of treating
`--metadata` itself as "go to the network now".

## Human-Facing Improvements After Metadata Exists

Once cached summary metadata is available, the human output can become much more
useful without large command-surface expansion:

- show title first, identity second
- use tighter timestamp formatting
- keep notes truncated by default
- keep list/search views compact and scannable
- reserve verbose detail for `get`

Without metadata, formatting improvements alone will not solve the underlying
usability problem.

## Suggested Next Steps

1. Add a local TMDb summary metadata table to the SQLite cache layer.
2. Add a join path so library reads can attach cached summary metadata.
3. Change library refresh flow so every library command performs CloudKit
   changes sync first unless offline.
4. Detect newly seen entries during refresh and hydrate TMDb summary metadata on
   add.
5. Make `library list`, `library search`, `library get`, and `library export`
   include cached summary metadata by default.
6. Rework the current inert `--metadata` flag into real cache-attachment
   behavior.
7. After summary metadata is working, revisit filters/sorts/field selection and
   more compact human output.

## Priority

The highest-value next vertical slice is:

- CloudKit changes refresh
- new-entry detection
- TMDb summary hydration on add
- cached metadata attached to current library commands

That solves the real usability problem while preserving the CLI's read-only
model.
