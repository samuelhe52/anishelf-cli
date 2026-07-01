from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from anishelf_cli.core.coercion import nonempty_string_or_none, strict_int_or_none
from anishelf_cli.core.output import emit_verbose
from anishelf_cli.core.redaction import SecretRedactor


class TMDbRequestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TMDbTitleSearchResult:
    movies: tuple[TMDbTitleSearchMatch, ...]
    series: tuple[TMDbTitleSearchMatch, ...]

    @property
    def movie_ids(self) -> set[int]:
        return {match.tmdb_id for match in self.movies}

    @property
    def series_ids(self) -> set[int]:
        return {match.tmdb_id for match in self.series}


@dataclass(frozen=True, slots=True)
class TMDbTitleSearchQuery:
    title: str | None = None
    year: int | None = None
    entry_type: str = "all"

    @property
    def mode(self) -> str:
        return "search" if self.title else "discover"


@dataclass(frozen=True, slots=True)
class TMDbTitleSearchMatch:
    entry_type: str
    tmdb_id: int
    title: str | None
    original_title: str | None
    release_date: str | None
    original_language_code: str | None
    overview: str | None
    poster_path: str | None
    details_url: str


@dataclass(frozen=True, slots=True)
class TMDbSummaryIdentity:
    entry_type: str
    tmdb_id: int
    parent_series_id: int | None = None
    season_number: int | None = None


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
            movie_payload = self._movie_search_payload(query)
            tv_payload = self._series_search_payload(query)
        except Exception as exc:
            if query.mode == "search":
                raise TMDbRequestError("TMDb title search failed.") from exc
            raise TMDbRequestError("TMDb discovery request failed.") from exc

        return TMDbTitleSearchResult(
            movies=_title_search_matches("movie", movie_payload),
            series=_title_search_matches("series", tv_payload),
        )

    def fetch_summary(self, identity: TMDbSummaryIdentity) -> dict[str, Any]:
        try:
            if identity.entry_type == "movie":
                payload = self._get(f"movie/{identity.tmdb_id}")
            elif identity.entry_type == "series":
                payload = self._get(f"tv/{identity.tmdb_id}")
            elif identity.entry_type == "season":
                if identity.parent_series_id is None or identity.season_number is None:
                    raise TMDbRequestError("Season metadata requires a parent series and season.")
                payload = self._get(
                    f"tv/{identity.parent_series_id}/season/{identity.season_number}"
                )
            else:
                raise TMDbRequestError(f"Unsupported TMDb entry type: {identity.entry_type}.")
        except TMDbRequestError:
            raise
        except Exception as exc:
            raise TMDbRequestError("TMDb summary metadata request failed.") from exc

        return _summary_payload(identity, payload)

    def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        request_params = dict(params or {})
        request_params["api_key"] = self.api_key
        response = self._get_with_retries(path, request_params)
        payload = response.json()
        if not isinstance(payload, dict):
            raise TMDbRequestError("TMDb response was not a JSON object.")
        return payload

    def _get_with_retries(self, path: str, params: dict[str, str]) -> httpx.Response:
        attempts = max(1, self.max_attempts)
        last_error: Exception | None = None
        url = f"https://api.themoviedb.org/3/{path}"
        redactor = self._redactor()
        for attempt in range(1, attempts + 1):
            params_log = json.dumps(params, sort_keys=True)
            emit_verbose(
                f"TMDb request -> GET {url} params={params_log} "
                f"attempt={attempt}/{attempts}",
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

    def _movie_search_payload(self, query: TMDbTitleSearchQuery) -> dict[str, Any]:
        if query.entry_type == "series":
            return {"results": []}
        if query.mode == "search":
            return self._get("search/movie", params=_movie_search_params(query))
        return self._get("discover/movie", params=_movie_discover_params(query))

    def _series_search_payload(self, query: TMDbTitleSearchQuery) -> dict[str, Any]:
        if query.entry_type == "movie":
            return {"results": []}
        if query.mode == "search":
            return self._get("search/tv", params=_series_search_params(query))
        return self._get("discover/tv", params=_series_discover_params(query))


def _title_search_matches(
    entry_type: str,
    payload: dict[str, Any],
) -> tuple[TMDbTitleSearchMatch, ...]:
    results = payload.get("results")
    if not isinstance(results, list):
        return ()

    matches: list[TMDbTitleSearchMatch] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            continue
        matches.append(
            TMDbTitleSearchMatch(
                entry_type=entry_type,
                tmdb_id=raw_id,
                title=nonempty_string_or_none(item.get("title"))
                or nonempty_string_or_none(item.get("name")),
                original_title=nonempty_string_or_none(item.get("original_title"))
                or nonempty_string_or_none(item.get("original_name")),
                release_date=nonempty_string_or_none(item.get("release_date"))
                or nonempty_string_or_none(item.get("first_air_date")),
                original_language_code=nonempty_string_or_none(item.get("original_language")),
                overview=nonempty_string_or_none(item.get("overview")),
                poster_path=nonempty_string_or_none(item.get("poster_path")),
                details_url=_details_link(
                    TMDbSummaryIdentity(
                        entry_type=entry_type,
                        tmdb_id=raw_id,
                    )
                ),
            )
        )
    return tuple(matches)


def _summary_payload(identity: TMDbSummaryIdentity, payload: dict[str, Any]) -> dict[str, Any]:
    name = nonempty_string_or_none(payload.get("title")) or nonempty_string_or_none(
        payload.get("name")
    )
    original_name = nonempty_string_or_none(payload.get("original_title")) or (
        nonempty_string_or_none(payload.get("original_name"))
    )
    on_air_date = (
        nonempty_string_or_none(payload.get("release_date"))
        or nonempty_string_or_none(payload.get("first_air_date"))
        or nonempty_string_or_none(payload.get("air_date"))
    )
    return {
        "entry_type": identity.entry_type,
        "tmdb_id": identity.tmdb_id,
        "parent_series_id": identity.parent_series_id,
        "season_number": identity.season_number,
        "language": None,
        "name": name,
        "name_translations": {},
        "original_name": original_name,
        "overview": nonempty_string_or_none(payload.get("overview")),
        "overview_translations": {},
        "poster_path": nonempty_string_or_none(payload.get("poster_path")),
        "backdrop_path": nonempty_string_or_none(payload.get("backdrop_path")),
        "logo_path": None,
        "original_language_code": nonempty_string_or_none(payload.get("original_language")),
        "on_air_date": on_air_date,
        "status": nonempty_string_or_none(payload.get("status")),
        "genres": _genres(payload.get("genres")),
        "runtime_minutes": _runtime_minutes(identity.entry_type, payload),
        "season_count": _season_count(identity.entry_type, payload),
        "episode_count": _episode_count(identity.entry_type, payload),
        "vote_average": _optional_number(payload.get("vote_average")),
        "vote_count": strict_int_or_none(payload.get("vote_count")),
        "popularity": _optional_number(payload.get("popularity")),
        "link_to_details": _details_link(identity),
        "source_version": "tmdb.http.summary.v2",
    }


def _details_link(identity: TMDbSummaryIdentity) -> str:
    if identity.entry_type == "movie":
        return f"https://www.themoviedb.org/movie/{identity.tmdb_id}"
    if identity.entry_type == "series":
        return f"https://www.themoviedb.org/tv/{identity.tmdb_id}"
    if identity.parent_series_id is not None and identity.season_number is not None:
        return f"https://www.themoviedb.org/tv/{identity.parent_series_id}/season/{identity.season_number}"
    return f"https://www.themoviedb.org/tv/{identity.tmdb_id}"


def _optional_number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _genres(value: object) -> list[dict[str, int | str]]:
    if not isinstance(value, list):
        return []

    genres: list[dict[str, int | str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        genre_id = strict_int_or_none(item.get("id"))
        name = nonempty_string_or_none(item.get("name"))
        if genre_id is None or name is None:
            continue
        genres.append({"id": genre_id, "name": name})
    return genres


def _runtime_minutes(entry_type: str, payload: dict[str, Any]) -> int | None:
    if entry_type != "movie":
        return None
    runtime = strict_int_or_none(payload.get("runtime"))
    if runtime is None or runtime <= 0:
        return None
    return runtime


def _season_count(entry_type: str, payload: dict[str, Any]) -> int | None:
    if entry_type != "series":
        return None
    count = strict_int_or_none(payload.get("number_of_seasons"))
    if count is None or count < 0:
        return None
    return count


def _episode_count(entry_type: str, payload: dict[str, Any]) -> int | None:
    if entry_type == "series":
        count = strict_int_or_none(payload.get("number_of_episodes"))
        if count is None or count < 0:
            return None
        return count
    if entry_type == "season":
        episodes = payload.get("episodes")
        if not isinstance(episodes, list):
            return None
        return len([episode for episode in episodes if isinstance(episode, dict)])
    return None


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
