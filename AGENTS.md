# Agent Notes

Use this file as routing guidance. Do not read every document by default; read
the relevant one when the task touches that area.

## Reference Docs

- `docs/north-star.md`: Read when changing product direction, command scope,
  read-only policy, configuration shape, or other broad CLI design decisions.
- `docs/implementation-state.md`: Read when you need the lightweight current
  repo state, near-term direction, or known open decisions.
- `docs/reference/cloudkit-auth.md`: Read when working on CloudKit login,
  logout, web auth token storage, authenticated request execution, successor
  token handling, locking, retry/error classification, or token redaction.
- `docs/reference/cloudkit-app-auth.md`: Read when working on embedded
  CloudKit app auth, environment overrides, app-token redaction, token
  invalidation diagnostics, manifest checks, or release rotation.
- `docs/reference/anishelf-domain.md`: Read when working on AniShelf library
  decoding, record identities, schema drift checks, local cache behavior, batch
  input/output, library commands, exports, TMDb metadata hydration, or metadata
  caching.

## Standing Constraints

- Keep the CLI read-only unless a future task explicitly changes the safety
  model.
- Never print secret values, raw callback URLs containing tokens, or stored auth
  tokens in normal output, JSON output, logs, errors, tests, or docs.
- Do not add user-facing CloudKit app-token setup or storage instructions.
- Do not reintroduce persisted local configuration or credential namespaces.
- Keep Keychain storage for user-scoped CloudKit web auth tokens and TMDb keys;
  do not add Keychain storage for CloudKit app auth.
- Commit messages should use concise `type: Subject` style, for example
  `feat: Add CloudKit login flow` or `fix: Tighten credential source handling`.
