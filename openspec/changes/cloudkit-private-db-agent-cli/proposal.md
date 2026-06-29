## Why

AniShelf's CloudKit private database is useful for local diagnostics, export, and agent-assisted inspection, but direct CloudKit HTTP access is brittle because private database auth is user-scoped and the web auth token rolls after requests. A dedicated CLI can make that access ergonomic and safe by hiding auth/token mechanics behind stable, read-only, AniShelf-aware commands.

## What Changes

- Add a modern Python CLI named `ani` for CloudKit private database access, defaulting to AniShelf's `iCloud.com.samuelhe.MyAnimeList` container, the production CloudKit environment, and read-only behavior.
- Implement browser-based user login, secure token storage, `whoami`, and a serialized HTTP executor that owns rolling `ckWebAuthToken` replacement.
- Resolve CloudKit API tokens through an internal provider abstraction, implementing only developer-configured env/Keychain providers in this change while leaving public embedded-token distribution to a separate change.
- Expose generic read commands for zones, record lookup/query, and zone-change fetches without exposing a raw CloudKit HTTP escape hatch in normal use.
- Expose AniShelf domain commands for listing, finding, hydrating metadata for, and exporting the user's library from the current lean CloudKit sync schema.
- Make batch operations feel native by accepting multiple positional values, stdin, input files, JSONL streams, chunked execution, deterministic output ordering, and partial-failure reporting.
- Add a schema-drift check that reads AniShelf's `DataProvider/Sources/LibrarySync` source as the schema authority and fails loudly when the CLI decoder is stale.
- Keep write/delete support out of this first change; future mutations must be proposed separately with dry-run, confirmation, conflict handling, and audit logging.

## Capabilities

### New Capabilities

- `cloudkit-readonly-cli`: A read-only CLI for authenticating to a user-authorized CloudKit private database, serializing token-consuming requests, querying AniShelf's CloudKit sync records, running ergonomic batch reads, hydrating TMDb metadata, exporting decoded library data, and detecting schema drift against AniShelf source.

### Modified Capabilities

- None.

## Impact

- New `ani` CLI package, commands, HTTP executor, auth callback handling, secure credential storage, locking, TMDb metadata adapter, and tests in this repository.
- New AniShelf CloudKit schema adapter based on verified source constants from `~/projects/AniShelf/DataProvider/Sources/LibrarySync`.
- New developer configuration for CloudKit API token providers, environment selection, and callback URL setup.
- No AniShelf app code changes are required for the first read-only version, but future AniShelf sync schema changes must be paired with a CLI schema snapshot/update or an intentional compatibility decision.
