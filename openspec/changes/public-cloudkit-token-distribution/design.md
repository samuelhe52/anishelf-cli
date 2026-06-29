## Context

CloudKit Web Services requires a container-scoped `ckAPIToken` for API-token access, and private database calls also require a user-scoped `ckWebAuthToken`. Normal AniShelf users cannot create an API token for `iCloud.com.samuelhe.MyAnimeList` because that token belongs to the developer team that owns the container.

The main CLI change now defines a CloudKit API-token provider interface. This change adds a public provider that intentionally distributes a dedicated CLI API token to avoid routing user private database traffic through a VPS relay. The token is public in practice: obfuscation only prevents casual string extraction and must not be treated as a security boundary.

## Goals / Non-Goals

**Goals:**

- Let normal users run the read-only CLI without Apple Developer team access.
- Avoid a relay service for CloudKit request transport.
- Provide deterministic token-version diagnostics when a shipped token is revoked or rotated.
- Keep rotation operationally tied to normal CLI releases.
- Avoid printing or logging the token value even though it is distributed.

**Non-Goals:**

- Do not promise that the embedded API token is secret.
- Do not build a remote decrypt/unwrap service.
- Do not proxy CloudKit private database requests through a VPS.
- Do not add CloudKit write/delete commands.
- Do not use the GitHub manifest to deliver secret material.

## Decisions

### Treat the API Token as Public but Dedicated

Create a dedicated CloudKit API token for the CLI and never reuse it for other clients or services. The provider returns the token value, source label `embedded-public`, and a monotonically increasing token version.

Alternatives considered:

- User-provided API tokens: blocked for normal users because they do not own the CloudKit container.
- VPS relay: keeps the API token private but puts user private data and rotating web auth tokens through the server.
- Remote token fetch: improves rotation but makes GitHub/VPS availability part of startup and still exposes the fetched token to clients.

### Use GitHub as a Public Rotation Signal, Not a Secret Store

Maintain a machine-readable `token-manifest.json` in the repository or release assets and a human-readable `TOKEN_ROTATIONS.md` log. The manifest contains only public metadata: current token version, minimum supported token version, minimum CLI version, release URL, issue URL, timestamp, and notes. It never contains a token value or reversible token material.

The CLI checks the manifest only after CloudKit returns an error classified as likely API-token invalidation, or when the user explicitly runs a token/status diagnostic command. It should not depend on GitHub for normal CloudKit reads.

### Rotate by Release

Rotating the embedded token requires a new CLI release. Old clients will fail after the old token is revoked; they then fetch the manifest and report either "update required" when the manifest version is newer than the embedded token version, or "not known to be rotated" with mitigation steps and an issue URL when the manifest does not indicate a newer token.

This favors no steady-state server dependency over seamless background rotation.

### Keep Obfuscation Modest

Store the token in a non-obvious, reconstructed form such as split fragments plus a simple reversible transform generated at build time. Tests should prove the reconstructed token matches a fixture pattern without printing the real token. The source code must document that this is extraction friction, not cryptographic protection.

## Risks / Trade-offs

- Extracted token can be reused outside the CLI -> Use a dedicated token, keep the CLI read-only, make revocation easy, and keep CloudKit writes out of scope.
- Old clients break after rotation -> Manifest-based diagnostics direct users to the release that contains the new token.
- GitHub manifest is unavailable -> Normal reads continue until a token failure occurs; token-failure diagnostics degrade to local mitigation text and issue URL.
- Error classification can be wrong -> Fetch the manifest only for likely token failures and phrase fallback guidance conservatively.
- Obfuscation can create false confidence -> Documentation must state that the token is public in practice.

## Migration Plan

The first public-token release switches the default CloudKit API-token provider from developer-configured env/Keychain to embedded public token for normal profiles, while preserving env/Keychain overrides for development and emergency testing.

Rollback is revoking the public token, publishing a manifest with the minimum supported CLI version, and directing users to a release that restores developer-configured token behavior or carries a replacement token.

## Open Questions

- Where should the public manifest live: repository root, GitHub Pages, release asset, or all of the above?
- Should the manifest be signed with an offline key, or is GitHub repository trust enough for the first release?
- Should packaged builds include a dormant fallback token version, or is single-token release rotation simpler and safer?
