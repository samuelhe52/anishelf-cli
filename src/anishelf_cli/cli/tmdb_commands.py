from __future__ import annotations

from enum import StrEnum
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.presentation import (
    normalized_tmdb_title,
    render_tmdb_search,
    tmdb_search_payload,
)
from anishelf_cli.core.output import emit_error, emit_json
from anishelf_cli.models import TMDbMetadataLanguage
from anishelf_cli.models.tmdb import TMDbTitleSearchQuery
from anishelf_cli.secrets import SecretStorageUnavailableError, default_secret_store
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, resolve_tmdb_api_token

tmdb_app = typer.Typer(
    help="Global TMDb anime discovery commands.",
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
    help="Search TMDb anime by title, or discover popular anime titles when no title is given.",
)
def tmdb_search(
    ctx: typer.Context,
    title: Annotated[
        str | None,
        typer.Option(
            "--title",
            "-t",
            help="Optional title query. When omitted, discover popular anime titles instead.",
        ),
    ] = None,
    year: Annotated[
        int | None,
        typer.Option("--year", "-y", min=1888, help="Filter to a release or first-air year."),
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
    tmdb_language: Annotated[
        TMDbMetadataLanguage | None,
        typer.Option(
            "--tmdb-language",
            help="Use a TMDb language for this search without changing the configured default.",
            show_default=False,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    defaults = _user_defaults_or_exit()
    language = _metadata_language(tmdb_language, preferred_language=defaults.tmdb.metadata_language)
    query = TMDbTitleSearchQuery(
        title=normalized_tmdb_title(title),
        year=year,
        entry_type=entry_type.value,
        language=language,
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


def _user_defaults_or_exit() -> config.UserDefaults:
    try:
        return config.load_user_defaults()
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc


def _metadata_language(value: TMDbMetadataLanguage | None, *, preferred_language: str) -> str:
    if value is None:
        return preferred_language
    return value.value
