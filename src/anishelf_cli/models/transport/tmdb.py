from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import (
    LibraryEntryMetadata,
    LibraryEntryMetadataEpisode,
    LibraryEntryMetadataGenre,
    LibraryEntryMetadataSeason,
    TMDbSummaryIdentity,
)

TMDB_METADATA_SOURCE_VERSION = "tmdb.http.metadata.v1"


class TMDbTransportModel(AniShelfBaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=False,
        str_strip_whitespace=False,
    )


class TMDbSearchItem(TMDbTransportModel):
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


class TMDbSearchResponse(TMDbTransportModel):
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


class _TMDbSummaryBase(TMDbTransportModel):
    id: StrictInt | None = None
    name: StrictStr | None = None
    title: StrictStr | None = None
    original_name: StrictStr | None = None
    original_title: StrictStr | None = None
    overview: StrictStr | None = None
    poster_path: StrictStr | None = None
    backdrop_path: StrictStr | None = None
    original_language: StrictStr | None = None
    genres: tuple[TMDbGenre, ...] = ()
    vote_average: StrictFloat | StrictInt | None = None
    vote_count: StrictInt | None = None
    popularity: StrictFloat | StrictInt | None = None
    status: StrictStr | None = None

    def _base_domain_metadata(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries | None = None,
        link_to_details: str | None,
        original_language_code: str | None = None,
        logo_path: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> LibraryEntryMetadata:
        translation_payload = translations or TranslationDictionaries()
        payload: dict[str, object] = {
            "entry_type": identity.entry_type,
            "tmdb_id": identity.tmdb_id,
            "parent_series_id": identity.parent_series_id,
            "season_number": identity.season_number,
            "language": language,
            "name": nonempty_string_or_none(self.title) or nonempty_string_or_none(self.name),
            "name_translations": translation_payload.name,
            "overview": nonempty_string_or_none(self.overview),
            "overview_translations": translation_payload.overview,
            "poster_path": nonempty_string_or_none(self.poster_path),
            "backdrop_path": nonempty_string_or_none(self.backdrop_path),
            "logo_path": logo_path,
            "original_language_code": original_language_code
            or nonempty_string_or_none(self.original_language),
            "on_air_date": self.on_air_date,
            "link_to_details": link_to_details,
            "genres": tuple(
                domain_genre
                for genre in self.genres
                if (domain_genre := genre.to_domain()) is not None
            ),
            "vote_average": optional_number(self.vote_average),
            "vote_count": self.vote_count,
            "popularity": optional_number(self.popularity),
            "status": nonempty_string_or_none(self.status),
            "source_version": TMDB_METADATA_SOURCE_VERSION,
        }
        if extra:
            payload.update(extra)
        return LibraryEntryMetadata.model_validate(payload)

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

    def to_domain(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries | None = None,
    ) -> LibraryEntryMetadata:
        return self._base_domain_metadata(
            identity,
            language=language,
            translations=translations,
            link_to_details=nonempty_string_or_none(self.homepage),
            extra={
                "runtime_minutes": self.runtime,
                "release_date": nonempty_string_or_none(self.release_date),
                "tagline": nonempty_string_or_none(self.tagline),
            },
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
    seasons: tuple[TMDbSeasonListItem, ...] = ()
    spoken_languages: tuple[dict[str, Any], ...] = ()
    tagline: StrictStr | None = None
    type: StrictStr | None = None

    @property
    def on_air_date(self) -> str | None:
        return nonempty_string_or_none(self.first_air_date)

    def to_domain(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries | None = None,
        season_summaries: tuple[LibraryEntryMetadataSeason, ...] = (),
        episode_summaries: tuple[LibraryEntryMetadataEpisode, ...] = (),
    ) -> LibraryEntryMetadata:
        return self._base_domain_metadata(
            identity,
            language=language,
            translations=translations,
            link_to_details=nonempty_string_or_none(self.homepage),
            extra={
                "number_of_seasons": self.number_of_seasons,
                "number_of_episodes": self.number_of_episodes,
                "episode_run_time_minutes": self.episode_run_time,
                "first_air_date": nonempty_string_or_none(self.first_air_date),
                "last_air_date": nonempty_string_or_none(self.last_air_date),
                "tagline": nonempty_string_or_none(self.tagline),
                "season_summaries": season_summaries,
                "episode_summaries": episode_summaries,
            },
        )


class TMDbSeasonSummaryResponse(_TMDbSummaryBase):
    mongo_id: StrictStr | None = Field(default=None, validation_alias="_id")
    air_date: StrictStr | None = None
    episodes: tuple[TMDbEpisodeSummaryItem, ...] = ()
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

    def to_domain(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries | None = None,
        parent_series: TMDbSeriesSummaryResponse,
    ) -> LibraryEntryMetadata:
        return self._base_domain_metadata(
            identity,
            language=language,
            translations=translations,
            link_to_details=nonempty_string_or_none(parent_series.homepage),
            original_language_code=nonempty_string_or_none(parent_series.original_language),
            extra={
                "number_of_episodes": len(self.episodes) if self.episodes else None,
                "episode_summaries": tuple(
                    episode.to_domain(default_season_number=identity.season_number)
                    for episode in self.episodes
                ),
            },
        )


class TMDbGenre(TMDbTransportModel):
    id: StrictInt | None = None
    name: StrictStr | None = None

    def to_domain(self) -> LibraryEntryMetadataGenre | None:
        name = nonempty_string_or_none(self.name)
        if self.id is None or name is None:
            return None
        return LibraryEntryMetadataGenre(id=self.id, name=name)


class TMDbSeasonListItem(TMDbTransportModel):
    air_date: StrictStr | None = None
    episode_count: StrictInt | None = None
    name: StrictStr | None = None
    overview: StrictStr | None = None
    poster_path: StrictStr | None = None
    season_number: StrictInt | None = None

    def to_domain(self) -> LibraryEntryMetadataSeason:
        return LibraryEntryMetadataSeason(
            season_number=self.season_number,
            name=nonempty_string_or_none(self.name),
            overview=nonempty_string_or_none(self.overview),
            air_date=nonempty_string_or_none(self.air_date),
            episode_count=self.episode_count,
            poster_path=nonempty_string_or_none(self.poster_path),
        )


class TMDbEpisodeSummaryItem(TMDbTransportModel):
    episode_number: StrictInt | None = None
    season_number: StrictInt | None = None
    name: StrictStr | None = None
    overview: StrictStr | None = None
    air_date: StrictStr | None = None
    still_path: StrictStr | None = None

    def to_domain(
        self,
        *,
        default_season_number: int | None = None,
    ) -> LibraryEntryMetadataEpisode:
        return LibraryEntryMetadataEpisode(
            episode_number=self.episode_number,
            season_number=self.season_number or default_season_number,
            name=nonempty_string_or_none(self.name),
            overview=nonempty_string_or_none(self.overview),
            air_date=nonempty_string_or_none(self.air_date),
            still_path=nonempty_string_or_none(self.still_path),
        )


class TMDbTranslationData(TMDbTransportModel):
    title: StrictStr | None = None
    name: StrictStr | None = None
    overview: StrictStr | None = None


class TMDbTranslationItem(TMDbTransportModel):
    iso_3166_1: StrictStr | None = None
    iso_639_1: StrictStr | None = None
    data: TMDbTranslationData | None = None


class TMDbTranslationsResponse(TMDbTransportModel):
    translations: tuple[TMDbTranslationItem, ...] = ()

    @field_validator("translations", mode="before")
    @classmethod
    def _default_translations(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    def to_dictionaries(self) -> TranslationDictionaries:
        names: list[tuple[str, str]] = []
        overviews: list[tuple[str, str]] = []
        for translation in self.translations:
            language_code = nonempty_string_or_none(translation.iso_639_1)
            country_code = nonempty_string_or_none(translation.iso_3166_1)
            if language_code is None or country_code is None or translation.data is None:
                continue
            key = f"{language_code}-{country_code}"
            name = nonempty_string_or_none(translation.data.title) or nonempty_string_or_none(
                translation.data.name
            )
            overview = nonempty_string_or_none(translation.data.overview)
            if name is not None:
                names.append((key, name))
            if overview is not None:
                overviews.append((key, overview))
        return TranslationDictionaries(name=tuple(names), overview=tuple(overviews))


class TranslationDictionaries(AniShelfBaseModel):
    name: tuple[tuple[str, str], ...] = ()
    overview: tuple[tuple[str, str], ...] = ()


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
