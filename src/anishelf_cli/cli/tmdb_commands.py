from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.presentation import (
    normalized_tmdb_title,
    render_tmdb_search,
    tmdb_search_payload,
)
from anishelf_cli.core.output import emit_error, emit_json
from anishelf_cli.secrets import SecretStorageUnavailableError, default_secret_store
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError, TMDbTitleSearchQuery
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, resolve_tmdb_api_token

tmdb_app = typer.Typer(
    help="Global TMDb discovery commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


class TMDbSearchType(StrEnum):
    ALL = "all"
    MOVIE = "movie"
    SERIES = "series"


def _tmdb_summary_client_or_exit() -> TMDbClient:
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
    except (MissingTMDbAPITokenError, SecretStorageUnavailableError) as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    return TMDbClient(tmdb_token.value)


@tmdb_app.command(
    "search",
    help="Search TMDb by title, or discover popular titles when no title is given.",
)
def tmdb_search(
    ctx: typer.Context,
    title: Annotated[
        str | None,
        typer.Option(
            "--title",
            help="Optional title query. When omitted, discover popular titles instead.",
        ),
    ] = None,
    year: Annotated[
        int | None,
        typer.Option("--year", min=1888, help="Filter to a release or first-air year."),
    ] = None,
    entry_type: Annotated[
        TMDbSearchType,
        typer.Option(
            "--type",
            "--entry-type",
            help="Limit results to movies, series, or both.",
            show_default=True,
        ),
    ] = TMDbSearchType.ALL,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    query = TMDbTitleSearchQuery(
        title=normalized_tmdb_title(title),
        year=year,
        entry_type=entry_type.value,
    )
    try:
        result = _tmdb_summary_client_or_exit().search_titles(query)
    except TMDbRequestError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc

    payload = tmdb_search_payload(query, result)
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json", exclude_none=True))
        return

    render_tmdb_search(query, result)
