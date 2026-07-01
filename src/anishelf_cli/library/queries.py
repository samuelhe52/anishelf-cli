from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from anishelf_cli.cache.sync import LibraryCacheRefreshResult
from anishelf_cli.library.entries import LibraryEntry
from anishelf_cli.models import LibraryListSort, MetadataDepth


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

    def list_entry_models(self, *, include_tombstones: bool = False) -> list[LibraryEntry]: ...

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
    ) -> list[LibraryEntry]: ...

    def search_entry_models_by_title(self, title: str) -> list[LibraryEntry]: ...

    def metadata_summary_status(self) -> dict[str, int | bool]: ...

    def attach_metadata_summary_models(
        self,
        entries: list[LibraryEntry],
    ) -> list[LibraryEntry]: ...


@dataclass(frozen=True, slots=True)
class MetadataCompletenessError(ValueError):
    action: str
    tracked: int
    hydrated: int
    missing: int
    hint: str

    def __str__(self) -> str:
        return (
            f"Cannot {self.action} because TMDb summary metadata is incomplete "
            f"({self.hydrated}/{self.tracked} hydrated, {self.missing} missing). "
            f"{self.hint}"
        )


@dataclass(frozen=True, slots=True)
class LibraryEntriesResult:
    entries: list[LibraryEntry]
    cache: dict[str, object]
    metadata: dict[str, object] | None = None
    filters: dict[str, object] | None = None
    query: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "summary": {
                "entries": len(self.entries),
                "cache": self.cache,
            },
            "entries": [entry.to_payload() for entry in self.entries],
        }
        if self.metadata is not None:
            payload["metadata"] = self.metadata
        if self.filters is not None:
            payload["filters"] = self.filters
        if self.query is not None:
            payload["query"] = self.query
        return payload


def build_library_list_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: dict[str, object],
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
        entries=entries,
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
    cache: dict[str, object],
) -> LibraryEntriesResult:
    require_metadata_ready(
        store,
        action="search cached library entries by title",
        hint="Run `ani library refresh-meta` after configuring a TMDb API key.",
    )
    entries = store.search_entry_models_by_title(title)
    entries = attach_metadata_for_depth(store, entries, metadata_depth)
    return LibraryEntriesResult(
        entries=entries,
        cache=cache,
        metadata=metadata_payload(metadata_depth),
        query={"title": title},
    )


def build_library_export_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: dict[str, object],
) -> LibraryEntriesResult:
    entries = store.list_entry_models(include_tombstones=False)
    entries = attach_metadata_for_depth(store, entries, metadata_depth)
    return LibraryEntriesResult(
        entries=entries,
        cache=cache,
        metadata=metadata_payload(metadata_depth),
    )


def cache_summary_payload(
    store: LibraryQueryStore,
    refresh_result: LibraryCacheRefreshResult | None,
) -> dict[str, object]:
    scope = store.scope
    return {
        "mode": "cached" if refresh_result is None else "updated",
        "updated": refresh_result is not None,
        "rebuilt": None if refresh_result is None else refresh_result.rebuilt,
        "pages": None if refresh_result is None else refresh_result.pages,
        "records": None if refresh_result is None else refresh_result.records,
        "metadata_requested": None if refresh_result is None else refresh_result.metadata_requested,
        "metadata_hydrated": None if refresh_result is None else refresh_result.metadata_hydrated,
        "metadata_errors": None if refresh_result is None else refresh_result.metadata_errors,
        "container": scope.container,
        "environment": scope.environment,
        "database": scope.database,
        "zone": scope.zone,
        "user_record_name": scope.user_record_name,
    }


def library_entries_payload(
    entries: list[LibraryEntry],
    store: LibraryQueryStore,
    refresh_result: LibraryCacheRefreshResult | None,
) -> dict[str, object]:
    return LibraryEntriesResult(
        entries=entries,
        cache=cache_summary_payload(store, refresh_result),
    ).to_payload()


def attach_metadata_for_depth(
    store: LibraryQueryStore,
    entries: list[LibraryEntry],
    metadata_depth: MetadataDepth,
) -> list[LibraryEntry]:
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
    if bool(status.get("ready")):
        return
    tracked = int(status.get("tracked_entries", 0))
    hydrated = int(status.get("hydrated_entries", 0))
    missing = int(status.get("missing_entries", 0))
    raise MetadataCompletenessError(
        action=action,
        tracked=tracked,
        hydrated=hydrated,
        missing=missing,
        hint=hint,
    )


def metadata_payload(metadata_depth: MetadataDepth) -> dict[str, object]:
    return {
        "requested": metadata_depth.value,
        "attached": metadata_depth is not MetadataDepth.NONE,
        "source": "cache" if metadata_depth is not MetadataDepth.NONE else None,
    }


def library_list_filters_payload(
    *,
    watch_status: str | None,
    hidden: bool,
    favorite: bool,
    on_display: bool | None,
    sort: LibraryListSort,
    limit: int | None,
) -> dict[str, object]:
    return {
        "watch_status": watch_status,
        "hidden": hidden,
        "favorite": favorite,
        "on_display": on_display,
        "sort": sort.value,
        "limit": limit,
    }


def sort_entries_by_title(
    entries: list[LibraryEntry],
    sort: LibraryListSort,
) -> list[LibraryEntry]:
    if sort is not LibraryListSort.TITLE:
        return entries
    return sorted(
        entries,
        key=lambda entry: (
            entry.title.lower(),
            entry.identity,
        ),
    )


def strip_entry_metadata(entries: list[LibraryEntry]) -> list[LibraryEntry]:
    return [entry.without_metadata() for entry in entries]
