## 1. Provider Integration

- [ ] 1.1 Implement an embedded public CloudKit API-token provider that satisfies the main CLI token provider interface.
- [ ] 1.2 Add token metadata fields for source label, embedded token version, and redacted display output.
- [ ] 1.3 Preserve developer env/Keychain override behavior for local testing and emergency diagnosis.
- [ ] 1.4 Add tests proving profile/status output never prints the reconstructed token.

## 2. Token Obfuscation

- [ ] 2.1 Add a generated or hand-maintained obfuscated token representation that avoids storing the full token as one obvious plaintext literal.
- [ ] 2.2 Add reconstruction tests using fixtures or fake tokens without committing a real test token.
- [ ] 2.3 Document in code and docs that obfuscation is extraction friction, not cryptographic security.

## 3. Public Manifest and Rotation Log

- [ ] 3.1 Define and validate `token-manifest.json` with current token version, minimum supported token version, minimum CLI version, release URL, issue URL, timestamp, and notes.
- [ ] 3.2 Add `TOKEN_ROTATIONS.md` with human-readable token version history and rotation procedure.
- [ ] 3.3 Implement manifest fetching with timeout, JSON validation, and tolerant failure behavior.
- [ ] 3.4 Add tests for current, outdated, malformed, unavailable, and future-version manifests.

## 4. Error Classification and Update Guidance

- [ ] 4.1 Extend CloudKit error classification with a likely API-token invalidation category.
- [ ] 4.2 Check the public manifest only for likely token invalidation failures or explicit diagnostics commands.
- [ ] 4.3 Report update-required guidance when the manifest token version is newer than the embedded version.
- [ ] 4.4 Report mitigation-and-issue guidance when no newer manifest version is known.
- [ ] 4.5 Add tests proving auth failures, expired zone sync tokens, throttling, and transient network errors do not trigger token-rotation update advice.

## 5. Release Verification

- [ ] 5.1 Document the public-token rotation checklist.
- [ ] 5.2 Document how to revoke an old CloudKit API token and publish the replacement release.
- [ ] 5.3 Manually smoke test a build using the embedded public provider against production CloudKit.
- [ ] 5.4 Manually simulate an outdated token version and verify the CLI recommends the new release from the manifest.
