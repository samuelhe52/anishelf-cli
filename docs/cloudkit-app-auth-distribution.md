# CloudKit App Auth Distribution

This document is developer-facing. It captures reference notes for distributing
and rotating the CLI's CloudKit app auth material without adding a relay service.

## Intent

Normal users should be able to run the read-only CLI without Apple Developer
team access and without sending their private CloudKit traffic through a relay.
The CLI can therefore carry dedicated public app auth material for the AniShelf
CloudKit container.

This material is public in practice. Any local reconstruction or obfuscation is
only extraction friction, not a security boundary. The user-private
`ckWebAuthToken` remains the sensitive auth state and must stay in secure local
storage.

## Design Rules

- Use a dedicated CLI CloudKit app token and do not reuse it for other clients or
  services.
- Resolve a developer process-env override first for local testing and emergency
  diagnosis.
- Use embedded public app auth when no override is present.
- Return non-secret metadata with resolved auth: source and version.
- Never print the reconstructed token value in status output, login output,
  logs, errors, tests, docs, or diagnostics.
- Keep CloudKit requests direct from the CLI to Apple.
- Do not build a remote decrypt/unwrap service.
- Do not proxy user private database requests through a VPS.
- Keep CloudKit write/delete commands out of scope for this read-only CLI.

## Embedded Representation

The embedded value should avoid appearing as one obvious plaintext literal in
source or tests. Split fragments or a small generated reversible transform are
enough. Do not describe this as cryptographic protection.

Tests should prove:

- environment override wins over embedded auth;
- override version metadata is surfaced when present;
- embedded auth is used when no override exists;
- missing embedded auth fails with a clear build/configuration error;
- status and login output never include the token value;
- callback URLs and user web auth tokens remain redacted.

## Rotation Manifest

A public machine-readable manifest can be used as an update signal. It must
contain only public metadata:

- current token version;
- minimum supported token version;
- minimum CLI version;
- release URL;
- issue URL;
- release timestamp;
- notes.

The manifest must not contain token values or reversible token material.

The CLI should not depend on the manifest for normal reads. It should fetch the
manifest only after a CloudKit failure classified as likely app-token
invalidation, or when a developer runs an explicit diagnostics command.

If the manifest is unavailable, diagnostics should degrade to local installed
version information and mitigation steps rather than blocking unrelated
commands.

## Invalidation Diagnostics

Before recommending an update, classify the CloudKit failure. Do not give token
rotation advice for ordinary user-auth failures, expired zone sync tokens,
throttling, quota, transient network errors, or schema issues.

When a request fails with likely app-token invalidation:

- If the manifest reports a newer token version than the installed build,
  report that the installed CLI is obsolete and direct the user to the minimum
  CLI version or release URL.
- If the manifest does not report a newer version, report that rotation is not
  known to be the cause and suggest retrying, re-login, checking network status,
  and filing an issue with redacted diagnostics.

## Rotation Process

Rotating the embedded token requires a new CLI release.

1. Create a replacement dedicated CloudKit app token.
2. Update the embedded representation.
3. Increment the embedded token version.
4. Run tests proving redaction and precedence.
5. Smoke test a build against production CloudKit.
6. Publish a CLI release.
7. Publish or update the public manifest.
8. Append a human-readable rotation log entry if a rotation log exists.
9. Revoke the old token after the replacement release path is available.

Old clients will fail once the old token is revoked. Their diagnostic path
should check the manifest when possible and report whether an update is required.

## Trade-Offs

- Extracted token reuse is possible. Mitigation is a dedicated token, read-only
  CLI surface, clear revocation path, and no CloudKit write/delete commands.
- Rotation is release-bound. This avoids steady-state server dependency at the
  cost of old clients needing updates after revocation.
- GitHub or release hosting may be unavailable during diagnostics. Normal reads
  should not depend on that availability.
- Obfuscation can create false confidence. Developer docs and code comments
  should be explicit that the token is public in practice.
