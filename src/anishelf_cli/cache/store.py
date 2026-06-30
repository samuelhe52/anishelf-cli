from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock

from anishelf_cli import config
from anishelf_cli.cloudkit.executor import ANI_SHELF_LIBRARY_ZONE_NAME, ZoneChangesPage
from anishelf_cli.library import (
    LibraryIdentityError,
    decode_library_entry_record,
    parse_library_identity,
)

CACHE_SCHEMA_VERSION = "1"
ZONE_SYNC_TOKEN_META_KEY = "zone_sync_token"
REBUILD_SYNC_TOKEN_META_KEY = "rebuild_sync_token"

EntryKind = Literal["snapshot", "tombstone", "deleted"]


class LibraryCacheError(RuntimeError):
    pass


class LibraryCacheNotAvailableError(LibraryCacheError):
    pass


@dataclass(frozen=True, slots=True)
class LibraryCacheScope:
    container: str
    environment: str
    database: str
    zone: str
    user_record_name: str

    @classmethod
    def default_for_user(cls, user_record_name: str) -> LibraryCacheScope:
        return cls(
            container=config.DEFAULT_CONTAINER,
            environment=config.DEFAULT_ENVIRONMENT,
            database=config.DEFAULT_DATABASE,
            zone=ANI_SHELF_LIBRARY_ZONE_NAME,
            user_record_name=user_record_name,
        )

    def key_payload(self) -> dict[str, str]:
        return {
            "container": self.container,
            "environment": self.environment,
            "database": self.database,
            "zone": self.zone,
            "user_record_name": self.user_record_name,
        }

    def cache_key(self) -> str:
        encoded = json.dumps(self.key_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LibraryCacheStore:
    scope: LibraryCacheScope
    path: Path
    lock_path: Path

    @classmethod
    def for_scope(cls, scope: LibraryCacheScope) -> LibraryCacheStore:
        cache_key = scope.cache_key()
        cache_root = config.cache_dir() / "library"
        lock_root = config.data_dir() / "locks"
        cache_root.mkdir(parents=True, exist_ok=True)
        lock_root.mkdir(parents=True, exist_ok=True)
        return cls(
            scope=scope,
            path=cache_root / f"{cache_key}.sqlite3",
            lock_path=lock_root / f"library-cache.{cache_key}.lock",
        )

    @classmethod
    def find_default_scope(cls) -> LibraryCacheStore:
        cache_root = config.cache_dir() / "library"
        candidates: list[LibraryCacheStore] = []
        for path in sorted(cache_root.glob("*.sqlite3")) if cache_root.exists() else []:
            scope = _scope_from_existing_database(path)
            if scope is None:
                continue
            if (
                scope.container == config.DEFAULT_CONTAINER
                and scope.environment == config.DEFAULT_ENVIRONMENT
                and scope.database == config.DEFAULT_DATABASE
                and scope.zone == ANI_SHELF_LIBRARY_ZONE_NAME
            ):
                candidates.append(cls.for_scope(scope))

        if not candidates:
            raise LibraryCacheNotAvailableError(
                "No local library cache is available. Run without --offline to refresh first."
            )
        if len(candidates) > 1:
            raise LibraryCacheNotAvailableError(
                "Multiple user-scoped library caches are available. Run without --offline "
                "to select the authenticated user."
            )
        return candidates[0]

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.lock_path)):
            yield

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            _initialize_schema(db)
            _write_scope_metadata(db, self.scope)

    def read_sync_token(self) -> str | None:
        with self._connect_initialized() as db:
            return _read_meta(db, ZONE_SYNC_TOKEN_META_KEY)

    def has_entries(self) -> bool:
        with self._connect_initialized() as db:
            row = db.execute("SELECT 1 FROM library_entries LIMIT 1").fetchone()
            return row is not None

    def begin_rebuild(self) -> None:
        with self._connect_initialized() as db:
            db.execute("BEGIN")
            db.execute("DROP TABLE IF EXISTS library_entries_stage")
            db.execute(_entries_table_sql("library_entries_stage"))
            _create_entries_indexes(db, "library_entries_stage", "idx_library_entries_stage")
            db.execute("DELETE FROM cache_meta WHERE key = ?", (REBUILD_SYNC_TOKEN_META_KEY,))
            db.commit()

    def apply_page(self, page: ZoneChangesPage, *, staging: bool) -> None:
        table = "library_entries_stage" if staging else "library_entries"
        token_key = REBUILD_SYNC_TOKEN_META_KEY if staging else ZONE_SYNC_TOKEN_META_KEY
        with self._connect_initialized() as db:
            db.execute("BEGIN")
            for record in page.records:
                _apply_record(db, table, record)
            _write_meta(db, token_key, page.sync_token)
            db.commit()

    def finish_rebuild(self) -> None:
        with self._connect_initialized() as db:
            sync_token = _read_meta(db, REBUILD_SYNC_TOKEN_META_KEY)
            if not sync_token:
                raise LibraryCacheError("Cannot finish library cache rebuild without a sync token.")
            db.execute("BEGIN")
            db.execute("DELETE FROM library_entries")
            db.execute(
                "INSERT INTO library_entries SELECT * FROM library_entries_stage",
            )
            _write_meta(db, ZONE_SYNC_TOKEN_META_KEY, sync_token)
            db.execute("DELETE FROM cache_meta WHERE key = ?", (REBUILD_SYNC_TOKEN_META_KEY,))
            db.execute("DROP TABLE library_entries_stage")
            db.commit()

    def list_entries(self, *, include_tombstones: bool = False) -> list[dict[str, Any]]:
        where = "" if include_tombstones else "WHERE kind = 'snapshot'"
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM library_entries
                {where}
                ORDER BY date_saved DESC NULLS LAST, identity ASC
                """
            ).fetchall()
        return [_decoded_row(row) for row in rows]

    def search_cached_entries(
        self,
        *,
        movie_ids: set[int],
        series_ids: set[int],
    ) -> list[dict[str, Any]]:
        query_parts: list[str] = []
        params: list[int] = []
        if movie_ids:
            query_parts.append(
                "SELECT decoded_json, date_saved, identity "
                "FROM library_entries "
                "WHERE kind = 'snapshot' "
                f"AND entry_type = 'movie' AND tmdb_id IN ({_placeholders(movie_ids)})"
            )
            params.extend(sorted(movie_ids))
        if series_ids:
            query_parts.append(
                "SELECT decoded_json, date_saved, identity "
                "FROM library_entries "
                "WHERE kind = 'snapshot' "
                f"AND entry_type = 'series' AND tmdb_id IN ({_placeholders(series_ids)})"
            )
            params.extend(sorted(series_ids))
            query_parts.append(
                "SELECT decoded_json, date_saved, identity "
                "FROM library_entries "
                "WHERE kind = 'snapshot' "
                f"AND entry_type = 'season' AND parent_series_id IN ({_placeholders(series_ids)})"
            )
            params.extend(sorted(series_ids))
        if not query_parts:
            return []

        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM ({' UNION ALL '.join(query_parts)})
                ORDER BY date_saved DESC NULLS LAST, identity ASC
                """,
                params,
            ).fetchall()
        return [_decoded_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _connect_initialized(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        with self._connect() as db:
            yield db


def _initialize_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    db.execute(_entries_table_sql("library_entries"))
    _create_entries_indexes(db, "library_entries", "idx_library_entries")
    _write_meta(db, "schema_version", CACHE_SCHEMA_VERSION)


def _create_entries_indexes(db: sqlite3.Connection, table: str, prefix: str) -> None:
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_snapshot_sort "
        f"ON {table}(kind, date_saved DESC, identity ASC)"
    )
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_tmdb_lookup "
        f"ON {table}(kind, entry_type, tmdb_id)"
    )
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_parent_series_lookup "
        f"ON {table}(kind, entry_type, parent_series_id)"
    )
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_kind_deleted_at ON {table}(kind, deleted_at)"
    )


def _entries_table_sql(table: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            identity TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            parent_series_id INTEGER,
            season_number INTEGER,
            watch_status TEXT,
            score INTEGER,
            favorite INTEGER,
            on_display INTEGER,
            date_saved TEXT,
            date_started TEXT,
            date_finished TEXT,
            is_date_tracking_enabled INTEGER,
            notes TEXT,
            using_custom_poster INTEGER,
            custom_poster_path TEXT,
            library_updated_at TEXT,
            tracking_updated_at TEXT,
            deleted_at TEXT,
            schema_version INTEGER,
            record_change_tag TEXT,
            raw_record_json TEXT NOT NULL,
            decoded_json TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """


def _write_scope_metadata(db: sqlite3.Connection, scope: LibraryCacheScope) -> None:
    for key, value in scope.key_payload().items():
        _write_meta(db, f"scope.{key}", value)


def _read_meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def _write_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """
        INSERT INTO cache_meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _scope_from_existing_database(path: Path) -> LibraryCacheScope | None:
    try:
        db = sqlite3.connect(path)
    except sqlite3.Error:
        return None
    db.row_factory = sqlite3.Row
    try:
        with db:
            values = {
                key: _read_meta(db, f"scope.{key}")
                for key in ("container", "environment", "database", "zone", "user_record_name")
            }
    except sqlite3.Error:
        return None
    finally:
        db.close()
    if not all(values.values()):
        return None
    return LibraryCacheScope(
        container=str(values["container"]),
        environment=str(values["environment"]),
        database=str(values["database"]),
        zone=str(values["zone"]),
        user_record_name=str(values["user_record_name"]),
    )


def _apply_record(db: sqlite3.Connection, table: str, record: dict[str, Any]) -> None:
    if _record_deleted(record):
        _upsert_decoded_entry(db, table, _deleted_entry(record), record)
        return

    decoded = decode_library_entry_record(record)
    _upsert_decoded_entry(db, table, decoded, record)


def _upsert_decoded_entry(
    db: sqlite3.Connection,
    table: str,
    entry: dict[str, Any],
    raw_record: dict[str, Any],
) -> None:
    db.execute(
        f"""
        INSERT INTO {table} (
            identity,
            kind,
            entry_type,
            tmdb_id,
            parent_series_id,
            season_number,
            watch_status,
            score,
            favorite,
            on_display,
            date_saved,
            date_started,
            date_finished,
            is_date_tracking_enabled,
            notes,
            using_custom_poster,
            custom_poster_path,
            library_updated_at,
            tracking_updated_at,
            deleted_at,
            schema_version,
            record_change_tag,
            raw_record_json,
            decoded_json,
            cached_at
        )
        VALUES (
            :identity,
            :kind,
            :entry_type,
            :tmdb_id,
            :parent_series_id,
            :season_number,
            :watch_status,
            :score,
            :favorite,
            :on_display,
            :date_saved,
            :date_started,
            :date_finished,
            :is_date_tracking_enabled,
            :notes,
            :using_custom_poster,
            :custom_poster_path,
            :library_updated_at,
            :tracking_updated_at,
            :deleted_at,
            :schema_version,
            :record_change_tag,
            :raw_record_json,
            :decoded_json,
            :cached_at
        )
        ON CONFLICT(identity) DO UPDATE SET
            kind = excluded.kind,
            entry_type = excluded.entry_type,
            tmdb_id = excluded.tmdb_id,
            parent_series_id = excluded.parent_series_id,
            season_number = excluded.season_number,
            watch_status = excluded.watch_status,
            score = excluded.score,
            favorite = excluded.favorite,
            on_display = excluded.on_display,
            date_saved = excluded.date_saved,
            date_started = excluded.date_started,
            date_finished = excluded.date_finished,
            is_date_tracking_enabled = excluded.is_date_tracking_enabled,
            notes = excluded.notes,
            using_custom_poster = excluded.using_custom_poster,
            custom_poster_path = excluded.custom_poster_path,
            library_updated_at = excluded.library_updated_at,
            tracking_updated_at = excluded.tracking_updated_at,
            deleted_at = excluded.deleted_at,
            schema_version = excluded.schema_version,
            record_change_tag = excluded.record_change_tag,
            raw_record_json = excluded.raw_record_json,
            decoded_json = excluded.decoded_json,
            cached_at = excluded.cached_at
        """,
        _entry_row_params(entry, raw_record),
    )


def _entry_row_params(entry: dict[str, Any], raw_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": entry["identity"],
        "kind": entry["kind"],
        "entry_type": entry["entry_type"],
        "tmdb_id": entry["tmdb_id"],
        "parent_series_id": entry.get("parent_series_id"),
        "season_number": entry.get("season_number"),
        "watch_status": entry.get("watch_status"),
        "score": entry.get("score"),
        "favorite": _optional_bool_int(entry.get("favorite")),
        "on_display": _optional_bool_int(entry.get("on_display")),
        "date_saved": entry.get("date_saved"),
        "date_started": entry.get("date_started"),
        "date_finished": entry.get("date_finished"),
        "is_date_tracking_enabled": _optional_bool_int(entry.get("is_date_tracking_enabled")),
        "notes": entry.get("notes"),
        "using_custom_poster": _optional_bool_int(entry.get("using_custom_poster")),
        "custom_poster_path": entry.get("custom_poster_path"),
        "library_updated_at": entry.get("library_updated_at"),
        "tracking_updated_at": entry.get("tracking_updated_at"),
        "deleted_at": entry.get("deleted_at"),
        "schema_version": entry.get("schema_version"),
        "record_change_tag": _record_change_tag(raw_record),
        "raw_record_json": json.dumps(raw_record, sort_keys=True, separators=(",", ":")),
        "decoded_json": json.dumps(entry, sort_keys=True, separators=(",", ":")),
        "cached_at": _now_iso(),
    }


def _deleted_entry(record: dict[str, Any]) -> dict[str, Any]:
    record_name = _record_name(record)
    try:
        identity = parse_library_identity(record_name)
    except LibraryIdentityError as exc:
        raise LibraryCacheError(
            f"CloudKit deleted record has invalid identity {record_name}."
        ) from exc
    deleted_at = _deleted_timestamp(record)
    return {
        "kind": "deleted",
        "identity": identity.raw,
        "entry_type": identity.entry_type,
        "tmdb_id": identity.tmdb_id,
        "parent_series_id": identity.parent_series_id,
        "season_number": identity.season_number,
        "deleted_at": deleted_at,
    }


def _record_deleted(record: dict[str, Any]) -> bool:
    return record.get("deleted") is True


def _record_name(record: dict[str, Any]) -> str:
    if record_name := _optional_string(record.get("recordName")):
        return record_name
    record_id = record.get("recordID")
    if isinstance(record_id, dict) and (
        record_name := _optional_string(record_id.get("recordName"))
    ):
        return record_name
    raise LibraryCacheError("CloudKit deleted record is missing recordName.")


def _record_change_tag(record: dict[str, Any]) -> str | None:
    return _optional_string(record.get("recordChangeTag"))


def _deleted_timestamp(record: dict[str, Any]) -> str:
    modified = record.get("modified")
    if isinstance(modified, dict):
        timestamp = modified.get("timestamp")
        if isinstance(timestamp, int | float) and not isinstance(timestamp, bool):
            value = float(timestamp)
            if abs(value) > 10_000_000_000:
                value /= 1000
            return _iso_z(datetime.fromtimestamp(value, UTC))
    return _now_iso()


def _optional_bool_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    return None


def _decoded_row(row: sqlite3.Row) -> dict[str, Any]:
    value = json.loads(str(row["decoded_json"]))
    if not isinstance(value, dict):
        raise LibraryCacheError("Cached library entry is corrupt.")
    return value


def _placeholders(values: set[int]) -> str:
    return ", ".join("?" for _ in values)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
