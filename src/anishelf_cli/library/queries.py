from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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

METADATA_SUMMARY_HINT = (
    "Run `ani lib sync` to retry missing metadata, or `ani lib refresh-meta` after "
    "configuring a TMDb API key."
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
        watch_statuses: Sequence[str] | None = None,
        entry_types: Sequence[str] | None = None,
        hidden: bool | None = None,
        favorite: bool | None = None,
        on_display: bool | None = None,
        sort: str = "updated",
        reverse: bool = False,
        limit: int | None = None,
    ) -> list[LibraryEntryModel]: ...

    def search_entry_models(
        self,
        query: str,
        *,
        metadata_language: str,
    ) -> list[LibraryEntryModel]: ...

    def metadata_status_for_entries(
        self,
        entries: list[LibraryEntryModel],
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


ATTACH_DEPTH_HINT = (
    "Set hydration depth with `ani config set-defaults --hydration-depth {depth}`, "
    "then run `ani lib refresh-meta`."
)


def _depth_label(depth: MetadataDepth) -> str:
    return "" if depth is MetadataDepth.SUMMARY else f"{depth.value} "


class MetadataCompletenessError(ValueError):
    action: str
    tracked: int
    hydrated: int
    missing: int
    depth: MetadataDepth
    hint: str

    def __init__(
        self,
        action: str,
        tracked: int,
        hydrated: int,
        missing: int,
        hint: str,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> None:
        self.action = action
        self.tracked = tracked
        self.hydrated = hydrated
        self.missing = missing
        self.depth = depth
        self.hint = hint

    def __str__(self) -> str:
        return (
            f"Cannot {self.action} because no entry has current cached TMDb "
            f"{_depth_label(self.depth)}metadata ({self.hydrated}/{self.tracked} hydrated). "
            f"{self.hint}"
        )


@dataclass(frozen=True, slots=True)
class MetadataCoverageGap:
    tracked: int
    missing: int
    depth: MetadataDepth
    hint: str

    def warning(self) -> str:
        return (
            f"Results may be incomplete: current cached TMDb {_depth_label(self.depth)}metadata "
            f"is missing for {self.missing} of {self.tracked} entries. {self.hint}"
        )


def _metadata_missing(gaps: list[MetadataCoverageGap | None]) -> int | None:
    # None means the command did not depend on metadata coverage at all.
    if not gaps:
        return None
    return max((gap.missing for gap in gaps if gap is not None), default=0)


def _coverage_warnings(gaps: list[MetadataCoverageGap | None]) -> tuple[str, ...]:
    # When several checks find gaps, the largest one (the deepest depth) subsumes the
    # others, so report it once instead of stacking near-duplicate warnings.
    present = [gap for gap in gaps if gap is not None]
    if not present:
        return ()
    return (max(present, key=lambda gap: gap.missing).warning(),)


def _attach_coverage_gap(
    store: LibraryQueryStore,
    entries: list[LibraryEntryModel],
    *,
    metadata_depth: MetadataDepth,
    metadata_language: str,
    live_metadata: bool,
) -> list[MetadataCoverageGap | None]:
    if live_metadata or metadata_depth not in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        return []
    return [
        check_metadata_coverage(
            store,
            entries,
            action=f"attach {metadata_depth.value} metadata",
            hint=ATTACH_DEPTH_HINT.format(depth=metadata_depth.value),
            metadata_language=metadata_language,
            metadata_depth=metadata_depth,
        )
    ]


def build_library_list_result(
    store: LibraryQueryStore,
    *,
    metadata_depth: MetadataDepth,
    cache: LibraryEntriesCacheResult,
    watch_statuses: Sequence[str] = (),
    entry_types: Sequence[str] = (),
    show_hidden: bool,
    favorite: bool,
    sort: LibraryListSort,
    reverse: bool = False,
    limit: int | None,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    live_metadata: bool = False,
) -> LibraryEntriesResult:
    gaps: list[MetadataCoverageGap | None] = []
    postfetch_limit = _sort_requires_postfetch_sort(sort)
    entries = store.list_entry_models_filtered(
        include_tombstones=False,
        watch_statuses=tuple(watch_statuses) or None,
        entry_types=tuple(entry_types) or None,
        hidden=None,
        favorite=True if favorite else None,
        on_display=None if show_hidden else True,
        sort=sort.value,
        reverse=reverse,
        limit=None if postfetch_limit else limit,
    )
    if _sort_requires_summary_metadata(sort):
        gaps.append(
            check_metadata_coverage(
                store,
                entries,
                action=f"sort library entries by {_sort_label(sort)}",
                hint=METADATA_SUMMARY_HINT,
                metadata_language=metadata_language,
                metadata_depth=MetadataDepth.SUMMARY,
            )
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
    entries = sort_entries_for_list(sort_entries, sort, reverse=reverse)
    if _sort_requires_summary_metadata(sort) and metadata_depth is MetadataDepth.NONE:
        entries = strip_entry_metadata(entries)
    if postfetch_limit and limit is not None:
        entries = entries[:limit]
    gaps.extend(
        _attach_coverage_gap(
            store,
            entries,
            metadata_depth=metadata_depth,
            metadata_language=metadata_language,
            live_metadata=live_metadata,
        )
    )
    return LibraryEntriesResult(
        entries=tuple(entries),
        cache=cache,
        metadata=metadata_payload(metadata_depth),
        metadata_missing=_metadata_missing(gaps),
        warnings=_coverage_warnings(gaps),
        filters=library_list_filters_payload(
            watch_statuses=watch_statuses,
            entry_types=entry_types,
            show_hidden=show_hidden,
            favorite=favorite,
            sort=sort,
            reverse=reverse,
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
    # Coverage is measured over every entry the query could have matched.
    searchable = store.list_entry_models(include_tombstones=False)
    if not show_hidden:
        searchable = _visible_snapshots(searchable)
    gaps: list[MetadataCoverageGap | None] = [
        check_metadata_coverage(
            store,
            searchable,
            action="search cached library entries",
            hint=METADATA_SUMMARY_HINT,
            metadata_language=metadata_language,
            metadata_depth=MetadataDepth.SUMMARY,
        )
    ]
    entries = store.search_entry_models(query, metadata_language=metadata_language)
    if not show_hidden:
        entries = _visible_snapshots(entries)
    if limit is not None:
        entries = entries[:limit]
    gaps.extend(
        _attach_coverage_gap(
            store,
            entries,
            metadata_depth=metadata_depth,
            metadata_language=metadata_language,
            live_metadata=live_metadata,
        )
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
        query=LibrarySearchQueryResult(text=query, limit=limit),
        metadata_missing=_metadata_missing(gaps),
        warnings=_coverage_warnings(gaps),
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
    gaps = _attach_coverage_gap(
        store,
        entries,
        metadata_depth=metadata_depth,
        metadata_language=metadata_language,
        live_metadata=live_metadata,
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
        metadata_missing=_metadata_missing(gaps),
        warnings=_coverage_warnings(gaps),
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


def check_metadata_coverage(
    store: LibraryQueryStore,
    entries: list[LibraryEntryModel],
    *,
    action: str,
    hint: str,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    metadata_depth: MetadataDepth = MetadataDepth.SUMMARY,
) -> MetadataCoverageGap | None:
    """Fail when none of `entries` has the metadata `action` needs; report partial gaps.

    A few entries can stay unhydrated for good (for example a TMDb id that was
    removed upstream), so partial coverage must not block the whole command. Only
    the entries the command actually considers count, so a hidden or filtered-out
    entry without metadata does not warn on every read.
    """
    status = store.metadata_status_for_entries(
        entries,
        language=metadata_language,
        depth=metadata_depth,
    )
    if status.ready:
        return None
    if status.hydrated_entries == 0:
        raise MetadataCompletenessError(
            action=action,
            tracked=status.tracked_entries,
            hydrated=status.hydrated_entries,
            missing=status.missing_entries,
            hint=hint,
            depth=metadata_depth,
        )
    return MetadataCoverageGap(
        tracked=status.tracked_entries,
        missing=status.missing_entries,
        depth=metadata_depth,
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
    watch_statuses: Sequence[str] = (),
    entry_types: Sequence[str] = (),
    favorite: bool,
    show_hidden: bool,
    sort: LibraryListSort,
    reverse: bool = False,
    limit: int | None,
) -> LibraryListFiltersResult:
    return LibraryListFiltersResult(
        watch_status=tuple(watch_statuses) or None,
        entry_type=tuple(entry_types) or None,
        show_hidden=show_hidden,
        favorite=favorite,
        sort=sort.value,
        reverse=reverse,
        limit=limit,
    )


def sort_entries_for_list(
    entries: list[LibraryEntryModel],
    sort: LibraryListSort,
    *,
    reverse: bool = False,
) -> list[LibraryEntryModel]:
    """Order metadata-backed sorts; entries lacking the sort value always go last."""
    if sort is LibraryListSort.TITLE:
        titled = [entry for entry in entries if entry.metadata_title is not None]
        untitled = [entry for entry in entries if entry.metadata_title is None]
        return sorted(
            titled,
            key=lambda entry: (entry.title.lower(), entry.identity),
            reverse=reverse,
        ) + sorted(untitled, key=lambda entry: entry.identity, reverse=reverse)
    if sort is LibraryListSort.AIR_DATE:
        dated = [entry for entry in entries if _air_date_sort_value(entry)]
        undated = [entry for entry in entries if not _air_date_sort_value(entry)]
        # Newest first, ties by id; reversing flips both.
        ordered = sorted(
            sorted(dated, key=lambda entry: entry.identity),
            key=lambda entry: _air_date_sort_value(entry) or "",
            reverse=True,
        )
        if reverse:
            ordered.reverse()
        return ordered + sorted(undated, key=lambda entry: entry.identity, reverse=reverse)
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
