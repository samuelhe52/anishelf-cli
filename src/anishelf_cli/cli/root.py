from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Annotated

import httpx
import typer

from anishelf_cli.cli import groups
from anishelf_cli.cli.common import state_from_context
from anishelf_cli.cloudkit.auth import (
    CloudKitAuthError,
    LoopbackLoginTimeoutError,
    capture_loopback_callback,
    extract_web_auth_token,
    initiate_login,
)
from anishelf_cli.cloudkit.tokens import (
    ConfiguredCloudKitAPITokenProvider,
    MissingCloudKitAPITokenError,
)
from anishelf_cli.core.output import emit_error, emit_json, emit_placeholder
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models import AppState, CallbackStrategy, MetadataDepth
from anishelf_cli.profiles import load_profile
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    delete_cloudkit_web_auth_token,
    store_cloudkit_web_auth_token,
)

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


def _make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


@app.command()
def login(
    ctx: typer.Context,
    callback_strategy: Annotated[
        CallbackStrategy | None,
        typer.Option(help="Override the configured login callback strategy."),
    ] = None,
    loopback_host: Annotated[
        str,
        typer.Option(help="Loopback host used when callback strategy is loopback."),
    ] = "127.0.0.1",
    loopback_port: Annotated[
        int,
        typer.Option(help="Loopback port used when callback strategy is loopback."),
    ] = 8765,
    loopback_timeout: Annotated[
        float,
        typer.Option(help="Seconds to wait for a loopback callback."),
    ] = 120.0,
) -> None:
    state = state_from_context(ctx)
    profile = load_profile(state.profile)
    strategy = callback_strategy or profile.callback_strategy
    redactor = SecretRedactor()

    try:
        api_token = ConfiguredCloudKitAPITokenProvider(state.profile, profile).resolve()
        redactor.register(api_token.value, "cloudkit-api-token")

        with _make_http_client() as client:
            initiation = initiate_login(profile, api_token, client)

        if strategy == CallbackStrategy.LOOPBACK:
            web_auth_token = capture_loopback_callback(
                initiation.redirect_url,
                host=loopback_host,
                port=loopback_port,
                timeout_seconds=loopback_timeout,
                browser_open=webbrowser.open,
            )
        else:
            webbrowser.open(initiation.redirect_url)
            callback_url = typer.prompt(
                "Paste final HTTPS callback URL",
                hide_input=True,
                err=True,
            ).strip()
            redactor.register(callback_url, "cloudkit-callback-url")
            web_auth_token = extract_web_auth_token(callback_url)

        redactor.register(web_auth_token, "cloudkit-web-auth-token")
        store_cloudkit_web_auth_token(state.profile, web_auth_token)
    except (
        CloudKitAuthError,
        MissingCloudKitAPITokenError,
        SecretStorageUnavailableError,
    ) as exc:
        code = 3 if isinstance(exc, LoopbackLoginTimeoutError) else 2
        emit_error(str(exc), redactor=redactor)
        raise typer.Exit(code=code) from exc

    payload = {
        "profile": state.profile,
        "status": "logged-in",
        "storage": "keychain",
        "callback_strategy": strategy,
        "api_token_source": api_token.source_label,
    }
    if state.json_output:
        emit_json(payload)
        return

    typer.echo(f"Logged in to CloudKit for profile {state.profile}.")


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
