# TMDb Metadata Level Split

Draft public contract for `--metadata none|summary|details|full`. This is a
design note for inspection before updating the canonical domain reference.

## Goals

- Keep library reads local-cache-first.
- Make metadata level an output projection, not a separate cache format.
- Allow the cache to hold fields needed for title sorting and search even when
  the requested output level is smaller.
- Keep `summary` compact enough for default CLI output.
- Let richer levels grow without forcing unrelated fields into summary output.
- Keep cache hydration bounded: details refresh should not require one
  TMDb request per episode.
- Hydrate enough metadata by default that normal read workflows do not
  immediately require a second metadata pass.

## Output Levels

### none

Do not attach TMDb metadata to entries.

Commands may still use cached metadata internally when the requested behavior
requires it, such as title sorting, cached library search, or readable display
titles in human output.

### summary

Default output level for library read commands.

Attach only the fields needed to identify and display an entry in compact
library output:

- `name`
- `overview`
- type-specific compact facts:
  - movie: `runtime_minutes`
  - series: `number_of_seasons` and `number_of_episodes`
  - season: `number_of_episodes`
- `on_air_date`
- `parent_series_title` for season entries when cached parent-series metadata is
  available

Summary intentionally includes overview because `lib get` human output needs a
useful short description without requiring a richer metadata level.

### details

Detail-page level for normal human inspection and richer exports.

This is the built-in default hydration level. It is richer than `summary`, but
it should remain bounded to item-level detail data and must not fan out into
per-episode TMDb detail endpoint requests.

Include every `summary` field plus:

- `poster_path`
- `backdrop_path`
- `logo_path`
- `original_language_code`
- `link_to_details`
- `episode_run_time_minutes` for series when TMDb provides it
- `genres`
- `vote_average`
- `vote_count`
- `popularity`
- status/date fields, such as `status`, `first_air_date`, `last_air_date`, and
  `release_date`
- tagline or subtitle fields when TMDb provides them

Do not include large nested season or episode arrays at this level.

### full

Full local metadata export/debug level.

Include every `details` field plus heavier nested data:

- `name_translations`
- `overview_translations`
- series season summaries
- season episode summaries
- episode summary fields such as `episode_number`, `season_number`, `name`,
  `air_date`, and `still_path`
- future heavy fields, such as credits or external IDs, if we decide to support
  them

This level can be verbose and is intended for consumers that explicitly ask for
the complete cached TMDb payload surface.

Do not make `full` mean "fetch every TMDb episode detail endpoint." AniShelf's
current detail model stores season episode summaries and fetches individual
episode preview/detail data lazily. The CLI should follow that boundary unless
we add a separate, explicit episode-detail cache later.

## Cache Shape

Use one logical metadata cache. The first implementation can keep this in one
physical table:

- `tmdb_metadata_items`: one row per `metadata_key + language`; stores identity,
  scalar summary/detail fields, translation and genre JSON blobs, full-level
  season and episode summary JSON blobs, depth readiness/version columns, and
  normalized source fields.

Separate child tables remain a reasonable future split if full-level season or
episode summaries become large enough to make normal reads measurably heavier:

- `tmdb_metadata_seasons`: one row per parent series, language, and season.
- `tmdb_metadata_episodes`: one row per parent series, language, season, and
  episode.

Do not store raw TMDb payloads by default. Store normalized fields that the CLI
emits, searches, sorts, or uses for readiness checks. Raw payload storage can be
added later as a separate debug-only cache if there is a concrete need.

## Readiness Rules

- `full` cached data satisfies `details` and `summary`.
- `details` cached data satisfies `summary`.
- `summary` cached data satisfies only `summary`.
- Search and title-sort readiness can depend on internal cached fields without
  changing the selected output level.
- If a command requests `details` or `full` and no returned entry has that
  depth cached, fail explicitly with a hydration hint. If only some entries lack
  it, warn on stderr and report `summary.metadata_missing` instead.

## Hydration Boundaries

- Hydration has a user-configurable default depth, separate from the output
  default.
- The built-in hydration default should be `details`.
- The configurable hydration default should accept `details` and `full`.
  `summary` remains an output projection and explicit minimum-depth request, not
  a recommended steady-state cache default.
- `lib init` should hydrate the configured default depth.
- `lib sync` should hydrate the configured default depth for newly added
  entries.
- `lib refresh-meta` should hydrate at the configured hydration depth. Users
  should change refresh depth with `config set-defaults --hydration-depth`
  rather than an ad-hoc refresh flag.
- `lib get --live-meta --metadata details|full` should refresh only the
  requested entries at the configured hydration depth, promoted to `full` when
  full output is requested.
- Ad-hoc `--tmdb-language` reads should continue to avoid writing back metadata
  when the requested language differs from the preferred persisted language.
- Full hydration for a series may fetch all season episode summaries for that
  series when the user explicitly requests `--metadata full` or has configured
  `full` as the default hydration depth.
- Full hydration may be part of `lib init` or `lib sync` only when the user has
  explicitly configured `full` as the hydration default.

## Cost Boundary

AniShelf's current app design separates lightweight entry metadata from heavier
detail data:

- `EntryMetadata` holds localized name, overview, image paths, original
  language, date, link, TMDb id, and entry type.
- `AnimeEntryDetailDTO` holds status, logo, genre IDs, rating, runtime/count
  fields, characters/staff, season summaries, and season episode summaries.
- Series detail contains season summaries, not every episode from every season.
- Season detail contains the episode summaries for that season.
- Individual episode preview/detail data is fetched lazily per episode.

The CLI split should mirror that: `summary` is enough for compact library
display, `details` is the normal cache-maintenance target and is enough for
entry detail pages and rich exports, and `full` adds nested season/episode
summaries without turning a library refresh into hundreds or thousands of
per-episode detail requests.

## Resolved Questions

- `summary` includes `overview`.
- Translations are emitted only at `full`.
- Cache normalized fields by default, not raw TMDb payloads.
- The built-in hydration default is `details`.
- Users can configure the hydration default to `details` or `full`.
- Explicit `full` hydration for a series should include all season episode
  summaries for the series, not just seasons that are already in the library.
- Per-episode TMDb detail endpoint payloads are out of scope for this metadata
  level split and should require a later explicit design.
