from __future__ import annotations

from typing import Protocol

from anishelf_cli.cache.sync import LibraryCacheRefreshResult
from anishelf_cli.models import LibraryListSort, MetadataDepth
from anishelf_cli.models.domain import LibraryEntryModel
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
        sort: str = "saved",
        limit: int | None = None,
    ) -> list[LibraryEntryModel]: ...

    def search_entry_models_by_title(self, title: str) -> list[LibraryEntryModel]: ...

    def metadata_summary_status(self) -> CacheMetadataStatusResult: ...

    def attach_metadata_summary_models(
        self,
        entries: list[LibraryEntryModel],
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
            f"Cannot {self.action} because TMDb summary metadata is incomplete "
            f"({self.hydrated}/{self.tracked} hydrated, {self.missing} missing). "
            f"{self.hint}"
        )


def build_library_list_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
    watch_status: str | None,
    hidden: bool,
    favorite: bool,
    on_display: bool | None,
    sort: LibraryListSort,
    limit: int | None,
) -> LibraryEntriesResult:
    if sort is LibraryListSort.TITLE:
        require_metadata_ready(
            store,
            action="sort library entries by title",
            hint="Run `ani library refresh-meta` after configuring a TMDb API key.",
        )
    entries = store.list_entry_models_filtered(
        include_tombstones=False,
        watch_status=watch_status,
        hidden=True if hidden else None,
        favorite=True if favorite else None,
        on_display=on_display,
        sort=sort.value,
        limit=None if sort is LibraryListSort.TITLE else limit,
    )
    sort_entries = attach_metadata_for_depth(store, entries, metadata_depth)
    if sort is LibraryListSort.TITLE and metadata_depth is MetadataDepth.NONE:
        sort_entries = store.attach_metadata_summary_models(entries)
    entries = sort_entries_by_title(sort_entries, sort)
    if sort is LibraryListSort.TITLE and metadata_depth is MetadataDepth.NONE:
        entries = strip_entry_metadata(entries)
    if sort is LibraryListSort.TITLE and limit is not None:
        entries = entries[:limit]
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache,
        metadata=metadata_payload(metadata_depth),
        filters=library_list_filters_payload(
            watch_status=watch_status,
            hidden=hidden,
            favorite=favorite,
            on_display=on_display,
            sort=sort,
            limit=limit,
        ),
    )


def build_library_search_result(
    store: LibraryQueryStore,
    *,
    title: str,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
) -> LibraryEntriesResult:
    require_metadata_ready(
        store,
        action="search cached library entries by title",
        hint="Run `ani library refresh-meta` after configuring a TMDb API key.",
    )
    entries = store.search_entry_models_by_title(title)
    entries = attach_metadata_for_depth(store, entries, metadata_depth)
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache,
        metadata=metadata_payload(metadata_depth),
        query=LibrarySearchQueryResult(title=title),
    )


def build_library_export_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
) -> LibraryEntriesResult:
    entries = store.list_entry_models(include_tombstones=False)
    entries = attach_metadata_for_depth(store, entries, metadata_depth)
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
) -> list[LibraryEntryModel]:
    if metadata_depth is MetadataDepth.NONE:
        return entries
    return store.attach_metadata_summary_models(entries)


def require_metadata_ready(
    store: LibraryQueryStore,
    *,
    action: str,
    hint: str,
) -> None:
    status = store.metadata_summary_status()
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
    hidden: bool,
    favorite: bool,
    on_display: bool | None,
    sort: LibraryListSort,
    limit: int | None,
) -> LibraryListFiltersResult:
    return LibraryListFiltersResult(
        watch_status=watch_status,
        hidden=hidden,
        favorite=favorite,
        on_display=on_display,
        sort=sort.value,
        limit=limit,
    )


def sort_entries_by_title(
    entries: list[LibraryEntryModel],
    sort: LibraryListSort,
) -> list[LibraryEntryModel]:
    if sort is not LibraryListSort.TITLE:
        return entries
    return sorted(
        entries,
        key=lambda entry: (
            entry.title.lower(),
            entry.identity,
        ),
    )


def strip_entry_metadata(entries: list[LibraryEntryModel]) -> list[LibraryEntryModel]:
    return [entry.without_metadata() for entry in entries]
