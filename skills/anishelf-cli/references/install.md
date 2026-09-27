# AniShelf CLI Install And First Run

Use this file only when `ani` is unavailable, the user asks to install or
update the CLI, or a new machine/account needs first-run setup.

## Verify Availability

Check whether `ani` is already available:

```bash
ani --help
```

If it works, prefer steady-state workflows instead of reinstalling.

## Install From The Skill

From the skill directory, use the bundled bootstrap helper:

```bash
python3 scripts/bootstrap_anishelf_cli.py
```

The installer requires `uv` and Python 3.13. It installs the beta Git release
as a `uv` tool, then verifies `ani --help`. If a user wants a specific fork or
branch, pass `--repo-url` and `--ref`.

Equivalent manual beta install:

```bash
uv tool install --python 3.13 git+https://github.com/samuelhe52/anishelf-cli.git@v0.2.0
uv tool update-shell
ani --help
```

For local development from a clone:

```bash
uv sync
uv run ani --help
make check
```

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
into agent chat, because it can contain auth material.

When guiding the CloudKit web login, ask the user to sign in manually with
their account and password instead of using passkeys when possible, and to
check `Keep me signed in` so the returned auth token expires less quickly.

TMDb is optional for basic CloudKit library export, but required for metadata
enrichment, `lib search`, and global `tmdb search`.
