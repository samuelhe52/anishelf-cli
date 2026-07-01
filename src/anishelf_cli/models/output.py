from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_serializer, model_serializer

from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import LibraryEntryModel
from anishelf_cli.models.transport.tmdb import TMDbTitleSearchMatch


class LibraryGetItemError(AniShelfBaseModel):
    code: str
    message: str


class LibraryGetItemFound(AniShelfBaseModel):
    identity: str
    status: Literal["found"] = "found"
    entry: LibraryEntryModel

    @field_serializer("entry", when_used="json")
    def _serialize_entry(self, entry: LibraryEntryModel) -> dict[str, object]:
        return entry.output_payload()


class LibraryGetItemErrorResult(AniShelfBaseModel):
    identity: str
    status: Literal["error"] = "error"
    error: LibraryGetItemError


LibraryGetItem = Annotated[
    LibraryGetItemFound | LibraryGetItemErrorResult,
    Field(discriminator="status"),
]


class LibraryGetSummary(AniShelfBaseModel):
    requested: int
    found: int
    errors: int


class LibraryGetEnvelope(AniShelfBaseModel):
    items: tuple[LibraryGetItem, ...]
    summary: LibraryGetSummary


class LibraryEntriesCacheResult(AniShelfBaseModel):
    mode: str
    updated: bool
    rebuilt: bool | None = None
    pages: int | None = None
    records: int | None = None
    metadata_requested: int | None = None
    metadata_hydrated: int | None = None
    metadata_errors: int | None = None
    container: str
    environment: str
    database: str
    zone: str
    user_record_name: str


class LibraryCacheUpdateSummaryResult(AniShelfBaseModel):
    cache: LibraryEntriesCacheResult


class LibraryCacheUpdateResult(AniShelfBaseModel):
    summary: LibraryCacheUpdateSummaryResult


class LibraryEntriesMetadataResult(AniShelfBaseModel):
    requested: str
    attached: bool
    source: str | None = None


class LibraryListFiltersResult(AniShelfBaseModel):
    watch_status: str | None = None
    hidden: bool
    favorite: bool
    on_display: bool | None = None
    sort: str
    limit: int | None = None


class LibrarySearchQueryResult(AniShelfBaseModel):
    title: str


class _LibraryEntriesSummaryResult(AniShelfBaseModel):
    entries: int
    cache: LibraryEntriesCacheResult


class _LibraryEntriesEnvelope(AniShelfBaseModel):
    summary: _LibraryEntriesSummaryResult
    entries: tuple[LibraryEntryModel, ...]
    metadata: LibraryEntriesMetadataResult | None = None
    filters: LibraryListFiltersResult | None = None
    query: LibrarySearchQueryResult | None = None

    @field_serializer("entries", when_used="json")
    def _serialize_entries(self, entries: tuple[LibraryEntryModel, ...]) -> list[dict[str, object]]:
        return [entry.output_payload() for entry in entries]


class LibraryEntriesResult(AniShelfBaseModel):
    entries: tuple[LibraryEntryModel, ...]
    cache: LibraryEntriesCacheResult
    metadata: LibraryEntriesMetadataResult | None = None
    filters: LibraryListFiltersResult | None = None
    query: LibrarySearchQueryResult | None = None

    @model_serializer(mode="plain", when_used="json")
    def _serialize(self) -> dict[str, object]:
        payload = _LibraryEntriesEnvelope(
            summary=_LibraryEntriesSummaryResult(entries=len(self.entries), cache=self.cache),
            entries=self.entries,
            metadata=self.metadata,
            filters=self.filters,
            query=self.query,
        ).model_dump(mode="json")
        if self.metadata is None:
            payload.pop("metadata", None)
        if self.filters is None:
            payload.pop("filters", None)
        if self.query is None:
            payload.pop("query", None)
        return payload


class CacheScopeResult(AniShelfBaseModel):
    container: str
    environment: str
    database: str
    zone: str
    user_record_name: str


class CacheMetadataStatusResult(AniShelfBaseModel):
    tracked_entries: int
    hydrated_entries: int
    missing_entries: int
    ready: bool


class CacheActiveResult(AniShelfBaseModel):
    initialized: bool
    entries: int
    has_sync_token: bool
    scope: CacheScopeResult | None = None
    metadata: CacheMetadataStatusResult


class CacheStatusSummaryResult(AniShelfBaseModel):
    initialized: bool
    scope_count: int
    cache_files: int
    lock_files: int


class CachePathsResult(AniShelfBaseModel):
    path: str
    lock_path: str


class _CacheStatusEnvelope(AniShelfBaseModel):
    summary: CacheStatusSummaryResult
    active: CacheActiveResult
    scopes: tuple[CacheScopeResult, ...]
    cache: CachePathsResult


class CacheStatusResult(AniShelfBaseModel):
    initialized: bool
    active: CacheActiveResult
    scopes: tuple[CacheScopeResult, ...]
    cache_path: str
    lock_path: str
    cache_files: int
    lock_files: int

    @model_serializer(mode="plain", when_used="json")
    def _serialize(self) -> dict[str, object]:
        return _CacheStatusEnvelope(
            summary=CacheStatusSummaryResult(
                initialized=self.initialized,
                scope_count=len(self.scopes),
                cache_files=self.cache_files,
                lock_files=self.lock_files,
            ),
            active=self.active,
            scopes=self.scopes,
            cache=CachePathsResult(
                path=self.cache_path,
                lock_path=self.lock_path,
            ),
        ).model_dump(mode="json")


class RemovedCacheFilesResult(AniShelfBaseModel):
    cache_files: int
    lock_files: int


class ClearedCachePathsResult(AniShelfBaseModel):
    cache_dir: str
    lock_dir: str


class LibraryClearCacheResult(AniShelfBaseModel):
    status: Literal["cleared"]
    removed: RemovedCacheFilesResult
    paths: ClearedCachePathsResult


class MetadataHydrationSummaryResult(AniShelfBaseModel):
    requested: int
    hydrated: int
    errors: int


class LibraryRefreshMetadataCacheResult(AniShelfBaseModel):
    container: str
    environment: str
    database: str
    zone: str
    user_record_name: str


class LibraryRefreshMetadataSummaryResult(AniShelfBaseModel):
    entries: int
    metadata: MetadataHydrationSummaryResult
    cache: LibraryRefreshMetadataCacheResult


class LibraryRefreshMetadataResult(AniShelfBaseModel):
    summary: LibraryRefreshMetadataSummaryResult


class TMDbSearchQueryResult(AniShelfBaseModel):
    mode: str
    type: str
    title: str | None = None
    year: int | None = None


class TMDbSearchSummaryResult(AniShelfBaseModel):
    movies: int
    series: int
    total: int


class TMDbSearchMatchResult(AniShelfBaseModel):
    entry_type: str
    tmdb_id: int
    title: str | None = None
    original_title: str | None = None
    release_date: str | None = None
    original_language_code: str | None = None
    overview: str | None = None
    poster_path: str | None = None
    details_url: str | None = None

    @classmethod
    def from_match(cls, match: TMDbTitleSearchMatch) -> TMDbSearchMatchResult:
        return cls.model_validate(match.model_dump(mode="python"))


class TMDbSearchResultsResult(AniShelfBaseModel):
    movies: tuple[TMDbSearchMatchResult, ...]
    series: tuple[TMDbSearchMatchResult, ...]


class TMDbSearchOutputResult(AniShelfBaseModel):
    query: TMDbSearchQueryResult
    summary: TMDbSearchSummaryResult
    results: TMDbSearchResultsResult
