---
name: anishelf-cli
description: Install, verify, and operate the read-only AniShelf CLI (`ani`) for user-authorized AniShelf library inspection, search, metadata refresh, TMDb anime lookup, JSON export, and CloudKit auth status checks. Use when a user asks an agent to install anishelf-cli from GitHub, bootstrap `ani`, inspect or search their AniShelf library, run `ani auth`, `ani config`, `ani lib`, or `ani tmdb` commands, produce jq-friendly JSON from AniShelf data, or troubleshoot safe local cache/auth setup without exposing secrets.
---

# AniShelf CLI

## Overview

Use this skill to install and drive `anishelf-cli`, whose console command is
`ani`. The CLI is read-only: it authenticates to the user's private AniShelf
CloudKit library, builds a rebuildable local SQLite cache, enriches output with
optional TMDb anime metadata, and emits scriptable JSON.

## Ground Rules

- Never print CloudKit web auth tokens, raw callback URLs containing tokens,
  stored credential values, or TMDb API keys.
- Prefer `ani ... --json` for agent workflows and parse stdout; progress,
  warnings, and diagnostics belong on stderr.
- Treat exported library JSON as private user data.
- Do not attempt CloudKit writes. This CLI is for inspection, cache refresh,
  metadata hydration, search, and export only.
- Use separated option values for metadata, such as `--metadata summary`; do
  not use bare `--metadata` or `--metadata=summary`.

## Install Or Verify

First check whether `ani` is already available:

```bash
ani --help
```

If it is missing, use the bundled installer:

```bash
python3 scripts/bootstrap_anishelf_cli.py
```

The installer requires `uv` and Python 3.13. It installs the beta Git release as
a `uv` tool, then verifies `ani --help`. If a user wants a specific fork or
branch, pass `--repo-url` and `--ref`.

## First Run

Use this order for a new machine or account:

```bash
ani auth status --json
ani auth login
ani config set-tmdb-api-key
ani lib init --json
ani lib status --json
```

`ani auth login` may require the user to complete a browser login or paste a
callback URL into the local CLI prompt. Do not ask the user to paste that URL
into the agent chat, because it can contain auth material.

When guiding the CloudKit web login, ask the user to sign in manually with their
account and password instead of using passkeys when possible, and to check
`Keep me signed in` so the returned auth token expires less quickly.

TMDb is optional for basic CloudKit library export, but required for metadata
enrichment, `lib search`, and global `tmdb search`.

## Common Agent Workflow

1. Run `ani lib status --json` to determine cache readiness.
2. If uninitialized, ask before running `ani lib init --json`; it contacts
   CloudKit and can hydrate TMDb metadata.
3. Use `--sync` only when the user wants a fresh CloudKit pull before a read.
4. Use JSON for downstream work:

```bash
ani lib list --json
ani lib search "Frieren" --limit 10 --json
ani lib get movie:55 --json
ani tmdb search "Frieren" --limit 5 --json
```

Collection commands use `.entries[]`. `lib get` uses `.items[]` because batch
lookups can mix found entries and item-level errors. Inspect summary fields and
item errors instead of relying only on the process exit code for batch reads.

## Scheduled Automation

Use `ani auth refresh --json` and `ani lib sync --json` as entries in macOS
`launchd` plists or Linux systemd user timers to keep auth state warm or the
library cache fresh. `lib sync` already refreshes the auth token, so a sync
schedule usually eliminates the need for a separate auth refresh schedule.

**Secret storage caveat:** Scheduled tasks run outside your desktop session,
where the system credential store (macOS Keychain, GNOME Keyring, D-Bus Secret
Service) may be locked. If `ani` commands fail with an auth storage error,
guide the user to switch to plaintext file storage with
`ani config set-secrets-backend plaintext-file`, noting the security caveat
that secrets are stored unencrypted on disk.

## References

Read `references/commands.md` when you need command recipes, JSON shapes,
metadata rules, cache behavior, troubleshooting steps, or examples for a user
request.
