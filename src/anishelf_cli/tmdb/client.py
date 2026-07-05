from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal, TypeVar

import httpx
from pydantic import ValidationError

from anishelf_cli import config
from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.core.logging import get_logger
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import (
    LibraryEntryMetadata,
    LibraryEntryMetadataEpisode,
    LibraryEntryMetadataSeason,
    TMDbSummaryIdentity,
)
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
    TMDbTranslationsResponse,
    TranslationDictionaries,
    details_link,
)

ModelT = TypeVar("ModelT", bound=AniShelfBaseModel)
TMDB_ANIME_GENRE_ID = 16
logger = get_logger(__name__)


class TMDbRequestError(RuntimeError):
    pass


def _validation_error_summary(exc: ValidationError) -> str:
    first_error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in first_error.get("loc", ()))
    message = str(first_error.get("msg", "validation failed"))
    if location:
        return f"{location}: {message}"
    return message


@dataclass(slots=True)
class TMDbClient:
    api_key: str
    language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE
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
        except TMDbRequestError:
            raise
        except Exception as exc:
            if query.mode == "search":
                raise TMDbRequestError("TMDb title search failed.") from exc
            raise TMDbRequestError("TMDb discovery request failed.") from exc

        return TMDbTitleSearchResult(
            movies=_title_search_matches("movie", movie_response),
            series=_title_search_matches("series", series_response),
        )

    def fetch_summary(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata:
        return self.fetch_metadata(identity, MetadataDepth.SUMMARY)

    def fetch_metadata(
        self,
        identity: TMDbSummaryIdentity,
        depth: MetadataDepth = MetadataDepth.DETAILS,
    ) -> LibraryEntryMetadata:
        try:
            if identity.entry_type == "movie":
                movie_response = self._get_model(
                    f"movie/{identity.tmdb_id}",
                    TMDbMovieSummaryResponse,
                    params={"language": self._http_language()},
                )
                return movie_response.to_domain(
                    identity,
                    language=self.language,
                    translations=self._translations(f"movie/{identity.tmdb_id}", depth),
                )
            elif identity.entry_type == "series":
                series_response = self._get_model(
                    f"tv/{identity.tmdb_id}",
                    TMDbSeriesSummaryResponse,
                    params={"language": self._http_language()},
                )
                season_summaries: tuple[LibraryEntryMetadataSeason, ...] = ()
                episode_summaries: tuple[LibraryEntryMetadataEpisode, ...] = ()
                if depth is MetadataDepth.FULL:
                    season_summaries = tuple(
                        season.to_domain() for season in series_response.seasons
                    )
                    episode_summaries = tuple(
                        episode
                        for season in series_response.seasons
                        for episode in self._series_season_episodes(
                            identity.tmdb_id,
                            season.season_number,
                        )
                    )
                return series_response.to_domain(
                    identity,
                    language=self.language,
                    translations=self._translations(f"tv/{identity.tmdb_id}", depth),
                    season_summaries=season_summaries,
                    episode_summaries=episode_summaries,
                )
            elif identity.entry_type == "season":
                if identity.parent_series_id is None or identity.season_number is None:
                    raise TMDbRequestError("Season metadata requires a parent series and season.")
                parent_series_response = self._get_model(
                    f"tv/{identity.parent_series_id}",
                    TMDbSeriesSummaryResponse,
                    params={"language": self._http_language()},
                )
                season_response = self._get_model(
                    f"tv/{identity.parent_series_id}/season/{identity.season_number}",
                    TMDbSeasonSummaryResponse,
                    params={"language": self._http_language()},
                )
                return season_response.to_domain(
                    identity,
                    language=self.language,
                    translations=self._translations(
                        f"tv/{identity.parent_series_id}/season/{identity.season_number}",
                        depth,
                    ),
                    parent_series=parent_series_response,
                )
            else:
                raise TMDbRequestError(f"Unsupported TMDb entry type: {identity.entry_type}.")
        except TMDbRequestError:
            raise
        except Exception as exc:
            raise TMDbRequestError("TMDb metadata request failed.") from exc

    def _translations(self, base_path: str, depth: MetadataDepth) -> TranslationDictionaries | None:
        if depth is MetadataDepth.SUMMARY:
            return None
        # Details hydration feeds cached library search, which matches translated
        # titles and overviews even though those fields are projected only in full output.
        translations_response = self._get_model(
            f"{base_path}/translations",
            TMDbTranslationsResponse,
        )
        return translations_response.to_dictionaries()

    def _series_season_episodes(
        self,
        series_id: int,
        season_number: int | None,
    ) -> tuple[LibraryEntryMetadataEpisode, ...]:
        if season_number is None:
            return ()
        season_response = self._get_model(
            f"tv/{series_id}/season/{season_number}",
            TMDbSeasonSummaryResponse,
            params={"language": self._http_language()},
        )
        return tuple(
            episode.to_domain(default_season_number=season_number)
            for episode in season_response.episodes
        )

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
            raise TMDbRequestError(
                f"TMDb response had an unexpected shape: {_validation_error_summary(exc)}"
            ) from exc

    def _http_language(self) -> str:
        return config.tmdb_http_language_tag(self.language)

    def _get_with_retries(self, path: str, params: dict[str, str]) -> httpx.Response:
        attempts = max(1, self.max_attempts)
        last_error: Exception | None = None
        url = f"https://api.themoviedb.org/3/{path}"
        redactor = self._redactor()
        for attempt in range(1, attempts + 1):
            params_log = json.dumps(params, sort_keys=True)
            logger.debug(
                f"TMDb request -> GET {url} params={params_log} attempt={attempt}/{attempts}",
                extra={"redactor": redactor},
            )
            try:
                response = self.client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout_seconds,
                )
                logger.debug(
                    f"TMDb response <- HTTP {response.status_code} GET {response.request.url}",
                    extra={"redactor": redactor},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.debug(
                    f"TMDb HTTP error <- HTTP {exc.response.status_code} GET {exc.request.url}",
                    extra={"redactor": redactor},
                )
                if not _retryable_status(exc.response.status_code) or attempt == attempts:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                logger.debug(
                    f"TMDb transport error <- GET {url}: {exc.__class__.__name__}: {exc}",
                    extra={"redactor": redactor},
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
        # TMDb search endpoints do not support genre filters, so enforce the
        # AniShelf animation-only policy against the returned genre ids.
        if TMDB_ANIME_GENRE_ID not in item.genre_ids:
            continue
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
    params = {"query": query.title or "", "language": config.tmdb_http_language_tag(query.language)}
    if query.year is not None:
        params["primary_release_year"] = str(query.year)
    return params


def _series_search_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {"query": query.title or "", "language": config.tmdb_http_language_tag(query.language)}
    if query.year is not None:
        params["first_air_date_year"] = str(query.year)
    return params


def _movie_discover_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {
        "sort_by": "popularity.desc",
        "with_genres": str(TMDB_ANIME_GENRE_ID),
        "language": config.tmdb_http_language_tag(query.language),
    }
    if query.year is not None:
        params["primary_release_year"] = str(query.year)
    return params


def _series_discover_params(query: TMDbTitleSearchQuery) -> dict[str, str]:
    params = {
        "sort_by": "popularity.desc",
        "with_genres": str(TMDB_ANIME_GENRE_ID),
        "language": config.tmdb_http_language_tag(query.language),
    }
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
