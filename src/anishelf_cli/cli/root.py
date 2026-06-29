from __future__ import annotations

import os
import sys
import termios
import webbrowser
from typing import Annotated, TextIO

import httpx
import typer

from anishelf_cli.cli import groups
from anishelf_cli.cli.common import state_from_context
from anishelf_cli.cloudkit.api_token import (
    MissingCloudKitAPITokenError,
    resolve_cloudkit_api_token,
)
from anishelf_cli.cloudkit.auth import (
    CloudKitAuthError,
    LoopbackLoginTimeoutError,
    capture_loopback_callback,
    extract_web_auth_token,
    initiate_login,
)
from anishelf_cli.core.output import emit_error, emit_json, emit_placeholder
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models import AppState, CallbackStrategy, MetadataDepth
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
) -> None:
    ctx.obj = AppState(
        json_output=json_output,
        verbosity=verbose,
        metadata_depth=metadata_depth,
    )


def _make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _manual_callback_instructions(redirect_url: str) -> None:
    typer.echo("", err=True)
    typer.echo("CloudKit sign-in", err=True)
    typer.echo("", err=True)
    typer.echo("1. Open this URL in your browser:", err=True)
    typer.echo("", err=True)
    typer.echo(redirect_url, err=True)
    typer.echo("", err=True)
    typer.echo("2. After Apple redirects you back, copy the full HTTPS callback URL.", err=True)
    typer.echo("3. Paste that callback URL below and press Enter.", err=True)
    typer.echo("", err=True)
    typer.echo("The pasted URL is hidden because it contains a login token.", err=True)
    typer.echo("", err=True)


def _read_callback_url(stream: TextIO | None = None) -> str:
    stream = stream or sys.stdin
    if stream.isatty():
        return _read_hidden_tty_line(stream).strip()
    return stream.readline().strip()


def _read_hidden_tty_line(stream: TextIO) -> str:
    fd = stream.fileno()
    original_attrs = termios.tcgetattr(fd)
    new_attrs = original_attrs[:]
    new_attrs[3] &= ~(termios.ECHO | termios.ICANON)
    new_attrs[6][termios.VMIN] = 1
    new_attrs[6][termios.VTIME] = 0

    chunks = bytearray()
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, new_attrs)
        while True:
            char = os.read(fd, 1)
            if not char or char in (b"\n", b"\r"):
                break
            if char in (b"\x03", b"\x04"):
                raise KeyboardInterrupt
            if char in (b"\x08", b"\x7f"):
                if chunks:
                    chunks.pop()
                continue
            chunks.extend(char)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original_attrs)
        typer.echo("", err=True)

    value = chunks.decode("utf-8", errors="replace")
    return value.removeprefix("\x1b[200~").removesuffix("\x1b[201~")


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
    strategy = callback_strategy or CallbackStrategy.MANUAL_PASTE
    redactor = SecretRedactor()

    try:
        api_token = resolve_cloudkit_api_token()
        redactor.register(api_token.value, "cloudkit-api-token")

        with _make_http_client() as client:
            initiation = initiate_login(api_token, client)

        if strategy == CallbackStrategy.LOOPBACK:
            web_auth_token = capture_loopback_callback(
                initiation.redirect_url,
                host=loopback_host,
                port=loopback_port,
                timeout_seconds=loopback_timeout,
                browser_open=webbrowser.open,
            )
        else:
            _manual_callback_instructions(initiation.redirect_url)
            typer.echo("Callback URL: ", nl=False, err=True)
            callback_url_value = _read_callback_url()
            redactor.register(callback_url_value, "cloudkit-callback-url")
            web_auth_token = extract_web_auth_token(callback_url_value)

        redactor.register(web_auth_token, "cloudkit-web-auth-token")
        store_cloudkit_web_auth_token(web_auth_token)
    except (
        CloudKitAuthError,
        MissingCloudKitAPITokenError,
        SecretStorageUnavailableError,
    ) as exc:
        code = 3 if isinstance(exc, LoopbackLoginTimeoutError) else 2
        emit_error(str(exc), redactor=redactor)
        raise typer.Exit(code=code) from exc

    payload = {
        "status": "logged-in",
        "storage": "keychain",
        "callback_strategy": strategy,
        "cloudkit_api_token_source": api_token.source,
        "cloudkit_api_token_version": api_token.version,
    }
    if state.json_output:
        emit_json(payload)
        return

    typer.echo("Logged in to CloudKit.")


@app.command()
def logout(ctx: typer.Context) -> None:
    state = ctx.obj
    if not isinstance(state, AppState):
        raise RuntimeError("CLI context was not initialized")

    try:
        delete_cloudkit_web_auth_token()
    except SecretStorageUnavailableError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    if state.json_output:
        from anishelf_cli.core.output import emit_json

        emit_json({"status": "logged-out"})
        return

    typer.echo("Removed CloudKit web auth token.")


@app.command()
def whoami(ctx: typer.Context) -> None:
    emit_placeholder(ctx.obj, "whoami")


app.add_typer(groups.config_app, name="config")
app.add_typer(groups.zones_app, name="zones")
app.add_typer(groups.records_app, name="records")
app.add_typer(groups.changes_app, name="changes")
app.add_typer(groups.library_app, name="library")
app.add_typer(groups.settings_app, name="settings")
app.add_typer(groups.tmdb_app, name="tmdb")
app.add_typer(groups.metadata_app, name="metadata")
app.add_typer(groups.schema_app, name="schema")
