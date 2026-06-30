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
- `auth logout` removes the stored CloudKit web auth token.
- `config show` and `config set-tmdb-api-key` are implemented.
- CloudKit app auth resolves from environment first, then embedded public app
  material.
- Secret redaction exists for known token values and sensitive URL query keys.
- Human output uses shared `core.output` blocks: sections for detail views and
  aligned tables for collections.
- `library get` does direct CloudKit record lookup for explicit semantic
  identities.
- `library list`, `library export`, and `library search --title` read from a
  rebuildable SQLite cache of `LibraryEntry` records. They refresh CloudKit
  `changes/zone` before reading unless `--offline` is passed where supported.
- `library search --title` uses the configured TMDb API key to search movie and
  TV titles, intersects those IDs with cached movie/series entries, and includes
  seasons whose parent series matched.
- Library metadata depth parsing exists as command-local `--metadata`, but
  deeper metadata hydration remains mostly inert.
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
- Metadata cache invalidation rule for TMDb summaries and details.
- Whether low-level CloudKit diagnostics need a separate dev-only entry point.
