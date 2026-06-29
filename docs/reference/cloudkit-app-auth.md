# CloudKit App Auth Reference

Developer reference for distributing and rotating the CLI's CloudKit app auth
material without adding a relay service.

## Intent

Normal users should be able to run the read-only CLI without Apple Developer
team access and without sending private CloudKit traffic through a relay. The
CLI can carry dedicated public app auth material for the AniShelf CloudKit
container.

This material is public in practice. Any local reconstruction or obfuscation is
extraction friction, not a security boundary. The user-private web auth token is
the sensitive auth state and must stay in secure local storage.

## Rules

- Use a dedicated CLI CloudKit app token.
- Resolve a process environment override first for local testing and emergency
  diagnostics.
- Use embedded public app auth when no override is present.
- Surface only non-secret metadata: source and version.
- Never print reconstructed app auth material in status output, login output,
  logs, errors, tests, docs, or diagnostics.
- Keep CloudKit requests direct from the CLI to Apple.
- Do not add user-facing app-token setup or storage commands.
- Do not add Keychain storage for CloudKit app auth.

## Rotation

Rotating embedded app auth requires a new CLI release:

1. Create replacement dedicated CloudKit app auth material.
2. Update the embedded representation and version.
3. Run redaction, precedence, and auth-flow tests.
4. Smoke test a build against production CloudKit.
5. Publish the CLI release.
6. Publish or update public rotation metadata if that path exists.
7. Revoke old material after the replacement release path is available.

Diagnostics may consult public metadata after a likely app-auth invalidation,
but normal reads should not depend on that metadata being reachable.
