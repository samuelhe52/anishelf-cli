from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cli.common import state_from_context
from anishelf_cli.core.output import emit_json, emit_placeholder
from anishelf_cli.profiles import load_profile

profile_app = typer.Typer(help="Profile inspection commands.", no_args_is_help=True)
config_app = typer.Typer(help="Local configuration commands.", no_args_is_help=True)
zones_app = typer.Typer(help="CloudKit zone commands.", no_args_is_help=True)
records_app = typer.Typer(help="CloudKit record commands.", no_args_is_help=True)
changes_app = typer.Typer(help="CloudKit change-feed commands.", no_args_is_help=True)
library_app = typer.Typer(help="AniShelf library commands.", no_args_is_help=True)
settings_app = typer.Typer(help="AniShelf settings commands.", no_args_is_help=True)
tmdb_app = typer.Typer(help="Global TMDb discovery commands.", no_args_is_help=True)
metadata_app = typer.Typer(help="Metadata hydration commands.", no_args_is_help=True)
schema_app = typer.Typer(help="AniShelf schema validation commands.", no_args_is_help=True)


@profile_app.command("status")
def profile_status(ctx: typer.Context) -> None:
    state = state_from_context(ctx)
    profile = load_profile(state.profile)
    payload = {
        "profile": state.profile,
        "container": profile.container,
        "environment": profile.environment,
        "database": profile.database,
        "callback_strategy": profile.callback_strategy,
        "cloudkit_token_source": profile.cloudkit_token_source,
        "tmdb_token_source": profile.tmdb_token_source,
        "env_file": str(profile.env_file) if profile.env_file else None,
        "anishelf_source": str(state.anishelf_source or profile.anishelf_source),
    }
    if state.json_output:
        emit_json(payload)
        return
    typer.echo(f"Profile: {payload['profile']}")
    typer.echo(f"Container: {payload['container']}")
    typer.echo(f"Environment: {payload['environment']}")
    typer.echo(f"Database: {payload['database']}")
    typer.echo(f"Callback strategy: {payload['callback_strategy']}")
    typer.echo(f"CloudKit token source: {payload['cloudkit_token_source']}")
    typer.echo(f"TMDb token source: {payload['tmdb_token_source']}")
    typer.echo(f"AniShelf source: {payload['anishelf_source']}")


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    state = state_from_context(ctx)
    payload = {
        "profile": state.profile,
        "config_dir": str(config.config_dir()),
        "cache_dir": str(config.cache_dir()),
        "data_dir": str(config.data_dir()),
    }
    emit_json(payload) if state.json_output else typer.echo(
        "\n".join(f"{key}: {value}" for key, value in payload.items())
    )


@config_app.command("set-cloudkit-token")
def config_set_cloudkit_token(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "config set-cloudkit-token")


@config_app.command("set-tmdb-token")
def config_set_tmdb_token(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "config set-tmdb-token")


@zones_app.command("list")
def zones_list(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "zones list")


@records_app.command("lookup")
def records_lookup(
    ctx: typer.Context,
    identities: Annotated[list[str], typer.Argument(help="Record identities to fetch.")],
) -> None:
    _ = identities
    emit_placeholder(state_from_context(ctx), "records lookup")


@records_app.command("query")
def records_query(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument()],
) -> None:
    _ = query
    emit_placeholder(state_from_context(ctx), "records query")


@changes_app.command("database")
def changes_database(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "changes database")


@changes_app.command("zone")
def changes_zone(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "changes zone")


@library_app.command("get")
def library_get(
    ctx: typer.Context,
    identities: Annotated[list[str], typer.Argument(help="AniShelf identities.")],
) -> None:
    _ = identities
    emit_placeholder(state_from_context(ctx), "library get")


@library_app.command("list")
def library_list(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "library list")


@library_app.command("search")
def library_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
) -> None:
    _ = title
    emit_placeholder(state_from_context(ctx), "library search")


@library_app.command("export")
def library_export(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "library export")


@library_app.command("changes")
def library_changes(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "library changes")


@settings_app.command("show")
def settings_show(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "settings show")


@tmdb_app.command("search")
def tmdb_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
) -> None:
    _ = title
    emit_placeholder(state_from_context(ctx), "tmdb search")


@metadata_app.command("hydrate")
def metadata_hydrate(
    ctx: typer.Context,
    input_path: Annotated[Path | None, typer.Option("--input")] = None,
) -> None:
    _ = input_path
    emit_placeholder(state_from_context(ctx), "metadata hydrate")


@schema_app.command("check")
def schema_check(ctx: typer.Context) -> None:
    emit_placeholder(state_from_context(ctx), "schema check")
