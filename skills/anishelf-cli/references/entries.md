# AniShelf CLI Entry Schema Reference

This file describes the JSON entry data agents can inspect from `ani` library
commands. Prefer this over guessing from AniShelf UI labels or raw CloudKit
field names.

## Where Entries Appear

Collection commands emit entries directly:

```json
{
  "entries": [
    {
      "id": "movie:55",
      "kind": "snapshot",
      "entry_type": "movie",
      "tmdb_id": 55
    }
  ],
  "summary": {
    "entries": 1,
    "cache": {}
  }
}
```

`lib get` emits ordered item results instead:

```json
{
  "items": [
    {
      "id": "movie:55",
      "status": "found",
      "entry": {
        "id": "movie:55",
        "entry_type": "movie",
        "tmdb_id": 55
      }
    }
  ],
  "summary": {
    "requested": 1,
    "found": 1,
    "errors": 0
  }
}
```

For `lib get`, each item can also be:

```json
{
  "id": "bad-id",
  "status": "error",
  "error": {
    "code": "invalid_id",
    "message": "..."
  }
}
```

Always inspect `summary.errors` and item-level `status`; partial batch failures
can be represented in JSON without making every requested id fail.

## Entry Identity

Public entry ids are stable AniShelf ids:

- `movie:<tmdbID>`
- `series:<tmdbID>`
- `season:<parentSeriesID>:<seasonNumber>:<tmdbID>`

Core identity fields:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Public AniShelf id. |
| `entry_type` | string | `movie`, `series`, or `season`. |
| `tmdb_id` | integer | TMDb id for this movie, series, or season. |
| `parent_series_id` | integer or null | Present for season entries. |
| `season_number` | integer or null | Present for season entries. |

Collection outputs can include `kind` and `schema_version` because they expose
the typed entry model. Normal visible entries are `kind: "snapshot"`.
Tombstones are sync internals and should not appear in normal public list,
search, or export output.

`lib get --json` sanitizes entry payloads for direct user workflows and omits
`kind`, `schema_version`, `using_custom_poster`, `custom_poster_path`,
`library_updated_at`, and `tracking_updated_at`.

## User Library State

Snapshot entries can carry these AniShelf user-state fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `on_display` | boolean | Whether the entry is visible in the user's library UI. |
| `date_saved` | string | Saved date or timestamp from AniShelf. |
| `watch_status` | string | One of `planToWatch`, `watching`, `watched`, or `dropped`. |
| `date_started` | string or null | User tracking start date when present. |
| `date_finished` | string or null | User tracking finish date when present. |
| `is_date_tracking_enabled` | boolean | Whether start/finish date tracking is enabled. |
| `score` | integer or null | User score when set. |
| `favorite` | boolean | User favorite flag. |
| `notes` | string | User notes, possibly empty. Treat as private user data. |
| `episode_progresses` | array | Season-level episode progress rows. |

`episode_progresses` items use:

| Field | Type | Meaning |
| --- | --- | --- |
| `season_number` | integer | Season number being tracked. |
| `watched_through_episode` | integer | Last watched episode number for that season. |
| `updated_at` | string or null | Progress update date or timestamp. |

Collection outputs may also include `using_custom_poster`,
`custom_poster_path`, `library_updated_at`, and `tracking_updated_at`. Treat
those as low-level library/cache state unless the user specifically asks about
custom posters or sync/update timing.

## Attached Metadata

Library reads attach TMDb metadata unless `--metadata none` is used. The
metadata object is absent when no metadata is attached.

```json
{
  "id": "movie:55",
  "entry_type": "movie",
  "tmdb_id": 55,
  "metadata": {
    "name": "Alien",
    "overview": "A crew answers a distress signal.",
    "runtime_minutes": 117,
    "on_air_date": "1979-05-25"
  }
}
```

Metadata fields depend on the requested projection:

| Projection | Fields agents can expect when available |
| --- | --- |
| `none` | No `metadata` object attached. |
| `summary` | `name`, `overview`, `runtime_minutes`, `number_of_seasons`, `number_of_episodes`, `on_air_date`; season entries can also receive `parent_series_title` when cached or fetched for the request. |
| `details` | Summary fields plus `poster_path`, `backdrop_path`, `logo_path`, `original_language_code`, `link_to_details`, `episode_run_time_minutes`, `genres`, `vote_average`, `vote_count`, `popularity`, `status`, `first_air_date`, `last_air_date`, `release_date`, `tagline`, and `subtitle`. |
| `full` | Details fields plus `name_translations`, `overview_translations`, `season_summaries`, `episode_summaries`, and metadata identity/source fields when stored. |

Metadata objects are sparse. Missing fields mean unavailable at the requested
projection or absent from TMDb, not necessarily false.

For season entries, TMDb season names are often low-information labels such as
`Season 1`. When `metadata.parent_series_title` is present, agents should
prefer a display label built from the parent series title and season number,
for example `Cowboy Bebop (S1)`, while preserving the raw season metadata name
when the user specifically asks for it. JSON outputs include
`metadata.parent_series_title` for season entries when metadata is attached and
the parent title is available; it can be `null` when unavailable and is absent
when `--metadata none` suppresses metadata.

## Command Envelope Hints

Collection command JSON includes:

| Field | Meaning |
| --- | --- |
| `entries` | Homogeneous list of entry objects for `lib list`, `lib search`, and `lib export`. |
| `summary.entries` | Count of returned entries. |
| `summary.cache` | Cache scope and refresh metadata for this read. |
| `metadata` | Requested metadata projection and whether metadata was attached. |
| `filters` | Present on `lib list`; captures filters, sort, visibility, favorite flag, and limit. |
| `query` | Present on `lib search`; captures search text and limit. |

`lib get` JSON includes:

| Field | Meaning |
| --- | --- |
| `items` | Ordered per-id results. Each item is `found` with `entry`, or `error` with `error`. |
| `summary.requested` | Number of ids requested. |
| `summary.found` | Number of found entries. |
| `summary.errors` | Number of item-level errors. |

## Agent Usage Rules

- Use `--json` and parse fields directly; do not scrape human output.
- Treat `notes`, ids, dates, scores, watch status, favorites, and progress as
  private user library data.
- Use `metadata.name` for display titles when attached; fall back to `id` when
  metadata is absent.
- Use `lib search` for the user's cached library and `tmdb search` for global
  TMDb discovery.
- Use `--metadata none` when the user only needs CloudKit user state and not
  TMDb enrichment.
