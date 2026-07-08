---
name: anishelf-cli
description: Operate the read-only AniShelf CLI (`ani`) for user-authorized AniShelf library inspection, cache-first reads, search, metadata refresh, TMDb anime lookup, JSON export, and CloudKit auth status checks. Use when a user asks an agent to inspect or search their AniShelf library, run `ani auth`, `ani config`, `ani lib`, or `ani tmdb` commands, produce jq-friendly JSON from AniShelf data, troubleshoot local cache/auth state without exposing secrets, or install/bootstrap anishelf-cli when `ani` is unavailable.
---

# AniShelf CLI

## Overview

Use this skill to drive `ani` for long-running, cache-first AniShelf library
workflows. The CLI is read-only: it authenticates to the user's private
AniShelf CloudKit library, maintains a rebuildable local SQLite cache, enriches
output with optional TMDb anime metadata, and emits scriptable JSON.

## Operating Rules

- Never print CloudKit web auth tokens, raw callback URLs containing tokens,
  stored credential values, or TMDb API keys.
- Prefer `ani ... --json` for agent workflows and parse stdout; progress,
  warnings, and diagnostics belong on stderr.
- Treat exported library JSON as private user data.
- Do not attempt CloudKit writes. This CLI is for inspection, cache refresh,
  metadata hydration, search, and export only.
- Use separated option values for metadata, such as `--metadata summary`; do
  not use bare `--metadata` or `--metadata=summary`.

## Steady-State Workflow

1. Run `ani lib status --json` to determine cache readiness and metadata state.
2. Prefer local reads from the initialized cache.
3. Use `--sync` only when the user wants a fresh CloudKit pull before a read.
4. Run `ani lib refresh-meta --json` only when metadata is missing or stale.
5. Inspect JSON summaries and item-level errors; do not rely only on exit code.

```bash
ani lib status --json
ani lib list --json
ani lib search "Frieren" --limit 10 --json
ani lib get movie:55 --json
ani tmdb search "Frieren" --limit 5 --json
```

Collection commands use `.entries[]`. `lib get` uses `.items[]` because batch
lookups can mix found entries and item-level errors. Inspect summary fields and
item errors instead of relying only on the process exit code for batch reads.

## References

- For questions about the user's specific library, prefer reading
  `references/workflows.md` and `references/entries.md` before choosing
  commands or interpreting results.
- Read `references/workflows.md` for cache freshness, sync, metadata, auth, and
  scheduled-use decisions.
- Read `references/commands.md` for command recipes, JSON envelopes, and
  troubleshooting.
- Read `references/entries.md` for library entry fields and season display
  rules.
- Read `references/install.md` only when `ani` is unavailable or first-run setup
  is needed.
