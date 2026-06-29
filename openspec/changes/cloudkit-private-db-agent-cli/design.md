## Context

AniShelf already syncs a lean, user-owned CloudKit private database schema through `DataProvider/Sources/LibrarySync`, not a rich SwiftData mirror. Verified current constants are container `iCloud.com.samuelhe.MyAnimeList`, custom zone `AniShelfLibrary`, record types `LibraryEntry` and `LibrarySettings`, and settings record name `userDefaults`. `LibraryEntry` record names are stable semantic identities: `movie:<tmdbID>`, `series:<tmdbID>`, and `season:<parentSeriesID>:<seasonNumber>:<tmdbID>`. The CLI command name is `ani`; the repository name remains `anishelf-cli`.

CloudKit Web Services private database access uses container-scoped API-token auth plus a user `ckWebAuthToken`. Apple documents the endpoint shape as `https://api.apple-cloudkit.com/database/1/<container>/<environment>/<operation-specific subpath>`, private database selection as the `private` path component for database operations, and the web auth token as single-round-trip: each successful request returns a successor token and invalidates the previous token. That makes concurrency control part of correctness, not a caller convention.

The first CLI should optimize for a human and agent using the same safe command surface. If the CLI is ergonomic, agent integration is mostly a matter of stable JSON output, clear exit codes, and commands that express user intent directly.

## Goals / Non-Goals

**Goals:**

- Build a modern Python CLI named `ani` with a repo-native package layout, typed adapters, structured JSON output, and shell-friendly commands.
- Authenticate a user to the AniShelf production CloudKit private database and store the rolling web auth token in the OS secure credential store.
- Serialize all token-consuming CloudKit HTTP requests per container/environment/account so concurrent CLI calls cannot corrupt token state.
- Provide read-only generic CloudKit inspection commands and read-only AniShelf domain commands with first-class TMDb metadata hydration.
- Decode AniShelf's current lean sync schema, including tombstones and settings, without requiring the agent to understand raw CloudKit records.
- Detect schema drift against `~/projects/AniShelf/DataProvider/Sources/LibrarySync` before commands silently misdecode records.

**Non-Goals:**

- Do not add write, delete, or record modification commands in this change.
- Do not expose a normal `cloudkit http` escape hatch for arbitrary requests.
- Do not read arbitrary iCloud data outside the configured app container, environment, database, and authenticated user.
- Do not join with AniShelf's local SwiftData store in the first pass.
- Do not require changes to the AniShelf app repository for initial read-only CLI implementation.

## Decisions

### Use Python 3.12+ With `uv`, Typer, HTTPX, Pydantic, Keyring, Platformdirs, Filelock, and `tmdbsimple`

Python is the best first implementation stack for agent-facing CLI ergonomics, quick iteration, typed JSON models, and testability. `uv` gives reproducible packaging and fast local commands. Typer provides discoverable command help. HTTPX gives explicit request/response handling. Pydantic models make the CloudKit and AniShelf decode boundary testable. `keyring` maps to macOS Keychain for local secure token storage; `platformdirs` and `filelock` provide per-user lock/config paths. `tmdbsimple` should be the default TMDb wrapper because it is a maintained Python wrapper for TMDb API v3 and maps closely to the endpoints AniShelf needs. Hide it behind an internal metadata adapter, with direct HTTPX fallback for endpoints or append combinations the wrapper does not expose cleanly.

Alternatives considered:

- Swift CLI: better Apple-native distribution and Keychain APIs, but slower iteration and less convenient agent/tool integration.
- TypeScript CLI: good JSON ergonomics, but credential storage and Python-centric agent workflows are weaker for this local tool.

### Keep Auth and Request Execution Below the Command Surface

The CLI will expose commands such as `ani login`, `ani whoami`, `ani zones list`, `ani records lookup`, `ani records query`, `ani changes zone`, `ani library list`, `ani library search`, `ani library export`, `ani tmdb search`, `ani metadata hydrate`, and `ani schema check`. Each CloudKit command builds a typed request and passes it through one executor.

The executor owns:

- appending `ckAPIToken` and `ckWebAuthToken`;
- holding a per-profile lock for the entire read-request-response-token-save cycle;
- extracting and atomically storing successor web auth tokens;
- clearing token state on authentication failures;
- retrying throttled/transient failures with bounded backoff;
- redacting tokens in logs and errors.

The CLI must never ask the agent to manage tokens, retry auth, or sequence requests manually.

Production is the default and only first-class CloudKit environment for both development and release use. Because the first capability is read-only, using production avoids testing against empty or divergent development data. The profile still records environment explicitly so future write-capable changes can revisit this.

CloudKit API token resolution must sit behind an internal provider interface that returns the token value, a non-secret source label, and an optional token version. This change implements only developer-configured providers: environment variable first, Keychain fallback. That supports headless/automation usage without requiring persistent API-token storage, while allowing `ani config set-cloudkit-token` to store the API token in Keychain for normal local use. Public embedded-token distribution, token obfuscation, GitHub token manifests, and release-gated token rotation are intentionally deferred to a separate change that can plug into the same provider interface. The rolling `ckWebAuthToken` must be stored in Keychain by default and replaced after every successful token-consuming CloudKit response.

For production CloudKit API tokens, the callback flow must assume an HTTPS redirect target, not a localhost loopback URL. Verified behavior on the AniShelf production container is that Dashboard offers HTTPS or a custom reverse-DNS scheme, while localhost is available only on the development container. For the CLI, the first production-safe login flow is therefore browser redirect to a user-controlled HTTPS callback page, followed by manual copy/paste of the redirected URL so the CLI can extract `ckWebAuthToken`. Loopback capture remains useful for development tokens and should stay optional, not primary, in the implementation.

Keychain updates are acceptable for this CLI because CloudKit network round trips dominate command latency and full-export commands should batch/page deliberately. Correctness requires write-through persistence of the successor token before releasing the lock; otherwise a crash after a successful request could leave only an invalid old token. If Keychain proves measurably slow, optimize by reducing CloudKit request count and keeping only an in-process token while a single command holds the lock, not by storing the rolling token in plaintext.

TMDb should use the API Read Access Token as a bearer token. Source order should be process environment first (`ANI_TMDB_READ_ACCESS_TOKEN`, then `TMDB_READ_ACCESS_TOKEN`), optional user env file second, and Keychain fallback third. The persistent default is Keychain via `ani config set-tmdb-token`, which should prompt without echoing. A plaintext env file is useful for headless machines, but it should be opt-in, live under the CLI config directory such as `~/.anishelf-cli/.env`, be excluded from exports/logs, and produce a warning if permissions are broader than user-readable. Avoid a raw `--tmdb-token <value>` option because command-line arguments leak through shell history and process listings; prefer `--tmdb-token-file` or `--tmdb-token-stdin` for one-off automation.

### Make Batch Operations a Native CLI Primitive

Batch behavior should be designed into the command grammar instead of exposed as a separate awkward mode. Any command that can naturally operate on one identity should accept many identities:

- positional values: `ani library get movie:1 series:2`;
- stdin: `ani library get --stdin`;
- file input: `ani library get --input ids.txt`;
- JSONL input for structured jobs: `ani metadata hydrate --input entries.jsonl --jsonl`.

Text inputs should accept one identity/query per line, ignore blank lines and comments, and preserve first-seen order while deduplicating requests before network execution. JSONL inputs should preserve a caller-provided correlation field when present so agents can map results back to their own work queue.

Execution should chunk work through the fewest practical CloudKit and TMDb requests while respecting API limits, retry budgets, and the CloudKit token lock. Batching is also the main mitigation for repeated Keychain successor-token writes because a batch command consumes fewer CloudKit round trips than an agent issuing many single-record commands.

Output should be predictable:

- human mode prints compact tables and summaries;
- `--json` prints one object with `results`, `errors`, and `summary`;
- `--jsonl` streams one result or error object per input item;
- progress and warnings go to stderr, never stdout, when structured output is enabled.

Partial failures are expected for large batches. The CLI should return success when all items succeed, a distinct partial-success exit code when at least one item succeeds and one item fails, and a failure exit code when no requested item succeeds.

### Maintain a Rebuildable Local Sync Cache

CloudKit supports three useful read shapes for this CLI:

- direct lookup by record name for exact identities;
- bounded query for diagnostic cases where indexes permit it;
- incremental zone changes for keeping a local view current.

The CLI should use the same strategic shape as the AniShelf app for library-wide operations: perform an initial zone-change fetch with no sync token, persist the returned zone sync token, and later fetch only changes since that token. The app's `CloudLibrarySyncImporter` already does this with `fetchRecordZoneChanges(in:since:)`, paging through `moreComing`, and `CloudLibrarySyncChangeTokenStore` persists tokens by container, account, owner, and zone. The CLI cache key should additionally include the CloudKit environment and database scope so future development/shared support cannot accidentally reuse a production private-database cursor.

For the CLI, the local state should be a derived, rebuildable SQLite cache under the platform user cache directory, not a second source of truth. SQLite is worth using over a JSON file because library-wide list/search/export commands need indexes, atomic cache updates, and predictable behavior for large libraries. Keep the schema intentionally small and versioned; on incompatible cache-schema change, schema drift, corruption, or token expiry, discard and rebuild rather than migrating aggressively. Persist:

- CloudKit zone sync token for `AniShelfLibrary`, keyed by profile, container, environment, database scope, authenticated user, owner, and zone;
- decoded lean `LibraryEntry` live snapshots and tombstones keyed by identity;
- `LibrarySettings` snapshot;
- raw CloudKit deletion markers keyed by record name, used to remove deleted records from the derived cache while keeping AniShelf's explicit `LibraryEntry` tombstone records as domain state;
- lightweight identity indexes by entry type, TMDb ID, parent series, season, watch status, score, and updated clocks;
- optional TMDb metadata cache keyed separately by media type, TMDb ID, language, and depth.

Commands should choose the cheapest path:

- `library get movie:1` can use direct CloudKit lookup or the cache when fresh enough.
- `library list`, `library export`, and `library search --title` should run an incremental sync first, then query the local cache.
- `library changes` should expose CloudKit cursor/token behavior directly for diagnostics and agent workflows.
- `--no-cache` should force direct CloudKit lookup only for commands that can operate without a library-wide view.
- `--refresh` should run incremental sync before reading.
- `--rebuild-cache` should discard the local cache and refetch the zone from the beginning.

If CloudKit reports an expired change token, the CLI should discard the affected zone token and rebuild the derived cache from the beginning. This is more complexity than stateless reads, but it is the only practical way to make repeated library search/export/list commands ergonomic without fetching the whole library every time.

Token advancement must be commit-after-apply. During pagination, each `moreComing` response provides the next `syncToken` to request the following page, but the durable cache token should only move forward after the corresponding record changes have been applied successfully. Implementations can commit page by page in SQLite transactions or stage all pages and commit the final token once the batch is fully applied; they must not persist a successor token ahead of decoded cache state.

### Default to Read-Only and Treat Mutations as a Separate Future Change

The first capability is read-only. Generic record modification and AniShelf domain writes are intentionally absent, even behind flags. This keeps the first implementation useful for diagnostics/export while avoiding conflict-resolution, audit, and destructive-action policy before the read path is proven.

Future write support should be a separate proposal with dry-run previews, explicit confirmations, conflict detection, audit logs, and tests against stale record changes.

### Use AniShelf Source as the Schema Authority, With a CLI Snapshot for Runtime Decoding

The CLI will include a committed schema snapshot generated from AniShelf's `CloudLibrarySyncClient.swift`, `LibraryEntrySyncSnapshot.swift`, and `LibrarySettingsSyncSnapshot.swift`. Runtime decoders use the snapshot so the CLI can run without importing Swift code.

`ani schema check --anishelf-path ~/projects/AniShelf` will parse those Swift files and compare:

- container identifier, zone name, record types, settings record name;
- field names and required/optional status;
- supported schema versions;
- entry type values and watch status values;
- identity formats;
- JSON-in-Data payload shapes for `episodeProgresses` and `payload`.

If AniShelf changes the CloudKit record contract, the CLI repo must update its snapshot/decoder in the same release window or explicitly document compatibility. Changes that only affect local rich SwiftData models or fetched metadata should not require CLI updates.

### Make AniShelf Domain Commands Hydrate Metadata Deliberately

The current CloudKit records do not store titles, overviews, posters for non-custom selections, runtime, characters, staff, seasons metadata, or localized TMDb data. Metadata is therefore first-class CLI functionality, but it must be fetched from TMDb rather than inferred from CloudKit.

The first domain surface should include:

- `library list` with filters for entry type, watch status, favorite, score, on-display state, tombstone inclusion, and updated-after clocks;
- `library get <identity...>` plus `--stdin` / `--input`;
- `library search --title ...`, backed by TMDb title candidates intersected with the user's library identities;
- `library get --title ... --fields notes,score` for one-command user-state lookup by name;
- `tmdb search --title ...` for global TMDb discovery that does not read CloudKit user data;
- `library export --format json|jsonl`;
- `metadata hydrate` for explicit metadata enrichment and cache warm-up;
- `settings show`;
- `schema check`.

`ani library search --title` is library-scoped. It must not hydrate the entire library on demand. The cheap path is to maintain or fetch a CloudKit-only identity set, query TMDb search for the title, map TMDb movie/TV candidates to AniShelf identities, and return only candidate identities that exist in the user's library. Cached metadata can enrich the output, but missing cache entries should not force full-library hydration. `ani tmdb search --title` is the separate global discovery surface and should not imply that the user has any returned item in their library. For name-based user-state lookup, `ani library get --title "Frieren" --fields notes,score --json` should resolve TMDb candidates, intersect them with library identities, and return notes/score only when the match is unambiguous. Ambiguous matches should return candidates unless the user supplies a specific identity, TMDb ID, media type, or `--first`.

Hydration should have depth levels:

- `none`: CloudKit user-state only.
- `summary`: titles, original titles, overview, dates, status, runtime/episode counts, genres, poster/backdrop/logo paths, popularity/vote fields, and external IDs where available.
- `details`: summary plus credits, images, videos, keywords, content ratings/release dates, translations, and per-season summaries for TV series.
- `full`: details plus full season detail calls for every relevant season, including episode lists and season credits where needed.

Default command behavior should be `none` for raw exports, cache-first `summary` for display/list output, and TMDb candidate search for title lookup. Commands should only fetch missing summary metadata when the user requests metadata output, a title lookup needs candidate details, or `--refresh-metadata` is explicit. Series queries should not fetch every season by default. They should include the series-level seasons array from the TV details response at `summary`, fetch per-season details only for season records in the user's CloudKit library at `details`, and fetch all seasons only when `--metadata full` or `metadata hydrate --all-seasons` is explicit.

### Prefer Zone Changes for Full Export

`records/lookup` is appropriate for known record identities, and `records/query` is useful for bounded inspection where CloudKit indexes permit it. Full AniShelf exports should use the custom zone change flow so the CLI can page through `AniShelfLibrary` without relying on query indexes that may not exist for every filter.

## Risks / Trade-offs

- Production CloudKit login cannot rely on localhost callback URLs -> Make HTTPS callback plus manual paste the primary production path, keep loopback callback optional for development tokens, and verify successor-token field handling with live API tokens.
- `keyring` behavior varies by OS and headless environments -> Support macOS Keychain as the primary path; fail closed with a clear message if no secure backend is available unless an explicit unsafe test mode is set.
- Schema parsing Swift source can be brittle -> Keep the parser narrow and fixture-backed; compare generated snapshots in tests so drift is caught by deterministic failures.
- CloudKit query filters may require dashboard indexes -> Treat `records query` as a generic diagnostic command and use zone changes plus client-side filtering for AniShelf export/list.
- Read-only commands still expose private user data to terminal output and agent transcripts -> Provide default concise output, explicit `--json`, redaction for tokens, and docs warning that library exports are private data.
- TMDb wrappers may lag the API or omit useful append combinations -> Keep the wrapper behind an internal adapter and use HTTPX fallback with fixture tests for endpoints the wrapper cannot cover.
- TMDb hydration can multiply request counts for large libraries -> Cache metadata by media type, TMDb ID, language, and hydration depth; default to summary metadata; require explicit `full` depth for all-season fan-out.
- Public `ckAPIToken` distribution has different release and abuse trade-offs than developer-configured tokens -> Keep the first implementation behind a pluggable API-token provider and handle public embedded-token rotation in a separate OpenSpec change.

## Migration Plan

This is a new repo-local CLI capability, so there is no data migration. Implementation should start with production-environment login and `whoami`, then the serialized executor, then read-only generic commands, then AniShelf decoding/export, then TMDb metadata hydration.

Rollback is deleting the CLI package files and the OpenSpec change. User credentials are stored outside the repo; `logout` must delete the web auth token from secure storage.

## Open Questions

- Which TMDb metadata fields should be included in `summary` versus `details` output after comparing against AniShelf's current UI/export model?
- Should metadata cache invalidation use a fixed TTL, TMDb change tracking, or both?
