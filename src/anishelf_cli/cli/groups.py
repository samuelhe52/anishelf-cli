from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cli.common import state_from_context
from anishelf_cli.cloudkit.api_token import resolve_cloudkit_api_token
from anishelf_cli.core.output import emit_json, emit_placeholder
from anishelf_cli.models import CallbackStrategy
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    default_secret_store,
    set_secret,
    tmdb_api_key_secret,
)

config_app = typer.Typer(help="Local configuration commands.", no_args_is_help=True)
zones_app = typer.Typer(help="CloudKit zone commands.", no_args_is_help=True)
records_app = typer.Typer(help="CloudKit record commands.", no_args_is_help=True)
changes_app = typer.Typer(help="CloudKit change-feed commands.", no_args_is_help=True)
library_app = typer.Typer(help="AniShelf library commands.", no_args_is_help=True)
settings_app = typer.Typer(help="AniShelf settings commands.", no_args_is_help=True)
tmdb_app = typer.Typer(help="Global TMDb discovery commands.", no_args_is_help=True)
metadata_app = typer.Typer(help="Metadata hydration commands.", no_args_is_help=True)
schema_app = typer.Typer(help="AniShelf schema validation commands.", no_args_is_help=True)


def _effective_scope_payload() -> dict[str, object]:
    api_token = resolve_cloudkit_api_token()
    return {
        "container": config.DEFAULT_CONTAINER,
        "environment": config.DEFAULT_ENVIRONMENT,
        "database": config.DEFAULT_DATABASE,
        "callback_strategy": CallbackStrategy.MANUAL_PASTE,
        "cloudkit_api_token_source": api_token.source,
        "cloudkit_api_token_version": api_token.version,
        "tmdb_api_key_envs": list(config.DEFAULT_TMDB_API_KEY_ENVS),
    }


@config_app.command("status", help="Show effective CloudKit, callback, and TMDb configuration.")
def config_status(ctx: typer.Context) -> None:
    state = state_from_context(ctx)
    payload = _effective_scope_payload()
    if state.json_output:
        emit_json(payload)
        return
    typer.echo(f"Container: {payload['container']}")
    typer.echo(f"Environment: {payload['environment']}")
    typer.echo(f"Database: {payload['database']}")
    typer.echo(f"Callback strategy: {payload['callback_strategy']}")
    typer.echo(f"CloudKit app auth source: {payload['cloudkit_api_token_source']}")
    typer.echo(f"CloudKit app auth version: {payload['cloudkit_api_token_version']}")
    typer.echo(f"TMDb API key envs: {', '.join(config.DEFAULT_TMDB_API_KEY_ENVS)}")


@config_app.command("show", help="Show local AniShelf CLI config, cache, and data paths.")
def config_show(ctx: typer.Context) -> None:
    state = state_from_context(ctx)
    payload = {
        "config_dir": str(config.config_dir()),
        "cache_dir": str(config.cache_dir()),
        "data_dir": str(config.data_dir()),
    }
    emit_json(payload) if state.json_output else typer.echo(
        "\n".join(f"{key}: {value}" for key, value in payload.items())
    )


@config_app.command("set-tmdb-api-key", help="Store a TMDb API key in the secure credential store.")
def config_set_tmdb_api_key(
    ctx: typer.Context,
    from_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read the API key from stdin instead of prompting."),
    ] = False,
) -> None:
    state = state_from_context(ctx)
    token = (
        sys.stdin.read().strip()
        if from_stdin
        else typer.prompt(
            "TMDb API key",
            hide_input=True,
        )
    )
    try:
        set_secret(tmdb_api_key_secret(), token, default_secret_store())
    except (SecretStorageUnavailableError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    _emit_secret_saved(state.json_output, "tmdb-api-key")


def _emit_secret_saved(json_output: bool, secret_type: str) -> None:
    payload = {
        "secret_type": secret_type,
        "status": "stored",
        "storage": "keychain",
    }
    if json_output:
        emit_json(payload)
        return
    typer.echo(f"Stored {secret_type} in Keychain.")


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
