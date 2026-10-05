from __future__ import annotations

import os
import sqlite3
from pathlib import Path

CACHE_SCHEMA_VERSION = "4"
TMDB_METADATA_SOURCE_VERSION = "tmdb.metadata.v1"
TMDB_SUMMARY_SOURCE_VERSION = TMDB_METADATA_SOURCE_VERSION
ZONE_SYNC_TOKEN_META_KEY = "zone_sync_token"
REBUILD_SYNC_TOKEN_META_KEY = "rebuild_sync_token"
UPDATED_SORT_EXPRESSION = (
    "CASE "
    "WHEN tracking_updated_at IS NULL AND library_updated_at IS NULL THEN date_saved "
    "WHEN tracking_updated_at IS NULL THEN library_updated_at "
    "WHEN library_updated_at IS NULL THEN tracking_updated_at "
    "WHEN tracking_updated_at >= library_updated_at THEN tracking_updated_at "
    "ELSE library_updated_at "
    "END"
)
ENTRY_TYPE_SORT_EXPRESSION = (
    "CASE "
    "WHEN entry_type = 'movie' THEN 0 "
    "WHEN entry_type = 'series' THEN 1 "
    "WHEN entry_type = 'season' THEN 2 "
    "ELSE 3 "
    "END"
)
WATCH_STATUS_SORT_EXPRESSION = (
    "CASE "
    "WHEN watch_status = 'planToWatch' THEN 0 "
    "WHEN watch_status = 'watching' THEN 1 "
    "WHEN watch_status = 'watched' THEN 2 "
    "WHEN watch_status = 'dropped' THEN 3 "
    "ELSE 4 "
    "END"
)


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
    existing_schema_version = read_meta(db, "schema_version")
    if existing_schema_version is not None and existing_schema_version != CACHE_SCHEMA_VERSION:
        reset_schema(db)
    db.execute(entries_table_sql("library_entries"))
    create_entries_indexes(db, "library_entries", "idx_library_entries")
    db.execute(metadata_items_table_sql())
    create_metadata_items_indexes(db)
    write_meta(db, "schema_version", CACHE_SCHEMA_VERSION)


def reset_schema(db: sqlite3.Connection) -> None:
    db.execute("DROP TABLE IF EXISTS library_entries")
    db.execute("DROP TABLE IF EXISTS library_entries_stage")
    db.execute("DROP TABLE IF EXISTS tmdb_metadata_summary")
    db.execute("DROP TABLE IF EXISTS tmdb_metadata_items")
    db.execute("DROP TABLE IF EXISTS tmdb_metadata_seasons")
    db.execute("DROP TABLE IF EXISTS tmdb_metadata_episodes")
    db.execute("DELETE FROM cache_meta")


def create_entries_indexes(db: sqlite3.Connection, table: str, prefix: str) -> None:
    db.execute(
        f"CREATE INDEX IF NOT EXISTS {prefix}_snapshot_updated_sort "
        f"ON {table}(kind, {UPDATED_SORT_EXPRESSION} DESC, identity ASC)"
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
            is_rewatching INTEGER,
            rewatch_count INTEGER,
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


def metadata_items_table_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS tmdb_metadata_items (
            metadata_key TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            parent_series_id INTEGER,
            season_number INTEGER,
            language TEXT NOT NULL,
            metadata_depth TEXT NOT NULL,
            name TEXT,
            name_translations_json TEXT NOT NULL,
            overview TEXT,
            overview_translations_json TEXT NOT NULL,
            runtime_minutes INTEGER,
            number_of_seasons INTEGER,
            number_of_episodes INTEGER,
            poster_path TEXT,
            backdrop_path TEXT,
            logo_path TEXT,
            original_language_code TEXT,
            on_air_date TEXT,
            link_to_details TEXT,
            episode_run_time_minutes_json TEXT NOT NULL,
            genres_json TEXT NOT NULL,
            vote_average REAL,
            vote_count INTEGER,
            popularity REAL,
            status TEXT,
            first_air_date TEXT,
            last_air_date TEXT,
            release_date TEXT,
            tagline TEXT,
            subtitle TEXT,
            season_summaries_json TEXT NOT NULL,
            episode_summaries_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source_version TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            PRIMARY KEY(metadata_key, language)
        )
    """


def create_metadata_items_indexes(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tmdb_metadata_items_fetched "
        "ON tmdb_metadata_items(fetched_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tmdb_metadata_items_depth "
        "ON tmdb_metadata_items(language, metadata_depth, source_version)"
    )


def list_order_by(sort: str, *, reverse: bool = False) -> str:
    # Reversing flips every sort direction but keeps missing values last, so a
    # reversed score sort lists the lowest scores rather than unscored entries.
    primary = "ASC" if reverse else "DESC"
    secondary = "DESC" if reverse else "ASC"
    updated = f"{UPDATED_SORT_EXPRESSION} {primary} NULLS LAST"
    identity = f"identity {secondary}"
    if sort == "saved":
        return f"ORDER BY date_saved {primary} NULLS LAST, {identity}"
    if sort == "updated":
        return f"ORDER BY {updated}, {identity}"
    if sort in {"title", "air-date"}:
        # Ordered in Python after metadata is attached.
        return "ORDER BY identity ASC"
    if sort == "score":
        return f"ORDER BY score {primary} NULLS LAST, {updated}, {identity}"
    if sort == "started":
        return f"ORDER BY date_started {primary} NULLS LAST, {updated}, {identity}"
    if sort == "finished":
        return f"ORDER BY date_finished {primary} NULLS LAST, {updated}, {identity}"
    if sort == "type":
        return f"ORDER BY {ENTRY_TYPE_SORT_EXPRESSION} {secondary}, {updated}, {identity}"
    if sort == "watch-status":
        return f"ORDER BY {WATCH_STATUS_SORT_EXPRESSION} {secondary}, {updated}, {identity}"
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
