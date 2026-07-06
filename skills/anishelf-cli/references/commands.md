# AniShelf CLI Command Reference

## Install

Use the bundled bootstrap helper from the skill directory:

```bash
python3 scripts/bootstrap_anishelf_cli.py
```

Equivalent manual beta install:

```bash
uv tool install --python 3.13 git+https://github.com/samuelhe52/anishelf-cli.git@v0.1.1
uv tool update-shell
ani --help
```

For local development from a clone:

```bash
uv sync
uv run ani --help
make check
```

## Auth And Configuration

Check auth without exposing tokens:

```bash
ani auth status --json
```

Sign in:

```bash
ani auth login
```

If login uses a manual callback, let the user paste the callback URL directly
into the CLI prompt. Do not echo or store callback URLs in chat, logs, or docs.
During the CloudKit web login, prefer manual account/password sign-in instead of
passkeys when possible, and check `Keep me signed in` for a longer-lived auth
token.

Configure TMDb when metadata enrichment or TMDb search is needed:

```bash
ani config set-tmdb-api-key
ani config show --json
```

Set user defaults:

```bash
ani config set-defaults --style list
ani config set-defaults --show-hidden
ani config set-defaults --tmdb-language ja
ani config set-defaults --hydration-depth full
```

Supported public TMDb language codes are `en`, `ja`, and `zh`.

## Cache Lifecycle

Initialize the local cache before library reads:

```bash
ani lib init --json
```

Check cache state:

```bash
ani lib status --json
```

Refresh an initialized cache:

```bash
ani lib sync --json
```

Clear local cache files only when the user asks:

```bash
ani lib clear-cache
ani lib clear-cache --yes
```

`ani auth logout` removes the stored CloudKit login and clears local library
cache files.

## Library Reads

List entries:

```bash
ani lib list --json
ani lib list --watch-status watching --limit 20 --json
ani lib list --favorite --sort updated --style list
ani lib list --fields title,id,status,score
```

Search the initialized library:

```bash
ani lib search "Frieren" --limit 10 --json
```

`lib search` depends on cached TMDb metadata because it searches localized
titles, translations, parent-series metadata, overviews, notes, and dates. If
metadata is incomplete, run:

```bash
ani lib refresh-meta --json
```

Read entries by AniShelf id:

```bash
ani lib get movie:55 --json
ani lib get series:1399 season:1399:1:3624 --json
```

AniShelf ids use these stable forms:

- `movie:<tmdbID>`
- `series:<tmdbID>`
- `season:<parentSeriesID>:<seasonNumber>:<tmdbID>`

Use `--sync` on a read command only when the user wants a one-shot CloudKit
refresh before reading:

```bash
ani lib get movie:55 --sync --json
ani lib list --sync --json
```

Use `--live-meta` for targeted fresh TMDb metadata on `lib get`:

```bash
ani lib get movie:55 --live-meta --metadata details --json
```

## Metadata Output

Library read commands include cached summary metadata by default. Select output
projection with separated values:

```bash
ani lib list --metadata none --json
ani lib get movie:55 --metadata summary --json
ani lib get movie:55 --metadata details --json
ani lib get movie:55 --metadata full --json
```

`--metadata none` suppresses metadata attachment. `summary`, `details`, and
`full` are output projections; hydration depth is configured separately with
`ani config set-defaults --hydration-depth details|full`.

Per-command language overrides fetch metadata for that request without changing
the configured cache language:

```bash
ani lib get movie:55 --tmdb-language zh --json
```

## TMDb Anime Search

Search global TMDb anime results:

```bash
ani tmdb search "Frieren" --limit 5 --json
ani tmdb search --title "Frieren" --type series --year 2023 --json
```

Discover popular anime by omitting the title:

```bash
ani tmdb search --limit 10 --json
```

`tmdb search` is anime-only. Use it for global discovery; use `lib search` for
the user's initialized AniShelf library.

## JSON Shapes

Prefer JSON for agent work:

```bash
ani lib status --json | jq '.summary'
ani lib list --json | jq '.entries[] | {id, watch_status}'
ani lib search "Alien" --limit 5 --json | jq '.entries[].id'
ani lib get movie:55 --json | jq '.items[].entry'
```

Collection commands emit `entries`. `lib get` emits `items` so each requested id
can contain either `entry` or an item-level error. Partial batch failures can
remain in the JSON payload, so inspect `summary.errors` and each item result.

## Troubleshooting

- If `ani` is not found after `uv tool install`, run `uv tool update-shell`,
  open a new shell, and try `ani --help`.
- If library commands report an uninitialized cache, run `ani lib init --json`.
- If `lib search` reports missing metadata, configure a TMDb key and run
  `ani lib refresh-meta --json`.
- If auth fails, run `ani auth status --json`, then `ani auth login` when the
  user is present for browser or callback handling.
- Use `-v` only for redacted diagnostics. Keep diagnostics on stderr separate
  from JSON stdout.
