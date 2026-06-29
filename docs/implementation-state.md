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
- `config show` and `config set-tmdb-api-key` are implemented.
- CloudKit app auth resolves from environment first, then embedded public app
  material.
- Secret redaction exists for known token values and sensitive URL query keys.
- `--verbose` is parsed into `AppState.verbosity`; new features should use it
  only for extra stderr diagnostics and must keep stdout stable for data.
- Human output uses shared `core.output` blocks: sections for detail views and
  aligned tables for collections.
- Domain command groups exist for library, TMDb, and metadata, but they
  currently return placeholders.
- Low-level CloudKit diagnostics, settings, and schema checks are not
  user-facing command groups.
- The cache module is only initial scaffolding.

## Near-Term Direction

- Keep the CLI read-only while filling in the first useful library inspection
  path.
- Route real CloudKit requests through the executor instead of adding one-off
  request code in commands.
- Keep JSON stdout clean and write progress, warnings, and diagnostics to
  stderr.
- Keep CLI help and parse errors plain enough for agents to read; use color for
  status and errors without box-heavy decorative formatting.
- Reuse the shared human-output blocks for command output before adding custom
  formatting.
- Add only the cache shape needed by the first implemented library commands.
- Treat schema drift checks as maintainer tooling rather than public CLI UX.
- Keep TMDb hydration optional and separable from CloudKit user-state export.

## Decisions Still Open

- First stable JSON envelope for decoded library records and exports.
- Exact command grammar for batch inputs and partial failures.
- Which library operations need persistent cache before direct reads become too
  slow or request-heavy.
- Metadata cache invalidation rule for TMDb summaries and details.
- Whether low-level CloudKit diagnostics need a separate dev-only entry point.
