# AniShelf CLI North Star

This document captures durable product direction for `ani`, the AniShelf CLI. It
is not an implementation spec and should avoid freezing details that are better
decided while building.

## North Star

`ani` should be a read-only, local-first CLI for inspecting and exporting a
user-authorized AniShelf library snapshot in a way that is safe for a human
operator and predictable for an agent.

The CLI should hide CloudKit authentication mechanics, avoid accidental writes,
emit stable machine-readable output, and present AniShelf library data through
library concepts instead of raw CloudKit records.

## Durable Constraints

- Default to read-only behavior. Write, delete, or mutation support requires a
  separate future safety model.
- Target AniShelf's production private CloudKit database as the primary use
  case.
- Never print secret values, raw callback URLs containing tokens, stored auth
  tokens, or TMDb API keys.
- Keep structured output deterministic, parseable, and free of progress text on
  stdout.
- Keep human output concise and terminal-friendly.
- Keep CloudKit mechanics behind domain commands where possible.
- Make metadata enrichment optional: exporting CloudKit user state must remain
  possible without TMDb.
- Keep private CloudKit traffic direct from the CLI to Apple unless a future
  design deliberately reopens that trade-off.
- Avoid persisted local scope/profile configuration unless the product need is
  proven.

## Stable Decisions

- The CLI command name is `ani`.
- The repository remains `anishelf-cli`.
- The implementation language is Python, packaged as a modern `uv` project.
- The default CloudKit container is `iCloud.com.samuelhe.MyAnimeList`.
- The default CloudKit environment is `production`.
- The default database scope is `private`.
- The AniShelf CloudKit custom zone is `AniShelfLibrary`.
- The CLI syncs `LibraryEntry` records only.
- User-scoped CloudKit web auth tokens belong in secure local storage.
- TMDb API keys belong in secure local storage or environment variables.
- CloudKit app auth may be embedded as public app material, with environment
  overrides for development and diagnostics.
- `LibrarySettings` is not a supported or planned CLI surface.
- TMDb metadata attachment belongs on library read commands via `--metadata`,
  while explicit cache refresh stays under `library refresh-meta` rather than a
  separate top-level workflow.

## Values To Preserve

- Prefer small vertical slices over large speculative specs.
- Let command grammar and cache shape follow working implementation evidence.
- Keep low-level CloudKit diagnostic commands separate from normal library UX if
  they are added.
- Treat exports as private user data even when they contain no credentials.
