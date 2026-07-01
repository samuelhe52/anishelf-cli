from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator

from anishelf_cli.core.coercion import nonempty_string_or_none, strict_int_or_none
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import (
    LibraryEntryMetadata,
    LibraryEntryMetadataGenre,
    TMDbSummaryIdentity,
)

TMDB_SUMMARY_SOURCE_VERSION = "tmdb.http.summary.v2"


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


class TMDbGenrePayload(AniShelfBaseModel):
    id: StrictInt | None = None
    name: StrictStr | None = None


class TMDbSearchItem(AniShelfBaseModel):
    id: StrictInt | None = None
    title: StrictStr | None = None
    name: StrictStr | None = None
    original_title: StrictStr | None = None
    original_name: StrictStr | None = None
    release_date: StrictStr | None = None
    first_air_date: StrictStr | None = None
    original_language: StrictStr | None = None
    overview: StrictStr | None = None
    poster_path: StrictStr | None = None
    adult: bool | None = None
    backdrop_path: StrictStr | None = None
    genre_ids: tuple[StrictInt, ...] = ()
    origin_country: tuple[StrictStr, ...] = ()
    popularity: StrictFloat | StrictInt | None = None
    video: bool | None = None
    vote_average: StrictFloat | StrictInt | None = None
    vote_count: StrictInt | None = None

    @field_validator("genre_ids", "origin_country", mode="before")
    @classmethod
    def _default_tuple(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    def to_match(self, entry_type: Literal["movie", "series"]) -> TMDbTitleSearchMatch | None:
        if self.id is None:
            return None
        return TMDbTitleSearchMatch(
            entry_type=entry_type,
            tmdb_id=self.id,
            title=nonempty_string_or_none(self.title) or nonempty_string_or_none(self.name),
            original_title=nonempty_string_or_none(self.original_title)
            or nonempty_string_or_none(self.original_name),
            release_date=nonempty_string_or_none(self.release_date)
            or nonempty_string_or_none(self.first_air_date),
            original_language_code=nonempty_string_or_none(self.original_language),
            overview=nonempty_string_or_none(self.overview),
            poster_path=nonempty_string_or_none(self.poster_path),
            details_url=details_link(TMDbSummaryIdentity(entry_type=entry_type, tmdb_id=self.id)),
        )


class TMDbSearchResponse(AniShelfBaseModel):
    results: tuple[TMDbSearchItem, ...] = ()
    page: StrictInt | None = None
    total_pages: StrictInt | None = None
    total_results: StrictInt | None = None

    @field_validator("results", mode="before")
    @classmethod
    def _default_results(cls, value: object) -> object:
        if value is None:
            return ()
        return value


class _TMDbSummaryBase(AniShelfBaseModel):
    id: StrictInt | None = None
    name: StrictStr | None = None
    title: StrictStr | None = None
    original_name: StrictStr | None = None
    original_title: StrictStr | None = None
    overview: StrictStr | None = None
    poster_path: StrictStr | None = None
    backdrop_path: StrictStr | None = None
    original_language: StrictStr | None = None
    status: StrictStr | None = None
    genres: tuple[TMDbGenrePayload, ...] = ()
    vote_average: StrictFloat | StrictInt | None = None
    vote_count: StrictInt | None = None
    popularity: StrictFloat | StrictInt | None = None

    @field_validator("genres", mode="before")
    @classmethod
    def _default_genres(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    def _base_domain_metadata(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata:
        return LibraryEntryMetadata(
            entry_type=identity.entry_type,
            tmdb_id=identity.tmdb_id,
            parent_series_id=identity.parent_series_id,
            season_number=identity.season_number,
            language=None,
            name=nonempty_string_or_none(self.title) or nonempty_string_or_none(self.name),
            name_translations=(),
            original_name=nonempty_string_or_none(self.original_title)
            or nonempty_string_or_none(self.original_name),
            overview=nonempty_string_or_none(self.overview),
            overview_translations=(),
            poster_path=nonempty_string_or_none(self.poster_path),
            backdrop_path=nonempty_string_or_none(self.backdrop_path),
            logo_path=None,
            original_language_code=nonempty_string_or_none(self.original_language),
            on_air_date=self.on_air_date,
            status=nonempty_string_or_none(self.status),
            genres=self.domain_genres,
            runtime_minutes=None,
            season_count=None,
            episode_count=None,
            vote_average=optional_number(self.vote_average),
            vote_count=strict_int_or_none(self.vote_count),
            popularity=optional_number(self.popularity),
            link_to_details=details_link(identity),
            source_version=TMDB_SUMMARY_SOURCE_VERSION,
        )

    @property
    def domain_genres(self) -> tuple[LibraryEntryMetadataGenre, ...]:
        genres: list[LibraryEntryMetadataGenre] = []
        for genre in self.genres:
            name = nonempty_string_or_none(genre.name)
            if genre.id is not None and name is not None:
                genres.append(LibraryEntryMetadataGenre(id=genre.id, name=name))
        return tuple(genres)

    @property
    def on_air_date(self) -> str | None:
        return None


class TMDbMovieSummaryResponse(_TMDbSummaryBase):
    adult: bool | None = None
    # Keep rarely used nested TMDb detail blobs raw until a concrete metadata depth needs them.
    belongs_to_collection: dict[str, Any] | None = None
    budget: StrictInt | None = None
    homepage: StrictStr | None = None
    imdb_id: StrictStr | None = None
    origin_country: tuple[StrictStr, ...] = ()
    production_companies: tuple[dict[str, Any], ...] = ()
    production_countries: tuple[dict[str, Any], ...] = ()
    release_date: StrictStr | None = None
    revenue: StrictInt | None = None
    runtime: StrictInt | None = None
    spoken_languages: tuple[dict[str, Any], ...] = ()
    tagline: StrictStr | None = None
    video: bool | None = None

    @property
    def on_air_date(self) -> str | None:
        return nonempty_string_or_none(self.release_date)

    def to_domain(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata:
        runtime = strict_int_or_none(self.runtime)
        return self._base_domain_metadata(identity).model_copy(
            update={"runtime_minutes": runtime if runtime is not None and runtime > 0 else None}
        )


class TMDbSeriesSummaryResponse(_TMDbSummaryBase):
    adult: bool | None = None
    created_by: tuple[dict[str, Any], ...] = ()
    episode_run_time: tuple[StrictInt, ...] = ()
    first_air_date: StrictStr | None = None
    homepage: StrictStr | None = None
    in_production: bool | None = None
    languages: tuple[StrictStr, ...] = ()
    last_air_date: StrictStr | None = None
    last_episode_to_air: dict[str, Any] | None = None
    networks: tuple[dict[str, Any], ...] = ()
    next_episode_to_air: dict[str, Any] | None = None
    number_of_episodes: StrictInt | None = None
    number_of_seasons: StrictInt | None = None
    origin_country: tuple[StrictStr, ...] = ()
    production_companies: tuple[dict[str, Any], ...] = ()
    production_countries: tuple[dict[str, Any], ...] = ()
    seasons: tuple[dict[str, Any], ...] = ()
    spoken_languages: tuple[dict[str, Any], ...] = ()
    tagline: StrictStr | None = None
    type: StrictStr | None = None

    @property
    def on_air_date(self) -> str | None:
        return nonempty_string_or_none(self.first_air_date)

    def to_domain(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata:
        season_count = strict_int_or_none(self.number_of_seasons)
        episode_count = strict_int_or_none(self.number_of_episodes)
        return self._base_domain_metadata(identity).model_copy(
            update={
                "season_count": (
                    season_count if season_count is not None and season_count >= 0 else None
                ),
                "episode_count": (
                    episode_count if episode_count is not None and episode_count >= 0 else None
                ),
            }
        )


class TMDbSeasonSummaryResponse(_TMDbSummaryBase):
    mongo_id: StrictStr | None = Field(default=None, validation_alias="_id")
    air_date: StrictStr | None = None
    episodes: tuple[dict[str, Any], ...] = ()
    season_number: StrictInt | None = None

    @field_validator("episodes", mode="before")
    @classmethod
    def _default_episodes(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @property
    def on_air_date(self) -> str | None:
        return nonempty_string_or_none(self.air_date)

    def to_domain(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata:
        return self._base_domain_metadata(identity).model_copy(
            update={"episode_count": len(self.episodes)}
        )


def details_link(identity: TMDbSummaryIdentity) -> str:
    if identity.entry_type == "movie":
        return f"https://www.themoviedb.org/movie/{identity.tmdb_id}"
    if identity.entry_type == "series":
        return f"https://www.themoviedb.org/tv/{identity.tmdb_id}"
    if identity.parent_series_id is not None and identity.season_number is not None:
        return f"https://www.themoviedb.org/tv/{identity.parent_series_id}/season/{identity.season_number}"
    return f"https://www.themoviedb.org/tv/{identity.tmdb_id}"


def optional_number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None
