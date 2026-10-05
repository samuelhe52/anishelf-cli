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
uv tool install --python 3.13 git+https://github.com/samuelhe52/anishelf-cli.git@v0.2.0
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

## Agent Skill

This repo includes a Codex-compatible skill at `skills/anishelf-cli`.
Agents can install that skill from the GitHub repo to bootstrap `ani`, preserve
the CLI's read-only safety boundaries, and use JSON-first library workflows.

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

During the CloudKit web login, prefer signing in manually with your account and
password instead of using passkeys when possible, and check `Keep me signed in`
so the returned auth token expires less quickly.

On headless Linux, the OS credential store may exist but be locked because
there is no desktop session to unlock GNOME Keyring or Secret Service. If that
happens during `ani auth login` or `ani config set-tmdb-api-key`, `ani` fails
closed and prints the config command for switching to plaintext file storage.
This is a deliberate security downgrade, not secure storage: CloudKit auth
tokens and TMDb API keys are stored unencrypted in the AniShelf CLI data
directory, and any process or user that can read that file can use those
secrets.

You can also select the backend explicitly:

```bash
ani config set-secrets-backend plaintext-file
ani config set-secrets-backend system
```

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
ani lib list -w watching -w planToWatch --type series
ani lib list --sort saved --reverse --limit 10
ani lib list --style list --fields title,id,status,score
```

Repeat `--watch-status` or `--type` (`movie`, `series`, `season`) to match any
of several values. `--reverse` flips the sort direction; entries without a value
for the sort key (unscored, undated, or without metadata) stay last.

Summarize your library (counts by type and status, scores, finishes per year,
and top genres when details metadata is cached):

```bash
ani lib stats
ani lib stats --json | jq '.scores'
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
ani lib search "Alien" --json | jq -r '.entries[].id' | ani lib get -
```

`-` reads whitespace-separated ids from stdin (whole lines starting with `#`
are ignored); empty stdin, or a terminal instead of a pipe, exits 2. `lib get` exits 1 only when nothing is found; add `--strict` to also
fail when any requested id is invalid or missing.

Search TMDb anime directly:

```bash
ani tmdb search "Frieren" --limit 5
ani tmdb search --year 2024 --type series
```

When a local library cache exists, results are marked with what you already
saved, including hidden entries: `yes` for the movie or series itself and
`S<n>` for saved seasons (JSON: `library_ids` per result and
`summary.in_library`). The lookup is read-only and offline; the markers are
omitted when there is no initialized cache or more than one user's cache.

Export your cached library:

```bash
ani lib export -o anishelf-library.json
ani lib export -o anishelf-library.csv --metadata details
ani lib export --format jsonl | jq -r '.id'
```

`--output` (`-o`) writes the file readable only by you, choosing the format
from `--format` or the file extension (`.json`, `.jsonl`/`.ndjson`, `.csv`);
`-o -` means stdout. Without `--output`, `--format json|jsonl|csv` streams to
stdout, and `--json` prints the full JSON envelope. Exports contain private
library data, so treat them like any other personal file.

CSV has one row per entry with stable columns (title, ids, status, rewatch
tracking, score, dates, episode progress, notes, air date, genres, TMDb URL,
homepage). `genres` and `homepage` need `--metadata details` or `full`. CSV files written with `-o`
start with a UTF-8 byte order mark so Excel shows Japanese and Chinese text
correctly. Text cells that would start a spreadsheet formula get a leading `'`,
so CSV is lossy for such values; use JSON or JSONL when you need exact data.

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

## Scheduled Automation

`ani` is a one-shot CLI, not a daemon. To keep auth state warm or refresh the
local library cache automatically, schedule the existing commands with your
operating system's user scheduler.

Use these commands as the scheduled targets:

- `ani auth refresh --json` to refresh the stored CloudKit auth token (usually
  expires in 30 minutes or 2 weeks after login, depending on whether 'Keep me
  signed in' was checked).
- `ani lib sync --json` to refresh the cached library from CloudKit. Note that
  this also refreshes the auth token, so if you schedule library sync regularly,
  you usually do not need a separate auth refresh schedule.

**macOS** — use `launchd`. Save a plist to
`~/Library/LaunchAgents/com.anishelf.sync.plist` and load it with
`launchctl load`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.anishelf.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/ani</string>
        <string>lib</string>
        <string>sync</string>
        <string>--json</string>
    </array>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/anishelf-sync.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/anishelf-sync.log</string>
</dict>
</plist>
```

Replace the `ani` path with the output of `which ani` after installing. The
example above runs every 30 minutes (1800 seconds). Load with:

```bash
launchctl load ~/Library/LaunchAgents/com.anishelf.sync.plist
```

**Linux** — use a systemd user timer. Create
`~/.config/systemd/user/anishelf-sync.service`:

```ini
[Unit]
Description=Sync AniShelf library cache

[Service]
Type=oneshot
ExecStart=/path/to/ani lib sync --json
```

Then create `~/.config/systemd/user/anishelf-sync.timer`:

```ini
[Unit]
Description=Sync AniShelf library cache every 30 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
```

Enable and start the timer:

```bash
systemctl --user enable --now anishelf-sync.timer
```

### Troubleshooting Scheduled Tasks

Scheduled tasks run outside your desktop session. The OS credential store
(GNOME Keyring, D-Bus Secret Service, macOS Keychain) may not be unlocked in
that context, causing `ani` commands to fail with an authentication storage
error. If that happens, consider switching to plaintext file storage for
scheduled environments:

```bash
ani config set-secrets-backend plaintext-file
```

This stores CloudKit auth tokens and TMDb API keys unencrypted in the AniShelf
CLI data directory. Only use this on machines where you accept that any process
or user that can read that file can use those secrets.

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
tools such as `jq`. Add `-v`/`--verbose` for redacted `[debug]` diagnostics on
stderr: request timing and CloudKit request ids, auth token rolling, cache
queries, and unknown CloudKit fields during sync.

```bash
ani lib status --json | jq '.summary'
ani lib list --json | jq '.entries[] | {id, watch_status}'
ani lib get movie:55 --json | jq '.items[].entry'
ani lib search "Alien" --limit 5 --json | jq '.entries[].id'
```

Collection commands emit entries under `.entries[]`. `lib get` emits `.items[]`
because batch lookups can contain a mix of found entries and per-item errors.

Entry JSON and JSON/JSONL/CSV exports include `is_rewatching` and
`rewatch_count`, using the same names as AniShelf's export. The boolean marks an
active rewatch and is true only while `watch_status` is `watching`; the count
records completed rewatches and is never negative. Entries saved before
rewatch tracking default to `false` and `0`. Upgrading from an older cache
schema resets the local cache; run `ani lib init` to rebuild it from CloudKit.

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

Use `ani config set-secrets-backend plaintext-file` only on machines where you
accept unencrypted local secret storage. `ani config show` reports the active
secret backend and the plaintext file path when plaintext storage is enabled.

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
