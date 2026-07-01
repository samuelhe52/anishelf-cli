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
from anishelf_cli.secrets import cloudkit_web_auth_token_secret
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
    insert_legacy_v1_metadata_summary as _insert_legacy_v1_metadata_summary,
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


def test_library_get_requires_init_before_lookup(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    result = runner.invoke(app, ["--json", "library", "get", "movie:55"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Run `ani library init` first" in result.stderr


def test_library_status_reports_uninitialized_cache(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["--json", "library", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["initialized"] is False
    assert payload["summary"]["scope_count"] == 0
    assert payload["active"]["scope"] is None
    assert payload["active"]["entries"] == 0
    assert payload["active"]["has_sync_token"] is False
    assert payload["active"]["metadata"] == {
        "tracked_entries": 0,
        "hydrated_entries": 0,
        "missing_entries": 0,
        "ready": False,
    }


def test_library_status_reports_initialized_cache(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "library", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["initialized"] is True
    assert payload["summary"]["scope_count"] == 1
    assert payload["active"]["entries"] == 1
    assert payload["active"]["has_sync_token"] is True
    assert payload["active"]["scope"]["user_record_name"] == "_user"
    assert payload["active"]["metadata"] == {
        "tracked_entries": 1,
        "hydrated_entries": 0,
        "missing_entries": 1,
        "ready": False,
    }


def test_library_status_reports_metadata_ready_when_summary_is_cached(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["--json", "library", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active"]["metadata"] == {
        "tracked_entries": 1,
        "hydrated_entries": 1,
        "missing_entries": 0,
        "ready": True,
    }


def test_library_status_treats_legacy_v1_summary_as_incomplete(
    tmp_path,
    monkeypatch,
) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    _insert_legacy_v1_metadata_summary(
        store,
        metadata_key="movie:55",
        entry_type="movie",
        tmdb_id=55,
    )

    result = runner.invoke(app, ["--json", "library", "status"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["active"]["metadata"] == {
        "tracked_entries": 1,
        "hydrated_entries": 0,
        "missing_entries": 1,
        "ready": False,
    }


def test_library_status_human_output_uses_empty_partial_complete_metadata_states(
    tmp_path,
    monkeypatch,
) -> None:
    _isolate_paths(monkeypatch, tmp_path)

    empty = runner.invoke(app, ["library", "status"])
    assert empty.exit_code == 0, empty.output
    assert "  Metadata           empty\n" in empty.stdout

    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    still_empty = runner.invoke(app, ["library", "status"])
    assert still_empty.exit_code == 0, still_empty.output
    assert "  Metadata           empty\n" in still_empty.stdout

    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))
    store.apply_page(
        ZoneChangesPage(
            records=[_live_record("movie:66", "movie", 66)],
            sync_token="t2",
            more_coming=False,
        ),
        staging=False,
    )
    partial = runner.invoke(app, ["library", "status"])
    assert partial.exit_code == 0, partial.output
    assert "  Metadata           partial\n" in partial.stdout

    store.upsert_metadata_summary(_metadata_summary("movie", 66, name="Aliens"))
    complete = runner.invoke(app, ["library", "status"])
    assert complete.exit_code == 0, complete.output
    assert "  Metadata           complete\n" in complete.stdout


def test_library_clear_cache_requires_confirmation(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["library", "clear-cache"], input="n\n")

    assert result.exit_code == 1
    assert "Aborted local library cache clear." in result.stderr
    assert LibraryCacheStore.find_default_scope().has_entries() is True


def test_library_clear_cache_yes_removes_all_local_cache_files(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.lock_path.parent.mkdir(parents=True, exist_ok=True)
    store.lock_path.write_text("locked")

    result = runner.invoke(app, ["--json", "library", "clear-cache", "--yes"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "cleared"
    assert payload["removed"]["cache_files"] == 1
    assert payload["removed"]["lock_files"] == 1
    assert not store.path.exists()
    assert not store.lock_path.exists()


def test_library_clear_cache_prompt_can_confirm(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "library", "clear-cache"], input="y\n")

    assert result.exit_code == 0, result.output
    assert not store.path.exists()


def test_library_init_then_get_success_json(tmp_path, monkeypatch) -> None:
    _isolate_paths(monkeypatch, tmp_path)
    store = _store_with_cloudkit_token("old-web-secret-token")
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
                        "records": [_live_record("movie:55", "movie", 55)],
                        "syncToken": "t1",
                        "moreComing": False,
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(library_commands, "_make_http_client", lambda: client)

    init_result = runner.invoke(app, ["--json", "library", "init"])
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(app, ["--json", "library", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["identity"] == "movie:55"
    assert payload["items"][0]["status"] == "found"
    entry = payload["items"][0]["entry"]
    assert entry["kind"] == "snapshot"
    assert entry["entry_type"] == "movie"
    assert entry["tmdb_id"] == 55
    assert entry["date_saved"] == "2026-05-01T00:00:00Z"
    assert entry["watch_status"] == "watched"
    assert entry["custom_poster_path"] == "/current/custom.jpg"
    assert entry["episode_progresses"] == [
        {
            "season_number": 1,
            "updated_at": "2026-05-08T00:00:00Z",
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

    result = runner.invoke(app, ["library", "get", "--json", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "movie:55"


def test_library_get_accepts_command_level_json_after_identity(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["library", "get", "movie:55", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "movie:55"


def test_library_get_reads_existing_cache(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "library", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "movie:55"


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

    result = runner.invoke(app, ["--json", "library", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "movie:55"
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

    result = runner.invoke(app, ["--json", "library", "get", "series:22", "--sync"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "series:22"
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

    result = runner.invoke(app, ["--json", "library", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["items"][0]["entry"]["identity"] == "movie:55"
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
    requested: list[tuple[str, int]] = []
    monkeypatch.setattr(
        library_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )

    class FakeTMDbClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "tmdb-secret-token"

        def fetch_summary(self, identity) -> LibraryEntryMetadata:
            requested.append((identity.entry_type, identity.tmdb_id))
            return _metadata_summary(identity.entry_type, identity.tmdb_id, name="Alien")

    monkeypatch.setattr(library_commands, "TMDbClient", FakeTMDbClient)

    result = runner.invoke(app, ["--json", "library", "get", "movie:55", "--live-meta"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert requested == [("movie", 55)]
    assert payload["items"][0]["entry"]["metadata"]["name"] == "Alien"
    assert payload["items"][0]["entry"]["metadata"]["genres"] == [
        {"id": 878, "name": "Science Fiction"}
    ]
    assert payload["items"][0]["entry"]["metadata"]["runtime_minutes"] == 117
    other_entry = store.attach_metadata_summary_models(
        list(store.get_entry_models_by_identity(["series:22"]).values())
    )[0]
    assert other_entry.metadata is None


def test_library_get_human_output_uses_entry_sections_not_a_table(tmp_path, monkeypatch) -> None:
    store = _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))
    store.upsert_metadata_summary(_metadata_summary("movie", 55, name="Alien"))

    result = runner.invoke(app, ["library", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    assert "Library entries\n" in result.stdout
    assert "Alien\n" in result.stdout
    assert "movie:55\n" in result.stdout
    assert "  Identity  Status" not in result.stdout
    assert "  Watch status      watched\n" in result.stdout
    assert "  Score             4\n" in result.stdout
    assert "  Favorite          yes\n" in result.stdout
    assert "  Date saved        2026-05-01\n" in result.stdout
    assert "  Custom poster     /current/custom.jpg\n" in result.stdout
    assert "  Episode progress  S1:E12 (2026-05-08T00:00:00Z)\n" in result.stdout
    assert "  Notes             Round trip\n" in result.stdout


def test_library_get_human_output_accepts_live_envelope_model() -> None:
    raw_envelope = {
        "items": [
            {
                "identity": "movie:55",
                "status": "found",
                "entry": {
                    "identity": "movie:55",
                    "kind": "snapshot",
                    "entry_type": "movie",
                    "tmdb_id": 55,
                    "schema_version": 2,
                    "on_display": True,
                    "date_saved": "2026-05-01T00:00:00Z",
                    "watch_status": "watched",
                    "date_started": None,
                    "date_finished": None,
                    "is_date_tracking_enabled": False,
                    "score": 4,
                    "favorite": True,
                    "notes": "",
                    "using_custom_poster": False,
                    "custom_poster_path": None,
                    "episode_progresses": [],
                    "library_updated_at": None,
                    "tracking_updated_at": None,
                    "metadata": {
                        "name": "Alien",
                        "original_name": "Alien",
                        "overview": "A crew answers a distress signal.",
                    },
                },
            }
        ],
        "summary": {"requested": 1, "found": 1, "errors": 0},
    }

    import io
    from contextlib import redirect_stdout

    from anishelf_cli.cli.presentation import render_library_get
    from anishelf_cli.models.output import LibraryGetEnvelope

    stream = io.StringIO()
    with redirect_stdout(stream):
        render_library_get(LibraryGetEnvelope.model_validate(raw_envelope))

    output = stream.getvalue()
    assert "Library entries\n" in output
    assert "Alien\n" in output
    assert "  Status            found\n" in output
    assert "  Identity          movie:55\n" in output
    assert "decode-error" not in output


def test_library_get_not_found_is_item_error_and_all_failures_exit_nonzero(
    tmp_path,
    monkeypatch,
) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "library", "get", "movie:404"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"] == [
        {
            "identity": "movie:404",
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

    result = runner.invoke(app, ["--json", "library", "get", "book:1"])

    assert result.exit_code == 1
    assert requests == []
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"][0]["identity"] == "book:1"
    assert payload["items"][0]["status"] == "error"
    assert payload["items"][0]["error"]["code"] == "invalid_identity"


def test_library_get_partial_batch_preserves_caller_order(tmp_path, monkeypatch) -> None:
    _install_cached_entry(tmp_path, monkeypatch, _live_record("series:22", "series", 22))

    result = runner.invoke(
        app,
        [
            "--json",
            "library",
            "get",
            "bad",
            "series:22",
            "season:22:3:33",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 3, "found": 1, "errors": 2}
    assert [item["identity"] for item in payload["items"]] == [
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

    result = runner.invoke(app, ["--json", "library", "init"])

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

    result = runner.invoke(app, ["--json", "library", "init"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["cache"]["records"] == 2
    assert "[progress] Starting local library cache rebuild from CloudKit." in result.stderr
    assert "[progress] Fetched page 1: 2 records (2 total)." in result.stderr
    assert "[progress] Hydrating TMDb summary metadata for 2 entries." in result.stderr
    assert "[progress] TMDb summary metadata 1/2 complete (0 errors)." in result.stderr
    assert "[progress] TMDb summary metadata 2/2 complete (0 errors)." in result.stderr
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

    result = runner.invoke(app, ["--verbose", "--json", "library", "init"])

    assert result.exit_code == 0, result.output
    assert (
        "[verbose] CloudKit request -> GET https://api.apple-cloudkit.com/database/1/"
        in result.stderr
    )
    assert (
        "[verbose] CloudKit request -> POST https://api.apple-cloudkit.com/database/1/"
        in result.stderr
    )
    assert "[verbose] CloudKit response <- HTTP 200 GET" in result.stderr
    assert "[verbose] CloudKit payload <- HTTP 200" in result.stderr
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

    result = runner.invoke(app, ["--json", "library", "get", "season:22:3:33"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"] == [
        {
            "identity": "season:22:3:33",
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


def _live_record(identity: str, entry_type: str, tmdb_id: int) -> dict[str, Any]:
    return live_record(
        identity,
        entry_type,
        tmdb_id,
        on_display=False,
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
