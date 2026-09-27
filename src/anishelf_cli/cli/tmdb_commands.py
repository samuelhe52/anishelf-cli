from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Annotated

import typer

from anishelf_cli import config
from anishelf_cli.cache.schema import LibraryCacheError
from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.presentation import (
    normalized_tmdb_title,
    render_tmdb_search,
    tmdb_search_payload,
)
from anishelf_cli.core.logging import get_logger
from anishelf_cli.core.output import emit_error, emit_json
from anishelf_cli.models import TMDbMetadataLanguage
from anishelf_cli.models.domain import LibraryEntryModel
from anishelf_cli.models.tmdb import TMDbTitleSearchQuery, TMDbTitleSearchResult
from anishelf_cli.secrets import SecretStorageUnavailableError, default_secret_store
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, resolve_tmdb_api_token

logger = get_logger(__name__)

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
    title_argument: Annotated[
        str | None,
        typer.Argument(
            help="Optional title query. When omitted, discover popular anime titles instead.",
        ),
    ] = None,
    title_option: Annotated[
        str | None,
        typer.Option(
            "--title",
            "-t",
            help="Optional title query. When omitted, discover popular anime titles instead.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", min=1, help="Limit the total number of results returned."),
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
    title = _resolved_title_or_exit(title_argument, title_option)
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
    result = _limited_tmdb_search_result(result, limit=limit)
    library_ids = _library_ids_for_matches(result)

    payload = tmdb_search_payload(query, result, limit=limit, library_ids=library_ids)
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json", exclude_none=True))
        return

    render_tmdb_search(query, result, library_ids=library_ids)


def _library_ids_for_matches(
    result: TMDbTitleSearchResult,
) -> dict[tuple[str, int], tuple[str, ...]] | None:
    """Map each search match to its saved library ids, or None when unavailable.

    Marking is best effort: TMDb search must keep working before `lib init`, so a
    missing, empty, ambiguous, or unreadable cache omits the markers instead of
    failing the search. The lookup is read-only and never takes the cache lock.
    """
    try:
        store = LibraryCacheStore.find_default_scope()
        if not store.has_entries(read_only=True):
            return None
        entries = store.search_cached_entry_models(
            movie_ids={match.tmdb_id for match in result.movies},
            series_ids={match.tmdb_id for match in result.series},
            read_only=True,
        )
    except (LibraryCacheError, sqlite3.Error, OSError) as exc:
        logger.debug("TMDb search library marker -> skipped reason=%s", exc)
        return None
    grouped: dict[tuple[str, int], list[LibraryEntryModel]] = {}
    for entry in entries:
        if entry.entry_type == "season" and entry.parent_series_id is not None:
            key = ("series", entry.parent_series_id)
        else:
            key = (entry.entry_type, entry.tmdb_id)
        grouped.setdefault(key, []).append(entry)
    return {
        key: tuple(
            entry.identity
            for entry in sorted(
                group,
                key=lambda entry: (entry.entry_type == "season", entry.season_number or 0),
            )
        )
        for key, group in grouped.items()
    }


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


def _resolved_title_or_exit(title_argument: str | None, title_option: str | None) -> str | None:
    normalized_argument = normalized_tmdb_title(title_argument)
    normalized_option = normalized_tmdb_title(title_option)
    if normalized_argument is not None and normalized_option is not None:
        emit_error("Use either positional TITLE or --title, not both.")
        raise typer.Exit(code=2)
    return normalized_argument if normalized_argument is not None else normalized_option


def _limited_tmdb_search_result(
    result: TMDbTitleSearchResult,
    *,
    limit: int | None,
) -> TMDbTitleSearchResult:
    if limit is None:
        return result
    movies = result.movies[:limit]
    remaining = max(limit - len(movies), 0)
    series = result.series[:remaining]
    return result.model_copy(update={"movies": movies, "series": series})
