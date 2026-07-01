from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from anishelf_cli.cache.sync import LibraryCacheRefreshResult
from anishelf_cli.library.entries import LibraryEntryModel, validate_library_entry
from anishelf_cli.library.metadata import LibraryEntryMetadata
from anishelf_cli.library.queries import (
    MetadataCompletenessError,
    build_library_list_result,
    build_library_search_result,
    cache_summary_payload,
    require_metadata_ready,
)
from anishelf_cli.models import LibraryListSort, MetadataDepth
from anishelf_cli.models.output import CacheMetadataStatusResult


def test_title_sort_uses_metadata_without_attaching_it_when_metadata_is_none() -> None:
    store = FakeQueryStore(
        [
            _entry("movie:55", "movie", 55),
            _entry("movie:66", "movie", 66),
        ],
        metadata={
            "movie:55": {"name": "Zulu"},
            "movie:66": {"name": "Alien"},
        },
    )

    result = build_library_list_result(
        store,
        metadata_depth=MetadataDepth.NONE,
        cache=cache_summary_payload(store, None),
        watch_status=None,
        hidden=False,
        favorite=False,
        on_display=None,
        sort=LibraryListSort.TITLE,
        limit=None,
    )

    assert [entry.identity for entry in result.entries] == ["movie:66", "movie:55"]
    assert all(entry.metadata is None for entry in result.entries)
    assert store.list_filter_kwargs["limit"] is None
    assert result.model_dump(mode="json")["metadata"] == {
        "requested": "none",
        "attached": False,
        "source": None,
    }


def test_cache_summary_payload_uses_structured_refresh_result() -> None:
    store = FakeQueryStore(
        [_entry("movie:55", "movie", 55)],
        metadata={},
    )

    result = cache_summary_payload(
        store,
        LibraryCacheRefreshResult(
            rebuilt=False,
            pages=2,
            records=3,
            metadata_requested=1,
            metadata_hydrated=1,
            metadata_errors=0,
        ),
    )

    assert result.model_dump(mode="json") == {
        "mode": "updated",
        "updated": True,
        "rebuilt": False,
        "pages": 2,
        "records": 3,
        "metadata_requested": 1,
        "metadata_hydrated": 1,
        "metadata_errors": 0,
        "container": "container",
        "environment": "production",
        "database": "private",
        "zone": "zone",
        "user_record_name": "_user",
    }


def test_metadata_completeness_error_is_typed_and_descriptive() -> None:
    store = FakeQueryStore(
        [_entry("movie:55", "movie", 55)],
        metadata={},
        metadata_ready=False,
    )

    with pytest.raises(MetadataCompletenessError) as exc_info:
        require_metadata_ready(
            store,
            action="search cached library entries by title",
            hint="Run `ani library refresh-meta`.",
        )

    exc = exc_info.value
    assert exc.tracked == 1
    assert exc.hydrated == 0
    assert exc.missing == 1
    assert str(exc) == (
        "Cannot search cached library entries by title because TMDb summary metadata "
        "is incomplete (0/1 hydrated, 1 missing). Run `ani library refresh-meta`."
    )


def test_search_result_attaches_requested_metadata_and_query_payload() -> None:
    store = FakeQueryStore(
        [_entry("movie:55", "movie", 55)],
        metadata={"movie:55": {"name": "Alien"}},
    )

    result = build_library_search_result(
        store,
        title="Alien",
        metadata_depth=MetadataDepth.SUMMARY,
        cache=cache_summary_payload(store, None),
    )

    assert store.search_title == "Alien"
    payload = result.model_dump(mode="json")
    assert payload["query"] == {"title": "Alien"}
    assert payload["metadata"] == {
        "requested": "summary",
        "attached": True,
        "source": "cache",
    }
    entries = payload["entries"]
    assert isinstance(entries, list)
    first_entry = entries[0]
    assert isinstance(first_entry, dict)
    assert first_entry["metadata"] == {"name": "Alien"}


class FakeQueryStore:
    def __init__(
        self,
        entries: list[dict[str, object]],
        *,
        metadata: dict[str, dict[str, object]],
        metadata_ready: bool = True,
    ) -> None:
        self.scope = SimpleNamespace(
            container="container",
            environment="production",
            database="private",
            zone="zone",
            user_record_name="_user",
        )
        self.entries = entries
        self.metadata = metadata
        self.metadata_ready = metadata_ready
        self.list_filter_kwargs: dict[str, Any] = {}
        self.search_title: str | None = None

    def list_entry_models(self, *, include_tombstones: bool = False) -> list[LibraryEntryModel]:
        _ = include_tombstones
        return [validate_library_entry(entry) for entry in self.entries]

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
    ) -> list[LibraryEntryModel]:
        self.list_filter_kwargs = {
            "include_tombstones": include_tombstones,
            "watch_status": watch_status,
            "hidden": hidden,
            "favorite": favorite,
            "on_display": on_display,
            "sort": sort,
            "limit": limit,
        }
        entries = self.entries[:limit] if limit is not None else self.entries
        return [validate_library_entry(entry) for entry in entries]

    def search_entry_models_by_title(self, title: str) -> list[LibraryEntryModel]:
        self.search_title = title
        return [validate_library_entry(entry) for entry in self.entries]

    def metadata_summary_status(self) -> CacheMetadataStatusResult:
        tracked = len(self.entries)
        missing = 0 if self.metadata_ready else tracked
        return CacheMetadataStatusResult(
            tracked_entries=tracked,
            hydrated_entries=tracked - missing,
            missing_entries=missing,
            ready=self.metadata_ready,
        )

    def attach_metadata_summary_models(
        self,
        entries: list[LibraryEntryModel],
    ) -> list[LibraryEntryModel]:
        attached: list[LibraryEntryModel] = []
        for entry in entries:
            attached.append(
                entry.with_metadata(
                    None
                    if str(entry.identity) not in self.metadata
                    else LibraryEntryMetadata.model_validate(self.metadata[str(entry.identity)])
                )
            )
        return attached


def _entry(identity: str, entry_type: str, tmdb_id: int) -> dict[str, object]:
    payload: dict[str, object] = {
        "identity": identity,
        "kind": "snapshot",
        "entry_type": entry_type,
        "tmdb_id": tmdb_id,
        "schema_version": 2,
        "on_display": True,
        "date_saved": "2026-05-01T00:00:00Z",
        "watch_status": "watched",
        "is_date_tracking_enabled": False,
        "favorite": False,
        "notes": "",
        "using_custom_poster": False,
        "episode_progresses": [],
    }
    if entry_type == "season":
        _, parent_series_id, season_number, _ = identity.split(":")
        payload["parent_series_id"] = int(parent_series_id)
        payload["season_number"] = int(season_number)
    return payload
