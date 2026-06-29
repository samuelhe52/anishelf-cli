## 1. Project Setup

- [x] 1.1 Create a Python 3.12+ `uv` project with package metadata, console script entry point, pytest configuration, and lint/type-check commands.
- [x] 1.2 Add initial dependencies for Typer, HTTPX, Pydantic, keyring, platformdirs, filelock, `tmdbsimple`, and rich/table output.
- [x] 1.3 Define the `ani` CLI command tree and global options for profile, JSON output, verbosity, metadata depth, and AniShelf source path.
- [x] 1.4 Add a token-redacting logging/error utility and tests proving CloudKit tokens, TMDb tokens, and callback URLs are never printed.

## 2. Profile and Credential Storage

- [ ] 2.1 Implement profile config load/save with container, production environment, private database, callback strategy, CloudKit API token provider selection, TMDb token source, optional env-file path, and optional AniShelf path.
- [ ] 2.2 Implement profile status output in human-readable and JSON formats without exposing secret values.
- [ ] 2.3 Implement a CloudKit API token provider interface returning token value, redacted source label, and optional token version, with developer-configured environment variable precedence and Keychain fallback in this change.
- [ ] 2.4 Implement TMDb token lookup with process environment precedence, optional user env-file support, and Keychain fallback.
- [ ] 2.5 Implement secure CloudKit web auth token storage through keyring with a fail-closed path when no secure backend is available.
- [ ] 2.6 Implement logout by deleting the stored web auth token for the selected profile.
- [ ] 2.7 Implement non-echoing config commands for storing CloudKit and TMDb tokens in Keychain, plus env-file permission warnings.

## 3. CloudKit Auth Flow

- [ ] 3.1 Verify current CloudKit Web Services auth behavior against official Apple docs and capture the endpoint, redirect, and successor-token response shape in code comments or fixtures.
- [ ] 3.2 Implement login initiation by calling a private database endpoint with `ckAPIToken` only and extracting the authentication redirect URL.
- [ ] 3.3 Implement the primary production login flow as browser redirect plus manual copy/paste of the final HTTPS callback URL containing `ckWebAuthToken`.
- [ ] 3.4 Implement optional loopback callback capture for development tokens that permit localhost callbacks, with timeout and clean failure behavior.
- [ ] 3.5 Add tests for successful manual-paste login, malformed callback URL handling, optional loopback timeout, and token redaction.

## 4. Serialized CloudKit Executor

- [ ] 4.1 Implement a CloudKit request builder for database v1 endpoints scoped to container, environment, and private database.
- [ ] 4.2 Implement a per-profile file lock that covers token read, HTTP request, response parsing, successor-token save, and failure handling.
- [ ] 4.3 Implement successor web auth token extraction and atomic replacement after successful responses.
- [ ] 4.4 Implement CloudKit error classification for auth, access denied, throttling, quota, conflict, invalid request, unknown item, missing zone, expired change token, and transient failures.
- [ ] 4.5 Add retry/backoff behavior for throttled and transient failures with bounded retry budgets.
- [ ] 4.6 Add concurrency tests showing two simultaneous token-consuming commands serialize and preserve the latest successor token.
- [ ] 4.7 Add a performance smoke test or benchmark guard documenting that one Keychain successor-token write per CloudKit round trip is acceptable for expected CLI usage.

## 5. Generic Read-Only Commands

- [ ] 5.1 Implement `whoami` against the current private database user endpoint.
- [ ] 5.2 Implement zone listing for the private database.
- [ ] 5.3 Implement batched record lookup by zone and record name.
- [ ] 5.4 Implement bounded record query with explicit warnings for index-related CloudKit failures.
- [ ] 5.5 Implement database-change and zone-change fetch commands with cursor input/output.
- [ ] 5.6 Add command tests using mocked CloudKit responses for human output, JSON output, per-record failures, and change-token-expired handling.

## 6. Batch Ergonomics

- [ ] 6.1 Implement shared batch input parsing for positional values, `--stdin`, `--input`, and JSONL inputs.
- [ ] 6.2 Implement order-preserving deduplication and per-item validation errors with caller correlation preservation for JSONL jobs.
- [ ] 6.3 Implement shared batch output renderers for human tables, aggregate JSON, and streaming JSONL.
- [ ] 6.4 Implement progress/warning routing to stderr whenever stdout is structured.
- [ ] 6.5 Implement chunk-size controls, partial-success exit codes, and item-level result/error summaries.
- [ ] 6.6 Add tests for mixed valid/invalid input, duplicate identities, partial failures, JSONL correlation, stdout/stderr separation, and chunk boundaries.

## 7. Local Sync Cache

- [ ] 7.1 Implement a rebuildable SQLite local cache under the platform user cache directory for decoded AniShelf library entries, AniShelf tombstone records, raw CloudKit deletion markers, settings, identity indexes, and zone sync token metadata keyed by profile, container, environment, database scope, authenticated user, owner, and zone.
- [ ] 7.2 Implement initial zone sync from nil token using CloudKit zone changes and `moreComing` pagination.
- [ ] 7.3 Implement incremental zone sync from stored token, applying upserts, raw deletion markers, AniShelf tombstone records, settings changes, and successor token persistence only after successful cache update.
- [ ] 7.4 Implement expired-token handling that clears the affected cache and rebuilds from the beginning.
- [ ] 7.5 Implement command options for `--refresh`, `--rebuild-cache`, and `--no-cache` where command semantics permit them.
- [ ] 7.6 Add tests for initial sync, incremental sync, pagination, raw deletion marker application, AniShelf tombstone application, settings application, token persistence, token expiry rebuild, and no-cache direct lookup.

## 8. AniShelf Schema Snapshot and Drift Check

- [ ] 8.1 Create a committed AniShelf schema snapshot from `CloudLibrarySyncClient.swift`, `LibraryEntrySyncSnapshot.swift`, and `LibrarySettingsSyncSnapshot.swift`.
- [ ] 8.2 Implement typed models for `LibraryEntry` live snapshots, tombstones, `LibrarySettings`, episode progresses, settings payload values, entry identity formats, entry types, and watch statuses.
- [ ] 8.3 Implement `schema check --anishelf-path` to compare source constants, fields, enum values, schema versions, and identity formats against the CLI snapshot.
- [ ] 8.4 Add fixture tests for matching schema, unsupported schema version, field drift, enum drift, and missing AniShelf checkout warning behavior.

## 9. AniShelf Read-Only Domain Commands

- [ ] 9.1 Implement `library get <identity...>` using the stable AniShelf record identity and zone with shared batch input/output support.
- [ ] 9.2 Implement `library list` using the local cache plus filters for entry type, watch status, favorite, score, display state, tombstone inclusion, and updated-after clocks.
- [ ] 9.3 Implement `library search --title` by resolving TMDb candidates, intersecting them with cached CloudKit library identities, and avoiding full-library metadata hydration.
- [ ] 9.4 Implement `library export --format json|jsonl` from the local cache with deterministic ordering, schema metadata, optional settings, optional tombstones, and optional metadata depth.
- [ ] 9.5 Implement `library changes` for explicit cursor/token-based remote change inspection.
- [ ] 9.6 Implement `library get --title --fields notes,score` with ambiguous-match candidate output.
- [ ] 9.7 Implement `settings show` for the `LibrarySettings` `userDefaults` record.
- [ ] 9.8 Add domain command tests for decoded entries, tombstones, settings, filters, library-scoped title search without hydrate-all behavior, ambiguous title lookup, deterministic exports, batch get behavior, changes output, and strict schema drift blocking.

## 10. TMDb Metadata Hydration

- [ ] 10.1 Implement a TMDb metadata adapter around `tmdbsimple` with HTTPX fallback for unsupported endpoints or append combinations.
- [ ] 10.2 Implement `tmdb search --title` as global TMDb discovery that does not read CloudKit library data.
- [ ] 10.3 Implement metadata depth modes `none`, `summary`, `details`, and `full`.
- [ ] 10.4 Implement summary hydration for movies, TV series, and seasons without fetching every season for a series by default.
- [ ] 10.5 Implement details hydration that fetches per-season details only for season records present in the CloudKit library.
- [ ] 10.6 Implement explicit full/all-seasons hydration and mark output metadata depth.
- [ ] 10.7 Implement metadata caching keyed by media type, TMDb ID, language, and hydration depth.
- [ ] 10.8 Add tests for metadata token configuration, global TMDb search, summary search, relevant-season details, explicit full hydration, cache hits, batch hydration deduplication, and no-token failures.

## 11. Documentation and Verification

- [ ] 11.1 Document CloudKit Dashboard setup, developer API token configuration, production HTTPS callback setup, manual copy/paste login flow, optional development loopback behavior, first login, and the deferred public-token provider boundary.
- [ ] 11.2 Document local cache behavior, initial sync, incremental sync, cache rebuild, and no-cache direct lookup limitations.
- [ ] 11.3 Document TMDb token setup, supported token sources, env-file security trade-offs, metadata depth behavior, season hydration trade-offs, and metadata cache invalidation.
- [ ] 11.4 Document batch input/output conventions, JSONL shape, partial-success exit codes, and stdout/stderr behavior.
- [ ] 11.5 Document security behavior, Keychain token storage, read-only scope, private-data output warnings, and unsupported write/delete behavior.
- [ ] 11.6 Document schema drift workflow for AniShelf changes and when both repositories must be updated.
- [ ] 11.7 Run the full local test suite, lint, and type checks.
- [ ] 11.8 Manually smoke test `login`, `whoami`, initial cache sync, incremental cache sync, `schema check`, one AniShelf export, one batch library get, and one metadata-backed title search against production.
