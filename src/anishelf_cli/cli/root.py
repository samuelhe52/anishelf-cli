from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from anishelf_cli.cli import groups
from anishelf_cli.core.output import emit_placeholder
from anishelf_cli.models import AppState, MetadataDepth
from anishelf_cli.secrets import SecretStorageUnavailableError, delete_cloudkit_web_auth_token

app = typer.Typer(
    add_completion=False,
    help="Read-only AniShelf and CloudKit inspection CLI.",
    no_args_is_help=True,
)


@app.callback()
def root_callback(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option(help="Profile name.")] = "default",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON when supported."),
    ] = False,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase log verbosity.",
        ),
    ] = 0,
    metadata_depth: Annotated[
        MetadataDepth | None,
        typer.Option(help="Override command-specific metadata depth defaults."),
    ] = None,
    anishelf_source: Annotated[
        Path | None,
        typer.Option(help="Path to the AniShelf checkout used for schema checks."),
    ] = None,
) -> None:
    ctx.obj = AppState(
        profile=profile,
        json_output=json_output,
        verbosity=verbose,
        metadata_depth=metadata_depth,
        anishelf_source=anishelf_source,
    )


@app.command()
def login(ctx: typer.Context) -> None:
    emit_placeholder(ctx.obj, "login")


@app.command()
def logout(ctx: typer.Context) -> None:
    state = ctx.obj
    if not isinstance(state, AppState):
        raise RuntimeError("CLI context was not initialized")

    try:
        delete_cloudkit_web_auth_token(state.profile)
    except SecretStorageUnavailableError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if state.json_output:
        from anishelf_cli.core.output import emit_json

        emit_json({"profile": state.profile, "status": "logged-out"})
        return

    typer.echo(f"Removed CloudKit web auth token for profile {state.profile}.")


@app.command()
def whoami(ctx: typer.Context) -> None:
    emit_placeholder(ctx.obj, "whoami")


app.add_typer(groups.profile_app, name="profile")
app.add_typer(groups.config_app, name="config")
app.add_typer(groups.zones_app, name="zones")
app.add_typer(groups.records_app, name="records")
app.add_typer(groups.changes_app, name="changes")
app.add_typer(groups.library_app, name="library")
app.add_typer(groups.settings_app, name="settings")
app.add_typer(groups.tmdb_app, name="tmdb")
app.add_typer(groups.metadata_app, name="metadata")
app.add_typer(groups.schema_app, name="schema")
