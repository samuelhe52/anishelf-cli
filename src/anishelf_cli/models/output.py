from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_serializer

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


class LibraryEntriesResult(AniShelfBaseModel):
    entries: tuple[LibraryEntryModel, ...]
    cache: LibraryEntriesCacheResult
    metadata: LibraryEntriesMetadataResult | None = None
    filters: LibraryListFiltersResult | None = None
    query: LibrarySearchQueryResult | None = None

    @model_serializer(mode="wrap", when_used="json")
    def _serialize(self, handler) -> dict[str, object]:
        payload = handler(self)
        cache = payload.pop("cache")
        payload["summary"] = {"entries": len(self.entries), "cache": cache}
        if payload.get("metadata") is None:
            payload.pop("metadata", None)
        if payload.get("filters") is None:
            payload.pop("filters", None)
        if payload.get("query") is None:
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


class CacheStatusResult(AniShelfBaseModel):
    initialized: bool
    active: CacheActiveResult
    scopes: tuple[CacheScopeResult, ...]
    cache_path: str
    lock_path: str
    cache_files: int
    lock_files: int

    @model_serializer(mode="wrap", when_used="json")
    def _serialize(self, handler) -> dict[str, object]:
        payload = handler(self)
        return {
            "summary": {
                "initialized": payload["initialized"],
                "scope_count": len(self.scopes),
                "cache_files": payload["cache_files"],
                "lock_files": payload["lock_files"],
            },
            "active": payload["active"],
            "scopes": payload["scopes"],
            "cache": {
                "path": payload["cache_path"],
                "lock_path": payload["lock_path"],
            },
        }


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
        return cls(
            entry_type=match.entry_type,
            tmdb_id=match.tmdb_id,
            title=match.title,
            original_title=match.original_title,
            release_date=match.release_date,
            original_language_code=match.original_language_code,
            overview=match.overview,
            poster_path=match.poster_path,
            details_url=match.details_url,
        )


class TMDbSearchResultsResult(AniShelfBaseModel):
    movies: tuple[TMDbSearchMatchResult, ...]
    series: tuple[TMDbSearchMatchResult, ...]


class TMDbSearchOutputResult(AniShelfBaseModel):
    query: TMDbSearchQueryResult
    summary: TMDbSearchSummaryResult
    results: TMDbSearchResultsResult
