from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from anishelf_cli.cli import groups
from anishelf_cli.cli.root import app
from anishelf_cli.config import KEYCHAIN_ACCOUNT
from anishelf_cli.library import LibraryRecordDecodeError, decode_library_entry_record
from anishelf_cli.secrets import cloudkit_web_auth_token_secret

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


def test_library_get_success_json_looks_up_records_in_library_zone(monkeypatch) -> None:
    store = _store_with_cloudkit_token("old-web-secret-token")
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)
    monkeypatch.setattr(groups, "library_lock_factory", lambda path: null_lock(path))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "webAuthToken": "new-web-secret-token",
                "records": [_live_record("movie:55", "movie", 55)],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(groups, "_make_http_client", lambda: client)

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

    request = requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith(
        "/database/1/iCloud.com.samuelhe.MyAnimeList/production/private/records/lookup"
    )
    assert request.url.params["ckAPIToken"] == "api-secret-token"
    assert request.url.params["ckWebAuthToken"] == "old-web-secret-token"
    assert json.loads(request.content) == {
        "records": [{"recordName": "movie:55"}],
        "zoneID": {"zoneName": "AniShelfLibrary"},
    }
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "new-web-secret-token"
    combined = result.stdout + result.stderr
    assert "api-secret-token" not in combined
    assert "old-web-secret-token" not in combined
    assert "new-web-secret-token" not in combined


def test_library_get_accepts_command_level_json_after_subcommand(monkeypatch) -> None:
    _install_lookup(monkeypatch, {"records": [_live_record("movie:55", "movie", 55)]})

    result = runner.invoke(app, ["library", "get", "--json", "movie:55"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "movie:55"


def test_library_get_accepts_command_level_json_after_identity(monkeypatch) -> None:
    _install_lookup(monkeypatch, {"records": [_live_record("movie:55", "movie", 55)]})

    result = runner.invoke(app, ["library", "get", "movie:55", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 1, "errors": 0}
    assert payload["items"][0]["entry"]["identity"] == "movie:55"


def test_library_get_human_output_uses_entry_sections_not_a_table(monkeypatch) -> None:
    _install_lookup(monkeypatch, {"records": [_live_record("movie:55", "movie", 55)]})

    result = runner.invoke(app, ["library", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    assert "Library entries\n" in result.stdout
    assert "movie:55\n" in result.stdout
    assert "  Identity  Status" not in result.stdout
    assert "  Watch status      watched\n" in result.stdout
    assert "  Score             4\n" in result.stdout
    assert "  Favorite          yes\n" in result.stdout
    assert "  Date saved        2026-05-01T00:00:00Z\n" in result.stdout
    assert "  Custom poster     /current/custom.jpg\n" in result.stdout
    assert "  Episode progress  S1:E12 (2026-05-08T00:00:00Z)\n" in result.stdout
    assert "  Notes             Round trip\n" in result.stdout


def test_library_get_not_found_is_item_error_and_all_failures_exit_nonzero(monkeypatch) -> None:
    _install_lookup(
        monkeypatch,
        {
            "records": [
                {
                    "recordName": "movie:404",
                    "serverErrorCode": "NOT_FOUND",
                    "reason": "Record not found.",
                }
            ]
        },
    )

    result = runner.invoke(app, ["--json", "library", "get", "movie:404"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"] == [
        {
            "identity": "movie:404",
            "status": "error",
            "error": {"code": "not_found", "message": "Record not found."},
        }
    ]


def test_library_get_invalid_identity_is_item_error_without_network(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    monkeypatch.setattr(
        groups,
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


def test_library_get_partial_batch_preserves_caller_order(monkeypatch) -> None:
    _install_lookup(
        monkeypatch,
        {
            "records": [
                _live_record("series:22", "series", 22),
                {
                    "recordName": "season:22:3:33",
                    "serverErrorCode": "NOT_FOUND",
                },
            ]
        },
    )

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


def test_library_get_redacts_tokens_from_cloudkit_request_errors(monkeypatch) -> None:
    store = _store_with_cloudkit_token("bad-web-secret-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)
    monkeypatch.setattr(groups, "library_lock_factory", lambda path: null_lock(path))

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
    monkeypatch.setattr(groups, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "library", "get", "movie:55"])

    assert result.exit_code == 2
    assert result.stdout == ""
    combined = result.stdout + result.stderr
    assert "BAD_REQUEST" in combined
    assert "api-secret-token" not in combined
    assert "bad-web-secret-token" not in combined
    assert "successor-secret-token" not in combined
    assert "callback-secret-token" not in combined
    assert "https://callback.example/done" not in combined


def test_library_tombstone_decodes_from_identity_fields_and_deleted_at() -> None:
    decoded = decode_library_entry_record(
        _tombstone_record("season:22:3:33", "season", 33, parent_series_id=22, season_number=3)
    )

    assert decoded == {
        "kind": "tombstone",
        "identity": "season:22:3:33",
        "schema_version": 2,
        "tmdb_id": 33,
        "entry_type": "season",
        "parent_series_id": 22,
        "season_number": 3,
        "deleted_at": "2026-05-12T00:00:00Z",
    }


def test_library_decoder_rejects_future_schema_versions() -> None:
    record = _live_record("movie:55", "movie", 55)
    record["fields"]["schemaVersion"] = {"value": 3}

    with pytest.raises(LibraryRecordDecodeError, match="Unsupported LibraryEntry schema version 3"):
        decode_library_entry_record(record)


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

    decoded = decode_library_entry_record(record)

    assert decoded["on_display"] is True
    assert decoded["is_date_tracking_enabled"] is True
    assert decoded["favorite"] is True
    assert decoded["using_custom_poster"] is False
    assert decoded["custom_poster_path"] is None


def test_library_decoder_accepts_empty_notes() -> None:
    record = _live_record("movie:55", "movie", 55)
    record["fields"]["notes"] = {"type": "STRING", "value": ""}

    decoded = decode_library_entry_record(record)

    assert decoded["notes"] == ""


def test_library_decoder_uses_swift_reference_epoch_for_episode_progress_dates() -> None:
    record = _live_record("movie:55", "movie", 55)
    decoded = decode_library_entry_record(record)

    assert decoded["date_saved"] == "2026-05-01T00:00:00Z"
    assert decoded["date_started"] == "2026-05-02T00:00:00Z"
    assert decoded["episode_progresses"][0]["updated_at"] == "2026-05-08T00:00:00Z"


def _install_lookup(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> MemorySecretStore:
    store = _store_with_cloudkit_token("web-secret-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)
    monkeypatch.setattr(groups, "library_lock_factory", lambda path: null_lock(path))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    monkeypatch.setattr(groups, "_make_http_client", lambda: client)
    return store


def _store_with_cloudkit_token(token: str) -> MemorySecretStore:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, KEYCHAIN_ACCOUNT, token)
    return store


def _live_record(identity: str, entry_type: str, tmdb_id: int) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schemaVersion": 2,
        "tmdbID": tmdb_id,
        "entryType": entry_type,
        "onDisplay": False,
        "dateSaved": "2026-05-01T00:00:00Z",
        "watchStatus": "watched",
        "dateStarted": "2026-05-02T00:00:00Z",
        "dateFinished": "2026-05-09T00:00:00Z",
        "isDateTrackingEnabled": False,
        "score": 4,
        "favorite": True,
        "notes": "Round trip",
        "usingCustomPoster": True,
        "customPosterPath": "/stale/custom.jpg",
        "customPosterURL": "https://image.tmdb.org/t/p/w342/current/custom.jpg",
        "episodeProgresses": _episode_progresses_bytes(),
        "libraryUpdatedAt": "2026-05-10T00:00:00Z",
        "trackingUpdatedAt": "2026-05-11T00:00:00Z",
    }
    if entry_type == "season":
        parts = identity.split(":")
        fields["parentSeriesID"] = int(parts[1])
        fields["seasonNumber"] = int(parts[2])
    return _record(identity, fields)


def _tombstone_record(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    parent_series_id: int | None = None,
    season_number: int | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schemaVersion": 2,
        "tmdbID": tmdb_id,
        "entryType": entry_type,
        "deletedAt": "2026-05-12T00:00:00Z",
    }
    if parent_series_id is not None:
        fields["parentSeriesID"] = parent_series_id
    if season_number is not None:
        fields["seasonNumber"] = season_number
    return _record(identity, fields)


def _record(identity: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "recordName": identity,
        "recordType": "LibraryEntry",
        "fields": {name: {"value": value} for name, value in fields.items()},
    }


def _episode_progresses_bytes() -> str:
    payload = json.dumps(
        [
            {
                "seasonNumber": 1,
                "watchedThroughEpisode": 12,
                "updatedAt": 799891200.0,
            }
        ],
        sort_keys=True,
    ).encode()
    return base64.b64encode(payload).decode()
