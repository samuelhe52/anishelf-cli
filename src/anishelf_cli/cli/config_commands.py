from __future__ import annotations

import sys
from dataclasses import replace
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.options import FieldListOption
from anishelf_cli.cli.secret_backend import (
    confirm_plaintext_backend,
    enable_plaintext_backend,
    plaintext_backend_warning,
    system_backend_unavailable_guidance,
)
from anishelf_cli.cloudkit.api_token import resolve_cloudkit_api_token
from anishelf_cli.core.output import (
    HumanSection,
    emit_error,
    emit_human_blocks,
    emit_json,
    emit_progress,
)
from anishelf_cli.models import (
    CallbackStrategy,
    HumanOutputStyle,
    MetadataDepth,
    SecretBackend,
    TMDbMetadataLanguage,
)
from anishelf_cli.models.output import (
    ConfigCallbackResult,
    ConfigCloudKitResult,
    ConfigLibraryResult,
    ConfigPathsResult,
    ConfigSecretsResult,
    ConfigSetDefaultsPayloadResult,
    ConfigSetDefaultsResult,
    ConfigSetSecretsBackendResult,
    ConfigShowResult,
    ConfigTMDbResult,
    LibraryDefaultsResult,
    TMDbDefaultsResult,
)
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    configured_secret_backend,
    secret_backend_storage_label,
    secret_store_for_backend,
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
    defaults = _user_defaults_or_exit()
    library_defaults = defaults.library_read
    tmdb_defaults = defaults.tmdb
    secret_defaults = defaults.secrets
    return ConfigShowResult(
        cloudkit=ConfigCloudKitResult(
            container=config.DEFAULT_CONTAINER,
            environment=config.DEFAULT_ENVIRONMENT,
            database=config.DEFAULT_DATABASE,
            app_auth_source=api_token.source,
            app_auth_version=api_token.version,
        ),
        callback=ConfigCallbackResult(strategy=CallbackStrategy.MANUAL_PASTE),
        tmdb=ConfigTMDbResult(
            api_key_envs=tuple(config.DEFAULT_TMDB_API_KEY_ENVS),
            defaults=TMDbDefaultsResult(
                metadata_language=tmdb_defaults.metadata_language,
                hydration_depth=tmdb_defaults.hydration_depth.value,
            ),
        ),
        library=ConfigLibraryResult(
            defaults=LibraryDefaultsResult(
                metadata=library_defaults.metadata.value,
                display_fields=(
                    tuple(library_defaults.display_fields)
                    if library_defaults.display_fields is not None
                    else None
                ),
                output_style=library_defaults.output_style.value,
                show_hidden=library_defaults.show_hidden,
            )
        ),
        secrets=ConfigSecretsResult(
            backend=secret_backend_storage_label(secret_defaults.backend),
            plaintext_file=(
                str(config.plaintext_keyring_file())
                if secret_defaults.backend is SecretBackend.PLAINTEXT_FILE
                else None
            ),
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
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
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
    display_fields_label = "built-in" if display_fields is None else ", ".join(display_fields)
    secrets = payload.secrets

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
                (
                    ("API key envs", ", ".join(config.DEFAULT_TMDB_API_KEY_ENVS)),
                    ("Metadata language", payload.tmdb.defaults.metadata_language),
                ),
            ),
            HumanSection(
                "Library",
                (
                    ("Metadata", library_defaults.metadata),
                    ("Display fields", display_fields_label),
                    ("Output style", library_defaults.output_style),
                    ("Show hidden", "yes" if library_defaults.show_hidden else "no"),
                ),
            ),
            HumanSection(
                "Secrets",
                (
                    ("Backend", secrets.backend),
                    (
                        "Plaintext file",
                        (
                            secrets.plaintext_file
                            if secrets.plaintext_file is not None
                            else "not used"
                        ),
                    ),
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


@config_app.command(
    "set-secrets-backend",
    help="Select where CloudKit and TMDb secrets are stored.",
)
def config_set_secrets_backend(
    ctx: typer.Context,
    backend: Annotated[
        SecretBackend,
        typer.Argument(help="Secret storage backend: system or plaintext-file."),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Confirm the plaintext-file security warning without prompting.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    plaintext_file_reminder: str | None = None
    if backend is SecretBackend.PLAINTEXT_FILE:
        typer.echo(plaintext_backend_warning(), err=True)
        if not yes:
            confirmed = confirm_plaintext_backend("Enable plaintext-file secret storage?")
            if not confirmed:
                raise typer.Exit(code=2)
        try:
            previous_secret_defaults = config.load_secret_defaults()
            secret_defaults = enable_plaintext_backend()
            path = config.user_config_file()
            if (
                previous_secret_defaults.backend is SecretBackend.SYSTEM
                and config.plaintext_keyring_file().exists()
            ):
                plaintext_file_reminder = (
                    "Plaintext secret storage is now enabled, and an existing plaintext "
                    f"keyring file is present at {config.plaintext_keyring_file()}. "
                    "Delete that file first if you do not want ani to reuse old "
                    "plaintext CloudKit or TMDb secrets."
                )
        except config.UserConfigError as exc:
            emit_error(str(exc))
            raise typer.Exit(code=2) from exc
    else:
        try:
            secret_defaults = config.SecretDefaults(backend=SecretBackend.SYSTEM)
            path = config.save_secret_defaults(secret_defaults)
        except config.UserConfigError as exc:
            emit_error(str(exc))
            raise typer.Exit(code=2) from exc

    payload = ConfigSetSecretsBackendResult(
        backend=secret_backend_storage_label(secret_defaults.backend),
        plaintext_file=(
            str(config.plaintext_keyring_file())
            if secret_defaults.backend is SecretBackend.PLAINTEXT_FILE
            else None
        ),
        path=str(path),
    )
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json"))
        if plaintext_file_reminder is not None:
            typer.echo(plaintext_file_reminder, err=True)
        return

    rows: list[tuple[str, str]] = [
        ("Backend", payload.backend),
        ("Config file", payload.path),
    ]
    if payload.plaintext_file is not None:
        rows.append(("Plaintext file", payload.plaintext_file))
    emit_human_blocks([HumanSection("Secrets", tuple(rows))])
    if plaintext_file_reminder is not None:
        typer.echo(plaintext_file_reminder, err=True)


@config_app.command("set-defaults", help="Store minimal user defaults for library read commands.")
def config_set_defaults(
    ctx: typer.Context,
    metadata: Annotated[
        MetadataDepth | None,
        typer.Option(
            "--metadata",
            "-m",
            help="Default metadata level for library read commands: none or summary.",
            show_default=False,
        ),
    ] = None,
    fields: FieldListOption = None,
    output_style: Annotated[
        HumanOutputStyle | None,
        typer.Option(
            "--style",
            "-s",
            help="Default human output style for library list/search.",
            show_default=False,
        ),
    ] = None,
    tmdb_language: Annotated[
        TMDbMetadataLanguage | None,
        typer.Option(
            "--tmdb-language",
            help="Preferred TMDb metadata language.",
            show_default=False,
        ),
    ] = None,
    hydration_depth: Annotated[
        str | None,
        typer.Option(
            "--hydration-depth",
            help="Default TMDb cache hydration depth: details or full.",
            show_default=False,
        ),
    ] = None,
    show_hidden: Annotated[
        bool | None,
        typer.Option(
            "--show-hidden/--hide-hidden",
            help="Default whether library read commands include hidden entries.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    try:
        defaults = config.load_user_defaults()
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    library_defaults = defaults.library_read
    tmdb_defaults = defaults.tmdb
    secret_defaults = defaults.secrets
    original_tmdb_language = tmdb_defaults.metadata_language

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

    if output_style is not None:
        library_defaults = replace(library_defaults, output_style=output_style)

    if tmdb_language is not None:
        tmdb_defaults = replace(tmdb_defaults, metadata_language=tmdb_language.value)

    if hydration_depth is not None:
        try:
            resolved_hydration_depth = config.resolve_configured_hydration_depth(hydration_depth)
        except config.UserConfigError as exc:
            emit_error(str(exc))
            raise typer.Exit(code=2) from exc
        tmdb_defaults = replace(tmdb_defaults, hydration_depth=resolved_hydration_depth)

    if show_hidden is not None:
        library_defaults = replace(library_defaults, show_hidden=show_hidden)

    defaults = config.UserDefaults(
        library_read=library_defaults,
        tmdb=tmdb_defaults,
        secrets=secret_defaults,
    )
    try:
        path = config.save_user_defaults(defaults)
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    tmdb_language_changed = original_tmdb_language != tmdb_defaults.metadata_language
    if tmdb_language_changed:
        emit_progress(
            "TMDb metadata language changed. Run `ani lib clear-cache --yes` and "
            "`ani lib init` to rebuild persisted metadata in the new language."
        )

    payload = ConfigSetDefaultsResult(
        defaults=ConfigSetDefaultsPayloadResult(
            library=LibraryDefaultsResult(
                metadata=library_defaults.metadata.value,
                display_fields=(
                    tuple(library_defaults.display_fields)
                    if library_defaults.display_fields is not None
                    else None
                ),
                output_style=library_defaults.output_style.value,
                show_hidden=library_defaults.show_hidden,
            ),
            tmdb=TMDbDefaultsResult(
                metadata_language=tmdb_defaults.metadata_language,
                hydration_depth=tmdb_defaults.hydration_depth.value,
            ),
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
                    ("Output style", library_defaults.output_style.value),
                    ("Show hidden", "yes" if library_defaults.show_hidden else "no"),
                    ("TMDb language", tmdb_defaults.metadata_language),
                    ("Hydration depth", tmdb_defaults.hydration_depth.value),
                    (
                        "Cache rebuild",
                        ("recommended" if tmdb_language_changed else "not needed for this change"),
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
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    try:
        secret_backend = configured_secret_backend()
        secret_store = secret_store_for_backend(secret_backend)
    except SecretStorageUnavailableError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    secret_descriptor = tmdb_api_key_secret()
    try:
        secret_store.get_password(secret_descriptor.service, secret_descriptor.account)
    except SecretStorageUnavailableError as exc:
        message = (
            system_backend_unavailable_guidance(exc, action="TMDb API key storage")
            if secret_backend is SecretBackend.SYSTEM
            else str(exc)
        )
        typer.echo(message, err=True)
        raise typer.Exit(code=2) from exc

    token = (
        sys.stdin.read().strip()
        if from_stdin
        else typer.prompt(
            "TMDb API key",
            hide_input=True,
        )
    )
    try:
        set_secret(secret_descriptor, token, secret_store)
    except SecretStorageUnavailableError as exc:
        message = (
            system_backend_unavailable_guidance(exc, action="TMDb API key storage")
            if secret_backend is SecretBackend.SYSTEM
            else str(exc)
        )
        typer.echo(message, err=True)
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    _emit_secret_saved(
        json_output_requested(ctx, json_output),
        "tmdb-api-key",
        secret_backend_storage_label(secret_backend),
    )


def _emit_secret_saved(json_output: bool, secret_type: str, storage: str) -> None:
    payload = {
        "secret_type": secret_type,
        "status": "stored",
        "storage": storage,
    }
    if json_output:
        emit_json(payload)
        return
    typer.echo(f"Stored {secret_type} in {storage} secret storage.")


def _user_defaults_or_exit() -> config.UserDefaults:
    try:
        return config.load_user_defaults()
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
