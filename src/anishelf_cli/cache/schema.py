from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CACHE_SCHEMA_VERSION = "1"
TMDB_LEGACY_SUMMARY_SOURCE_VERSION = "tmdbsummary.v1"
TMDB_SUMMARY_SOURCE_VERSION = "tmdbsummary.v2"
ZONE_SYNC_TOKEN_META_KEY = "zone_sync_token"
REBUILD_SYNC_TOKEN_META_KEY = "rebuild_sync_token"


class LibraryCacheError(RuntimeError):
    pass


class LibraryCacheNotAvailableError(LibraryCacheError):
    pass


def initialize_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    db.execute(entries_table_sql("library_entries"))
    create_entries_indexes(db, "library_entries", "idx_library_entries")
    db.execute(metadata_summary_table_sql())
    create_metadata_summary_indexes(db)
    write_meta(db, "schema_version", CACHE_SCHEMA_VERSION)


def create_entries_indexes(db: sqlite3.Connection, table: str, prefix: str) -> None:
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


def entries_table_sql(table: str) -> str:
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


def metadata_summary_table_sql() -> str:
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


def create_metadata_summary_indexes(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tmdb_metadata_summary_fetched "
        "ON tmdb_metadata_summary(fetched_at)"
    )


def list_order_by(sort: str) -> str:
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


def read_meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else None


def write_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        """
        INSERT INTO cache_meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def remove_matching_files(root: Path, pattern: str) -> int:
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
