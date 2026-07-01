from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from anishelf_cli.library.queries import (
    MetadataCompletenessError,
    build_library_list_result,
    build_library_search_result,
    require_metadata_ready,
)
from anishelf_cli.models import LibraryListSort, MetadataDepth


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
        cache={"mode": "cached"},
        watch_status=None,
        hidden=False,
        favorite=False,
        on_display=None,
        sort=LibraryListSort.TITLE,
        limit=None,
    )

    assert [entry["identity"] for entry in result.entries] == ["movie:66", "movie:55"]
    assert all("metadata" not in entry for entry in result.entries)
    assert store.list_filter_kwargs["limit"] is None
    assert result.to_payload()["metadata"] == {
        "requested": "none",
        "attached": False,
        "source": None,
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
        cache={"mode": "cached"},
    )

    assert store.search_title == "Alien"
    payload = result.to_payload()
    assert payload["query"] == {"title": "Alien"}
    assert payload["metadata"] == {
        "requested": "summary",
        "attached": True,
        "source": "cache",
    }
    assert payload["entries"][0]["metadata"] == {"name": "Alien"}


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

    def list_entries(self, *, include_tombstones: bool = False) -> list[dict[str, object]]:
        _ = include_tombstones
        return self.entries

    def list_entries_filtered(
        self,
        *,
        include_tombstones: bool = False,
        watch_status: str | None = None,
        hidden: bool | None = None,
        favorite: bool | None = None,
        on_display: bool | None = None,
        sort: str = "saved",
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        self.list_filter_kwargs = {
            "include_tombstones": include_tombstones,
            "watch_status": watch_status,
            "hidden": hidden,
            "favorite": favorite,
            "on_display": on_display,
            "sort": sort,
            "limit": limit,
        }
        return self.entries[:limit] if limit is not None else self.entries

    def search_entries_by_title(self, title: str) -> list[dict[str, object]]:
        self.search_title = title
        return self.entries

    def metadata_summary_status(self) -> dict[str, int | bool]:
        tracked = len(self.entries)
        missing = 0 if self.metadata_ready else tracked
        return {
            "tracked_entries": tracked,
            "hydrated_entries": tracked - missing,
            "missing_entries": missing,
            "ready": self.metadata_ready,
        }

    def attach_metadata_summary(
        self,
        entries: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        attached: list[dict[str, object]] = []
        for entry in entries:
            clone = dict(entry)
            clone["metadata"] = self.metadata.get(str(entry["identity"]))
            attached.append(clone)
        return attached


def _entry(identity: str, entry_type: str, tmdb_id: int) -> dict[str, object]:
    return {
        "identity": identity,
        "kind": "snapshot",
        "entry_type": entry_type,
        "tmdb_id": tmdb_id,
    }
