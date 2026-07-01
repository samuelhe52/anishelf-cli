from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from anishelf_cli.cache import metadata
from anishelf_cli.cache.schema import LibraryCacheError
from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.library import (
    LibraryIdentityError,
    decode_library_entry_record,
    parse_library_identity,
)
from anishelf_cli.tmdb.client import TMDbSummaryIdentity


def apply_record(db: sqlite3.Connection, table: str, record: dict[str, Any]) -> None:
    if record_deleted(record):
        upsert_decoded_entry(db, table, deleted_entry(record), record)
        return

    decoded = decode_library_entry_record(record)
    upsert_decoded_entry(db, table, decoded, record)


def summary_target_from_record(
    db: sqlite3.Connection,
    table: str,
    record: dict[str, Any],
) -> TMDbSummaryIdentity | None:
    if record_deleted(record):
        return None
    decoded = decode_library_entry_record(record)
    if decoded.get("kind") != "snapshot":
        return None
    if entry_exists(db, table, str(decoded["identity"])):
        return None
    return metadata.metadata_target_from_entry(decoded)


def entry_exists(db: sqlite3.Connection, table: str, identity: str) -> bool:
    row = db.execute(f"SELECT 1 FROM {table} WHERE identity = ?", (identity,)).fetchone()
    return row is not None


def upsert_decoded_entry(
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
        entry_row_params(entry, raw_record),
    )


def entry_row_params(entry: dict[str, Any], raw_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": entry["identity"],
        "kind": entry["kind"],
        "entry_type": entry["entry_type"],
        "tmdb_id": entry["tmdb_id"],
        "parent_series_id": entry.get("parent_series_id"),
        "season_number": entry.get("season_number"),
        "watch_status": entry.get("watch_status"),
        "score": entry.get("score"),
        "favorite": optional_bool_int(entry.get("favorite")),
        "on_display": optional_bool_int(entry.get("on_display")),
        "date_saved": entry.get("date_saved"),
        "date_started": entry.get("date_started"),
        "date_finished": entry.get("date_finished"),
        "is_date_tracking_enabled": optional_bool_int(entry.get("is_date_tracking_enabled")),
        "notes": entry.get("notes"),
        "using_custom_poster": optional_bool_int(entry.get("using_custom_poster")),
        "custom_poster_path": entry.get("custom_poster_path"),
        "library_updated_at": entry.get("library_updated_at"),
        "tracking_updated_at": entry.get("tracking_updated_at"),
        "deleted_at": entry.get("deleted_at"),
        "schema_version": entry.get("schema_version"),
        "record_change_tag": record_change_tag(raw_record),
        "raw_record_json": json.dumps(raw_record, sort_keys=True, separators=(",", ":")),
        "decoded_json": json.dumps(entry, sort_keys=True, separators=(",", ":")),
        "cached_at": _now_iso(),
    }


def deleted_entry(record: dict[str, Any]) -> dict[str, Any]:
    name = record_name(record)
    try:
        identity = parse_library_identity(name)
    except LibraryIdentityError as exc:
        raise LibraryCacheError(f"CloudKit deleted record has invalid identity {name}.") from exc
    deleted_at = deleted_timestamp(record)
    return {
        "kind": "deleted",
        "identity": identity.raw,
        "entry_type": identity.entry_type,
        "tmdb_id": identity.tmdb_id,
        "parent_series_id": identity.parent_series_id,
        "season_number": identity.season_number,
        "deleted_at": deleted_at,
    }


def record_deleted(record: dict[str, Any]) -> bool:
    return record.get("deleted") is True


def record_name(record: dict[str, Any]) -> str:
    if name := nonempty_string_or_none(record.get("recordName")):
        return name
    record_id = record.get("recordID")
    if isinstance(record_id, dict) and (
        name := nonempty_string_or_none(record_id.get("recordName"))
    ):
        return name
    raise LibraryCacheError("CloudKit deleted record is missing recordName.")


def record_change_tag(record: dict[str, Any]) -> str | None:
    return nonempty_string_or_none(record.get("recordChangeTag"))


def deleted_timestamp(record: dict[str, Any]) -> str:
    modified = record.get("modified")
    if isinstance(modified, dict):
        timestamp = modified.get("timestamp")
        if isinstance(timestamp, int | float) and not isinstance(timestamp, bool):
            value = float(timestamp)
            if abs(value) > 10_000_000_000:
                value /= 1000
            return _iso_z(datetime.fromtimestamp(value, UTC))
    return _now_iso()


def optional_bool_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    return None


def decoded_row(row: sqlite3.Row) -> dict[str, Any]:
    value = json.loads(str(row["decoded_json"]))
    if not isinstance(value, dict):
        raise LibraryCacheError("Cached library entry is corrupt.")
    return value


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
