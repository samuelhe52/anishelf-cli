from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class TMDbRequestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TMDbTitleSearchResult:
    movie_ids: set[int]
    series_ids: set[int]


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

    def search_title(self, title: str) -> TMDbTitleSearchResult:
        try:
            movie_payload = self._get("search/movie", params={"query": title})
            tv_payload = self._get("search/tv", params={"query": title})
        except Exception as exc:
            raise TMDbRequestError("TMDb title search failed.") from exc

        return TMDbTitleSearchResult(
            movie_ids=_result_ids(movie_payload),
            series_ids=_result_ids(tv_payload),
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
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.get(
                    f"https://api.themoviedb.org/3/{path}",
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if not _retryable_status(exc.response.status_code) or attempt == attempts:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise
                last_error = exc
            else:
                return response
            time.sleep(min(0.25 * attempt, 1.0))

        raise TMDbRequestError("TMDb request failed.") from last_error


def _result_ids(payload: dict[str, Any]) -> set[int]:
    results = payload.get("results")
    if not isinstance(results, list):
        return set()

    ids: set[int] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
            ids.add(raw_id)
    return ids


def _summary_payload(identity: TMDbSummaryIdentity, payload: dict[str, Any]) -> dict[str, Any]:
    name = _optional_string(payload.get("title")) or _optional_string(payload.get("name"))
    original_name = _optional_string(payload.get("original_title")) or _optional_string(
        payload.get("original_name")
    )
    on_air_date = (
        _optional_string(payload.get("release_date"))
        or _optional_string(payload.get("first_air_date"))
        or _optional_string(payload.get("air_date"))
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
        "overview": _optional_string(payload.get("overview")),
        "overview_translations": {},
        "poster_path": _optional_string(payload.get("poster_path")),
        "backdrop_path": _optional_string(payload.get("backdrop_path")),
        "logo_path": None,
        "original_language_code": _optional_string(payload.get("original_language")),
        "on_air_date": on_air_date,
        "link_to_details": _details_link(identity),
        "source_version": "tmdb.http.summary.v1",
    }


def _details_link(identity: TMDbSummaryIdentity) -> str:
    if identity.entry_type == "movie":
        return f"https://www.themoviedb.org/movie/{identity.tmdb_id}"
    if identity.entry_type == "series":
        return f"https://www.themoviedb.org/tv/{identity.tmdb_id}"
    if identity.parent_series_id is not None and identity.season_number is not None:
        return f"https://www.themoviedb.org/tv/{identity.parent_series_id}/season/{identity.season_number}"
    return f"https://www.themoviedb.org/tv/{identity.tmdb_id}"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599
