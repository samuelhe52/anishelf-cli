from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console

from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models import AppState


def console(stderr: bool = False) -> Console:
    return Console(stderr=stderr)


def emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def emit_placeholder(state: AppState, area: str) -> None:
    message = {
        "status": "not-implemented",
        "area": area,
        "profile": state.profile,
    }
    if state.json_output:
        emit_json(message)
        raise typer.Exit(code=1)

    console(stderr=True).print(
        f"[yellow]{area} is scaffolded but not implemented yet.[/yellow]"
    )
    raise typer.Exit(code=1)


def emit_error(message: str, *, redactor: SecretRedactor | None = None) -> None:
    output = redactor.redact(message) if redactor else message
    console(stderr=True).print(f"[red]{output}[/red]")

