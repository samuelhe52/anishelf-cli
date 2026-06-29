from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cli.common import state_from_context
from anishelf_cli.cloudkit.api_token import resolve_cloudkit_api_token
from anishelf_cli.core.output import HumanSection, emit_human_blocks, emit_json, emit_placeholder
from anishelf_cli.models import CallbackStrategy
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    default_secret_store,
    set_secret,
    tmdb_api_key_secret,
)

config_app = typer.Typer(
    help="Configuration commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
library_app = typer.Typer(
    help="AniShelf library commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
tmdb_app = typer.Typer(
    help="Global TMDb discovery commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
metadata_app = typer.Typer(
    help="Metadata hydration commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _config_payload() -> dict[str, object]:
    api_token = resolve_cloudkit_api_token()
    return {
        "cloudkit": {
            "container": config.DEFAULT_CONTAINER,
            "environment": config.DEFAULT_ENVIRONMENT,
            "database": config.DEFAULT_DATABASE,
            "app_auth_source": api_token.source,
            "app_auth_version": api_token.version,
        },
        "callback": {
            "strategy": CallbackStrategy.MANUAL_PASTE,
        },
        "tmdb": {
            "api_key_envs": list(config.DEFAULT_TMDB_API_KEY_ENVS),
        },
        "paths": {
            "config_dir": str(config.config_dir()),
            "cache_dir": str(config.cache_dir()),
            "data_dir": str(config.data_dir()),
        },
    }


@config_app.command("show", help="Show effective configuration and local paths.")
def config_show(ctx: typer.Context) -> None:
    state = state_from_context(ctx)
    payload = _config_payload()
    if state.json_output:
        emit_json(payload)
        return
    cloudkit = payload["cloudkit"]
    callback = payload["callback"]
    tmdb = payload["tmdb"]
    paths = payload["paths"]
    if not (
        isinstance(cloudkit, dict)
        and isinstance(callback, dict)
        and isinstance(tmdb, dict)
        and isinstance(paths, dict)
    ):
        raise RuntimeError("config payload was not initialized correctly")

    app_auth = str(cloudkit["app_auth_source"])
    if cloudkit["app_auth_version"]:
        app_auth += f", version {cloudkit['app_auth_version']}"

    emit_human_blocks(
        [
            HumanSection(
                "CloudKit",
                (
                    ("Container", cloudkit["container"]),
                    ("Environment", cloudkit["environment"]),
                    ("Database", cloudkit["database"]),
                    ("App auth", app_auth),
                ),
            ),
            HumanSection(
                "Callback",
                (("Strategy", callback["strategy"]),),
            ),
            HumanSection(
                "TMDb",
                (("API key envs", ", ".join(config.DEFAULT_TMDB_API_KEY_ENVS)),),
            ),
            HumanSection(
                "Paths",
                (
                    ("Config", paths["config_dir"]),
                    ("Cache", paths["cache_dir"]),
                    ("Data", paths["data_dir"]),
                ),
            ),
        ]
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
