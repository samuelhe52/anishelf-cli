## ADDED Requirements

### Requirement: Embedded public CloudKit API token provider

The system SHALL provide a public CloudKit API-token provider that reconstructs a dedicated CLI API token from an obfuscated local representation and exposes token metadata through the existing provider interface.

#### Scenario: Provider returns token metadata

- **WHEN** a CloudKit request resolves the public token provider
- **THEN** the provider returns the API token value, source label `embedded-public`, and embedded token version without printing the token

#### Scenario: Obfuscation is documented as friction

- **WHEN** maintainers inspect the provider implementation or generated documentation
- **THEN** the system states that token obfuscation is not a security boundary and that the token is public in practice

#### Scenario: Developer overrides remain available

- **WHEN** a developer configures an environment or Keychain API-token override
- **THEN** the CLI can use the override according to the main provider precedence rules without modifying the embedded public token

### Requirement: Public token rotation manifest

The system SHALL consume a public machine-readable token manifest that reports the latest token version and update guidance without containing secret token material.

#### Scenario: Manifest contains only public metadata

- **WHEN** the token manifest is published
- **THEN** it contains current token version, minimum supported token version, minimum CLI version, release URL, issue URL, release timestamp, and notes, and does not contain the API token or reversible token material

#### Scenario: Explicit token status checks manifest

- **WHEN** the user runs a token status or diagnostics command
- **THEN** the CLI fetches the manifest, compares it to the embedded token version, and reports whether the installed CLI token is current, outdated, or unknown

#### Scenario: Manifest fetch failure degrades locally

- **WHEN** the CLI cannot fetch or parse the token manifest
- **THEN** it reports the local embedded token version and provides local mitigation steps without blocking commands that do not require the manifest

### Requirement: Token invalidation diagnostics

The system SHALL distinguish likely CloudKit API-token invalidation from user authentication, network, throttling, schema, and CloudKit change-token failures before recommending an update.

#### Scenario: Newer manifest version requires update

- **WHEN** a CloudKit request fails with an error classified as likely API-token invalidation and the manifest current token version is newer than the embedded token version
- **THEN** the CLI reports that the installed CLI token is obsolete and directs the user to update to the minimum CLI version from the manifest

#### Scenario: No newer manifest version gives mitigations

- **WHEN** a CloudKit request fails with an error classified as likely API-token invalidation and the manifest does not report a newer token version
- **THEN** the CLI reports that token rotation is not known to be the cause and suggests mitigations such as retrying, re-login, checking network status, and filing an issue with redacted diagnostics

#### Scenario: Non-token failures do not trigger update advice

- **WHEN** a CloudKit request fails because of user authentication, expired zone sync token, throttling, or transient network failure
- **THEN** the CLI handles the failure through the normal error path and does not tell the user to update for token rotation

### Requirement: Token rotation release process

The system SHALL define a release-gated process for rotating the public CloudKit API token without adding a runtime relay service.

#### Scenario: Rotation requires a release

- **WHEN** maintainers rotate the public CloudKit API token
- **THEN** they update the embedded token, increment the embedded token version, publish a new CLI release, update the token manifest, and append an entry to `TOKEN_ROTATIONS.md`

#### Scenario: Old clients fail cleanly

- **WHEN** an old CLI version uses a revoked embedded API token
- **THEN** it fails without exposing tokens, checks the public manifest when possible, and reports whether the user must update

#### Scenario: Rotation log is human-readable

- **WHEN** maintainers or users inspect `TOKEN_ROTATIONS.md`
- **THEN** it lists token version history, release versions, rotation dates, reason categories, and compatibility notes without containing token values
