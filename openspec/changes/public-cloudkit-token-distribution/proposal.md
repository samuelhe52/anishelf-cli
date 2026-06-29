## Why

The read-only AniShelf CLI should be usable by normal users without requiring Apple Developer team access or a relay service that proxies their private CloudKit traffic. Direct distribution of a dedicated public CloudKit API token is acceptable only if the CLI has a clear rotation story, update diagnostics, and modest extraction friction.

## What Changes

- Add a public-token provider for the existing CloudKit API-token provider interface.
- Embed a dedicated CLI `ckAPIToken` in an obfuscated form with an associated token version.
- Add a GitHub-hosted machine-readable token manifest and human token rotation log.
- Classify likely API-token invalidation failures and check the manifest before telling users to update.
- Keep CloudKit requests direct from the CLI to Apple; do not add a VPS relay or remote token unwrap service.
- Keep token obfuscation as friction only, not as a security guarantee.

## Capabilities

### New Capabilities

- `public-cloudkit-token-distribution`: Public distribution, rotation diagnostics, and release process for the CLI CloudKit API token.

### Modified Capabilities

- None.

## Impact

- New embedded public-token provider plugged into the main CLI's API-token provider interface.
- New release artifact or repository file for `token-manifest.json`.
- New `TOKEN_ROTATIONS.md` operational log.
- New error-handling path for likely API-token invalidation.
- No server dependency, no private CloudKit traffic relay, and no change to AniShelf app code.
