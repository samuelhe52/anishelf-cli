# UX Evaluation: Current CLI Review

This note started as a 2026-06-30 from-scratch run against an empty cache. It
was re-reviewed on 2026-07-05 against the current repository and local command
surface. Treat the findings below as current unless a later pass updates this
file again.

The current product scope is a read-only, local-first `ani` CLI for inspecting,
searching, syncing, and exporting an AniShelf library snapshot. The public
command surface is `auth`, `config`, `lib`, and `tmdb`; `lib` is the only public
library group.

## Current Review Scope

Checked against current code, help output, README/domain docs, and local smoke
commands:

- `ani --help`, `ani lib --help`, `ani lib list/search/get/status --help`,
  `ani tmdb search --help`
- initialized-cache reads: `lib status`, `lib list --limit`, `lib list --json`,
  `lib search --json`, `lib export --json`, `lib get ... --json`
- uninitialized-cache reads with a temporary cache directory
- parse errors for unsupported positional `tmdb search` titles and unsupported
  search limits
- code paths for JSON emission, human table rendering, and auth failure handling

## Since The Original Run

These original concerns are resolved or materially changed:

- `lib search` now accepts a positional query: `ani lib search "Alien"`.
- `library` is no longer a public alias; examples and docs should use `lib`.
- The public identifier surface is now `id`, not `identity`.
- README now has a user-facing quick start with `auth login`, TMDb key setup,
  `lib init`, and local-cache behavior.
- `lib list` now has `--limit`, `--sort`, `--style table|list`, `--fields`, and
  `--show-hidden`.
- Human collection output has a compact width-aware table path plus a
  list-style fallback.
- `lib status` now reports total entries, visible entries, hidden entries, sync
  token state, and metadata readiness.
- `tmdb search` is intentionally anime-only and supports discover mode when
  `--title` is omitted.
- Partial `lib get` batch failures are now documented in README and the domain
  reference: agents must inspect `.summary.errors` or item statuses.
- `auth status` now describes expired CloudKit credentials as a normal re-login
  condition instead of an alarming authentication failure/data-loss event.
- CLI-native first-run guidance is now visible in root help, and `lib status`
  labels the active cache user explicitly. Uninitialized status also points to
  `ani lib init`.
- Search surfaces now support human-sized result caps: `lib search <query>
  --limit <n>` and `tmdb search [title|--title <title>] --limit <n>`. `tmdb
  search Alien` is accepted as an alias for `tmdb search --title Alien`.
- Default human tables now budget against terminal width and truncate overlong
  cells with `...`, so normal collection output no longer wraps long titles or
  identifiers.

## What Works Well

- **JSON/stdout hygiene remains correct.** Progress and diagnostics go to
  stderr; JSON output is parseable on stdout.
- **`lib get` is agent-friendly.** The ordered envelope keeps item-level
  failures beside successful entries:
  `{summary:{requested,found,errors}, items:[{status, entry|error}]}`.
- **Uninitialized-cache errors are consistent.** Read commands fail closed with
  exit 2 and tell the user to run `ani lib init` first.
- **Global and command-level JSON flags both work.** `ani --json lib get ...`
  and `ani lib get ... --json` are both supported, with `-j` aliases.
- **Local-first semantics are clear.** `lib init` bootstraps the cache, `lib
  sync` refreshes it explicitly, and read commands default to cached data unless
  `--sync` or targeted live metadata is requested.
- **Hidden entries are no longer invisible accounting.** Status and filters now
  make the visible/hidden split explicit.

## Current Human-Facing Issues

No open human-facing issues remain from this review pass.

## Current Agent-Facing Issues

### 5. JSON still escapes non-ASCII text

`emit_json` currently calls `json.dumps(payload, indent=2, sort_keys=True)`,
which keeps Python's default `ensure_ascii=True`. Machine parsing is fine, but
piped debug output for Japanese/Chinese titles and overviews is hard to read.

Recommendation: switch user-facing JSON emission to `ensure_ascii=False`.
Stored cache JSON can keep compact ASCII-safe serialization if desired.

### 6. Collection keys still differ between commands

The current JSON shapes are stable but asymmetric:

| Command | Collection key |
| --- | --- |
| `lib list` | `entries` |
| `lib search` | `entries` |
| `lib export` | `entries` |
| `lib get` | `items` |

This is defensible because `get` preserves per-request item status, while the
others return homogeneous entry collections. Agents still need to remember the
split.

Recommendation: if this remains the contract, document the command-to-key table
in README's JSON section rather than normalizing the shape late.

### 7. Partial batch failures rely on payload inspection

This is no longer an undocumented surprise, but it remains important for agents:
mixed `lib get` batches exit 0 when at least one item is found, even when some
items contain `status: "error"`. The README and domain reference correctly tell
agents to inspect `.summary.errors`.

Recommendation: keep the current contract documented. A future `--strict` flag
could make any item error nonzero, but that is optional rather than urgent.

## Suggested Priority

1. Switch emitted JSON to `ensure_ascii=False`.
2. Document the `entries` / `items` JSON collection-key split.
