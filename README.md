# anishelf-cli

`anishelf-cli` is a read-only command-line companion for AniShelf. It signs in to
your AniShelf CloudKit library, builds a local SQLite snapshot, and lets you
inspect, search, refresh metadata for, and export that library from the
terminal.

The project is currently in beta testing. The main user workflows are in place,
but the PyPI package is not published yet. Install the beta from the tagged Git
URL.

## What It Does

- Reads your private AniShelf CloudKit library without writing changes back.
- Keeps a rebuildable local cache so normal library reads are fast and local.
- Searches the initialized library across titles, metadata, notes, dates, and
  parent-series context.
- Enriches library output with TMDb anime metadata when a TMDb API key is
  configured.
- Exports library data as deterministic JSON for private backup, scripts, or
  analysis.

`ani` is an inspection tool, not a library editor. It does not add, update, or
delete AniShelf entries in CloudKit.

## Requirements

- Python 3.13 or newer
- `uv`
- Access to the AniShelf CloudKit account you want to inspect
- A TMDb API key, if you want metadata enrichment and TMDb search

## Install For Beta Use

Install the tagged GitHub release as a `uv` tool:

```bash
uv tool install --python 3.13 git+https://github.com/samuelhe52/anishelf-cli.git@v0.1.0
```

This installs the `ani` command into your shell. If your shell cannot find it,
add the `uv` tool binary directory to `PATH`:

```bash
uv tool update-shell
```

Then open a new shell and verify the install:

```bash
ani --help
```

The Git URL install path is the supported beta install path until the package is
published to PyPI.

## First Run

```bash
ani auth login
ani config set-tmdb-api-key
ani lib init
ani lib list
```

`ani auth login` opens a CloudKit web login flow and stores the resulting user
auth token in the OS credential store. `ani config set-tmdb-api-key` stores your
TMDb key in the same kind of secure local storage.

`ani lib init` is the required first library step. It downloads your AniShelf
library into the local SQLite cache and hydrates TMDb metadata when a TMDb key is
available. After that, read commands use the local cache by default.

## Everyday Workflow

Check login and cache state:

```bash
ani auth status
ani lib status
```

Refresh the initialized cache from CloudKit:

```bash
ani lib sync
```

List and filter entries:

```bash
ani lib list
ani lib list --watch-status watching
ani lib list --favorite --sort updated --limit 20
ani lib list --style list --fields title,id,status,score
```

Search your cached library:

```bash
ani lib search "Alien" --limit 5
```

Read specific entries by AniShelf id:

```bash
ani lib get movie:55
ani lib get series:1399
ani lib get season:1399:1:3624
```

Search TMDb anime directly:

```bash
ani tmdb search "Frieren" --limit 5
ani tmdb search --year 2024 --type series
```

Export your cached library:

```bash
ani lib export --json > anishelf-library.json
```

## Cache And Sync

The local cache is explicit:

- `ani lib init` creates or rebuilds it from CloudKit.
- `ani lib sync` refreshes an initialized cache.
- Read commands accept `--sync` when you want a one-shot refresh before reading.
- `ani lib clear-cache` removes local cache files after confirmation.
- `ani auth logout` removes the stored CloudKit login and clears local cache
  files.

Hidden AniShelf entries are excluded from collection output by default. Use
`--show-hidden` for a single command, or persist that preference with:

```bash
ani config set-defaults --show-hidden
```

## Metadata

Library read commands include cached TMDb summary metadata by default. Use
`--metadata` with a separated value to change the output projection:

```bash
ani lib list --metadata none
ani lib get movie:55 --metadata details
ani lib get movie:55 --metadata full
```

Supported metadata levels are `none`, `summary`, `details`, and `full`.
Hydration depth is separate from output depth: `lib init`, `lib sync`,
`lib refresh-meta`, and `lib get --live-meta` use the configured hydration depth
when deciding how much TMDb data to cache.

Set the default hydration depth:

```bash
ani config set-defaults --hydration-depth full
ani lib refresh-meta
```

TMDb metadata is cached in the configured preferred language, `en` by default.
Supported public language codes are `en`, `ja`, and `zh`.

```bash
ani config set-defaults --tmdb-language ja
ani lib clear-cache --yes
ani lib init
```

For one command only, pass `--tmdb-language <code>`. When the override differs
from the configured default, library commands fetch that language live for the
request and do not write it back to the metadata cache.

## JSON And Scripting

Commands that support JSON accept `--json` either globally or on the command:

```bash
ani --json lib status
ani lib list --json
ani lib get movie:55 --json
```

JSON output keeps progress and diagnostics off stdout so it can be piped to
tools such as `jq`:

```bash
ani lib status --json | jq '.summary'
ani lib list --json | jq '.entries[] | {id, watch_status}'
ani lib get movie:55 --json | jq '.items[].entry'
ani lib search "Alien" --limit 5 --json | jq '.entries[].id'
```

Collection commands emit entries under `.entries[]`. `lib get` emits `.items[]`
because batch lookups can contain a mix of found entries and per-item errors.

## Configuration

Show effective configuration and local paths:

```bash
ani config show
```

Set common defaults:

```bash
ani config set-defaults --style list
ani config set-defaults --show-hidden
ani config set-defaults --tmdb-language zh
ani config set-defaults --hydration-depth details
```

Use `ani config set-tmdb-api-key` to update the stored TMDb API key.

## Development

For local development, clone the repository and install dependencies with `uv`:

```bash
git clone https://github.com/samuelhe52/anishelf-cli.git
cd anishelf-cli
uv sync
uv run ani --help
```

Common project workflows use the Makefile:

```bash
make sync
make check
```

`make check` runs formatting checks, linting, type checking, and tests.
