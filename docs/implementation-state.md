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
- `config show`, `config set-defaults`, and `config set-tmdb-api-key` are
  implemented. Defaults include library output behavior, preferred TMDb
  metadata language (`en`), and TMDb hydration depth (`details`).
- CloudKit app auth resolves from environment first, then embedded public app
  material.
- Secret redaction exists for known token values and sensitive URL query keys.
- Human output uses shared `core.output` blocks: sections for detail views,
  width-aware aligned tables for collections, and an opt-in list style for
  `lib list` / `lib search`.
- `lib init` initializes a rebuildable SQLite cache of `LibraryEntry`
  records from CloudKit, and `lib sync` refreshes that initialized cache.
- `lib init` now keeps JSON stdout clean while still emitting cache and
  TMDb hydration progress to stderr.
- `lib status` reports local cache initialization state, visible/hidden entry
  counts, TMDb metadata readiness, and
  `lib clear-cache` removes all local library cache files after explicit
  confirmation.
- `lib get`, `lib list`, `lib export`, and `lib search <query>`
  read from the initialized local cache and fail closed until init has been
  run. These read commands also support `--sync` for an explicit CloudKit
  refresh before serving results.
- `lib search <query>` requires complete cached TMDb metadata and
  searches titles, translations, parent-series metadata, overviews, notes, and
  on-air dates in the same priority order as AniShelf's library search. It
  accepts `--limit` to cap visible matches.
- The SQLite cache keeps CloudKit-derived library state separate from
  `tmdb_metadata_items`. Library reads attach cached summary metadata by
  default, `--metadata none` suppresses attachment, and `--metadata
  summary|details|full` controls output projection. Cached metadata is keyed by
  the configured preferred TMDb metadata language and stores a readiness depth.
- `lib init` hydrates TMDb metadata for the full fetched library at the
  configured hydration depth when a TMDb key is available. Later `lib sync`
  refreshes hydrate all newly added or insufficiently hydrated entries
  automatically. `lib refresh-meta` explicitly refreshes cached TMDb metadata
  for the full local library, and `lib get` supports `--live-meta` for targeted
  per-entry refresh at the configured hydration depth, promoted to `full` when
  full output is requested. A
  per-command `--tmdb-language` override on library reads is ad-hoc when it
  differs from the preferred language: metadata is fetched live for output
  and is not written to the cache.
- `lib list` has first-pass ergonomic filters and ordering for common
  questions: watch status, favorites, saved/updated/title sort, and result
  limits. Human output supports `--style table|list` and configurable display
  fields. Table output keeps inflexible fields readable, truncates flexible
  title/id fields when needed, uses compact dates and missing-value labels,
  and omits the display column unless hidden entries are included. List-style
  human output appends bounded metadata rows according to
  `--metadata`, while `--metadata none` suppresses metadata rows but may still
  use cached TMDb display titles. Hidden entries are excluded from collection
  reads by default and can be included with `--show-hidden` or a library config
  default.
- Low-level CloudKit diagnostics and schema checks are not
  user-facing command groups.
- `tmdb search` performs global TMDb anime title search from either positional
  title or `--title`, and discover-style popular anime lookup when no title is
  provided. It accepts `--limit` to cap returned rows and sends the preferred
  TMDb metadata language unless `--tmdb-language` is supplied for that request.

## Near-Term Direction

- Keep the CLI read-only while expanding library inspection and export depth.
- Use one model system for structured data that crosses a boundary or moves
  between layers: pydantic v2. Raw dict/list values are allowed only at
  `httpx.Response.json()`, SQLite JSON columns, and Typer argument parsing.
  Immediately after ingress, validate with `model_validate(...)`,
  `model_validate_json(...)`, or `TypeAdapter(...)`; at egress, dump explicitly
  with `model_dump(...)` or `model_dump_json(...)`.
- Name transport models `*Payload` or `*Response`, internal models with domain
  names, and CLI payloads `*Result` or `*Envelope`. Avoid `dict[str, Any]` in
  service/store/query signatures except for true raw transport edge helpers.
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

- Exact command grammar for filters and stdin/file batch inputs.
- Staleness and invalidation rules for TMDb metadata depth refreshes.
- Whether low-level CloudKit diagnostics need a separate dev-only entry point.
