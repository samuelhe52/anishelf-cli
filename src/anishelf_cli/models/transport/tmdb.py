from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import (
    LibraryEntryMetadata,
    TMDbSummaryIdentity,
)

TMDB_SUMMARY_SOURCE_VERSION = "tmdb.http.summary.v3"


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

    def _base_domain_metadata(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries,
        link_to_details: str | None,
        original_language_code: str | None = None,
    ) -> LibraryEntryMetadata:
        return LibraryEntryMetadata(
            entry_type=identity.entry_type,
            tmdb_id=identity.tmdb_id,
            parent_series_id=identity.parent_series_id,
            season_number=identity.season_number,
            language=language,
            name=nonempty_string_or_none(self.title) or nonempty_string_or_none(self.name),
            name_translations=translations.name,
            overview=nonempty_string_or_none(self.overview),
            overview_translations=translations.overview,
            poster_path=nonempty_string_or_none(self.poster_path),
            backdrop_path=nonempty_string_or_none(self.backdrop_path),
            logo_path=None,
            original_language_code=original_language_code
            or nonempty_string_or_none(self.original_language),
            on_air_date=self.on_air_date,
            link_to_details=link_to_details,
            source_version=TMDB_SUMMARY_SOURCE_VERSION,
        )

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
        translations: TranslationDictionaries,
    ) -> LibraryEntryMetadata:
        return self._base_domain_metadata(
            identity,
            language=language,
            translations=translations,
            link_to_details=nonempty_string_or_none(self.homepage),
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

    def to_domain(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries,
    ) -> LibraryEntryMetadata:
        return self._base_domain_metadata(
            identity,
            language=language,
            translations=translations,
            link_to_details=nonempty_string_or_none(self.homepage),
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

    def to_domain(
        self,
        identity: TMDbSummaryIdentity,
        *,
        language: str,
        translations: TranslationDictionaries,
        parent_series: TMDbSeriesSummaryResponse,
    ) -> LibraryEntryMetadata:
        return self._base_domain_metadata(
            identity,
            language=language,
            translations=translations,
            link_to_details=nonempty_string_or_none(parent_series.homepage),
            original_language_code=nonempty_string_or_none(parent_series.original_language),
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
            name = (
                nonempty_string_or_none(translation.data.title)
                or nonempty_string_or_none(translation.data.name)
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
