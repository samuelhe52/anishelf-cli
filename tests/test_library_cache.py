from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from anishelf_cli.cache.store import LibraryCacheScope, LibraryCacheStore
from anishelf_cli.cache.sync import LibraryCacheSync
from anishelf_cli.cli import groups
from anishelf_cli.cli.root import app
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken
from anishelf_cli.cloudkit.executor import (
    CloudKitChangeTokenExpiredError,
    CloudKitExecutor,
    ZoneChangesPage,
)
from anishelf_cli.config import KEYCHAIN_ACCOUNT
from anishelf_cli.library import LibraryRecordDecodeError
from anishelf_cli.secrets import cloudkit_web_auth_token_secret
from anishelf_cli.tmdb.client import TMDbTitleSearchResult
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, TMDbAPIToken

runner = CliRunner()


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.values[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@contextmanager
def null_lock(path: Path) -> Iterator[None]:
    _ = path
    yield


def test_cache_apply_page_is_idempotent_and_scoped(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    scope = LibraryCacheScope.default_for_user("_user_a")
    store = LibraryCacheStore.for_scope(scope)
    page = ZoneChangesPage(
        records=[_live_record("movie:55", "movie", 55)],
        sync_token="t1",
        more_coming=False,
    )

    store.initialize()
    store.apply_page(page, staging=False)
    store.apply_page(page, staging=False)

    assert store.read_sync_token() == "t1"
    entries = store.list_entries()
    assert [entry["identity"] for entry in entries] == ["movie:55"]
    assert LibraryCacheStore.for_scope(scope).path != LibraryCacheStore.for_scope(
        LibraryCacheScope.default_for_user("_user_b")
    ).path


def test_cache_initializes_kind_scoped_lookup_indexes(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user"))

    store.initialize()

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


def test_cache_excludes_tombstones_by_default(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user"))
    page = ZoneChangesPage(
        records=[
            _live_record("movie:55", "movie", 55),
            _tombstone_record("series:22", "series", 22),
            {"recordName": "movie:66", "deleted": True, "modified": {"timestamp": 1_780_000_000}},
        ],
        sync_token="t1",
        more_coming=False,
    )

    store.initialize()
    store.apply_page(page, staging=False)

    assert [entry["identity"] for entry in store.list_entries()] == ["movie:55"]
    assert [entry["identity"] for entry in store.list_entries(include_tombstones=True)] == [
        "movie:55",
        "movie:66",
        "series:22",
    ]


def test_cache_search_matches_movies_series_and_seasons_in_saved_order(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user"))
    store.initialize()
    store.apply_page(
        ZoneChangesPage(
            records=[
                _live_record("movie:55", "movie", 55, date_saved="2026-05-01T00:00:00Z"),
                _live_record("series:22", "series", 22, date_saved="2026-05-03T00:00:00Z"),
                _live_record("season:22:1:33", "season", 33, date_saved="2026-05-02T00:00:00Z"),
                _tombstone_record("series:99", "series", 99),
            ],
            sync_token="t1",
            more_coming=False,
        ),
        staging=False,
    )

    entries = store.search_cached_entries(movie_ids={55}, series_ids={22, 99})

    assert [entry["identity"] for entry in entries] == [
        "series:22",
        "season:22:1:33",
        "movie:55",
    ]


def test_cache_does_not_advance_token_when_apply_fails(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user"))
    store.initialize()
    page = ZoneChangesPage(
        records=[{"recordName": "bad", "recordType": "LibraryEntry"}],
        sync_token="t1",
        more_coming=False,
    )

    with pytest.raises(LibraryRecordDecodeError):
        store.apply_page(page, staging=False)

    assert store.read_sync_token() is None
    assert store.list_entries(include_tombstones=True) == []


def test_expired_token_rebuild_preserves_old_rows_until_final_promotion(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user"))
    store.initialize()
    store.apply_page(
        ZoneChangesPage(
            records=[_live_record("movie:55", "movie", 55)],
            sync_token="old",
            more_coming=False,
        ),
        staging=False,
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
            assert [entry["identity"] for entry in store.list_entries()] == ["movie:55"]
            return ZoneChangesPage(
                records=[_live_record("series:22", "series", 22)],
                sync_token="new",
                more_coming=False,
            )

    result = LibraryCacheSync(store=store, executor=FakeExecutor()).refresh()  # type: ignore[arg-type]

    assert result.rebuilt is True
    assert store.read_sync_token() == "new"
    assert [entry["identity"] for entry in store.list_entries()] == ["series:22"]


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
    assert page.records[0]["recordName"] == "movie:55"
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
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)
    monkeypatch.setattr(groups, "library_lock_factory", lambda path: null_lock(path))

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
    monkeypatch.setattr(groups, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "library", "list"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert [entry["identity"] for entry in payload["entries"]] == ["series:22", "movie:55"]
    assert payload["summary"]["cache"]["mode"] == "refreshed"
    assert any(request.url.path.endswith("/changes/zone") for request in requests)
    assert not any(request.url.path.endswith("/records/query") for request in requests)


def test_library_list_refresh_decode_error_exits_cleanly_in_json_mode(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)
    monkeypatch.setattr(groups, "library_lock_factory", lambda path: null_lock(path))

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
    monkeypatch.setattr(groups, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "library", "list"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Unsupported LibraryEntry schema version 3" in result.stderr
    assert "Traceback" not in result.stderr


def test_library_export_offline_reads_existing_cache_with_tombstone_option(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user("_user"))
    store.initialize()
    store.apply_page(
        ZoneChangesPage(
            records=[
                _live_record("movie:55", "movie", 55),
                _tombstone_record("series:22", "series", 22),
            ],
            sync_token="t1",
            more_coming=False,
        ),
        staging=False,
    )

    result = runner.invoke(
        app,
        ["--json", "library", "export", "--offline", "--include-tombstones"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["mode"] == "offline"
    assert payload["summary"]["tombstones"] == 1
    assert [entry["identity"] for entry in payload["entries"]] == ["movie:55", "series:22"]


def test_library_search_requires_tmdb_key_without_printing_secrets(monkeypatch) -> None:
    monkeypatch.setattr(
        groups,
        "_library_store_for_read",
        lambda *, offline: (_fake_search_store(), None),
    )
    monkeypatch.setattr(
        groups,
        "resolve_tmdb_api_token",
        lambda store: (_ for _ in ()).throw(MissingTMDbAPITokenError("missing tmdb key")),
    )

    result = runner.invoke(app, ["--json", "library", "search", "--title", "Alien"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "missing tmdb key" in result.stderr


def test_library_search_intersects_tmdb_results_and_includes_series_seasons(monkeypatch) -> None:
    fake_store = _fake_search_store()
    monkeypatch.setattr(groups, "_library_store_for_read", lambda *, offline: (fake_store, None))
    monkeypatch.setattr(
        groups,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def search_title(self, title: str) -> TMDbTitleSearchResult:
            assert title == "Alien"
            return TMDbTitleSearchResult(movie_ids={55}, series_ids={22})

    monkeypatch.setattr(groups, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "library", "search", "--title", "Alien"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["query"]["tmdb_movie_ids"] == [55]
    assert payload["query"]["tmdb_series_ids"] == [22]
    assert [entry["identity"] for entry in payload["entries"]] == [
        "movie:55",
        "series:22",
        "season:22:1:33",
    ]
    assert fake_store.search_args == (  # type: ignore[attr-defined]
        {55},
        {22},
    )
    assert "tmdb-secret-token" not in result.stdout + result.stderr


def _isolate_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ANISHELF_CLI_DATA_DIR", str(tmp_path / "data"))


def _store_with_cloudkit_token(token: str) -> MemorySecretStore:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, KEYCHAIN_ACCOUNT, token)
    return store


def _index_columns(db: sqlite3.Connection, index_name: str) -> list[str]:
    return [row[2] for row in db.execute(f"PRAGMA index_info({index_name})")]


def _fake_search_store() -> object:
    class FakeStore:
        search_args: tuple[set[int], set[int]] | None = None
        scope = LibraryCacheScope.default_for_user("_user")

        def search_cached_entries(
            self,
            *,
            movie_ids: set[int],
            series_ids: set[int],
        ) -> list[dict[str, Any]]:
            self.search_args = (movie_ids, series_ids)
            return [
                {"identity": "movie:55", "kind": "snapshot", "entry_type": "movie"},
                {"identity": "series:22", "kind": "snapshot", "entry_type": "series"},
                {"identity": "season:22:1:33", "kind": "snapshot", "entry_type": "season"},
            ]

    return FakeStore()


def _live_record(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    date_saved: str = "2026-05-01T00:00:00Z",
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schemaVersion": 2,
        "tmdbID": tmdb_id,
        "entryType": entry_type,
        "onDisplay": True,
        "dateSaved": date_saved,
        "watchStatus": "watched",
        "dateStarted": "2026-05-02T00:00:00Z",
        "dateFinished": "2026-05-09T00:00:00Z",
        "isDateTrackingEnabled": False,
        "score": 4,
        "favorite": True,
        "notes": "Round trip",
        "usingCustomPoster": False,
        "episodeProgresses": [],
        "libraryUpdatedAt": "2026-05-10T00:00:00Z",
        "trackingUpdatedAt": "2026-05-11T00:00:00Z",
    }
    if entry_type == "season":
        parts = identity.split(":")
        fields["parentSeriesID"] = int(parts[1])
        fields["seasonNumber"] = int(parts[2])
    return _record(identity, fields)


def _tombstone_record(identity: str, entry_type: str, tmdb_id: int) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schemaVersion": 2,
        "tmdbID": tmdb_id,
        "entryType": entry_type,
        "deletedAt": "2026-05-12T00:00:00Z",
    }
    return _record(identity, fields)


def _record(identity: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "recordName": identity,
        "recordType": "LibraryEntry",
        "recordChangeTag": f"tag-{identity}",
        "fields": {name: {"value": value} for name, value in fields.items()},
    }
