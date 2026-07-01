from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from anishelf_cli.cache.scope import LibraryCacheScope
from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cloudkit.executor import ZoneChangesPage
from anishelf_cli.config import KEYCHAIN_ACCOUNT
from anishelf_cli.library.metadata import LibraryEntryMetadata
from anishelf_cli.models.transport.cloudkit import CloudKitRecord
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
def null_lock(path: Path) -> Generator[None]:
    _ = path
    yield


def isolate_paths(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ANISHELF_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ANISHELF_CLI_DATA_DIR", str(tmp_path / "data"))


def create_cache_store(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    user_record_name: str = "_user",
) -> LibraryCacheStore:
    isolate_paths(monkeypatch, tmp_path)
    store = LibraryCacheStore.for_scope(LibraryCacheScope.default_for_user(user_record_name))
    store.initialize()
    return store


def seed_cache_store(
    store: LibraryCacheStore,
    *records: dict[str, Any],
    sync_token: str = "t1",
) -> LibraryCacheStore:
    if records:
        store.apply_page(
            ZoneChangesPage(records=list(records), sync_token=sync_token, more_coming=False),
            staging=False,
        )
    return store


def create_seeded_cache_store(
    monkeypatch: Any,
    tmp_path: Path,
    *records: dict[str, Any],
    user_record_name: str = "_user",
    sync_token: str = "t1",
) -> LibraryCacheStore:
    store = create_cache_store(
        monkeypatch,
        tmp_path,
        user_record_name=user_record_name,
    )
    return seed_cache_store(store, *records, sync_token=sync_token)


def patch_library_read_store(monkeypatch: Any, target: Any, store: LibraryCacheStore) -> None:
    monkeypatch.setattr(target, "_library_store_for_read", lambda: store)


def store_with_cloudkit_token(token: str) -> MemorySecretStore:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, KEYCHAIN_ACCOUNT, token)
    return store


def metadata_summary(
    entry_type: str,
    tmdb_id: int,
    *,
    name: str,
    parent_series_id: int | None = None,
    season_number: int | None = None,
    source_version: str = "test",
) -> LibraryEntryMetadata:
    return LibraryEntryMetadata.model_validate(
        {
            "entry_type": entry_type,
            "tmdb_id": tmdb_id,
            "parent_series_id": parent_series_id,
            "season_number": season_number,
            "language": None,
            "name": name,
            "name_translations": {},
            "original_name": name,
            "overview": f"{name} overview.",
            "overview_translations": {},
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "logo_path": None,
            "original_language_code": "en",
            "on_air_date": "1979-05-25",
            "status": "Released" if entry_type == "movie" else "Returning Series",
            "genres": [{"id": 878, "name": "Science Fiction"}],
            "runtime_minutes": 117 if entry_type == "movie" else None,
            "season_count": 3 if entry_type == "series" else None,
            "episode_count": (
                22 if entry_type == "series" else 10 if entry_type == "season" else None
            ),
            "vote_average": 8.2,
            "vote_count": 15432,
            "popularity": 44.5,
            "link_to_details": f"https://www.themoviedb.org/{entry_type}/{tmdb_id}",
            "source_version": source_version,
        }
    )


def insert_legacy_v1_metadata_summary(
    store: LibraryCacheStore,
    *,
    metadata_key: str,
    entry_type: str,
    tmdb_id: int,
) -> None:
    with sqlite3.connect(store.path) as db:
        db.execute(
            """
            INSERT INTO tmdb_metadata_summary (
                metadata_key,
                entry_type,
                tmdb_id,
                parent_series_id,
                season_number,
                language,
                name,
                name_translations_json,
                original_name,
                overview,
                overview_translations_json,
                poster_path,
                backdrop_path,
                logo_path,
                original_language_code,
                on_air_date,
                link_to_details,
                fetched_at,
                source_version,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata_key,
                entry_type,
                tmdb_id,
                None,
                None,
                "",
                "Alien",
                "{}",
                "Alien",
                "Legacy overview.",
                "{}",
                "/poster.jpg",
                "/backdrop.jpg",
                None,
                "en",
                "1979-05-25",
                f"https://www.themoviedb.org/{entry_type}/{tmdb_id}",
                "2026-06-30T00:00:00Z",
                "tmdbsummary.v1",
                json.dumps(
                    {
                        "entry_type": entry_type,
                        "tmdb_id": tmdb_id,
                        "language": None,
                        "name": "Alien",
                        "name_translations": {},
                        "original_name": "Alien",
                        "overview": "Legacy overview.",
                        "overview_translations": {},
                        "poster_path": "/poster.jpg",
                        "backdrop_path": "/backdrop.jpg",
                        "logo_path": None,
                        "original_language_code": "en",
                        "on_air_date": "1979-05-25",
                        "link_to_details": f"https://www.themoviedb.org/{entry_type}/{tmdb_id}",
                        "fetched_at": "2026-06-30T00:00:00Z",
                        "source_version": "tmdbsummary.v1",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        db.commit()


def snapshot_entry_payload(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    parent_series_id: int | None = None,
    season_number: int | None = None,
) -> dict[str, object]:
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
    if parent_series_id is not None:
        payload["parent_series_id"] = parent_series_id
    if season_number is not None:
        payload["season_number"] = season_number
    return payload


def cloudkit_record(record: dict[str, Any]) -> CloudKitRecord:
    return CloudKitRecord.model_validate(record)


def live_record(
    identity: str,
    entry_type: str,
    tmdb_id: int,
    *,
    date_saved: str = "2026-05-01T00:00:00Z",
    watch_status: str = "watched",
    on_display: bool = True,
    using_custom_poster: bool = False,
    custom_poster_path: str | None = None,
    custom_poster_url: str | None = None,
    episode_progresses: Any = None,
    parent_series_id: int | None = None,
    season_number: int | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "schemaVersion": 2,
        "tmdbID": tmdb_id,
        "entryType": entry_type,
        "onDisplay": on_display,
        "dateSaved": date_saved,
        "watchStatus": watch_status,
        "dateStarted": "2026-05-02T00:00:00Z",
        "dateFinished": "2026-05-09T00:00:00Z",
        "isDateTrackingEnabled": False,
        "score": 4,
        "favorite": True,
        "notes": "Round trip",
        "usingCustomPoster": using_custom_poster,
        "episodeProgresses": [] if episode_progresses is None else episode_progresses,
        "libraryUpdatedAt": "2026-05-10T00:00:00Z",
        "trackingUpdatedAt": "2026-05-11T00:00:00Z",
    }
    if custom_poster_path is not None:
        fields["customPosterPath"] = custom_poster_path
    if custom_poster_url is not None:
        fields["customPosterURL"] = custom_poster_url
    if entry_type == "season":
        if parent_series_id is None or season_number is None:
            _, series_id, season_index, _ = identity.split(":")
            parent_series_id = int(series_id)
            season_number = int(season_index)
        fields["parentSeriesID"] = parent_series_id
        fields["seasonNumber"] = season_number
    return _record(identity, fields)


def tombstone_record(
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


def episode_progresses_bytes() -> str:
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


def _record(identity: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "recordName": identity,
        "recordType": "LibraryEntry",
        "recordChangeTag": f"tag-{identity}",
        "fields": {name: {"value": value} for name, value in fields.items()},
    }
