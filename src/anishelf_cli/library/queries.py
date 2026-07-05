from __future__ import annotations

from typing import Protocol

from anishelf_cli import config
from anishelf_cli.cache.sync import LibraryCacheRefreshResult
from anishelf_cli.models import LibraryListSort, MetadataDepth
from anishelf_cli.models.domain import LibraryEntryMetadata, LibraryEntryModel, LibraryEntrySnapshot
from anishelf_cli.models.output import (
    CacheMetadataStatusResult,
    LibraryEntriesCacheResult,
    LibraryEntriesMetadataResult,
    LibraryEntriesResult,
    LibraryListFiltersResult,
    LibrarySearchQueryResult,
)


class LibraryQueryScope(Protocol):
    @property
    def container(self) -> str: ...

    @property
    def environment(self) -> str: ...

    @property
    def database(self) -> str: ...

    @property
    def zone(self) -> str: ...

    @property
    def user_record_name(self) -> str: ...


class LibraryQueryStore(Protocol):
    @property
    def scope(self) -> LibraryQueryScope: ...

    def list_entry_models(self, *, include_tombstones: bool = False) -> list[LibraryEntryModel]: ...

    def list_entry_models_filtered(
        self,
        *,
        include_tombstones: bool = False,
        watch_status: str | None = None,
        hidden: bool | None = None,
        favorite: bool | None = None,
        on_display: bool | None = None,
        sort: str = "updated",
        limit: int | None = None,
    ) -> list[LibraryEntryModel]: ...

    def search_entry_models(
        self,
        query: str,
        *,
        metadata_language: str,
    ) -> list[LibraryEntryModel]: ...

    def metadata_summary_status(
        self,
        *,
        language: str,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> CacheMetadataStatusResult: ...

    def attach_metadata_summary_models(
        self,
        entries: list[LibraryEntryModel],
        *,
        language: str,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[LibraryEntryModel]: ...


class MetadataCompletenessError(ValueError):
    action: str
    tracked: int
    hydrated: int
    missing: int
    hint: str

    def __init__(self, action: str, tracked: int, hydrated: int, missing: int, hint: str) -> None:
        self.action = action
        self.tracked = tracked
        self.hydrated = hydrated
        self.missing = missing
        self.hint = hint

    def __str__(self) -> str:
        return (
            f"Cannot {self.action} because TMDb metadata is incomplete "
            f"({self.hydrated}/{self.tracked} hydrated, {self.missing} missing). "
            f"{self.hint}"
        )


def build_library_list_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
    watch_status: str | None,
    show_hidden: bool,
    favorite: bool,
    sort: LibraryListSort,
    limit: int | None,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    live_metadata: bool = False,
) -> LibraryEntriesResult:
    if _sort_requires_summary_metadata(sort):
        require_metadata_ready(
            store,
            action=f"sort library entries by {_sort_label(sort)}",
            hint="Run `ani lib refresh-meta` after configuring a TMDb API key.",
            metadata_language=metadata_language,
            metadata_depth=MetadataDepth.SUMMARY,
        )
    if not live_metadata and metadata_depth in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        require_metadata_ready(
            store,
            action=f"attach {metadata_depth.value} metadata",
            hint=(
                f"Set hydration depth with `ani config set-defaults --hydration-depth "
                f"{metadata_depth.value}`, then run `ani lib refresh-meta`."
            ),
            metadata_language=metadata_language,
            metadata_depth=metadata_depth,
        )
    entries = store.list_entry_models_filtered(
        include_tombstones=False,
        watch_status=watch_status,
        hidden=None,
        favorite=True if favorite else None,
        on_display=None if show_hidden else True,
        sort=sort.value,
        limit=None if _sort_requires_postfetch_sort(sort) else limit,
    )
    sort_entries = attach_metadata_for_depth(
        store,
        entries,
        metadata_depth,
        metadata_language=metadata_language,
    )
    if _sort_requires_summary_metadata(sort) and metadata_depth is MetadataDepth.NONE:
        sort_entries = store.attach_metadata_summary_models(
            entries,
            language=metadata_language,
        )
    entries = sort_entries_for_list(sort_entries, sort)
    if _sort_requires_summary_metadata(sort) and metadata_depth is MetadataDepth.NONE:
        entries = strip_entry_metadata(entries)
    if _sort_requires_postfetch_sort(sort) and limit is not None:
        entries = entries[:limit]
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache,
        metadata=metadata_payload(metadata_depth),
        filters=library_list_filters_payload(
            watch_status=watch_status,
            show_hidden=show_hidden,
            favorite=favorite,
            sort=sort,
            limit=limit,
        ),
    )


def build_library_search_result(
    store: LibraryQueryStore,
    *,
    query: str,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
    show_hidden: bool,
    limit: int | None = None,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    live_metadata: bool = False,
) -> LibraryEntriesResult:
    require_metadata_ready(
        store,
        action="search cached library entries",
        hint="Run `ani lib refresh-meta` after configuring a TMDb API key.",
        metadata_language=metadata_language,
        metadata_depth=MetadataDepth.SUMMARY,
    )
    if not live_metadata and metadata_depth in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        require_metadata_ready(
            store,
            action=f"attach {metadata_depth.value} metadata",
            hint=(
                f"Set hydration depth with `ani config set-defaults --hydration-depth "
                f"{metadata_depth.value}`, then run `ani lib refresh-meta`."
            ),
            metadata_language=metadata_language,
            metadata_depth=metadata_depth,
        )
    entries = store.search_entry_models(query, metadata_language=metadata_language)
    if not show_hidden:
        entries = _visible_snapshots(entries)
    if limit is not None:
        entries = entries[:limit]
    entries = attach_metadata_for_depth(
        store,
        entries,
        metadata_depth,
        metadata_language=metadata_language,
    )
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache,
        metadata=metadata_payload(metadata_depth),
        query=LibrarySearchQueryResult(text=query, limit=limit),
    )


def build_library_export_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
    show_hidden: bool,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    live_metadata: bool = False,
) -> LibraryEntriesResult:
    entries = store.list_entry_models(include_tombstones=False)
    if not show_hidden:
        entries = _visible_snapshots(entries)
    if not live_metadata and metadata_depth in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        require_metadata_ready(
            store,
            action=f"attach {metadata_depth.value} metadata",
            hint=(
                f"Set hydration depth with `ani config set-defaults --hydration-depth "
                f"{metadata_depth.value}`, then run `ani lib refresh-meta`."
            ),
            metadata_language=metadata_language,
            metadata_depth=metadata_depth,
        )
    entries = attach_metadata_for_depth(
        store,
        entries,
        metadata_depth,
        metadata_language=metadata_language,
    )
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache,
        metadata=metadata_payload(metadata_depth),
    )


def cache_summary_payload(
    store: LibraryQueryStore,
    refresh_result: LibraryCacheRefreshResult | None,
) -> LibraryEntriesCacheResult:
    scope = store.scope
    if refresh_result is not None:
        return refresh_result.cache_result(scope)
    return LibraryEntriesCacheResult(
        mode="cached",
        updated=False,
        container=scope.container,
        environment=scope.environment,
        database=scope.database,
        zone=scope.zone,
        user_record_name=scope.user_record_name,
    )


def library_entries_payload(
    entries: list[LibraryEntryModel],
    store: LibraryQueryStore,
    refresh_result: LibraryCacheRefreshResult | None,
) -> dict[str, object]:
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache_summary_payload(store, refresh_result),
    ).model_dump(mode="json")


def attach_metadata_for_depth(
    store: LibraryQueryStore,
    entries: list[LibraryEntryModel],
    metadata_depth: MetadataDepth,
    *,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
) -> list[LibraryEntryModel]:
    if metadata_depth is MetadataDepth.NONE:
        return entries
    return store.attach_metadata_summary_models(
        entries,
        language=metadata_language,
        depth=metadata_depth,
    )


def require_metadata_ready(
    store: LibraryQueryStore,
    *,
    action: str,
    hint: str,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    metadata_depth: MetadataDepth = MetadataDepth.SUMMARY,
) -> None:
    status = store.metadata_summary_status(language=metadata_language, depth=metadata_depth)
    if status.ready:
        return
    raise MetadataCompletenessError(
        action=action,
        tracked=status.tracked_entries,
        hydrated=status.hydrated_entries,
        missing=status.missing_entries,
        hint=hint,
    )


def metadata_payload(metadata_depth: MetadataDepth) -> LibraryEntriesMetadataResult:
    return LibraryEntriesMetadataResult(
        requested=metadata_depth.value,
        attached=metadata_depth is not MetadataDepth.NONE,
        source="cache" if metadata_depth is not MetadataDepth.NONE else None,
    )


def library_list_filters_payload(
    *,
    watch_status: str | None,
    favorite: bool,
    show_hidden: bool,
    sort: LibraryListSort,
    limit: int | None,
) -> LibraryListFiltersResult:
    return LibraryListFiltersResult(
        watch_status=watch_status,
        show_hidden=show_hidden,
        favorite=favorite,
        sort=sort.value,
        limit=limit,
    )


def sort_entries_for_list(
    entries: list[LibraryEntryModel],
    sort: LibraryListSort,
) -> list[LibraryEntryModel]:
    if sort is LibraryListSort.TITLE:
        return sorted(
            entries,
            key=lambda entry: (
                entry.title.lower(),
                entry.identity,
            ),
        )
    if sort is LibraryListSort.AIR_DATE:
        entries_by_identity = sorted(entries, key=lambda entry: entry.identity)
        return sorted(
            entries_by_identity,
            key=lambda entry: _air_date_sort_value(entry) or "",
            reverse=True,
        )
    return entries


def _sort_requires_summary_metadata(sort: LibraryListSort) -> bool:
    return sort in {LibraryListSort.TITLE, LibraryListSort.AIR_DATE}


def _sort_requires_postfetch_sort(sort: LibraryListSort) -> bool:
    return sort in {LibraryListSort.TITLE, LibraryListSort.AIR_DATE}


def _sort_label(sort: LibraryListSort) -> str:
    if sort is LibraryListSort.AIR_DATE:
        return "air date"
    return sort.value.replace("-", " ")


def _air_date_sort_value(entry: LibraryEntryModel) -> str | None:
    metadata = getattr(entry, "metadata", None)
    if not isinstance(metadata, LibraryEntryMetadata):
        return None
    if metadata.on_air_date is not None:
        return metadata.on_air_date
    return metadata.release_date or metadata.first_air_date


def strip_entry_metadata(entries: list[LibraryEntryModel]) -> list[LibraryEntryModel]:
    return [entry.without_metadata() for entry in entries]


def _visible_snapshots(entries: list[LibraryEntryModel]) -> list[LibraryEntryModel]:
    return [
        entry for entry in entries if isinstance(entry, LibraryEntrySnapshot) and entry.on_display
    ]
