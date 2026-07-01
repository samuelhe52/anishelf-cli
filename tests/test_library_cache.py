from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from anishelf_cli.cache.records import entry_row_params
from anishelf_cli.cache.scope import LibraryCacheScope
from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cache.sync import LibraryCacheSync
from anishelf_cli.cli import library_commands
from anishelf_cli.cli.root import app
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken
from anishelf_cli.cloudkit.executor import (
    CloudKitChangeTokenExpiredError,
    CloudKitExecutor,
    ZoneChangesPage,
)
from anishelf_cli.library import LibraryRecordDecodeError
from anishelf_cli.library.entries import LibraryEntryModel, validate_library_entry
from anishelf_cli.library.metadata import LibraryEntryMetadata
from anishelf_cli.models.output import CacheMetadataStatusResult
from anishelf_cli.models.transport.cloudkit import (
    CloudKitLibraryEntrySnapshotFields,
    CloudKitZoneChangesResponse,
)
from anishelf_cli.secrets import SecretStorageUnavailableError
from anishelf_cli.tmdb.client import TMDbRequestError, TMDbSummaryIdentity
from anishelf_cli.tmdb.tokens import TMDbAPIToken
from tests.support import (
    cloudkit_record,
    create_cache_store,
    create_seeded_cache_store,
    live_record,
    null_lock,
    runner,
    tombstone_record,
)
from tests.support import (
    insert_legacy_v1_metadata_summary as _insert_legacy_v1_metadata_summary,
)
from tests.support import (
    isolate_paths as _isolate_paths,
)
from tests.support import (
    metadata_summary as _metadata_summary,
)
from tests.support import (
    snapshot_entry_payload as _snapshot_entry_payload,
)
from tests.support import (
    store_with_cloudkit_token as _store_with_cloudkit_token,
)


def test_cloudkit_record_types_nested_cloudkit_metadata() -> None:
    payload = {
        "recordID": {
            "recordName": "movie:55",
            "zoneID": {
                "zoneName": "AniShelfLibrary",
                "ownerRecordName": "_defaultOwner",
            },
        },
        "recordType": "LibraryEntry",
        "created": {
            "timestamp": 1_779_000_000,
            "userRecordName": "_creator",
            "deviceID": "device-a",
        },
        "modified": {
            "timestamp": 1_780_000_000,
            "userRecordName": "_editor",
            "deviceID": "device-b",
        },
        "fields": {},
    }

    record = cloudkit_record(payload)

    assert record.effective_record_name == "movie:55"
    assert record.record_id is not None
    assert record.record_id.zone_id is not None
    assert record.record_id.zone_id.zone_name == "AniShelfLibrary"
    assert record.record_id.zone_id.owner_record_name == "_defaultOwner"
    assert record.created is not None
    assert record.created.timestamp == 1_779_000_000
    assert record.created.user_record_name == "_creator"
    assert record.created.device_id == "device-a"
    assert record.modified is not None
    assert record.modified.timestamp == 1_780_000_000
    assert record.modified.user_record_name == "_editor"
    assert record.modified.device_id == "device-b"
    assert record.modified_timestamp == 1_780_000_000
    assert record.to_cloudkit_payload() == payload


def test_cloudkit_zone_changes_response_types_zone_ids() -> None:
    response = CloudKitZoneChangesResponse.model_validate(
        {
            "zones": [
                {
                    "zoneID": {
                        "zoneName": "AniShelfLibrary",
                        "ownerRecordName": "_defaultOwner",
                    },
                    "records": None,
                    "syncToken": "t1",
                    "moreComing": False,
                }
            ]
        }
    )

    zone = response.zones[0]

    assert zone.zone_id is not None
    assert zone.zone_id.zone_name == "AniShelfLibrary"
    assert zone.zone_id.owner_record_name == "_defaultOwner"
    assert zone.records == ()


def test_cloudkit_record_validate_fields_normalizes_library_entry_snapshot_values() -> None:
    record = cloudkit_record(
        live_record(
            "movie:55",
            "movie",
            55,
            on_display=False,
            using_custom_poster=True,
            custom_poster_path="/stale/custom.jpg",
            custom_poster_url="https://image.tmdb.org/t/p/w342/current/custom.jpg",
            episode_progresses='[{"seasonNumber":1,"watchedThroughEpisode":12,"updatedAt":799891200.0}]',
        )
    )

    fields = record.validate_fields(CloudKitLibraryEntrySnapshotFields)

    assert fields.on_display is False
    assert fields.date_saved == "2026-05-01T00:00:00Z"
    assert fields.using_custom_poster is True
    assert fields.custom_poster_path == "/stale/custom.jpg"
    assert fields.custom_poster_url == "https://image.tmdb.org/t/p/w342/current/custom.jpg"
    assert len(fields.episode_progresses) == 1
    assert fields.episode_progresses[0].season_number == 1
    assert fields.episode_progresses[0].watched_through_episode == 12
    assert fields.episode_progresses[0].updated_at == "2026-05-08T00:00:00Z"


def test_cache_apply_page_is_idempotent_and_scoped(tmp_path, monkeypatch) -> None:
    scope = LibraryCacheScope.default_for_user("_user_a")
    store = create_cache_store(monkeypatch, tmp_path, user_record_name="_user_a")
    page = ZoneChangesPage(
        records=[_live_record("movie:55", "movie", 55)],
        sync_token="t1",
        more_coming=False,
    )

    store.apply_page(page, staging=False)
    store.apply_page(page, staging=False)

    assert store.read_sync_token() == "t1"
    entries = store.list_entry_models()
    assert [entry.identity for entry in entries] == ["movie:55"]
    assert (
        LibraryCacheStore.for_scope(scope).path
        != LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user_b")).path
    )


def test_cache_initializes_kind_scoped_lookup_indexes(tmp_path, monkeypatch) -> None:
    store = create_cache_store(monkeypatch, tmp_path)

    with sqlite3.connect(store.path) as db:
        index_names = {row[1] for row in db.execute("PRAGMA index_list(library_entries)")}
        assert "idx_library_entries_snapshot_sort" in index_names
        assert "idx_library_entries_tmdb_lookup" in index_names
        assert "idx_library_entries_parent_series_lookup" in index_names
        assert _index_columns(db, "idx_library_entries_snapshot_sort") == [
            "kind",
            "date_saved",
            "identity",
        ]
        assert _index_columns(db, "idx_library_entries_tmdb_lookup") == [
            "kind",
            "entry_type",
            "tmdb_id",
        ]
        assert _index_columns(db, "idx_library_entries_parent_series_lookup") == [
            "kind",
            "entry_type",
            "parent_series_id",
        ]
        metadata_columns = {
            row[1] for row in db.execute("PRAGMA table_info(tmdb_metadata_summary)")
        }
        assert {
            "metadata_key",
            "entry_type",
            "tmdb_id",
            "parent_series_id",
            "season_number",
            "language",
            "name",
            "overview",
            "poster_path",
            "source_version",
        } <= metadata_columns


def test_cache_preserves_raw_cloudkit_record_json_shape(tmp_path, monkeypatch) -> None:
    record = _live_record("movie:55", "movie", 55)
    store = create_seeded_cache_store(monkeypatch, tmp_path, record)

    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "SELECT raw_record_json, record_change_tag FROM library_entries WHERE identity = ?",
            ("movie:55",),
        ).fetchone()

    assert row is not None
    assert json.loads(row[0]) == record
    assert row[1] == "tag-movie:55"


def test_entry_row_params_preserves_sqlite_contract_for_snapshot_and_tombstone_rows() -> None:
    snapshot = validate_library_entry(_snapshot_entry_payload("movie:55", "movie", 55))
    tombstone = validate_library_entry(
        {
            "identity": "movie:55",
            "kind": "tombstone",
            "entry_type": "movie",
            "tmdb_id": 55,
            "deleted_at": "2026-07-01T00:00:00Z",
        }
    )

    snapshot_row = entry_row_params(snapshot, {"recordName": "movie:55"}, "tag-1")
    tombstone_row = entry_row_params(tombstone, {"recordName": "movie:55"}, "tag-2")

    assert snapshot_row["favorite"] == 0
    assert snapshot_row["on_display"] == 1
    assert snapshot_row["is_date_tracking_enabled"] == 0
    assert snapshot_row["using_custom_poster"] == 0
    assert snapshot_row["watch_status"] == "watched"
    assert snapshot_row["deleted_at"] is None
    assert json.loads(snapshot_row["decoded_json"])["kind"] == "snapshot"

    assert tombstone_row["favorite"] is None
    assert tombstone_row["on_display"] is None
    assert tombstone_row["using_custom_poster"] is None
    assert tombstone_row["watch_status"] is None
    assert tombstone_row["deleted_at"] == "2026-07-01T00:00:00Z"
    assert json.loads(tombstone_row["decoded_json"])["kind"] == "tombstone"


def test_metadata_summary_is_stored_separately_and_attached_on_read(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    raw_entry = store.list_entry_models()[0]
    assert raw_entry.metadata is None
    attached = store.attach_metadata_summary_models([raw_entry])[0]
    assert attached.metadata is not None
    assert attached.metadata.name == "Alien"
    assert attached.metadata.poster_path == "/poster.jpg"
    assert [genre.model_dump(mode="json") for genre in attached.metadata.genres] == [
        {"id": 878, "name": "Science Fiction"}
    ]
    assert attached.metadata.runtime_minutes == 117
    assert attached.metadata.vote_average == 8.2

    with sqlite3.connect(store.path) as db:
        decoded_json = db.execute("SELECT decoded_json FROM library_entries").fetchone()[0]
        assert "Alien" not in decoded_json


def test_metadata_summary_read_normalizes_legacy_rows_with_new_fields(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    _insert_legacy_v1_metadata_summary(
        store,
        metadata_key="movie:55",
        entry_type="movie",
        tmdb_id=55,
    )

    attached = store.attach_metadata_summary_models(store.list_entry_models())[0].metadata
    assert attached is not None

    assert attached.status is None
    assert attached.genres == ()
    assert attached.runtime_minutes is None
    assert attached.season_count is None
    assert attached.episode_count is None
    assert attached.vote_average is None
    assert attached.vote_count is None
    assert attached.popularity is None


def test_attach_metadata_summary_preserves_dict_compatibility(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    raw_entry = store.list_entry_models()[0]
    assert getattr(raw_entry, "metadata", None) is None

    attached = store.attach_metadata_summary_models([raw_entry])[0]

    assert attached.identity == "movie:55"
    assert attached.metadata is not None
    assert attached.metadata.name == "Alien"
    assert attached.metadata.poster_path == "/poster.jpg"


def test_metadata_summary_status_treats_legacy_v1_rows_as_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    _insert_legacy_v1_metadata_summary(
        store,
        metadata_key="movie:55",
        entry_type="movie",
        tmdb_id=55,
    )

    assert store.missing_metadata_summary_targets() == []
    assert store.outdated_metadata_summary_targets() == [
        TMDbSummaryIdentity(entry_type="movie", tmdb_id=55)
    ]
    assert store.incomplete_metadata_summary_targets() == [
        TMDbSummaryIdentity(entry_type="movie", tmdb_id=55)
    ]
    assert store.metadata_summary_status().model_dump(mode="json") == {
        "tracked_entries": 1,
        "hydrated_entries": 0,
        "missing_entries": 1,
        "ready": False,
    }


def test_cache_sync_hydrates_tmdb_summary_for_new_entries_only(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_cache_store(monkeypatch, tmp_path)

    class FakeExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = desired_record_types
            self.calls += 1
            if sync_token is None:
                return ZoneChangesPage(
                    records=[_live_record("movie:55", "movie", 55)],
                    sync_token="t1",
                    more_coming=False,
                )
            return ZoneChangesPage(
                records=[
                    _live_record("movie:55", "movie", 55),
                    _live_record("series:22", "series", 22),
                ],
                sync_token="t2",
                more_coming=False,
            )

    class FakeTMDb:
        def __init__(self) -> None:
            self.targets: list[tuple[str, int]] = []

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            self.targets.append((identity.entry_type, identity.tmdb_id))
            return _metadata_summary(
                identity.entry_type,
                identity.tmdb_id,
                name=f"Name {identity.tmdb_id}",
            )

    executor = FakeExecutor()
    tmdb = FakeTMDb()

    first = LibraryCacheSync(store=store, executor=executor, tmdb_client=tmdb).refresh()  # type: ignore[arg-type]
    second = LibraryCacheSync(store=store, executor=executor, tmdb_client=tmdb).refresh()  # type: ignore[arg-type]

    assert first.metadata_requested == 1
    assert first.metadata_hydrated == 1
    assert second.metadata_requested == 1
    assert second.metadata_hydrated == 1
    assert tmdb.targets == [("movie", 55), ("series", 22)]


def test_cache_sync_backfills_legacy_v1_metadata_without_new_entries(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    _insert_legacy_v1_metadata_summary(
        store,
        metadata_key="movie:55",
        entry_type="movie",
        tmdb_id=55,
    )

    class FakeExecutor:
        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = desired_record_types
            assert sync_token == "t1"
            return ZoneChangesPage(records=[], sync_token="t2", more_coming=False)

    class FakeTMDb:
        def __init__(self) -> None:
            self.targets: list[tuple[str, int]] = []

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            self.targets.append((identity.entry_type, identity.tmdb_id))
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Alien")

    tmdb = FakeTMDb()

    result = LibraryCacheSync(
        store=store,
        executor=FakeExecutor(),  # type: ignore[arg-type]
        tmdb_client=tmdb,
    ).refresh()

    assert result.rebuilt is False
    assert result.records == 0
    assert result.metadata_requested == 1
    assert result.metadata_hydrated == 1
    assert tmdb.targets == [("movie", 55)]
    refreshed = store.attach_metadata_summary_models(store.list_entry_models())[0].metadata
    assert refreshed is not None
    assert refreshed.source_version == "tmdbsummary.v2"
    assert refreshed.runtime_minutes == 117
    assert store.metadata_summary_status().ready is True


def test_cache_sync_skips_outdated_metadata_scan_when_target_collection_disabled(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))

    class FakeExecutor:
        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = desired_record_types
            assert sync_token == "t1"
            return ZoneChangesPage(records=[], sync_token="t2", more_coming=False)

    def fail_outdated_scan(self) -> list[TMDbSummaryIdentity]:
        raise AssertionError("outdated metadata scan should be skipped")

    monkeypatch.setattr(
        LibraryCacheStore,
        "outdated_metadata_summary_targets",
        fail_outdated_scan,
    )

    result = LibraryCacheSync(
        store=store,
        executor=FakeExecutor(),  # type: ignore[arg-type]
        collect_metadata_targets=False,
    ).refresh()

    assert result.rebuilt is False
    assert result.records == 0
    assert result.metadata_requested == 0
    assert result.metadata_targets == ()
    assert store.read_sync_token() == "t2"


def test_cache_sync_hydrates_metadata_with_bounded_parallel_requests(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_cache_store(monkeypatch, tmp_path)
    both_started = threading.Event()
    first_can_finish = threading.Event()

    class FakeExecutor:
        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = sync_token, desired_record_types
            return ZoneChangesPage(
                records=[
                    _live_record("movie:55", "movie", 55),
                    _live_record("series:22", "series", 22),
                ],
                sync_token="t1",
                more_coming=False,
            )

    class BlockingTMDb:
        def __init__(self) -> None:
            self.started: list[int] = []
            self.lock = threading.Lock()

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            with self.lock:
                self.started.append(identity.tmdb_id)
                if len(self.started) == 2:
                    both_started.set()
            if identity.tmdb_id == 55:
                assert first_can_finish.wait(timeout=2)
            else:
                assert both_started.wait(timeout=2)
                first_can_finish.set()
            return _metadata_summary(
                identity.entry_type,
                identity.tmdb_id,
                name=f"Name {identity.tmdb_id}",
            )

    result = LibraryCacheSync(
        store=store,
        executor=FakeExecutor(),  # type: ignore[arg-type]
        tmdb_client=BlockingTMDb(),
        metadata_workers=2,
    ).refresh()

    assert result.metadata_requested == 2
    assert result.metadata_hydrated == 2
    assert both_started.is_set()
    attached = store.attach_metadata_summary_models(store.list_entry_models())
    assert {entry.metadata.name for entry in attached if entry.metadata is not None} == {
        "Name 22",
        "Name 55",
    }


def test_cache_sync_does_not_retry_old_metadata_failures_without_new_entries(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_cache_store(monkeypatch, tmp_path)

    class FakeExecutor:
        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = desired_record_types
            if sync_token is None:
                return ZoneChangesPage(
                    records=[_live_record("movie:55", "movie", 55)],
                    sync_token="t1",
                    more_coming=False,
                )
            return ZoneChangesPage(records=[], sync_token="t2", more_coming=False)

    class FlakyTMDb:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            self.calls += 1
            if self.calls == 1:
                raise TMDbRequestError("temporary failure")
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Alien")

    tmdb = FlakyTMDb()

    first = LibraryCacheSync(store=store, executor=FakeExecutor(), tmdb_client=tmdb).refresh()  # type: ignore[arg-type]
    second = LibraryCacheSync(store=store, executor=FakeExecutor(), tmdb_client=tmdb).refresh()  # type: ignore[arg-type]

    assert first.metadata_requested == 1
    assert first.metadata_hydrated == 0
    assert first.metadata_errors == 1
    assert second.metadata_requested == 0
    assert second.metadata_hydrated == 0
    assert store.attach_metadata_summary_models(store.list_entry_models())[0].metadata is None


def test_season_metadata_uses_full_identity_context_for_cache_and_hydration(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_cache_store(monkeypatch, tmp_path)

    class FakeExecutor:
        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = sync_token, desired_record_types
            return ZoneChangesPage(
                records=[_live_record("season:22:1:33", "season", 33)],
                sync_token="t1",
                more_coming=False,
            )

    class FakeTMDb:
        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            assert identity.entry_type == "season"
            assert identity.tmdb_id == 33
            assert identity.parent_series_id == 22
            assert identity.season_number == 1
            return _metadata_summary(
                "season",
                33,
                name="Season 1",
                parent_series_id=22,
                season_number=1,
            )

    result = LibraryCacheSync(
        store=store,
        executor=FakeExecutor(),  # type: ignore[arg-type]
        tmdb_client=FakeTMDb(),
    ).refresh()

    assert result.metadata_hydrated == 1
    attached = store.attach_metadata_summary_models(store.list_entry_models())[0]
    assert attached.metadata is not None
    assert attached.metadata.name == "Season 1"
    assert attached.metadata.parent_series_id == 22
    assert attached.metadata.season_number == 1


def test_cache_excludes_tombstones_by_default(tmp_path, monkeypatch) -> None:
    store = create_cache_store(monkeypatch, tmp_path)
    page = ZoneChangesPage(
        records=[
            _live_record("movie:55", "movie", 55),
            _tombstone_record("series:22", "series", 22),
            {"recordName": "movie:66", "deleted": True, "modified": {"timestamp": 1_780_000_000}},
        ],
        sync_token="t1",
        more_coming=False,
    )

    store.apply_page(page, staging=False)

    assert [entry.identity for entry in store.list_entry_models()] == ["movie:55"]
    assert [entry.identity for entry in store.list_entry_models(include_tombstones=True)] == [
        "movie:55",
        "movie:66",
        "series:22",
    ]


def test_cache_search_matches_movies_series_and_seasons_in_saved_order(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55, date_saved="2026-05-01T00:00:00Z"),
        _live_record("series:22", "series", 22, date_saved="2026-05-03T00:00:00Z"),
        _live_record("season:22:1:33", "season", 33, date_saved="2026-05-02T00:00:00Z"),
        _tombstone_record("series:99", "series", 99),
    )

    entries = store.search_cached_entry_models(movie_ids={55}, series_ids={22, 99})

    assert [entry.identity for entry in entries] == [
        "series:22",
        "season:22:1:33",
        "movie:55",
    ]


def test_cache_does_not_advance_token_when_apply_fails(tmp_path, monkeypatch) -> None:
    store = create_cache_store(monkeypatch, tmp_path)
    page = ZoneChangesPage(
        records=[{"recordName": "bad", "recordType": "LibraryEntry"}],
        sync_token="t1",
        more_coming=False,
    )

    with pytest.raises(LibraryRecordDecodeError):
        store.apply_page(page, staging=False)

    assert store.read_sync_token() is None
    assert store.list_entry_models(include_tombstones=True) == []


def test_expired_token_rebuild_preserves_old_rows_until_final_promotion(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
        sync_token="old",
    )

    class FakeExecutor:
        def fetch_zone_changes(
            self,
            *,
            sync_token: str | None,
            desired_record_types: list[str] | None = None,
        ) -> ZoneChangesPage:
            _ = desired_record_types
            if sync_token == "old":
                raise CloudKitChangeTokenExpiredError("expired")
            assert sync_token is None
            assert [entry.identity for entry in store.list_entry_models()] == ["movie:55"]
            return ZoneChangesPage(
                records=[_live_record("series:22", "series", 22)],
                sync_token="new",
                more_coming=False,
            )

    result = LibraryCacheSync(store=store, executor=FakeExecutor()).refresh()  # type: ignore[arg-type]

    assert result.rebuilt is True
    assert store.read_sync_token() == "new"
    assert [entry.identity for entry in store.list_entry_models()] == ["series:22"]


def test_executor_fetches_zone_changes_with_pagination_token(monkeypatch) -> None:
    store = _store_with_cloudkit_token("web-secret-token")
    requests: list[httpx.Request] = []
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [_live_record("movie:55", "movie", 55)],
                        "syncToken": "next-token",
                        "moreComing": True,
                    }
                ]
            },
        )

    executor = CloudKitExecutor(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_token_resolver=lambda: CloudKitAPIToken("api-secret-token", "env"),
        secret_store=store,
        lock_factory=lambda path: null_lock(path),
    )

    page = executor.fetch_zone_changes(sync_token="cursor-1", desired_record_types=["LibraryEntry"])

    assert page.more_coming is True
    assert page.sync_token == "next-token"
    assert page.records[0].effective_record_name == "movie:55"
    request = requests[0]
    assert request.url.path.endswith("/production/private/changes/zone")
    assert request.url.params["ckAPIToken"] == "api-secret-token"
    assert request.url.params["ckWebAuthToken"] == "web-secret-token"
    assert json.loads(request.content) == {
        "desiredRecordTypes": ["LibraryEntry"],
        "resultsLimit": 400,
        "zones": [
            {
                "syncToken": "cursor-1",
                "zoneID": {"zoneName": "AniShelfLibrary"},
            }
        ],
    }


def test_executor_classifies_top_level_expired_change_token(monkeypatch) -> None:
    store = _store_with_cloudkit_token("web-secret-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    executor = CloudKitExecutor(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    400,
                    json={
                        "serverErrorCode": "CHANGE_TOKEN_EXPIRED",
                        "reason": "token expired",
                    },
                )
            )
        ),
        api_token_resolver=lambda: CloudKitAPIToken("api-secret-token", "env"),
        secret_store=store,
        lock_factory=lambda path: null_lock(path),
    )

    with pytest.raises(CloudKitChangeTokenExpiredError):
        executor.fetch_zone_changes(sync_token="stale-token")


def test_library_list_refreshes_cache_and_emits_clean_json(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("web-secret-token")
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [
                            _live_record(
                                "movie:55",
                                "movie",
                                55,
                                date_saved="2026-05-01T00:00:00Z",
                            ),
                            _live_record(
                                "series:22",
                                "series",
                                22,
                                date_saved="2026-05-03T00:00:00Z",
                            ),
                        ],
                        "syncToken": "t1",
                        "moreComing": False,
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "library", "init"])

    assert result.exit_code == 0, result.output
    assert "[progress] Starting local library cache rebuild from CloudKit." in result.stderr
    assert "[progress] Fetched page 1: 2 records (2 total)." in result.stderr
    assert "api-secret-token" not in result.stderr
    assert "web-secret-token" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["records"] == 2
    assert payload["summary"]["cache"]["mode"] == "updated"
    assert any(request.url.path.endswith("/changes/zone") for request in requests)
    assert not any(request.url.path.endswith("/records/query") for request in requests)


def test_library_refresh_does_not_hold_cache_lock_during_tmdb_hydration(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("web-secret-token")
    lock_depth = 0

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)

    @contextmanager
    def tracking_lock(path: Path) -> Generator[None]:
        nonlocal lock_depth
        _ = path
        lock_depth += 1
        try:
            yield
        finally:
            lock_depth -= 1

    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    monkeypatch.setattr(LibraryCacheStore, "locked", lambda self: tracking_lock(self.lock_path))
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [_live_record("movie:55", "movie", 55)],
                        "syncToken": "t1",
                        "moreComing": False,
                    }
                ]
            },
        )

    class AssertingTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            assert lock_depth == 0
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Alien")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)
    monkeypatch.setattr(library_commands, "TMDbClient", AssertingTMDbClient)

    result = runner.invoke(app, ["--json", "library", "init"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["metadata_requested"] == 1


def test_library_initialization_hydrates_full_library(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    records = [
        _live_record(
            f"movie:{tmdb_id}",
            "movie",
            tmdb_id,
            date_saved=f"2026-05-{tmdb_id:02d}T00:00:00Z",
        )
        for tmdb_id in range(1, 13)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": records,
                        "syncToken": "t1",
                        "moreComing": False,
                    }
                ]
            },
        )

    calls: list[int] = []

    class CountingTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            calls.append(identity.tmdb_id)
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Movie")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)
    monkeypatch.setattr(library_commands, "TMDbClient", CountingTMDbClient)

    result = runner.invoke(app, ["--json", "library", "init"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["metadata_requested"] == 12
    assert len(calls) == 12


def test_library_init_rejects_existing_cache_and_points_to_sync(
    tmp_path,
    monkeypatch,
) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    secret_store = _store_with_cloudkit_token("web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: secret_store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(200, json={"userRecordName": "_user"})
                    if request.url.path.endswith("/users/current")
                    else httpx.Response(500)
                )
            )
        ),
    )

    result = runner.invoke(app, ["--json", "library", "init"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Run `ani library sync` instead." in result.stderr


def test_library_sync_refreshes_existing_cache_and_emits_clean_json(
    tmp_path,
    monkeypatch,
) -> None:
    initialized_store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
    )
    secret_store = _store_with_cloudkit_token("web-secret-token")
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: secret_store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [_live_record("series:22", "series", 22)],
                        "syncToken": "t2",
                        "moreComing": False,
                    }
                ]
            },
        )

    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = runner.invoke(app, ["--json", "library", "sync"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["records"] == 1
    assert payload["summary"]["cache"]["rebuilt"] is False
    assert any(request.url.path.endswith("/changes/zone") for request in requests)
    cached_entries = initialized_store.list_entry_models()
    assert {entry.identity for entry in cached_entries} == {"series:22", "movie:55"}


def test_library_sync_hydrates_tmdb_metadata_and_emits_progress(
    tmp_path,
    monkeypatch,
) -> None:
    initialized_store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
    )
    secret_store = _store_with_cloudkit_token("web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: secret_store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [_live_record("series:22", "series", 22)],
                        "syncToken": "t2",
                        "moreComing": False,
                    }
                ]
            },
        )

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            assert identity.entry_type == "series"
            assert identity.tmdb_id == 22
            return _metadata_summary("series", 22, name="Alien Nation")

    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "library", "sync"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["records"] == 1
    assert payload["summary"]["cache"]["metadata_requested"] == 1
    assert payload["summary"]["cache"]["metadata_hydrated"] == 1
    assert payload["summary"]["cache"]["metadata_errors"] == 0
    assert "[progress] Starting local library cache sync from CloudKit." in result.stderr
    assert "[progress] Fetched page 1: 1 records (1 total)." in result.stderr
    assert "[progress] Hydrating TMDb summary metadata for 1 entries." in result.stderr
    assert "[progress] TMDb summary metadata 1/1 complete (0 errors)." in result.stderr
    refreshed = initialized_store.attach_metadata_summary_models(
        initialized_store.list_entry_models()
    )
    series = next(entry for entry in refreshed if entry.identity == "series:22")
    assert series.metadata is not None
    assert series.metadata.name == "Alien Nation"


def test_library_init_reports_tmdb_secure_storage_failure(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    secret_store = _store_with_cloudkit_token("web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: secret_store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(200, json={"userRecordName": "_user"})
                    if request.url.path.endswith("/users/current")
                    else httpx.Response(500)
                )
            )
        ),
    )
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: (_ for _ in ()).throw(
            SecretStorageUnavailableError("Secure credential backend is unavailable")
        ),
    )

    result = runner.invoke(app, ["library", "init"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Secure credential backend is unavailable" in result.stderr
    assert "TMDb API key is not configured." not in result.stderr


def test_library_list_refresh_decode_error_exits_cleanly_in_json_mode(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))

    bad_record = _live_record("movie:55", "movie", 55)
    bad_record["fields"]["schemaVersion"]["value"] = 3

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [bad_record],
                        "syncToken": "t1",
                        "moreComing": False,
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "library", "init"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Unsupported LibraryEntry schema version 3" in result.stderr
    assert "Traceback" not in result.stderr


def test_library_export_excludes_tombstones_from_public_output(
    tmp_path,
    monkeypatch,
) -> None:
    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
        _tombstone_record("series:22", "series", 22),
    )

    result = runner.invoke(
        app,
        ["--json", "library", "export"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["mode"] == "cached"
    assert payload["summary"]["entries"] == 1
    assert [entry["identity"] for entry in payload["entries"]] == ["movie:55"]


def test_library_list_reads_existing_cache_without_cloudkit_update(
    tmp_path,
    monkeypatch,
) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(500)
            )
        ),
    )

    result = runner.invoke(app, ["--json", "library", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["mode"] == "cached"
    assert [entry["identity"] for entry in payload["entries"]] == ["movie:55"]
    assert requests == []


def test_library_list_sync_refreshes_cache_before_reading(
    tmp_path,
    monkeypatch,
) -> None:
    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
    )
    secret_store = _store_with_cloudkit_token("web-secret-token")
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: secret_store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/users/current"):
            return httpx.Response(200, json={"userRecordName": "_user"})
        return httpx.Response(
            200,
            json={
                "zones": [
                    {
                        "records": [_live_record("series:22", "series", 22)],
                        "syncToken": "t2",
                        "moreComing": False,
                    }
                ]
            },
        )

    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = runner.invoke(app, ["--json", "library", "list", "--sync"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["mode"] == "updated"
    assert payload["summary"]["cache"]["records"] == 1
    assert any(request.url.path.endswith("/changes/zone") for request in requests)
    assert {entry["identity"] for entry in payload["entries"]} == {"movie:55", "series:22"}


def test_library_export_reads_existing_cache_without_cloudkit_update(
    tmp_path,
    monkeypatch,
) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(500)
            )
        ),
    )

    result = runner.invoke(app, ["--json", "library", "export"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["mode"] == "cached"
    assert [entry["identity"] for entry in payload["entries"]] == ["movie:55"]
    assert requests == []


def test_library_list_attaches_cached_metadata_by_default_and_none_suppresses_it(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    with_metadata = runner.invoke(app, ["--json", "library", "list"])
    without_metadata = runner.invoke(
        app,
        ["--json", "library", "list", "--metadata", "none"],
    )
    human = runner.invoke(app, ["library", "list"])

    assert with_metadata.exit_code == 0, with_metadata.output
    with_payload = json.loads(with_metadata.stdout)
    assert with_payload["metadata"] == {
        "requested": "summary",
        "attached": True,
        "source": "cache",
    }
    assert with_payload["entries"][0]["metadata"]["name"] == "Alien"
    assert without_metadata.exit_code == 0, without_metadata.output
    without_payload = json.loads(without_metadata.stdout)
    assert without_payload["metadata"]["requested"] == "none"
    assert "metadata" not in without_payload["entries"][0]
    assert human.exit_code == 0, human.output
    assert "Title" in human.stdout
    assert "Alien" in human.stdout
    assert "movie:55" in human.stdout


def test_library_list_uses_configured_metadata_none_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('[library]\nmetadata = "none"\n')
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["--json", "library", "list"])
    human = runner.invoke(app, ["library", "list"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["metadata"] == {
        "requested": "none",
        "attached": False,
        "source": None,
    }
    assert "metadata" not in payload["entries"][0]
    assert human.exit_code == 0, human.output
    assert "Alien" in human.stdout


def test_library_list_title_sort_uses_cached_metadata_when_output_metadata_is_disabled(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('[library]\nmetadata = "none"\n')
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
        _live_record("movie:66", "movie", 66),
    )
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Zulu"))
    store.upsert_metadata_summary(_metadata_summary("movie", 66, name="Alien"))

    result = runner.invoke(app, ["--json", "library", "list", "--sort", "title"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["metadata"] == {
        "requested": "none",
        "attached": False,
        "source": None,
    }
    assert [entry["identity"] for entry in payload["entries"]] == ["movie:66", "movie:55"]
    assert all("metadata" not in entry for entry in payload["entries"])


def test_library_list_metadata_flag_overrides_configured_default(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('[library]\nmetadata = "none"\n')
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["--json", "library", "list", "--metadata", "summary"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["metadata"]["requested"] == "summary"
    assert payload["entries"][0]["metadata"]["name"] == "Alien"


def test_library_list_filters_sorts_and_limits_without_jq(tmp_path, monkeypatch) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record(
            "movie:55",
            "movie",
            55,
            date_saved="2026-05-01T00:00:00Z",
            watch_status="watching",
            on_display=False,
        ),
        _live_record(
            "series:22",
            "series",
            22,
            date_saved="2026-05-03T00:00:00Z",
            watch_status="watched",
            on_display=True,
        ),
        _live_record(
            "movie:66",
            "movie",
            66,
            date_saved="2026-05-02T00:00:00Z",
            watch_status="watching",
            on_display=False,
        ),
    )
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Zulu"))
    store.upsert_metadata_summary(_metadata_summary("movie", 66, name="Alien"))
    store.upsert_metadata_summary(_metadata_summary("series", 22, name="Cowboy Bebop"))

    result = runner.invoke(
        app,
        [
            "--json",
            "library",
            "list",
            "--watch-status",
            "watching",
            "--hidden",
            "--sort",
            "title",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["filters"]["watch_status"] == "watching"
    assert payload["filters"]["hidden"] is True
    assert payload["filters"]["sort"] == "title"
    assert [entry["identity"] for entry in payload["entries"]] == ["movie:66"]


def test_library_list_uses_configured_display_fields_for_human_output(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\ndisplay_fields = ["title", "saved"]\n'
    )
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["library", "list"])

    assert result.exit_code == 0, result.output
    assert "Title" in result.stdout
    assert "Saved" in result.stdout
    assert "Identity" not in result.stdout
    assert "Status" not in result.stdout


def test_library_list_fields_flag_overrides_configured_display_fields(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\ndisplay_fields = ["title", "saved"]\n'
    )
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["library", "list", "--fields", "identity,status"])

    assert result.exit_code == 0, result.output
    assert "Identity" in result.stdout
    assert "Status" in result.stdout
    assert "Saved" not in result.stdout


def test_library_list_fields_rejected_for_json_output(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "library", "list", "--fields", "title"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--fields only applies to human table output." in result.stderr


def test_library_refresh_meta_updates_full_library_cache(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            assert identity.entry_type == "movie"
            assert identity.tmdb_id == 55
            return _metadata_summary("movie", 55, name="Alien")

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(
        app,
        ["--json", "library", "refresh-meta"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["entries"] == 1
    assert payload["summary"]["metadata"] == {
        "requested": 1,
        "hydrated": 1,
        "errors": 0,
    }
    refreshed = store.attach_metadata_summary_models(store.list_entry_models())[0]
    assert refreshed.metadata is not None
    assert refreshed.metadata.name == "Alien"


def test_tmdb_summary_upsert_canonicalizes_source_version_for_storage(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))

    summary = _metadata_summary("movie", 55, name="Alien")
    summary = summary.model_copy(update={"source_version": "tmdb.http.summary.v2"})
    store.upsert_metadata_summary(summary)

    attached = store.attach_metadata_summary_models(store.list_entry_models())[0].metadata
    assert attached is not None
    assert attached.source_version == "tmdbsummary.v2"
    with sqlite3.connect(store.path) as db:
        stored = db.execute(
            "SELECT source_version FROM tmdb_metadata_summary WHERE metadata_key = ?",
            ("movie:55",),
        ).fetchone()
    assert stored[0] == "tmdbsummary.v2"


def test_library_export_attaches_cached_metadata_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("series:22", "series", 22),
    )
    store.upsert_metadata_summary(_metadata_summary("series", 22, name="Cowboy Bebop"))

    result = runner.invoke(app, ["--json", "library", "export"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["metadata"]["requested"] == "summary"
    assert payload["entries"][0]["metadata"]["name"] == "Cowboy Bebop"


def test_library_export_does_not_sync_from_config_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('[library]\nmetadata = "summary"\n')
    create_seeded_cache_store(monkeypatch, tmp_path, _live_record("series:22", "series", 22))
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        library_commands,
        "_make_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(500)
            )
        ),
    )

    result = runner.invoke(app, ["--json", "library", "export"])

    assert result.exit_code == 0, result.output
    assert requests == []


def test_library_search_matches_cached_titles_without_tmdb(monkeypatch) -> None:
    fake_store = _fake_search_store()
    monkeypatch.setattr(library_commands, "_library_store_for_read", lambda: fake_store)

    result = runner.invoke(app, ["--json", "library", "search", "--title", "Alien"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["query"] == {"title": "Alien"}
    assert [entry["identity"] for entry in payload["entries"]] == [
        "movie:55",
        "series:22",
        "season:22:1:33",
    ]
    assert fake_store.search_title_arg == "Alien"  # type: ignore[attr-defined]


def test_library_search_metadata_default_and_none(monkeypatch) -> None:
    class FakeStore:
        scope = LibraryCacheScope.default_for_user("_user")
        attach_calls = 0

        def metadata_summary_status(self) -> CacheMetadataStatusResult:
            return CacheMetadataStatusResult(
                tracked_entries=1,
                hydrated_entries=1,
                missing_entries=0,
                ready=True,
            )

        def search_entry_models_by_title(self, title: str) -> list[LibraryEntryModel]:
            assert title == "Alien"
            return [validate_library_entry(_snapshot_entry_payload("movie:55", "movie", 55))]

        def attach_metadata_summary_models(
            self,
            entries: list[LibraryEntryModel],
        ) -> list[LibraryEntryModel]:
            self.attach_calls += 1
            return [
                entry.with_metadata(_metadata_summary("movie", 55, name="Alien"))
                for entry in entries
            ]

    fake_store = FakeStore()
    monkeypatch.setattr(library_commands, "_library_store_for_read", lambda: fake_store)

    with_metadata = runner.invoke(app, ["--json", "library", "search", "--title", "Alien"])
    without_metadata = runner.invoke(
        app,
        ["--json", "library", "search", "--title", "Alien", "--metadata", "none"],
    )

    assert with_metadata.exit_code == 0, with_metadata.output
    assert json.loads(with_metadata.stdout)["entries"][0]["metadata"]["name"] == "Alien"
    assert without_metadata.exit_code == 0, without_metadata.output
    assert "metadata" not in json.loads(without_metadata.stdout)["entries"][0]
    assert fake_store.attach_calls == 1


def test_library_search_human_uses_cached_titles_when_configured_metadata_default_is_none(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('[library]\nmetadata = "none"\n')
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["library", "search", "--title", "Alien"])

    assert result.exit_code == 0, result.output
    assert "Alien" in result.stdout


def test_library_search_uses_configured_display_fields_for_human_output(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\ndisplay_fields = ["identity", "status"]\n'
    )
    store = create_seeded_cache_store(monkeypatch, tmp_path, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["library", "search", "--title", "Alien"])

    assert result.exit_code == 0, result.output
    assert "Identity" in result.stdout
    assert "Status" in result.stdout
    assert "Title" not in result.stdout


def _index_columns(db: sqlite3.Connection, index_name: str) -> list[str]:
    return [row[2] for row in db.execute(f"PRAGMA index_info({index_name})")]


def _fake_search_store() -> object:
    class FakeStore:
        search_title_arg: str | None = None
        scope = LibraryCacheScope.default_for_user("_user")

        def metadata_summary_status(self) -> CacheMetadataStatusResult:
            return CacheMetadataStatusResult(
                tracked_entries=3,
                hydrated_entries=3,
                missing_entries=0,
                ready=True,
            )

        def search_entry_models_by_title(self, title: str) -> list[LibraryEntryModel]:
            self.search_title_arg = title
            return [
                validate_library_entry(_snapshot_entry_payload("movie:55", "movie", 55)),
                validate_library_entry(_snapshot_entry_payload("series:22", "series", 22)),
                validate_library_entry(
                    _snapshot_entry_payload(
                        "season:22:1:33",
                        "season",
                        33,
                        parent_series_id=22,
                        season_number=1,
                    )
                ),
            ]

        def attach_metadata_summary_models(
            self,
            entries: list[LibraryEntryModel],
        ) -> list[LibraryEntryModel]:
            return entries

    return FakeStore()

def _live_record(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    date_saved: str = "2026-05-01T00:00:00Z",
    watch_status: str = "watched",
    on_display: bool = True,
) -> dict[str, Any]:
    return live_record(
        identity,
        entry_type,
        tmdb_id,
        date_saved=date_saved,
        watch_status=watch_status,
        on_display=on_display,
    )


def _tombstone_record(identity: str, entry_type: str, tmdb_id: int) -> dict[str, Any]:
    return tombstone_record(identity, entry_type, tmdb_id)
