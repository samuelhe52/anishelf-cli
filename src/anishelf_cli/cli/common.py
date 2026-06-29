from __future__ import annotations

import typer

from anishelf_cli.models import AppState


def state_from_context(ctx: typer.Context) -> AppState:
    state = ctx.obj
    if not isinstance(state, AppState):
        raise RuntimeError("CLI context was not initialized")
    return state


def json_output_requested(ctx: typer.Context, command_json_output: bool = False) -> bool:
    return state_from_context(ctx).json_output or command_json_output
