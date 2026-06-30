# Implementation State

This file tracks the repo at a useful level of detail. It should stay short:
enough to orient the next implementation pass, not enough to become a frozen
spec.

## Current Shape

- Python `>=3.13` package managed with `uv`; console script is `ani`.
- Typer command tree is in `src/anishelf_cli/cli/`.
- Implemented command groups are `auth`, `config`, and the first library read
  surfaces.
- `auth login` starts CloudKit web auth, supports manual callback paste and an
  optional loopback callback strategy, and stores the user web auth token in
  Keychain via `keyring`.
- `auth status` and `auth refresh` call CloudKit `users/current` through
  `CloudKitExecutor`, including local locking around rolling web auth token
  use.
- `auth logout` removes the stored CloudKit web auth token and clears all local
  library cache files.
- `config show` and `config set-tmdb-api-key` are implemented.
- CloudKit app auth resolves from environment first, then embedded public app
  material.
- Secret redaction exists for known token values and sensitive URL query keys.
- Human output uses shared `core.output` blocks: sections for detail views and
  aligned tables for collections.
- `library init` initializes a rebuildable SQLite cache of `LibraryEntry`
  records from CloudKit, and `library sync` refreshes that initialized cache.
- `library status` reports local cache initialization state, and
  TMDb summary metadata readiness, and
  `library clear-cache` removes all local library cache files after explicit
  confirmation.
- `library get`, `library list`, `library export`, and `library search --title`
  read from the initialized local cache and fail closed until init has been
  run.
- `library search --title` requires complete cached TMDb summary metadata and
  fails explicitly when that metadata is unavailable or incomplete.
- The SQLite cache keeps CloudKit-derived library state separate from
  `tmdb_metadata_summary`. Library reads attach cached summary metadata by
  default, `--metadata none` suppresses attachment, and `details`/`full` are
  reserved until detail cache behavior exists.
- `library init` hydrates TMDb summary metadata for the full fetched library
  when a TMDb key is available. Later `library sync` refreshes hydrate all
  newly added entries automatically. Library read commands also support
  `--refresh-meta`, and `library get` supports `--live-meta`, for explicit
  summary refresh.
- `library list` has first-pass ergonomic filters and ordering for common
  questions: watch status, hidden/display state, favorites, saved/updated/title
  sort, and result limits.
- Low-level CloudKit diagnostics, settings, and schema checks are not
  user-facing command groups.
- `library changes` and top-level `tmdb search` are still placeholders.

## Near-Term Direction

- Keep the CLI read-only while expanding library inspection and export depth.
- Route real CloudKit requests through the executor instead of adding one-off
  request code in commands.
- Keep JSON stdout clean and write progress, warnings, and diagnostics to
  stderr.
- Keep CLI help and parse errors plain enough for agents to read; use color for
  status and errors without box-heavy decorative formatting.
- Reuse the shared human-output blocks for command output before adding custom
  formatting.
- Keep the cache rebuildable and scoped by CloudKit container, environment,
  database, zone, and authenticated user.
- Treat schema drift checks as maintainer tooling rather than public CLI UX.
- Keep TMDb enrichment optional and attached to library reads/exports through
  `--metadata`, including an explicit `none` level so CloudKit user-state
  export remains possible without TMDb.

## Decisions Still Open

- Exact command grammar for filters, stdin/file batch inputs, and partial
  failures.
- Staleness and invalidation rules for TMDb summaries and details.
- Whether low-level CloudKit diagnostics need a separate dev-only entry point.
