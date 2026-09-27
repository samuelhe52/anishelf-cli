from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cli import library_commands
from anishelf_cli.cli.root import app
from anishelf_cli.cloudkit.executor import ZoneChangesPage
from anishelf_cli.library import LibraryRecordDecodeError, decode_library_entry_record
from anishelf_cli.library.metadata import LibraryEntryMetadata
from anishelf_cli.models import MetadataDepth
from anishelf_cli.secrets import cloudkit_web_auth_token_secret
from anishelf_cli.tmdb.client import TMDbRequestError
from anishelf_cli.tmdb.tokens import TMDbAPIToken
from tests.support import (
    MemorySecretStore,
    create_seeded_cache_store,
    episode_progresses_bytes,
    live_record,
    null_lock,
    patch_library_read_store,
    runner,
    tombstone_record,
)
from tests.support import (
    cloudkit_record as _cloudkit_record,
)
from tests.support import (
    isolate_paths as _isolate_paths,
)
from tests.support import (
    metadata_summary as _metadata_summary,
)
from tests.support import (
    store_with_cloudkit_token as _store_with_cloudkit_token,
)


def _rich_metadata(
    entry_type: str,
    tmdb_id: int,
    *,
    name: str,
) -> LibraryEntryMetadata:
    return _metadata_summary(entry_type, tmdb_id, name=name).with_updates(
        runtime_minutes=117 if entry_type == "movie" else None,
        link_to_details=f"https://example.com/{entry_type}/{tmdb_id}",
        genres=({"id": 878, "name": "Science Fiction"}, {"id": 27, "name": "Horror"}),
        vote_average=8.5,
        vote_count=1200,
        popularity=35.5,
        status="Released",
        release_date="1979-05-25",
        tagline="In space no one can hear you scream.",
        subtitle="Director's cut",
        logo_path="/logo.svg",
        season_summaries=(
            {
                "season_number": 1,
                "name": "Season 1",
                "episode_count": 10,
            },
        ),
        episode_summaries=(
            {
                "season_number": 1,
                "episode_number": 1,
                "name": "Pilot",
            },
        ),
    )


def test_library_get_requires_init_before_lookup(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    result = runner.invoke(app, ["--json", "lib", "get", "movie:55"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Run `ani lib init` first" in result.stderr


def test_library_status_reports_uninitialized_cache(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["--json", "lib", "status"])
    human_result = runner.invoke(app, ["lib", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["initialized"] is False
    assert payload["summary"]["scope_count"] == 0
    assert payload["active"]["scope"] is None
    assert payload["active"]["entries"] == 0
    assert payload["active"]["visible_entries"] == 0
    assert payload["active"]["hidden_entries"] == 0
    assert payload["active"]["has_sync_token"] is False
    assert payload["active"]["metadata"] == {
        "tracked_entries": 0,
        "hydrated_entries": 0,
        "missing_entries": 0,
        "ready": False,
    }
    assert human_result.exit_code == 0, human_result.output
    assert "Active cache user" in human_result.stdout
    assert "Active user" not in human_result.stdout
    assert "Next step" in human_result.stdout
    assert "ani lib init" in human_result.stdout


def test_library_status_reports_initialized_cache(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(
        tmp_path,
        monkeypatch,
        _live_record("movie:55", "movie", 55, on_display=True),
    )
    store.apply_page(
        ZoneChangesPage(
            records=[_live_record("movie:66", "movie", 66, on_display=False)],
            sync_token="t2",
            more_coming=False,
        ),
        staging=False,
    )

    result = runner.invoke(app, ["--json", "lib", "status"])
    human_result = runner.invoke(app, ["lib", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["initialized"] is True
    assert payload["summary"]["scope_count"] == 1
    assert payload["active"]["entries"] == 2
    assert payload["active"]["visible_entries"] == 1
    assert payload["active"]["hidden_entries"] == 1
    assert payload["active"]["has_sync_token"] is True
    assert payload["active"]["scope"]["user_record_name"] == "_user"
    assert payload["active"]["metadata"] == {
        "tracked_entries": 2,
        "hydrated_entries": 0,
        "missing_entries": 2,
        "ready": False,
    }
    assert human_result.exit_code == 0, human_result.output
    assert "Active cache user" in human_result.stdout
    assert "_user" in human_result.stdout
    assert "Active user" not in human_result.stdout
    assert "Next step" not in human_result.stdout


def test_library_status_reports_metadata_ready_when_summary_is_cached(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["--json", "lib", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active"]["metadata"] == {
        "tracked_entries": 1,
        "hydrated_entries": 1,
        "missing_entries": 0,
        "ready": True,
    }


def test_library_status_reports_summary_ready_when_details_are_cached(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summaries(
        [_metadata_summary("movie", 55, name="Alien")],
        depth=library_commands.MetadataDepth.DETAILS,
    )

    result = runner.invoke(app, ["--json", "lib", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active"]["metadata"] == {
        "tracked_entries": 1,
        "hydrated_entries": 1,
        "missing_entries": 0,
        "ready": True,
    }


def test_library_status_human_output_uses_empty_partial_complete_metadata_states(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)

    empty = runner.invoke(app, ["lib", "status"])
    assert empty.exit_code == 0, empty.output
    assert "  Metadata" in empty.stdout
    assert "empty\n" in empty.stdout

    store = _install_cached_entry(
        tmp_path,
        monkeypatch,
        _live_record("movie:55", "movie", 55, on_display=True),
    )
    still_empty = runner.invoke(app, ["lib", "status"])
    assert still_empty.exit_code == 0, still_empty.output
    assert "  Entries" in still_empty.stdout
    assert "1\n" in still_empty.stdout
    assert "  Visible entries" in still_empty.stdout
    assert "  Hidden entries" in still_empty.stdout
    assert "  Metadata" in still_empty.stdout
    assert "empty\n" in still_empty.stdout

    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))
    store.apply_page(
        ZoneChangesPage(
            records=[_live_record("movie:66", "movie", 66)],
            sync_token="t2",
            more_coming=False,
        ),
        staging=False,
    )
    partial = runner.invoke(app, ["lib", "status"])
    assert partial.exit_code == 0, partial.output
    assert "partial\n" in partial.stdout

    store.upsert_metadata_summary(_metadata_summary("movie", 66, name="Aliens"))
    complete = runner.invoke(app, ["lib", "status"])
    assert complete.exit_code == 0, complete.output
    assert "complete\n" in complete.stdout


def test_library_clear_cache_requires_confirmation(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["lib", "clear-cache"], input="n\n")

    assert result.exit_code == 1
    assert "Aborted local library cache clear." in result.stderr
    assert LibraryCacheStore.find_default_scope().has_entries() is True


def test_library_clear_cache_yes_removes_all_local_cache_files(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.lock_path.parent.mkdir(parents=True, exist_ok=True)
    store.lock_path.write_text("locked")

    result = runner.invoke(app, ["--json", "lib", "clear-cache", "--yes"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "cleared"
    assert payload["removed"]["cache_files"] == 1
    assert payload["removed"]["lock_files"] == 1
    assert not store.path.exists()
    assert not store.lock_path.exists()


def test_library_clear_cache_y_alias_removes_all_local_cache_files(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["-j", "lib", "clear-cache", "-y"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "cleared"
    assert payload["removed"]["cache_files"] == 1
    assert not store.path.exists()


def test_library_clear_cache_prompt_can_confirm(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "lib", "clear-cache"], input="y\n")

    assert result.exit_code == 0, result.output
    assert not store.path.exists()


def test_library_init_then_get_success_json(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("old-web-secret-token")
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)

    init_result = runner.invoke(app, ["--json", "lib", "init"])
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["id"] == "movie:55"
    assert payload["items"][0]["status"] == "found"
    entry = payload["items"][0]["entry"]
    assert "kind" not in entry
    assert entry["entry_type"] == "movie"
    assert entry["tmdb_id"] == 55
    assert entry["date_saved"] == "2026-05-01"
    assert entry["date_started"] == "2026-05-02"
    assert entry["date_finished"] == "2026-05-09"
    assert entry["watch_status"] == "watched"
    assert "using_custom_poster" not in entry
    assert "custom_poster_path" not in entry
    assert "library_updated_at" not in entry
    assert "tracking_updated_at" not in entry
    assert "schema_version" not in entry
    assert entry["episode_progresses"] == [
        {
            "season_number": 1,
            "updated_at": "2026-05-08",
            "watched_through_episode": 12,
        }
    ]

    change_request = next(
        request for request in requests if request.url.path.endswith("/changes/zone")
    )
    assert change_request.method == "POST"
    assert change_request.url.params["ckAPIToken"] == "api-secret-token"
    assert change_request.url.params["ckWebAuthToken"] == "old-web-secret-token"
    assert json.loads(change_request.content) == {
        "desiredRecordTypes": ["LibraryEntry"],
        "resultsLimit": 400,
        "zones": [{"zoneID": {"zoneName": "AniShelfLibrary"}}],
    }
    assert not any(request.url.path.endswith("/records/lookup") for request in requests)
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "old-web-secret-token"
    combined = result.stdout + result.stderr
    assert "api-secret-token" not in combined
    assert "old-web-secret-token" not in combined
    assert "new-web-secret-token" not in combined


def test_library_get_accepts_command_level_json_after_subcommand(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["lib", "get", "--json", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["id"] == "movie:55"


def test_library_get_accepts_command_level_json_after_identity(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["lib", "get", "movie:55", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["id"] == "movie:55"


def test_library_get_uses_existing_cache_without_cloudkit_requests(
    tmp_path,
    monkeypatch,
) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
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

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["id"] == "movie:55"
    assert requests == []


def test_library_get_sync_refreshes_cache_before_lookup(
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
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

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

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Synced")

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "lib", "get", "series:22", "--sync"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["id"] == "series:22"
    assert any(request.url.path.endswith("/changes/zone") for request in requests)


def test_library_get_does_not_sync_from_config_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('[library]\nmetadata = "summary"\n')
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

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["items"][0]["entry"]["id"] == "movie:55"
    assert requests == []


def test_library_get_live_meta_refreshes_only_requested_entries(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
        _live_record("series:22", "series", 22),
    )
    store.upsert_metadata_summaries(
        [_metadata_summary("movie", 55, name="Stale")],
        depth=MetadataDepth.DETAILS,
    )
    requested: list[tuple[str, int, MetadataDepth]] = []
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_metadata(
            self,
            identity,
            depth: MetadataDepth,
        ) -> LibraryEntryMetadata:
            requested.append((identity.entry_type, identity.tmdb_id, depth))
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Alien")

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55", "--live-meta"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert requested == [("movie", 55, MetadataDepth.DETAILS)]
    assert payload["items"][0]["entry"]["metadata"]["name"] == "Alien"
    assert "poster_path" not in payload["items"][0]["entry"]["metadata"]
    assert "language" not in payload["items"][0]["entry"]["metadata"]
    assert "name_translations" not in payload["items"][0]["entry"]["metadata"]
    refreshed_entry = store.attach_metadata_summary_models(
        list(store.get_entry_models_by_identity(["movie:55"]).values()),
        depth=MetadataDepth.DETAILS,
    )[0]
    assert refreshed_entry.metadata is not None
    assert refreshed_entry.metadata.name == "Alien"
    assert refreshed_entry.metadata.poster_path == "/poster.jpg"
    other_entry = store.attach_metadata_summary_models(
        list(store.get_entry_models_by_identity(["series:22"]).values())
    )[0]
    assert other_entry.metadata is None


def test_library_get_json_omits_internal_fields_and_compacts_metadata_dates(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(
        _metadata_summary("movie", 55, name="Alien").with_updates(
            fetched_at="2026-05-13T12:34:56Z",
        )
    )

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55", "--metadata", "summary"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    entry = payload["items"][0]["entry"]
    metadata = entry["metadata"]
    assert "poster_path" not in metadata
    assert "fetched_at" not in metadata
    assert metadata["on_air_date"] == "1979-05-25"
    assert "kind" not in entry
    assert "schema_version" not in entry
    assert "custom_poster_path" not in entry


def test_library_get_json_details_includes_detail_metadata_fields(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summaries(
        [_metadata_summary("movie", 55, name="Alien")],
        depth=MetadataDepth.DETAILS,
    )

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55", "--metadata", "details"])

    assert result.exit_code == 0, result.output
    metadata = json.loads(result.stdout)["items"][0]["entry"]["metadata"]
    assert metadata["poster_path"] == "/poster.jpg"
    assert metadata["backdrop_path"] == "/backdrop.jpg"
    assert metadata["link_to_details"] == "https://example.com/movie/55"
    assert "name_translations" not in metadata


def test_library_get_json_sets_parent_series_title_null_for_non_seasons(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    metadata = json.loads(result.stdout)["items"][0]["entry"]["metadata"]
    assert metadata["name"] == "Alien"
    assert metadata["parent_series_title"] is None


def test_library_get_json_details_does_not_attach_summary_cache_row(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summaries(
        [_metadata_summary("movie", 55, name="Alien")],
        depth=MetadataDepth.SUMMARY,
    )

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55", "--metadata", "details"])

    assert result.exit_code == 0, result.output
    entry = json.loads(result.stdout)["items"][0]["entry"]
    assert entry["id"] == "movie:55"
    assert "metadata" not in entry


def test_library_get_ad_hoc_tmdb_language_does_not_update_cache(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
    )
    requested: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    class FakeTMDbClient:
        language = "en"

        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            requested.append((identity.entry_type, identity.tmdb_id, self.language))
            return _metadata_summary(
                identity.entry_type,
                identity.tmdb_id,
                name="エイリアン",
            ).with_updates(language=self.language)

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(
        app,
        ["--json", "lib", "get", "movie:55", "--tmdb-language", "ja"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert requested == [("movie", 55, "ja")]
    assert payload["items"][0]["entry"]["metadata"]["name"] == "エイリアン"
    assert "language" not in payload["items"][0]["entry"]["metadata"]
    assert payload["items"][0]["entry"]["metadata"]["name"] == "エイリアン"
    cached = store.attach_metadata_summary_models(store.list_entry_models(), language="ja")[0]
    assert cached.metadata is None


def test_library_get_live_meta_surfaces_specific_tmdb_errors(
    tmp_path,
    monkeypatch,
) -> None:
    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
    )
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            raise TMDbRequestError(
                "TMDb response had an unexpected shape: results.0.softcore: "
                "Extra inputs are not permitted"
            )

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "lib", "get", "movie:55", "--live-meta"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["items"][0]["entry"]["id"] == "movie:55"
    assert "TMDb response had an unexpected shape: results.0.softcore" in result.stderr


def test_library_get_human_output_uses_entry_sections_not_a_table(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["lib", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    assert "Library entries\n" in result.stdout
    assert "Alien\n" in result.stdout
    assert "movie:55\n" in result.stdout
    assert "  ID  Status" not in result.stdout
    assert "  Watch status      watched\n" in result.stdout
    assert "  Score             4\n" in result.stdout
    assert "  Favorite          yes\n" in result.stdout
    assert "  Date saved        26/05/01\n" in result.stdout
    assert "  Date started      26/05/02\n" in result.stdout
    assert "  Date finished     26/05/09\n" in result.stdout
    assert "  Custom poster" not in result.stdout
    assert "  Kind" not in result.stdout
    assert "  Library updated" not in result.stdout
    assert "  Tracking updated" not in result.stdout
    assert "  Schema" not in result.stdout
    assert "  Episode progress  S1:E12 (26/05/08)\n" in result.stdout
    assert "  Notes\n" in result.stdout
    assert "    Round trip\n" in result.stdout


def test_library_get_human_metadata_none_preserves_cached_display_title_without_rows(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summaries(
        [_rich_metadata("movie", 55, name="Alien")],
        depth=MetadataDepth.FULL,
    )

    result = runner.invoke(app, ["lib", "get", "movie:55", "--metadata", "none"])

    assert result.exit_code == 0, result.output
    assert "\nAlien\n" in result.stdout
    assert "  Title             Alien\n" in result.stdout
    assert "  Overview" not in result.stdout
    assert "  Link" not in result.stdout
    assert "  Genres" not in result.stdout
    assert "  Name translations" not in result.stdout


@pytest.mark.parametrize(
    ("metadata_depth", "expected", "unexpected"),
    [
        (
            "summary",
            ("Overview", "On air", "Runtime"),
            (
                "Link",
                "Poster path",
                "Backdrop path",
                "Logo path",
                "Genres",
                "Rating",
                "Name translations",
                "Season summaries",
            ),
        ),
        (
            "details",
            (
                "Overview",
                "Link",
                "Poster path",
                "Backdrop path",
                "Logo path",
                "Genres",
                "Rating",
                "Original language",
                "Release date",
            ),
            ("Name translations", "Season summaries", "Episode summaries"),
        ),
        (
            "full",
            (
                "Overview",
                "Link",
                "Poster path",
                "Backdrop path",
                "Logo path",
                "Genres",
                "Rating",
                "Name translations",
                "Season summaries",
                "Episode summaries",
            ),
            (),
        ),
    ],
)
def test_library_get_human_output_respects_metadata_depth(
    tmp_path,
    monkeypatch,
    metadata_depth: str,
    expected: tuple[str, ...],
    unexpected: tuple[str, ...],
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summaries(
        [_rich_metadata("movie", 55, name="Alien")],
        depth=MetadataDepth.FULL,
    )

    result = runner.invoke(app, ["lib", "get", "movie:55", "--metadata", metadata_depth])

    assert result.exit_code == 0, result.output
    assert "\nAlien\n" in result.stdout
    for text in expected:
        assert f"  {text}" in result.stdout
    for text in unexpected:
        assert f"  {text}" not in result.stdout


def test_library_get_not_found_is_item_error_and_all_failures_exit_nonzero(
    tmp_path,
    monkeypatch,
) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "lib", "get", "movie:404"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"] == [
        {
            "id": "movie:404",
            "status": "error",
            "error": {"code": "not_found", "message": "Library entry not found."},
        }
    ]


def test_library_get_invalid_identity_is_item_error_without_network(monkeypatch) -> None:
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

    result = runner.invoke(app, ["--json", "lib", "get", "book:1"])

    assert result.exit_code == 1
    assert requests == []
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"][0]["id"] == "book:1"
    assert payload["items"][0]["status"] == "error"
    assert payload["items"][0]["error"]["code"] == "invalid_id"


def test_library_get_human_output_uses_parent_series_title_for_seasons(
    tmp_path,
    monkeypatch,
) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("season:22:1:33", "season", 33),
    )
    store.upsert_metadata_summary(_metadata_summary("series", 22, name="Cowboy Bebop"))
    store.upsert_metadata_summary(
        _metadata_summary("season", 33, name="Season 1", parent_series_id=22, season_number=1)
    )

    json_result = runner.invoke(app, ["--json", "lib", "get", "season:22:1:33"])
    result = runner.invoke(app, ["lib", "get", "season:22:1:33"])

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.stdout)
    assert payload["items"][0]["entry"]["metadata"]["name"] == "Season 1"
    assert payload["items"][0]["entry"]["metadata"]["parent_series_title"] == "Cowboy Bebop"
    assert result.exit_code == 0, result.output
    assert "Cowboy Bebop\n" in result.stdout
    assert "  Title             Cowboy Bebop\n" in result.stdout
    assert "  Season title      Season 1\n" in result.stdout


def test_library_get_partial_batch_preserves_caller_order(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("series:22", "series", 22))

    result = runner.invoke(
        app,
        [
            "--json",
            "lib",
            "get",
            "bad",
            "series:22",
            "season:22:3:33",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 3, "found": 1, "errors": 2}
    assert [item["id"] for item in payload["items"]] == [
        "bad",
        "series:22",
        "season:22:3:33",
    ]
    assert [item["status"] for item in payload["items"]] == ["error", "found", "error"]


def test_library_init_redacts_tokens_from_cloudkit_request_errors(monkeypatch) -> None:
    store = _store_with_cloudkit_token("bad-web-secret-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "serverErrorCode": "BAD_REQUEST",
                    "reason": (
                        "ckWebAuthToken=bad-web-secret-token "
                        "ckAPIToken=api-secret-token "
                        "https://callback.example/done?ckWebAuthToken=callback-secret-token"
                    ),
                    "webAuthToken": "successor-secret-token",
                },
            )
        )
    )
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "lib", "init"])

    assert result.exit_code == 2
    assert result.stdout == ""
    combined = result.stdout + result.stderr
    assert "BAD_REQUEST" in combined
    assert "api-secret-token" not in combined
    assert "bad-web-secret-token" not in combined
    assert "successor-secret-token" not in combined
    assert "callback-secret-token" not in combined
    assert "https://callback.example/done" not in combined


def test_library_init_emits_stderr_progress_without_touching_json_stdout(
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
        _live_record("movie:55", "movie", 55),
        _live_record("movie:66", "movie", 66),
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

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            return _metadata_summary(
                identity.entry_type, identity.tmdb_id, name=f"Movie {identity.tmdb_id}"
            )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)
    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "lib", "init"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["records"] == 2
    assert "[progress] Starting local library cache rebuild from CloudKit." in result.stderr
    assert "[progress] Fetched page 1: 2 records (2 total)." in result.stderr
    assert "[progress] Hydrating TMDb metadata for 2 entries." in result.stderr
    assert "[progress] TMDb metadata 1/2 complete (0 errors)." in result.stderr
    assert "[progress] TMDb metadata 2/2 complete (0 errors)." in result.stderr
    assert "tmdb-secret-token" not in result.stderr
    assert "api-secret-token" not in result.stderr


def test_library_init_verbose_cloudkit_logs_are_redacted(
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

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Alien")

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--verbose", "--json", "lib", "init"])

    assert result.exit_code == 0, result.output
    assert (
        "[debug] CloudKit request -> GET https://api.apple-cloudkit.com/database/1/"
        in result.stderr
    )
    assert (
        "[debug] CloudKit request -> POST https://api.apple-cloudkit.com/database/1/"
        in result.stderr
    )
    assert "[debug] CloudKit response <- HTTP 200 GET" in result.stderr
    assert "[debug] CloudKit payload <- HTTP 200" in result.stderr
    assert "[debug] Library cache refresh decision -> rebuild" in result.stderr
    assert "[debug] TMDb summary client -> configured source=env:ANI_TMDB_API_KEY" in result.stderr
    assert "json={" not in result.stderr
    assert '"syncToken"' not in result.stderr
    assert "t1" not in result.stderr
    assert "tmdb-secret-token" not in result.stderr
    assert "api-secret-token" not in result.stderr
    assert "web-secret-token" not in result.stderr
    assert "ckWebAuthToken=web-secret-token" not in result.stderr
    assert "<redacted:ckAPIToken>" in result.stderr or "<redacted:sensitive-url>" in result.stderr


def test_library_tombstone_decodes_from_identity_fields_and_deleted_at() -> None:
    decoded = decode_library_entry_record(
        _cloudkit_record(
            _tombstone_record(
                "season:22:3:33",
                "season",
                33,
                parent_series_id=22,
                season_number=3,
            )
        )
    )

    assert decoded.kind == "tombstone"
    assert decoded.identity == "season:22:3:33"
    assert decoded.schema_version == 2
    assert decoded.tmdb_id == 33
    assert decoded.entry_type == "season"
    assert decoded.parent_series_id == 22
    assert decoded.season_number == 3
    assert decoded.deleted_at == "2026-05-12T00:00:00Z"


def test_library_get_tombstone_identity_is_treated_as_not_found(
    tmp_path,
    monkeypatch,
) -> None:
    _install_cached_entry(
        tmp_path,
        monkeypatch,
        _tombstone_record("season:22:3:33", "season", 33, parent_series_id=22, season_number=3),
    )

    result = runner.invoke(app, ["--json", "lib", "get", "season:22:3:33"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"] == [
        {
            "id": "season:22:3:33",
            "status": "error",
            "error": {"code": "not_found", "message": "Library entry not found."},
        }
    ]


def test_library_decoder_rejects_future_schema_versions() -> None:
    record = _live_record("movie:55", "movie", 55)
    record["fields"]["schemaVersion"] = {"value": 3}

    with pytest.raises(LibraryRecordDecodeError, match="Unsupported LibraryEntry schema version 3"):
        decode_library_entry_record(_cloudkit_record(record))


def test_library_decoder_accepts_cloudkit_int64_boolean_wrappers() -> None:
    record = _live_record("movie:55", "movie", 55)
    for field in (
        "onDisplay",
        "isDateTrackingEnabled",
        "favorite",
        "usingCustomPoster",
    ):
        record["fields"][field] = {
            "type": "INT64",
            "value": 1 if field != "usingCustomPoster" else 0,
        }

    decoded = decode_library_entry_record(_cloudkit_record(record))

    assert decoded.on_display is True
    assert decoded.is_date_tracking_enabled is True
    assert decoded.favorite is True
    assert decoded.using_custom_poster is False
    assert decoded.custom_poster_path is None


def test_library_decoder_accepts_empty_notes() -> None:
    record = _live_record("movie:55", "movie", 55)
    record["fields"]["notes"] = {"type": "STRING", "value": ""}

    decoded = decode_library_entry_record(_cloudkit_record(record))

    assert decoded.notes == ""


def test_library_decoder_uses_swift_reference_epoch_for_episode_progress_dates() -> None:
    record = _live_record("movie:55", "movie", 55)
    decoded = decode_library_entry_record(_cloudkit_record(record))

    assert decoded.date_saved == "2026-05-01T00:00:00Z"
    assert decoded.date_started == "2026-05-02T00:00:00Z"
    assert decoded.episode_progresses[0].updated_at == "2026-05-08T00:00:00Z"


def _install_lookup(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> MemorySecretStore:
    store = _store_with_cloudkit_token("web-secret-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(library_commands, "default_secret_store", lambda: store)
    monkeypatch.setattr(library_commands, "library_lock_factory", lambda path: null_lock(path))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)
    return store


def _install_cached_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, Any],
) -> LibraryCacheStore:
    store = create_seeded_cache_store(monkeypatch, tmp_path, record)
    patch_library_read_store(monkeypatch, library_commands, store)
    return store


def _live_record(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    on_display: bool = False,
) -> dict[str, Any]:
    return live_record(
        identity,
        entry_type,
        tmdb_id,
        on_display=on_display,
        using_custom_poster=True,
        custom_poster_path="/stale/custom.jpg",
        custom_poster_url="https://image.tmdb.org/t/p/w342/current/custom.jpg",
        episode_progresses=episode_progresses_bytes(),
    )


def _tombstone_record(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    parent_series_id: int | None = None,
    season_number: int | None = None,
) -> dict[str, Any]:
    return tombstone_record(
        identity,
        entry_type,
        tmdb_id,
        parent_series_id=parent_series_id,
        season_number=season_number,
    )


def _seed_get_library(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        _live_record("movie:55", "movie", 55),
        _live_record("series:22", "series", 22),
    )


def test_library_get_reads_ids_from_stdin_in_caller_order(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["--json", "lib", "get", "series:22", "-", "--metadata", "none"],
        input="# ids from a pipeline\nmovie:55  movie:404\n\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["id"] for item in payload["items"]] == [
        "series:22",
        "movie:55",
        "movie:404",
    ]
    assert payload["summary"] == {"requested": 3, "found": 2, "errors": 1}


def test_library_get_rejects_empty_stdin(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)

    result = runner.invoke(app, ["--json", "lib", "get", "-"], input="\n# nothing\n")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No AniShelf ids were provided on stdin." in result.stderr


def test_library_get_strict_fails_on_partial_errors(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)

    lenient = runner.invoke(app, ["--json", "lib", "get", "movie:55", "bogus"])
    strict = runner.invoke(app, ["--json", "lib", "get", "movie:55", "bogus", "--strict"])
    strict_clean = runner.invoke(app, ["--json", "lib", "get", "movie:55", "--strict"])

    assert lenient.exit_code == 0, lenient.output
    assert strict.exit_code == 1
    assert json.loads(strict.stdout)["summary"]["errors"] == 1
    assert strict_clean.exit_code == 0, strict_clean.output


def test_library_get_stdin_handles_bom_crlf_and_duplicates(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["--json", "lib", "get", "-", "--metadata", "none"],
        input="﻿movie:55\r\nmovie:55\r\nseries:22\r\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["id"] for item in payload["items"]] == ["movie:55", "movie:55", "series:22"]
    assert payload["summary"] == {"requested": 3, "found": 3, "errors": 0}


@pytest.mark.parametrize(
    ("args", "stdin", "message"),
    [
        (["-", "-"], "movie:55\n", "Pass `-` at most once"),
        (["-"], b"movie:55\n\xff\n", "not valid UTF-8"),
    ],
)
def test_library_get_rejects_unusable_stdin(
    tmp_path,
    monkeypatch,
    args: list[str],
    stdin: str | bytes,
    message: str,
) -> None:
    _seed_get_library(tmp_path, monkeypatch)

    result = runner.invoke(app, ["--json", "lib", "get", *args], input=stdin)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert message in result.stderr


def test_library_get_refuses_to_wait_on_an_interactive_terminal(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)
    monkeypatch.setattr(library_commands, "_stdin_is_interactive", lambda: True)

    result = runner.invoke(app, ["--json", "lib", "get", "-"])

    assert result.exit_code == 2
    assert "pipe them in" in result.stderr


def test_library_get_does_not_read_stdin_without_a_dash(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)

    # Undecodable stdin would fail with exit 2 if it were read.
    result = runner.invoke(
        app,
        ["--json", "lib", "get", "movie:55", "--metadata", "none"],
        input=b"\xff\xfe",
    )

    assert result.exit_code == 0, result.output


def test_library_get_handles_batches_larger_than_one_sql_chunk(tmp_path, monkeypatch) -> None:
    _seed_get_library(tmp_path, monkeypatch)
    missing_ids = [f"movie:{100_000 + index}" for index in range(1_200)]

    result = runner.invoke(
        app,
        ["--json", "lib", "get", "-", "--metadata", "none"],
        input="\n".join([*missing_ids, "series:22"]),
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["summary"] == {
        "requested": 1_201,
        "found": 1,
        "errors": 1_200,
    }
