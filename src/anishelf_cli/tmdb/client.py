from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tmdbsimple as tmdb  # type: ignore[import-untyped]


class TMDbRequestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TMDbTitleSearchResult:
    movie_ids: set[int]
    series_ids: set[int]


@dataclass(slots=True)
class TMDbClient:
    api_key: str

    def search_title(self, title: str) -> TMDbTitleSearchResult:
        previous_api_key = tmdb.API_KEY
        tmdb.API_KEY = self.api_key
        try:
            search = tmdb.Search()
            movie_payload = search.movie(query=title)
            tv_payload = search.tv(query=title)
        except Exception as exc:
            raise TMDbRequestError("TMDb title search failed.") from exc
        finally:
            tmdb.API_KEY = previous_api_key

        return TMDbTitleSearchResult(
            movie_ids=_result_ids(movie_payload),
            series_ids=_result_ids(tv_payload),
        )


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
