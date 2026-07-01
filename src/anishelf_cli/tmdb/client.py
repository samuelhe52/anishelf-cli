from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal, TypeVar

import httpx
from pydantic import ValidationError

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.core.output import emit_verbose
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import LibraryEntryMetadata, TMDbSummaryIdentity
from anishelf_cli.models.tmdb import (
    TMDbTitleSearchMatch,
    TMDbTitleSearchQuery,
    TMDbTitleSearchResult,
)
from anishelf_cli.models.transport.tmdb import (
    TMDbMovieSummaryResponse,
    TMDbSearchItem,
    TMDbSearchResponse,
    TMDbSeasonSummaryResponse,
    TMDbSeriesSummaryResponse,
    details_link,
)

ModelT = TypeVar("ModelT", bound=AniShelfBaseModel)


class TMDbRequestError(RuntimeError):
    pass


@dataclass(slots=True)
class TMDbClient:
    api_key: str
    timeout_seconds: float = 20.0
    max_attempts: int = 3
    client: httpx.Client = field(default_factory=httpx.Client, repr=False)

    def _redactor(self) -> SecretRedactor:
        redactor = SecretRedactor()
        redactor.register(self.api_key, "tmdb-api-key")
        return redactor

    def search_title(self, title: str) -> TMDbTitleSearchResult:
        return self.search_titles(TMDbTitleSearchQuery(title=title))

    def search_titles(self, query: TMDbTitleSearchQuery) -> TMDbTitleSearchResult:
        try:
            movie_response = self._movie_search_response(query)
            series_response = self._series_search_response(query)
        except Exception as exc:
            if query.mode == "search":
                raise TMDbRequestError("TMDb title search failed.") from exc
            raise TMDbRequestError("TMDb discovery request failed.") from exc

        return TMDbTitleSearchResult(
            movies=_title_search_matches("movie", movie_response),
            series=_title_search_matches("series", series_response),
        )

    def fetch_summary(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata:
        try:
            if identity.entry_type == "movie":
                movie_response = self._get_model(
                    f"movie/{identity.tmdb_id}",
                    TMDbMovieSummaryResponse,
                )
                return movie_response.to_domain(identity)
            elif identity.entry_type == "series":
                series_response = self._get_model(
                    f"tv/{identity.tmdb_id}",
                    TMDbSeriesSummaryResponse,
                )
                return series_response.to_domain(identity)
            elif identity.entry_type == "season":
                if identity.parent_series_id is None or identity.season_number is None:
                    raise TMDbRequestError("Season metadata requires a parent series and season.")
                season_response = self._get_model(
                    f"tv/{identity.parent_series_id}/season/{identity.season_number}",
                    TMDbSeasonSummaryResponse,
                )
                return season_response.to_domain(identity)
            else:
                raise TMDbRequestError(f"Unsupported TMDb entry type: {identity.entry_type}.")
        except TMDbRequestError:
            raise
        except Exception as exc:
            raise TMDbRequestError("TMDb summary metadata request failed.") from exc

    def _get_model(
        self,
        path: str,
        model_type: type[ModelT],
        *,
        params: dict[str, str] | None = None,
    ) -> ModelT:
        request_params = dict(params or {})
        request_params["api_key"] = self.api_key
        response = self._get_with_retries(path, request_params)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TMDbRequestError("TMDb response was not valid JSON.") from exc
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise TMDbRequestError("TMDb response had an unexpected shape.") from exc

    def _get_with_retries(self, path: str, params: dict[str, str]) -> httpx.Response:
        attempts = max(1, self.max_attempts)
        last_error: Exception | None = None
        url = f"https://api.themoviedb.org/3/{path}"
        redactor = self._redactor()
        for attempt in range(1, attempts + 1):
            params_log = json.dumps(params, sort_keys=True)
            emit_verbose(
                f"TMDb request -> GET {url} params={params_log} attempt={attempt}/{attempts}",
                redactor=redactor,
            )
            try:
                response = self.client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout_seconds,
                )
                emit_verbose(
                    f"TMDb response <- HTTP {response.status_code} GET {response.request.url}",
                    redactor=redactor,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                emit_verbose(
                    f"TMDb HTTP error <- HTTP {exc.response.status_code} GET {exc.request.url}",
                    redactor=redactor,
                )
                if not _retryable_status(exc.response.status_code) or attempt == attempts:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                emit_verbose(
                    f"TMDb transport error <- GET {url}: {exc.__class__.__name__}: {exc}",
                    redactor=redactor,
                )
                if attempt == attempts:
                    raise
                last_error = exc
            else:
                return response
            time.sleep(min(0.25 * attempt, 1.0))

        raise TMDbRequestError("TMDb request failed.") from last_error

    def _movie_search_response(self, query: TMDbTitleSearchQuery) -> TMDbSearchResponse:
        if query.entry_type == "series":
            return TMDbSearchResponse()
        if query.mode == "search":
            return self._get_model(
                "search/movie",
                TMDbSearchResponse,
                params=_movie_search_params(query),
            )
        return self._get_model(
            "discover/movie",
            TMDbSearchResponse,
            params=_movie_discover_params(query),
        )

    def _series_search_response(self, query: TMDbTitleSearchQuery) -> TMDbSearchResponse:
        if query.entry_type == "movie":
            return TMDbSearchResponse()
        if query.mode == "search":
            return self._get_model(
                "search/tv",
                TMDbSearchResponse,
                params=_series_search_params(query),
            )
        return self._get_model(
            "discover/tv",
            TMDbSearchResponse,
            params=_series_discover_params(query),
        )


def _title_search_matches(
    entry_type: Literal["movie", "series"],
    response: TMDbSearchResponse,
) -> tuple[TMDbTitleSearchMatch, ...]:
    matches: list[TMDbTitleSearchMatch] = []
    for item in response.results:
        match = _title_search_match(entry_type, item)
        if match is not None:
            matches.append(match)
    return tuple(matches)


def _title_search_match(
    entry_type: Literal["movie", "series"],
    item: TMDbSearchItem,
) -> TMDbTitleSearchMatch | None:
    if item.id is None:
        return None
    return TMDbTitleSearchMatch(
        entry_type=entry_type,
        tmdb_id=item.id,
        title=nonempty_string_or_none(item.title) or nonempty_string_or_none(item.name),
        original_title=nonempty_string_or_none(item.original_title)
        or nonempty_string_or_none(item.original_name),
        release_date=nonempty_string_or_none(item.release_date)
        or nonempty_string_or_none(item.first_air_date),
        original_language_code=nonempty_string_or_none(item.original_language),
        overview=nonempty_string_or_none(item.overview),
        poster_path=nonempty_string_or_none(item.poster_path),
        details_url=details_link(TMDbSummaryIdentity(entry_type=entry_type, tmdb_id=item.id)),
    )


def _retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def _movie_search_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {"query": query.title or ""}
    if query.year is not None:
        params["primary_release_year"] = str(query.year)
    return params


def _series_search_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {"query": query.title or ""}
    if query.year is not None:
        params["first_air_date_year"] = str(query.year)
    return params


def _movie_discover_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {"sort_by": "popularity.desc"}
    if query.year is not None:
        params["primary_release_year"] = str(query.year)
    return params


def _series_discover_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {"sort_by": "popularity.desc"}
    if query.year is not None:
        params["first_air_date_year"] = str(query.year)
    return params


__all__ = [
    "TMDbClient",
    "TMDbRequestError",
    "TMDbSummaryIdentity",
    "TMDbTitleSearchMatch",
    "TMDbTitleSearchQuery",
    "TMDbTitleSearchResult",
]
