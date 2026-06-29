# Implementation State

This file tracks the repo at a useful level of detail. It should stay short:
enough to orient the next implementation pass, not enough to become a frozen
spec.

## Current Shape

- Python `>=3.13` package managed with `uv`; console script is `ani`.
- Typer command tree is in `src/anishelf_cli/cli/`.
- Implemented command groups are mostly `auth` and `config`.
- `auth login` starts CloudKit web auth, supports manual callback paste and an
  optional loopback callback strategy, and stores the user web auth token in
  Keychain via `keyring`.
- `auth status` and `auth refresh` call CloudKit `users/current` through
  `CloudKitExecutor`, including local locking around rolling web auth token
  use.
- `auth logout` removes the stored CloudKit web auth token.
- `config status`, `config show`, and `config set-tmdb-api-key` are implemented.
- CloudKit app auth resolves from environment first, then embedded public app
  material.
- Secret redaction exists for known token values and sensitive URL query keys.
- Domain command groups exist for zones, records, changes, library, settings,
  TMDb, metadata, and schema, but they currently return placeholders.
- Cache and AniShelf schema modules are only initial scaffolding.

## Near-Term Direction

- Keep the CLI read-only while filling in the first useful library inspection
  path.
- Route real CloudKit requests through the executor instead of adding one-off
  request code in commands.
- Keep JSON stdout clean and write progress, warnings, and diagnostics to
  stderr.
- Add only the cache shape needed by the first implemented library commands.
- Treat schema drift checks as maintainer tooling until there is a concrete user
  workflow.
- Keep TMDb hydration optional and separable from CloudKit user-state export.

## Decisions Still Open

- First stable JSON envelope for decoded library records and exports.
- Exact command grammar for batch inputs and partial failures.
- Which library operations need persistent cache before direct reads become too
  slow or request-heavy.
- Metadata cache invalidation rule for TMDb summaries and details.
- Whether low-level CloudKit diagnostics remain public commands or become
  maintainer-only tooling.
