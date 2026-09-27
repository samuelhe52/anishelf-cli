# AniShelf CLI Command Reference

For install, bootstrap, or first-run setup, read `references/install.md`.

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
ani lib list -w watching -w planToWatch --type series --json
ani lib list --sort saved --reverse --limit 5 --json
ani lib list --favorite --sort updated --style list
ani lib list --fields title,id,status,score
```

Repeat `--watch-status` or `--type` to match any of several values; different
filters combine with AND. `--reverse` flips the sort direction while entries
without a value for the sort key (unscored, undated, or without metadata) stay
last, so `--sort score --reverse --limit 5` lists the five lowest scores. JSON `filters.watch_status` and
`filters.entry_type` are lists, or `null` when not filtered.

Search the initialized library:

```bash
ani lib search "Frieren" --limit 10 --json
```

`lib search` depends on cached TMDb metadata because it searches localized
titles, translations, parent-series metadata, overviews, notes, and dates. It
fails (exit 2) only when no entry has cached metadata. When some entries lack
metadata it still succeeds, warns on stderr, and sets
`summary.metadata_missing` to the number of affected entries; treat a nonzero
value as possibly incomplete results. `lib sync` retries missing metadata; to
refetch everything, run:

```bash
ani lib refresh-meta --json
```

Export the library (private user data; only write files the user asked for):

```bash
ani lib export --json
ani lib export --format jsonl --metadata none
ani --json lib export -o library.csv
```

`--format jsonl` streams one entry object per line; `--format csv` streams a
flat table. With `-o`, the format comes from `--format` or the file extension,
the file is created with owner-only permissions, and `--json` prints
`{path, format, entries, cache}` instead of the data.

Read entries by AniShelf id:

```bash
ani lib get movie:55 --json
ani lib get series:1399 season:1399:1:3624 --json
ani lib list -w watching --json | jq -r '.entries[].id' | ani lib get - --json
ani lib get movie:55 bogus --strict --json
```

`-` reads whitespace-separated ids from stdin in place of the `-` argument
(`#` lines ignored). Without `--strict`, mixed batches exit 0 when at least one
id is found, so inspect `summary.errors`; with `--strict`, any invalid or
missing id exits 1 while still printing the full envelope.

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
For the entry field contract, read `references/entries.md`.

## Troubleshooting

- If `ani` is not found after `uv tool install`, run `uv tool update-shell`,
  open a new shell, and try `ani --help`.
- If library commands report an uninitialized cache, run `ani lib init --json`.
- If `lib search` fails for missing metadata, configure a TMDb key and run
  `ani lib refresh-meta --json`. If it succeeds with a nonzero
  `summary.metadata_missing`, run `ani lib sync --json` to retry those entries.
- If auth fails, run `ani auth status --json`, then `ani auth login` when the
  user is present for browser or callback handling.
- Use `-v` only for redacted diagnostics. Keep diagnostics on stderr separate
  from JSON stdout.
