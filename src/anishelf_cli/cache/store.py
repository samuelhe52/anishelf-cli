from __future__ import annotations

import hashlib
import json
import os
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
TMDB_LEGACY_SUMMARY_SOURCE_VERSION = "tmdbsummary.v1"
TMDB_SUMMARY_SOURCE_VERSION = "tmdbsummary.v2"
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
    def library_cache_root(cls) -> Path:
        return config.cache_dir() / "library"

    @classmethod
    def library_lock_root(cls) -> Path:
        return config.data_dir() / "locks"

    @classmethod
    def find_default_scope(cls) -> LibraryCacheStore:
        cache_root = cls.library_cache_root()
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
                "No local library cache is available. Run `ani library init` first."
            )
        if len(candidates) > 1:
            raise LibraryCacheNotAvailableError(
                "Multiple user-scoped library caches are available. Run `ani library init` "
                "to select the authenticated user."
            )
        return candidates[0]

    @classmethod
    def existing_scopes(cls) -> list[LibraryCacheScope]:
        cache_root = cls.library_cache_root()
        scopes: list[LibraryCacheScope] = []
        for path in sorted(cache_root.glob("*.sqlite3")) if cache_root.exists() else []:
            scope = _scope_from_existing_database(path)
            if scope is not None:
                scopes.append(scope)
        return scopes

    @classmethod
    def remove_all_local_caches(cls) -> dict[str, int]:
        cache_root = cls.library_cache_root()
        lock_root = cls.library_lock_root()
        removed_cache_files = _remove_matching_files(cache_root, "*.sqlite3")
        removed_lock_files = _remove_matching_files(lock_root, "library-cache.*.lock")
        return {
            "cache_files": removed_cache_files,
            "lock_files": removed_lock_files,
        }

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

    def apply_page_and_collect_new_summary_targets(
        self,
        page: ZoneChangesPage,
        *,
        staging: bool,
    ) -> list[dict[str, Any]]:
        table = "library_entries_stage" if staging else "library_entries"
        token_key = REBUILD_SYNC_TOKEN_META_KEY if staging else ZONE_SYNC_TOKEN_META_KEY
        new_targets: list[dict[str, Any]] = []
        with self._connect_initialized() as db:
            db.execute("BEGIN")
            try:
                for record in page.records:
                    target = _summary_target_from_record(db, table, record)
                    _apply_record(db, table, record)
                    if target is not None and not _metadata_summary_exists(db, target):
                        new_targets.append(target)
                _write_meta(db, token_key, page.sync_token)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return _dedupe_summary_targets(new_targets)

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
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if not include_tombstones:
            where_parts.append("kind = 'snapshot'")
        if watch_status is not None:
            where_parts.append("watch_status = ?")
            params.append(watch_status)
        if hidden is not None:
            where_parts.append("kind = 'snapshot'")
            where_parts.append("on_display = ?")
            params.append(0 if hidden else 1)
        if favorite is not None:
            where_parts.append("kind = 'snapshot'")
            where_parts.append("favorite = ?")
            params.append(1 if favorite else 0)
        if on_display is not None:
            where_parts.append("kind = 'snapshot'")
            where_parts.append("on_display = ?")
            params.append(1 if on_display else 0)

        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        order_by = _list_order_by(sort)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(limit)

        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM library_entries
                {where}
                {order_by}
                {limit_clause}
                """,
                params,
            ).fetchall()
        return [_decoded_row(row) for row in rows]

    def get_entries_by_identity(self, identities: list[str]) -> dict[str, dict[str, Any]]:
        if not identities:
            return {}
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM library_entries
                WHERE kind = 'snapshot'
                AND identity IN ({_placeholders(identities)})
                """,
                identities,
            ).fetchall()
        entries = [_decoded_row(row) for row in rows]
        return {str(entry["identity"]): entry for entry in entries}

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
                FROM ({" UNION ALL ".join(query_parts)})
                ORDER BY date_saved DESC NULLS LAST, identity ASC
                """,
                params,
            ).fetchall()
        return [_decoded_row(row) for row in rows]

    def search_entries_by_title(
        self,
        title: str,
    ) -> list[dict[str, Any]]:
        query = title.strip()
        if not query:
            return []

        pattern = f"%{query.lower()}%"
        with self._connect_initialized() as db:
            rows = db.execute(
                """
                SELECT library_entries.decoded_json
                FROM library_entries
                LEFT JOIN tmdb_metadata_summary
                    ON tmdb_metadata_summary.metadata_key = CASE
                        WHEN library_entries.entry_type = 'season' THEN
                            'season:' || library_entries.parent_series_id || ':' ||
                            library_entries.season_number || ':' || library_entries.tmdb_id
                        ELSE
                            library_entries.entry_type || ':' || library_entries.tmdb_id
                    END
                    AND tmdb_metadata_summary.language = ''
                WHERE library_entries.kind = 'snapshot'
                    AND (
                        LOWER(library_entries.identity) LIKE ?
                        OR LOWER(COALESCE(tmdb_metadata_summary.name, '')) LIKE ?
                        OR LOWER(COALESCE(tmdb_metadata_summary.original_name, '')) LIKE ?
                    )
                ORDER BY library_entries.date_saved DESC NULLS LAST, library_entries.identity ASC
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        return [_decoded_row(row) for row in rows]

    def upsert_metadata_summary(self, summary: dict[str, Any]) -> None:
        with self._connect_initialized() as db:
            _upsert_metadata_summary(db, summary)

    def upsert_metadata_summaries(self, summaries: list[dict[str, Any]]) -> None:
        if not summaries:
            return
        with self._connect_initialized() as db:
            for summary in summaries:
                _upsert_metadata_summary(db, summary)

    def metadata_summary_targets_for_entries(
        self,
        entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return _dedupe_summary_targets(
            [
                {
                    "entry_type": entry["entry_type"],
                    "tmdb_id": entry["tmdb_id"],
                    "parent_series_id": entry.get("parent_series_id"),
                    "season_number": entry.get("season_number"),
                }
                for entry in entries
                if entry.get("kind") == "snapshot"
            ]
        )

    def missing_metadata_summary_targets(self) -> list[dict[str, Any]]:
        entries = self.list_entries(include_tombstones=False)
        if not entries:
            return []
        with self._connect_initialized() as db:
            missing: list[dict[str, Any]] = []
            for entry in entries:
                target = {
                    "entry_type": entry["entry_type"],
                    "tmdb_id": entry["tmdb_id"],
                    "parent_series_id": entry.get("parent_series_id"),
                    "season_number": entry.get("season_number"),
                }
                if _metadata_summary_state(db, target) == "missing":
                    missing.append(target)
        return _dedupe_summary_targets(missing)

    def outdated_metadata_summary_targets(self) -> list[dict[str, Any]]:
        entries = self.list_entries(include_tombstones=False)
        if not entries:
            return []
        with self._connect_initialized() as db:
            outdated: list[dict[str, Any]] = []
            for entry in entries:
                target = {
                    "entry_type": entry["entry_type"],
                    "tmdb_id": entry["tmdb_id"],
                    "parent_series_id": entry.get("parent_series_id"),
                    "season_number": entry.get("season_number"),
                }
                if _metadata_summary_state(db, target) == "outdated":
                    outdated.append(target)
        return _dedupe_summary_targets(outdated)

    def incomplete_metadata_summary_targets(self) -> list[dict[str, Any]]:
        entries = self.list_entries(include_tombstones=False)
        if not entries:
            return []
        with self._connect_initialized() as db:
            missing: list[dict[str, Any]] = []
            for entry in entries:
                target = {
                    "entry_type": entry["entry_type"],
                    "tmdb_id": entry["tmdb_id"],
                    "parent_series_id": entry.get("parent_series_id"),
                    "season_number": entry.get("season_number"),
                }
                if _metadata_summary_state(db, target) != "current":
                    missing.append(target)
        return _dedupe_summary_targets(missing)

    def metadata_summary_status(self) -> dict[str, int | bool]:
        entries = self.list_entries(include_tombstones=False)
        if not entries:
            return {
                "tracked_entries": 0,
                "hydrated_entries": 0,
                "missing_entries": 0,
                "ready": True,
            }

        tracked = self.metadata_summary_targets_for_entries(entries)
        missing = self.incomplete_metadata_summary_targets()
        tracked_count = len(tracked)
        missing_count = len(missing)
        return {
            "tracked_entries": tracked_count,
            "hydrated_entries": tracked_count - missing_count,
            "missing_entries": missing_count,
            "ready": missing_count == 0,
        }

    def attach_metadata_summary(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not entries:
            return []
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT metadata_json
                FROM tmdb_metadata_summary
                WHERE metadata_key IN ({_placeholders(entries)})
                AND language = ''
                """,
                _metadata_lookup_params(entries),
            ).fetchall()
        metadata_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = _metadata_row(row)
            metadata_by_key[_metadata_key_from_summary(metadata)] = metadata
        attached: list[dict[str, Any]] = []
        for entry in entries:
            clone = dict(entry)
            attached_metadata = metadata_by_key.get(_metadata_key_from_entry(entry))
            clone["metadata"] = attached_metadata
            attached.append(clone)
        return attached

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
    db.execute(_metadata_summary_table_sql())
    _create_metadata_summary_indexes(db)
    _write_meta(db, "schema_version", CACHE_SCHEMA_VERSION)


def _create_entries_indexes(db: sqlite3.Connection, table: str, prefix: str) -> None:
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_snapshot_sort "
        f"ON {table}(kind, date_saved DESC, identity ASC)"
    )
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_tmdb_lookup ON {table}(kind, entry_type, tmdb_id)"
    )
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_parent_series_lookup "
        f"ON {table}(kind, entry_type, parent_series_id)"
    )
    db.execute(f"CREATE INDEX IF NOT EXISTS {prefix}_kind_deleted_at ON {table}(kind, deleted_at)")


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


def _metadata_summary_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS tmdb_metadata_summary (
            metadata_key TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            parent_series_id INTEGER,
            season_number INTEGER,
            language TEXT NOT NULL,
            name TEXT,
            name_translations_json TEXT NOT NULL,
            original_name TEXT,
            overview TEXT,
            overview_translations_json TEXT NOT NULL,
            poster_path TEXT,
            backdrop_path TEXT,
            logo_path TEXT,
            original_language_code TEXT,
            on_air_date TEXT,
            link_to_details TEXT,
            fetched_at TEXT NOT NULL,
            source_version TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            PRIMARY KEY(metadata_key, language)
        )
    """


def _create_metadata_summary_indexes(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tmdb_metadata_summary_fetched "
        "ON tmdb_metadata_summary(fetched_at)"
    )


def _list_order_by(sort: str) -> str:
    if sort == "saved":
        return "ORDER BY date_saved DESC NULLS LAST, identity ASC"
    if sort == "updated":
        return (
            "ORDER BY COALESCE(tracking_updated_at, library_updated_at, date_saved) "
            "DESC NULLS LAST, identity ASC"
        )
    if sort == "title":
        return "ORDER BY identity ASC"
    raise LibraryCacheError(f"Unsupported library list sort: {sort}.")


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


def _summary_target_from_record(
    db: sqlite3.Connection,
    table: str,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    if _record_deleted(record):
        return None
    decoded = decode_library_entry_record(record)
    if decoded.get("kind") != "snapshot":
        return None
    if _entry_exists(db, table, str(decoded["identity"])):
        return None
    return {
        "entry_type": decoded["entry_type"],
        "tmdb_id": decoded["tmdb_id"],
        "parent_series_id": decoded.get("parent_series_id"),
        "season_number": decoded.get("season_number"),
    }


def _entry_exists(db: sqlite3.Connection, table: str, identity: str) -> bool:
    row = db.execute(f"SELECT 1 FROM {table} WHERE identity = ?", (identity,)).fetchone()
    return row is not None


def _metadata_summary_exists(db: sqlite3.Connection, target: dict[str, Any]) -> bool:
    row = db.execute(
        """
        SELECT 1 FROM tmdb_metadata_summary
        WHERE metadata_key = ? AND language = ''
        """,
        (_metadata_key_from_entry(target),),
    ).fetchone()
    return row is not None


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


def _metadata_row(row: sqlite3.Row) -> dict[str, Any]:
    value = json.loads(str(row["metadata_json"]))
    if not isinstance(value, dict):
        raise LibraryCacheError("Cached TMDb metadata summary is corrupt.")
    return _normalized_metadata_summary(value)


def _upsert_metadata_summary(db: sqlite3.Connection, summary: dict[str, Any]) -> None:
    metadata = _normalized_metadata_summary(summary, for_storage=True)
    metadata.setdefault("fetched_at", _now_iso())
    metadata.setdefault("source_version", TMDB_SUMMARY_SOURCE_VERSION)
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
        VALUES (
            :metadata_key,
            :entry_type,
            :tmdb_id,
            :parent_series_id,
            :season_number,
            :language,
            :name,
            :name_translations_json,
            :original_name,
            :overview,
            :overview_translations_json,
            :poster_path,
            :backdrop_path,
            :logo_path,
            :original_language_code,
            :on_air_date,
            :link_to_details,
            :fetched_at,
            :source_version,
            :metadata_json
        )
        ON CONFLICT(metadata_key, language) DO UPDATE SET
            language = excluded.language,
            name = excluded.name,
            name_translations_json = excluded.name_translations_json,
            original_name = excluded.original_name,
            overview = excluded.overview,
            overview_translations_json = excluded.overview_translations_json,
            poster_path = excluded.poster_path,
            backdrop_path = excluded.backdrop_path,
            logo_path = excluded.logo_path,
            original_language_code = excluded.original_language_code,
            on_air_date = excluded.on_air_date,
            link_to_details = excluded.link_to_details,
            fetched_at = excluded.fetched_at,
            source_version = excluded.source_version,
            metadata_json = excluded.metadata_json
        """,
        _metadata_summary_params(metadata),
    )


def _metadata_summary_params(summary: dict[str, Any]) -> dict[str, Any]:
    metadata_json = _metadata_json(summary)
    return {
        "metadata_key": _metadata_key_from_entry(summary),
        "entry_type": summary["entry_type"],
        "tmdb_id": summary["tmdb_id"],
        "parent_series_id": summary.get("parent_series_id"),
        "season_number": summary.get("season_number"),
        "language": summary.get("language") or "",
        "name": summary.get("name"),
        "name_translations_json": json.dumps(
            summary.get("name_translations") or {},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "original_name": summary.get("original_name"),
        "overview": summary.get("overview"),
        "overview_translations_json": json.dumps(
            summary.get("overview_translations") or {},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "poster_path": summary.get("poster_path"),
        "backdrop_path": summary.get("backdrop_path"),
        "logo_path": summary.get("logo_path"),
        "original_language_code": summary.get("original_language_code"),
        "on_air_date": summary.get("on_air_date"),
        "link_to_details": summary.get("link_to_details"),
        "fetched_at": summary["fetched_at"],
        "source_version": summary["source_version"],
        "metadata_json": metadata_json,
    }


def _metadata_json(summary: dict[str, Any]) -> str:
    metadata = {
        "entry_type": summary["entry_type"],
        "tmdb_id": summary["tmdb_id"],
        "parent_series_id": summary.get("parent_series_id"),
        "season_number": summary.get("season_number"),
        "language": summary.get("language") or None,
        "name": summary.get("name"),
        "name_translations": summary.get("name_translations") or {},
        "original_name": summary.get("original_name"),
        "overview": summary.get("overview"),
        "overview_translations": summary.get("overview_translations") or {},
        "poster_path": summary.get("poster_path"),
        "backdrop_path": summary.get("backdrop_path"),
        "logo_path": summary.get("logo_path"),
        "original_language_code": summary.get("original_language_code"),
        "on_air_date": summary.get("on_air_date"),
        "status": summary.get("status"),
        "genres": summary.get("genres") or [],
        "runtime_minutes": summary.get("runtime_minutes"),
        "season_count": summary.get("season_count"),
        "episode_count": summary.get("episode_count"),
        "vote_average": summary.get("vote_average"),
        "vote_count": summary.get("vote_count"),
        "popularity": summary.get("popularity"),
        "link_to_details": summary.get("link_to_details"),
        "fetched_at": summary["fetched_at"],
        "source_version": summary["source_version"],
    }
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


def _normalized_metadata_summary(
    summary: dict[str, Any],
    *,
    for_storage: bool = False,
) -> dict[str, Any]:
    source_version = (
        TMDB_SUMMARY_SOURCE_VERSION
        if for_storage
        else _canonical_metadata_source_version(summary.get("source_version"))
    )
    if source_version is None:
        source_version = TMDB_SUMMARY_SOURCE_VERSION
    return {
        "entry_type": str(summary["entry_type"]),
        "tmdb_id": int(summary["tmdb_id"]),
        "parent_series_id": _metadata_optional_int(summary.get("parent_series_id")),
        "season_number": _metadata_optional_int(summary.get("season_number")),
        "language": _optional_string(summary.get("language")),
        "name": _optional_string(summary.get("name")),
        "name_translations": _metadata_translation_map(summary.get("name_translations")),
        "original_name": _optional_string(summary.get("original_name")),
        "overview": _optional_string(summary.get("overview")),
        "overview_translations": _metadata_translation_map(summary.get("overview_translations")),
        "poster_path": _optional_string(summary.get("poster_path")),
        "backdrop_path": _optional_string(summary.get("backdrop_path")),
        "logo_path": _optional_string(summary.get("logo_path")),
        "original_language_code": _optional_string(summary.get("original_language_code")),
        "on_air_date": _optional_string(summary.get("on_air_date")),
        "status": _optional_string(summary.get("status")),
        "genres": _metadata_genres(summary.get("genres")),
        "runtime_minutes": _metadata_optional_int(summary.get("runtime_minutes")),
        "season_count": _metadata_optional_int(summary.get("season_count")),
        "episode_count": _metadata_optional_int(summary.get("episode_count")),
        "vote_average": _metadata_optional_float(summary.get("vote_average")),
        "vote_count": _metadata_optional_int(summary.get("vote_count")),
        "popularity": _metadata_optional_float(summary.get("popularity")),
        "link_to_details": _optional_string(summary.get("link_to_details")),
        "fetched_at": _optional_string(summary.get("fetched_at")) or _now_iso(),
        "source_version": source_version,
    }


def _canonical_metadata_source_version(value: object) -> str | None:
    source_version = _optional_string(value)
    if source_version is None:
        return None
    if source_version in {TMDB_SUMMARY_SOURCE_VERSION, "tmdb.http.summary.v2"}:
        return TMDB_SUMMARY_SOURCE_VERSION
    if source_version in {TMDB_LEGACY_SUMMARY_SOURCE_VERSION, "tmdb.http.summary.v1"}:
        return TMDB_LEGACY_SUMMARY_SOURCE_VERSION
    return source_version


def _metadata_summary_state(
    db: sqlite3.Connection,
    target: dict[str, Any],
) -> Literal["current", "missing", "outdated"]:
    row = db.execute(
        """
        SELECT source_version
        FROM tmdb_metadata_summary
        WHERE metadata_key = ? AND language = ''
        """,
        (_metadata_key_from_entry(target),),
    ).fetchone()
    if row is None:
        return "missing"
    source_version = _canonical_metadata_source_version(row["source_version"])
    if source_version == TMDB_SUMMARY_SOURCE_VERSION:
        return "current"
    return "outdated"


def _metadata_translation_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    translations: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        text = _optional_string(item)
        if text is not None:
            translations[key] = text
    return translations


def _metadata_genres(value: object) -> list[dict[str, int | str]]:
    if not isinstance(value, list):
        return []

    genres: list[dict[str, int | str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        genre_id = _metadata_optional_int(item.get("id"))
        name = _optional_string(item.get("name"))
        if genre_id is None or name is None:
            continue
        genres.append({"id": genre_id, "name": name})
    return genres


def _metadata_optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value)
    return None


def _metadata_optional_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return None


def _placeholders(values: set[int] | list[str] | list[dict[str, Any]]) -> str:
    return ", ".join("?" for _ in values)


def _metadata_lookup_params(entries: list[dict[str, Any]]) -> list[Any]:
    return [_metadata_key_from_entry(entry) for entry in entries]


def _dedupe_summary_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for target in targets:
        key = _metadata_key_from_entry(target)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def _metadata_key_from_summary(summary: dict[str, Any]) -> str:
    return _metadata_key_from_entry(summary)


def _metadata_key_from_entry(entry: dict[str, Any]) -> str:
    entry_type = str(entry["entry_type"])
    tmdb_id = int(entry["tmdb_id"])
    if entry_type == "season":
        parent_series_id = entry.get("parent_series_id")
        season_number = entry.get("season_number")
        if parent_series_id is None or season_number is None:
            raise LibraryCacheError("Season metadata is missing parent series context.")
        return f"season:{int(parent_series_id)}:{int(season_number)}:{tmdb_id}"
    return f"{entry_type}:{tmdb_id}"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _remove_matching_files(root: Path, pattern: str) -> int:
    if not root.exists():
        return 0
    removed = 0
    for path in root.glob(pattern):
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        removed += 1
    return removed


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
