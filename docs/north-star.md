# AniShelf CLI North Star

This document captures the durable intent for `ani`, the AniShelf CLI. It is not
a stage implementation spec. Stage specs should stay small and lock only the
behavior that is knowable for that stage.

## North Star Capability

`ani` should be a read-only, local-first CLI for inspecting and exporting a
user-authorized AniShelf CloudKit private database in a way that is safe for a
human operator and predictable for an agent.

The CLI should hide CloudKit authentication mechanics, avoid accidental writes,
emit stable machine-readable output, and present AniShelf library data through
domain concepts rather than raw CloudKit records whenever practical.

## Valuable Demands

- The CLI must default to read-only behavior. Write, delete, or mutation support
  requires a separate future decision with its own safety model.
- The CLI must target AniShelf's CloudKit container and private database as the
  primary use case.
- A user should not have to manually sequence CloudKit web auth tokens or reason
  about rolling token replacement.
- Secrets must not be printed in normal output, structured output, logs, errors,
  profile status, or debug diagnostics.
- Structured output must be suitable for agents: deterministic, parseable, and
  free of progress text on stdout.
- Human output should be concise and terminal-friendly.
- Batch workflows should be natural where they materially reduce repetitive CLI
  calls, but the exact grammar should be decided stage by stage.
- AniShelf domain commands should decode the app's lean sync schema instead of
  forcing users or agents to inspect raw CloudKit payloads.
- Full-library operations should avoid unnecessary CloudKit requests and should
  become incrementally efficient when the implementation has enough evidence to
  choose the right cache shape.
- Metadata enrichment is useful, but CloudKit user-state export must remain
  possible without contacting TMDb.
- Public distribution should not route private CloudKit traffic through a relay
  service unless a later stage intentionally reopens that trade-off.

## Safe-To-Lock-In Decisions

- The CLI command name is `ani`.
- The repository remains `anishelf-cli`.
- The implementation language is Python, packaged as a modern `uv` project.
- The default CloudKit container is `iCloud.com.samuelhe.MyAnimeList`.
- The default CloudKit environment is `production`.
- The default database scope is `private`.
- The CLI should use an internal CloudKit API-token provider interface rather
  than hard-coding token lookup inside command handlers.
- Developer-configured CloudKit API tokens may come from process environment or
  secure local storage.
- User-scoped `ckWebAuthToken` values should be stored in secure local storage
  when available.
- The first-class production login flow may require browser login followed by
  manual paste of an HTTPS callback URL.
- Local profile configuration should describe the effective container,
  environment, database, callback strategy, token-source choices, and AniShelf
  source path without exposing secret values.
- The CLI should use stable exit behavior and stable error codes for automated
  callers, but the exact code table should be defined in the relevant stage spec.

## Planning Guidance

Planning notes should stay small and describe behavior that is ready to commit
now, plus the local tests and manual checks needed for that slice.

Good stage boundaries:

- Profile configuration and secret storage.
- CloudKit login/logout and current-user read.
- Serialized CloudKit executor for authenticated reads.
- Generic read-only CloudKit lookup/query/change commands.
- AniShelf schema snapshot and minimal domain decoding.
- Library list/get/export behavior.
- Local cache behavior, once real command usage proves the needed shape.
- TMDb metadata hydration, once output needs are settled.
- Public API-token distribution and rotation diagnostics.

Avoid putting the following in an early stage spec unless the implementation has
already produced enough evidence:

- Exact SQLite schema.
- Full batch grammar.
- Complete JSON/JSONL schemas for commands not being implemented in the stage.
- TMDb summary/details/full field lists.
- Token rotation release mechanics.
- Broad Swift source parsing rules.
- Performance claims that have not been measured.

## Current Open Questions

- What is the smallest useful first vertical slice after profile and login?
- Which CloudKit successor-token response fields are verified by live requests?
- What stable JSON envelope should the first implemented command use?
- Should schema compatibility start from a committed snapshot before attempting
  source parsing?
- Which library operations actually need a persistent cache, and which can stay
  direct-read for longer?
