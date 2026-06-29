## ADDED Requirements

### Requirement: CLI profile configuration

The system SHALL let a user configure a CloudKit profile containing container identifier, production environment, private database selection, API token source, callback strategy, optional AniShelf repository path, optional TMDb token source, and optional user env-file path.

#### Scenario: Profile status shows effective scope

- **WHEN** the user runs the profile status command after configuration
- **THEN** the CLI reports the selected container, production environment, database, callback strategy, CloudKit token-source type, TMDb token-source type, and AniShelf schema path without printing secret token values

#### Scenario: Missing API token blocks network calls

- **WHEN** a command requires CloudKit network access and no API token is available from the environment or Keychain
- **THEN** the CLI exits with a clear configuration error before opening a CloudKit request

#### Scenario: API token provider is abstracted

- **WHEN** a command requires CloudKit API-token access
- **THEN** the CLI resolves the token through an internal provider interface that reports a non-secret source label and optional token version without exposing the token value

#### Scenario: API token source order is deterministic

- **WHEN** both the CloudKit API token environment variable and a Keychain token are available
- **THEN** the CLI uses the environment variable value for that process and does not overwrite the Keychain value

#### Scenario: TMDb token source order is deterministic

- **WHEN** a command requires TMDb access
- **THEN** the CLI resolves a TMDb API Read Access Token from process environment, then the configured user env file, then Keychain, and never prints the token source value

#### Scenario: Plaintext env file warns on broad permissions

- **WHEN** the CLI reads a configured user env file containing token values and the file is readable by other users
- **THEN** the CLI warns on stderr and continues only when the command does not require strict secret-storage checks

### Requirement: Browser-based CloudKit login

The system SHALL authenticate the user through CloudKit Web Services private database auth, capture a `ckWebAuthToken` from a redirected callback URL, and store the token in the OS secure credential store. For production tokens, the primary flow SHALL be browser redirect to an HTTPS callback page plus manual copy/paste of the redirected URL. Loopback callback capture MAY be supported for development tokens when the configured API token allows localhost callbacks.

#### Scenario: Login stores web auth token securely

- **WHEN** the user completes the browser login flow and CloudKit redirects with a `ckWebAuthToken`
- **THEN** the CLI stores the token through the secure credential backend and prints a login success message without printing the token

#### Scenario: Production login uses manual paste

- **WHEN** the user runs login against a production CloudKit API token
- **THEN** the CLI opens the browser sign-in flow, instructs the user to copy the final redirected HTTPS callback URL, extracts `ckWebAuthToken` from the pasted URL, and stores it securely without printing the token

#### Scenario: Optional loopback callback times out

- **WHEN** the login command is using optional loopback callback capture and no callback is received before the timeout
- **THEN** the CLI exits with an authentication timeout error and does not create a partial token entry

#### Scenario: Manual paste flow completes login

- **WHEN** the user pastes a redirected callback URL containing `ckWebAuthToken`
- **THEN** the CLI extracts the token, stores it securely, and redacts the pasted URL from logs and errors

### Requirement: Logout clears stored auth

The system SHALL provide a logout command that removes the stored CloudKit web auth token for the selected profile.

#### Scenario: Logout removes token

- **WHEN** the user runs logout for a configured profile
- **THEN** the CLI deletes the stored web auth token and future authenticated commands require login again

### Requirement: Serialized CloudKit executor

The system SHALL route every CloudKit HTTP request that uses `ckWebAuthToken` through a serialized executor that holds a per-profile lock for the entire token read, request, response, successor-token save, and error-handling cycle.

#### Scenario: Concurrent commands do not reuse stale tokens

- **WHEN** two authenticated CLI commands are started concurrently for the same profile
- **THEN** only one command at a time sends a token-consuming CloudKit request and the second command reads the successor token saved by the first command

#### Scenario: Successor token replaces old token

- **WHEN** CloudKit returns a successor web auth token with a successful response
- **THEN** the executor atomically replaces the stored token before releasing the profile lock

#### Scenario: Authentication failure clears token

- **WHEN** CloudKit returns an authentication-required or authentication-failed error for a token-consuming request
- **THEN** the executor clears the stored web auth token and returns an error instructing the user to login again

### Requirement: CloudKit error handling

The system SHALL classify CloudKit Web Services errors into actionable CLI errors for authentication, access denial, throttling, quota, conflict, invalid request, unknown item, missing zone, expired change token, and transient server failures.

#### Scenario: Throttled request retries with backoff

- **WHEN** CloudKit returns a throttling response with retry guidance
- **THEN** the executor waits according to the retry guidance before retrying within the configured retry budget

#### Scenario: Access denied does not retry blindly

- **WHEN** CloudKit returns access denied for a request
- **THEN** the CLI stops without retrying and reports that the authenticated user cannot access the selected scope

#### Scenario: Change token expired is recoverable

- **WHEN** a zone-change command receives a change-token-expired error
- **THEN** the CLI reports that the cursor is invalid and either rebuilds the derived cache or instructs the caller to retry without that cursor for a full zone resync

### Requirement: Generic read-only CloudKit commands

The system SHALL expose read-only CloudKit commands under the `ani` command for current-user lookup, zone listing, record lookup, record query, database changes, and zone changes.

#### Scenario: whoami reads current CloudKit user

- **WHEN** the user runs the whoami command while logged in
- **THEN** the CLI calls the private database current-user endpoint and prints the current CloudKit user identifier in human-readable or JSON form

#### Scenario: Record lookup batches identifiers

- **WHEN** the user requests lookup for multiple record identifiers in one command
- **THEN** the CLI sends one batched CloudKit lookup operation where CloudKit supports batching and returns per-record success or failure results

#### Scenario: Generic commands are read-only

- **WHEN** the user lists available generic CloudKit commands
- **THEN** the CLI does not expose modify, delete, create, or arbitrary raw HTTP commands in the normal command surface

### Requirement: Native batch input and output

The system SHALL make batch reads a native part of commands that operate on identities, queries, or metadata hydration jobs.

#### Scenario: Multiple positional identities are batched

- **WHEN** the user passes multiple identities to a command such as library get
- **THEN** the CLI deduplicates equivalent inputs, preserves first-seen output order, executes chunked lookup requests, and returns one result or error per requested identity

#### Scenario: Stdin and file inputs are accepted

- **WHEN** the user provides batch input through `--stdin` or `--input`
- **THEN** the CLI reads one item per line, ignores blank lines and comments, validates every item, and reports item-level validation errors without aborting valid items

#### Scenario: JSONL input preserves correlation

- **WHEN** the user provides JSONL batch input with caller correlation fields
- **THEN** the CLI includes those correlation fields in JSON or JSONL output for each corresponding result or error

#### Scenario: Structured batch output is machine safe

- **WHEN** the user runs a batch command with `--json` or `--jsonl`
- **THEN** the CLI writes only structured result data to stdout and writes progress, warnings, and diagnostics to stderr

#### Scenario: Partial batch failure has a distinct result

- **WHEN** a batch command has at least one successful item and at least one failed item
- **THEN** the CLI reports item-level errors, includes a partial-success summary, and exits with a distinct partial-success exit code

### Requirement: Rebuildable local sync cache

The system SHALL maintain a rebuildable local cache of AniShelf CloudKit library records and zone sync tokens for library-wide read operations.

#### Scenario: Initial sync fetches the whole zone once

- **WHEN** a library-wide command runs without a stored cache or zone sync token
- **THEN** the CLI fetches all current records from the AniShelf custom zone through the zone-change API, follows `moreComing` pagination, stores decoded records locally, and persists the returned final sync token only after cache application succeeds

#### Scenario: Incremental sync fetches only changes

- **WHEN** a library-wide command runs with an existing zone sync token
- **THEN** the CLI fetches only records changed since that token, applies record upserts, raw CloudKit deletion markers, AniShelf tombstone records, and settings changes to the local cache, and persists each successor token only after the corresponding cache update succeeds

#### Scenario: Expired sync token rebuilds cache

- **WHEN** CloudKit reports that the stored zone sync token has expired
- **THEN** the CLI discards the affected local cache and token, refetches the zone from the beginning, and reports that a rebuild occurred

#### Scenario: Exact identity lookup can avoid cache

- **WHEN** the user requests a specific record identity and passes a no-cache option
- **THEN** the CLI performs a direct CloudKit record lookup without requiring a full library cache

#### Scenario: Library-wide commands use cache

- **WHEN** the user runs library list, library export, or library title search
- **THEN** the CLI incrementally refreshes the local cache unless disabled and then evaluates the command against cached decoded records

### Requirement: AniShelf schema adapter

The system SHALL decode AniShelf CloudKit sync records from the verified lean schema with container `iCloud.com.samuelhe.MyAnimeList`, zone `AniShelfLibrary`, record types `LibraryEntry` and `LibrarySettings`, and settings record name `userDefaults`.

#### Scenario: LibraryEntry snapshot decodes

- **WHEN** a `LibraryEntry` record contains a supported live snapshot schema version and required fields
- **THEN** the CLI decodes identity, TMDb IDs, entry type, display state, saved date, watch status, dates, score, favorite, notes, custom poster path, episode progresses, and update clocks into a typed AniShelf entry object

#### Scenario: LibraryEntry tombstone decodes

- **WHEN** a `LibraryEntry` record contains valid identity fields and `deletedAt`
- **THEN** the CLI decodes it as a tombstone and does not require live snapshot fields such as watch status, notes, or episode progress

#### Scenario: LibrarySettings decodes

- **WHEN** the settings record `userDefaults` contains a supported settings schema version, updated timestamp, and payload data
- **THEN** the CLI decodes the payload values as booleans, strings, or string arrays

#### Scenario: Unsupported schema version fails explicitly

- **WHEN** a CloudKit record contains a schema version newer than the CLI supports
- **THEN** the CLI reports an unsupported schema error instead of silently dropping or guessing fields

### Requirement: AniShelf read-only domain commands

The system SHALL expose AniShelf-specific read commands for listing library entries, searching entries in the user's library, retrieving a library entry by identity or unambiguous title, exporting library records, showing synced settings, and checking schema compatibility.

#### Scenario: Library list filters by synced fields

- **WHEN** the user runs the library list command with filters for entry type, watch status, favorite state, score, display state, tombstone inclusion, or updated-after clocks
- **THEN** the CLI returns only decoded AniShelf records matching those CloudKit-synced fields

#### Scenario: Library get uses stable identity

- **WHEN** the user runs library get with an identity such as `movie:123`, `series:456`, or `season:456:1:789`
- **THEN** the CLI looks up the corresponding CloudKit record in the AniShelf zone and returns the decoded entry or tombstone

#### Scenario: Library get supports batch identities

- **WHEN** the user runs library get with multiple positional identities or input-file identities
- **THEN** the CLI batches CloudKit lookups, preserves requested output order, and returns item-level not-found or decode errors without hiding successful entries

#### Scenario: Library search avoids full-library hydration

- **WHEN** the user runs library search with a title query and TMDb metadata credentials are configured
- **THEN** the CLI resolves TMDb title candidates, intersects them with the user's CloudKit library identities, and returns only matching user library entries without hydrating metadata for unrelated library entries

#### Scenario: Library search uses cached metadata opportunistically

- **WHEN** matching library entries already have cached metadata
- **THEN** the CLI includes cached metadata summaries in search output without requiring fresh TMDb detail requests

#### Scenario: Global TMDb search is separate from library search

- **WHEN** the user runs a TMDb search command with a title query
- **THEN** the CLI returns TMDb discovery candidates without reading CloudKit library records or implying that the user has those entries saved

#### Scenario: Notes and score lookup by title handles ambiguity

- **WHEN** the user requests notes and score for a title that resolves to exactly one saved library entry
- **THEN** the CLI returns that entry's notes and score from CloudKit user-state fields

#### Scenario: Ambiguous title lookup returns candidates

- **WHEN** the user requests notes and score for a title that resolves to multiple saved library entries
- **THEN** the CLI returns candidate identities and metadata summaries instead of choosing one silently

#### Scenario: Library export produces stable JSON

- **WHEN** the user runs library export with JSON output
- **THEN** the CLI writes decoded AniShelf entries, tombstones when requested, settings when requested, source profile metadata, and schema snapshot version in a deterministic JSON or JSONL format

#### Scenario: Raw export can omit metadata

- **WHEN** the user runs library export without metadata flags
- **THEN** the CLI can export CloudKit user-state records without making TMDb requests

### Requirement: TMDb metadata hydration

The system SHALL provide first-class TMDb metadata hydration for AniShelf library entries using a Python TMDb integration package behind an internal adapter with HTTP fallback when needed.

#### Scenario: Summary hydration enriches entries

- **WHEN** the user requests summary metadata for library entries
- **THEN** the CLI returns titles, original titles, overviews, dates, status, runtime or episode counts, genres, poster/backdrop/logo paths, popularity/vote fields, and external IDs where available

#### Scenario: Series summary does not fetch every season

- **WHEN** the CLI hydrates a TV series at summary depth
- **THEN** it uses series-level TMDb data including the seasons summary array and does not issue per-season detail requests for every season

#### Scenario: Details hydrate relevant seasons

- **WHEN** the CLI hydrates at details depth for a library containing season records
- **THEN** it fetches per-season details only for season records present in the user's CloudKit library unless the user explicitly requests all seasons

#### Scenario: Full hydration is explicit

- **WHEN** the user requests full metadata or passes an all-seasons option
- **THEN** the CLI may fetch all seasons and episode lists for matching TV series and marks the output metadata depth as full

#### Scenario: Metadata hydration batches TMDb requests

- **WHEN** the user hydrates metadata for multiple library entries
- **THEN** the CLI groups requests by media type, TMDb ID, language, and hydration depth, reuses cached metadata, and avoids duplicate TMDb requests within the same command

#### Scenario: Missing TMDb token degrades predictably

- **WHEN** a command requires metadata and no TMDb token is available from the environment, configured env file, or Keychain
- **THEN** the CLI exits with a clear metadata configuration error instead of returning partial title-based results

### Requirement: Schema drift detection

The system SHALL provide a schema check command that compares the CLI AniShelf schema snapshot against the source files in an AniShelf checkout.

#### Scenario: Matching schema passes

- **WHEN** the checked AniShelf source has the same CloudKit constants, record fields, enum values, identity formats, and supported schema versions as the CLI snapshot
- **THEN** the schema check command exits successfully and reports the matched snapshot version

#### Scenario: Drift blocks AniShelf commands when strict

- **WHEN** strict schema checking is enabled and the checked AniShelf source differs from the CLI snapshot
- **THEN** AniShelf domain commands fail before reading CloudKit records and report the drifted fields or constants

#### Scenario: Missing AniShelf checkout degrades with warning

- **WHEN** no AniShelf source path is configured or found
- **THEN** the CLI can run with its committed schema snapshot but prints a warning for schema-sensitive commands unless the user disables the warning

### Requirement: Agent-friendly output and exit behavior

The system SHALL make every command suitable for agentic use by supporting structured JSON output, deterministic exit codes, stable error codes, and CloudKit/TMDb token redaction.

#### Scenario: JSON output is stable

- **WHEN** the user runs a read command with `--json`
- **THEN** the CLI prints machine-readable JSON with stable top-level fields and no progress text mixed into stdout

#### Scenario: Secrets are redacted

- **WHEN** the CLI logs requests, errors, profile status, or debug diagnostics
- **THEN** it never prints `ckAPIToken`, `ckWebAuthToken`, successor tokens, TMDb tokens, or raw callback URLs containing tokens

#### Scenario: Human output remains concise

- **WHEN** the user runs a read command without `--json`
- **THEN** the CLI prints concise tables or summaries that are useful in a terminal and does not require the user to inspect raw CloudKit response JSON
