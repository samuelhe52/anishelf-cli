from __future__ import annotations

import sys
from dataclasses import replace
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.options import FieldListOption
from anishelf_cli.cloudkit.api_token import resolve_cloudkit_api_token
from anishelf_cli.core.output import HumanSection, emit_error, emit_human_blocks, emit_json
from anishelf_cli.models import CallbackStrategy
from anishelf_cli.models.output import (
    ConfigCallbackResult,
    ConfigCloudKitResult,
    ConfigLibraryResult,
    ConfigPathsResult,
    ConfigSetDefaultsPayloadResult,
    ConfigSetDefaultsResult,
    ConfigShowResult,
    ConfigTMDbResult,
    LibraryDefaultsResult,
)
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


def _config_payload() -> ConfigShowResult:
    api_token = resolve_cloudkit_api_token()
    defaults = _user_defaults_or_exit().library_read
    return ConfigShowResult(
        cloudkit=ConfigCloudKitResult(
            container=config.DEFAULT_CONTAINER,
            environment=config.DEFAULT_ENVIRONMENT,
            database=config.DEFAULT_DATABASE,
            app_auth_source=api_token.source,
            app_auth_version=api_token.version,
        ),
        callback=ConfigCallbackResult(strategy=CallbackStrategy.MANUAL_PASTE),
        tmdb=ConfigTMDbResult(api_key_envs=tuple(config.DEFAULT_TMDB_API_KEY_ENVS)),
        library=ConfigLibraryResult(
            defaults=LibraryDefaultsResult(
                metadata=defaults.metadata.value,
                display_fields=(
                    tuple(defaults.display_fields)
                    if defaults.display_fields is not None
                    else None
                ),
            )
        ),
        paths=ConfigPathsResult(
            config_dir=str(config.config_dir()),
            config_file=str(config.user_config_file()),
            cache_dir=str(config.cache_dir()),
            data_dir=str(config.data_dir()),
        ),
    )


@config_app.command("show", help="Show effective configuration and local paths.")
def config_show(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    payload = _config_payload()
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json"))
        return
    cloudkit = payload.cloudkit
    library_defaults = payload.library.defaults

    app_auth = cloudkit.app_auth_source
    if cloudkit.app_auth_version:
        app_auth += f", version {cloudkit.app_auth_version}"
    display_fields = library_defaults.display_fields
    display_fields_label = (
        "built-in" if display_fields is None else ", ".join(display_fields)
    )

    emit_human_blocks(
        [
            HumanSection(
                "CloudKit",
                (
                    ("Container", cloudkit.container),
                    ("Environment", cloudkit.environment),
                    ("Database", cloudkit.database),
                    ("App auth", app_auth),
                ),
            ),
            HumanSection(
                "Callback",
                (("Strategy", payload.callback.strategy),),
            ),
            HumanSection(
                "TMDb",
                (("API key envs", ", ".join(config.DEFAULT_TMDB_API_KEY_ENVS)),),
            ),
            HumanSection(
                "Library",
                (
                    ("Metadata", library_defaults.metadata),
                    ("Display fields", display_fields_label),
                ),
            ),
            HumanSection(
                "Paths",
                (
                    ("Config", payload.paths.config_dir),
                    ("Config file", payload.paths.config_file),
                    ("Cache", payload.paths.cache_dir),
                    ("Data", payload.paths.data_dir),
                ),
            ),
        ]
    )


@config_app.command("set-defaults", help="Store minimal user defaults for library read commands.")
def config_set_defaults(
    ctx: typer.Context,
    metadata: Annotated[
        str | None,
        typer.Option(
            "--metadata",
            help="Default metadata level for library read commands: none or summary.",
            show_default=False,
        ),
    ] = None,
    fields: FieldListOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    has_replacements = metadata is not None or fields is not None
    try:
        defaults = config.load_user_defaults()
    except config.UserConfigError as exc:
        if not has_replacements:
            emit_error(str(exc))
            raise typer.Exit(code=2) from exc
        defaults = config.UserDefaults()
    library_defaults = defaults.library_read

    if metadata is not None:
        try:
            metadata = config.resolve_configured_metadata_depth(metadata)
        except config.UserConfigError as exc:
            emit_error(str(exc))
            raise typer.Exit(code=2) from exc
        library_defaults = replace(library_defaults, metadata=metadata)

    if fields is not None:
        if fields.strip().lower() == "default":
            display_fields = None
        else:
            try:
                display_fields = config.normalize_library_display_fields(fields)
            except config.UserConfigError as exc:
                emit_error(str(exc))
                raise typer.Exit(code=2) from exc
        library_defaults = replace(library_defaults, display_fields=display_fields)

    defaults = config.UserDefaults(library_read=library_defaults)
    try:
        path = config.save_user_defaults(defaults)
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc

    payload = ConfigSetDefaultsResult(
        defaults=ConfigSetDefaultsPayloadResult(
            library=LibraryDefaultsResult(
                metadata=library_defaults.metadata.value,
                display_fields=(
                    tuple(library_defaults.display_fields)
                    if library_defaults.display_fields is not None
                    else None
                ),
            )
        ),
        path=str(path),
    )
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json"))
        return

    display_fields = library_defaults.display_fields
    emit_human_blocks(
        [
            HumanSection(
                "Library defaults",
                (
                    ("Metadata", library_defaults.metadata.value),
                    (
                        "Display fields",
                        "built-in"
                        if display_fields is None
                        else ", ".join(str(field) for field in display_fields),
                    ),
                    ("Config file", str(path)),
                ),
            )
        ]
    )


@config_app.command("set-tmdb-api-key", help="Store a TMDb API key in the secure credential store.")
def config_set_tmdb_api_key(
    ctx: typer.Context,
    from_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read the API key from stdin instead of prompting."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
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
    _emit_secret_saved(json_output_requested(ctx, json_output), "tmdb-api-key")


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


def _user_defaults_or_exit() -> config.UserDefaults:
    try:
        return config.load_user_defaults()
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
