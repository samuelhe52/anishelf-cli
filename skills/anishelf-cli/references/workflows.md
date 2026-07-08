# AniShelf CLI Long-Term Workflows

Use this file for recurring agent operation after `ani` is installed.

## Cache-First Read Loop

1. Run `ani lib status --json`.
2. If `summary.initialized` is false, ask before running `ani lib init --json`.
3. Prefer local reads when the cache is initialized.
4. Use `--sync` on read commands only when the user asks for fresh CloudKit
   state for that read.
5. Use `ani lib sync --json` for an explicit full cache refresh.

Common reads:

```bash
ani lib list --json
ani lib search "Frieren" --limit 10 --json
ani lib get movie:55 --json
ani lib export --json
```

## Metadata Decisions

- Use `--metadata none` for pure CloudKit user state: ids, status, dates,
  score, favorite, notes, and episode progress.
- Use `--metadata summary` for display titles, overviews, runtime/count facts,
  and season parent-series labels.
- Use `--metadata details` or `full` only when the user needs richer TMDb data.
- Run `ani lib refresh-meta --json` when metadata readiness is incomplete or
  stale.
- Use `ani tmdb search ... --json` for global TMDb discovery, not library
  search.

## Auth And Secrets

- Use `ani auth status --json` to inspect auth state without exposing tokens.
- Use `ani auth login` only when the user is present for browser or callback
  handling.
- Never ask the user to paste CloudKit callback URLs into chat.
- Keep JSON stdout separate from stderr progress and diagnostics.

## Scheduled Operation

Use `ani lib sync --json` in macOS `launchd` plists or Linux systemd user
timers to keep auth state warm and the library cache fresh. `lib sync` already
refreshes the auth token, so a separate `ani auth refresh --json` schedule is
usually unnecessary.

Scheduled tasks run outside the desktop session, where macOS Keychain, GNOME
Keyring, or D-Bus Secret Service may be locked. If scheduled commands fail with
an auth storage error, guide the user to switch to plaintext file storage with
`ani config set-secrets-backend plaintext-file`, noting that secrets are then
stored unencrypted on disk.

## Season Display

For season entries, prefer parent series title plus season number, such as
`Cowboy Bebop (S1)`, when `metadata.parent_series_title` is available. Raw
season metadata names such as `Season 1` are often less useful.
