from __future__ import annotations

from typing import Literal

from pydantic import StrictInt, StrictStr

from anishelf_cli.models.common import AniShelfBaseModel


class TMDbTitleSearchQuery(AniShelfBaseModel):
    title: StrictStr | None = None
    year: StrictInt | None = None
    entry_type: Literal["all", "movie", "series"] = "all"

    @property
    def mode(self) -> str:
        return "search" if self.title else "discover"


class TMDbTitleSearchMatch(AniShelfBaseModel):
    entry_type: StrictStr
    tmdb_id: StrictInt
    title: StrictStr | None
    original_title: StrictStr | None
    release_date: StrictStr | None
    original_language_code: StrictStr | None
    overview: StrictStr | None
    poster_path: StrictStr | None
    details_url: StrictStr


class TMDbTitleSearchResult(AniShelfBaseModel):
    movies: tuple[TMDbTitleSearchMatch, ...]
    series: tuple[TMDbTitleSearchMatch, ...]

    @property
    def movie_ids(self) -> set[int]:
        return {match.tmdb_id for match in self.movies}

    @property
    def series_ids(self) -> set[int]:
        return {match.tmdb_id for match in self.series}


__all__ = [
    "TMDbTitleSearchMatch",
    "TMDbTitleSearchQuery",
    "TMDbTitleSearchResult",
]
