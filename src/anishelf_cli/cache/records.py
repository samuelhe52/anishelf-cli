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
from anishelf_cli.models.domain import (
    LibraryEntryModel,
    LibraryEntryTombstone,
    validate_library_entry_json,
)
from anishelf_cli.models.transport.cloudkit import CloudKitRecord
from anishelf_cli.tmdb.client import TMDbSummaryIdentity


def apply_record(db: sqlite3.Connection, table: str, record: CloudKitRecord) -> None:
    payload = _cloudkit_record_payload(record)
    if record_deleted(payload):
        upsert_decoded_entry(db, table, deleted_entry(payload), payload)
        return

    decoded = decode_library_entry_record(record)
    upsert_decoded_entry(db, table, decoded, payload)


def summary_target_from_record(
    db: sqlite3.Connection,
    table: str,
    record: CloudKitRecord,
) -> TMDbSummaryIdentity | None:
    payload = _cloudkit_record_payload(record)
    if record_deleted(payload):
        return None
    decoded = decode_library_entry_record(record)
    if decoded.kind != "snapshot":
        return None
    if entry_exists(db, table, decoded.identity):
        return None
    return metadata.metadata_target_from_entry(decoded)


def entry_exists(db: sqlite3.Connection, table: str, identity: str) -> bool:
    row = db.execute(f"SELECT 1 FROM {table} WHERE identity = ?", (identity,)).fetchone()
    return row is not None


def upsert_decoded_entry(
    db: sqlite3.Connection,
    table: str,
    entry: LibraryEntryModel,
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


def entry_row_params(entry: LibraryEntryModel, raw_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": entry.identity,
        "kind": entry.kind,
        "entry_type": entry.entry_type,
        "tmdb_id": entry.tmdb_id,
        "parent_series_id": entry.parent_series_id,
        "season_number": entry.season_number,
        "watch_status": getattr(entry, "watch_status", None),
        "score": getattr(entry, "score", None),
        "favorite": optional_bool_int(getattr(entry, "favorite", None)),
        "on_display": optional_bool_int(getattr(entry, "on_display", None)),
        "date_saved": getattr(entry, "date_saved", None),
        "date_started": getattr(entry, "date_started", None),
        "date_finished": getattr(entry, "date_finished", None),
        "is_date_tracking_enabled": optional_bool_int(
            getattr(entry, "is_date_tracking_enabled", None)
        ),
        "notes": getattr(entry, "notes", None),
        "using_custom_poster": optional_bool_int(getattr(entry, "using_custom_poster", None)),
        "custom_poster_path": getattr(entry, "custom_poster_path", None),
        "library_updated_at": getattr(entry, "library_updated_at", None),
        "tracking_updated_at": getattr(entry, "tracking_updated_at", None),
        "deleted_at": getattr(entry, "deleted_at", None),
        "schema_version": entry.schema_version,
        "record_change_tag": record_change_tag(raw_record),
        "raw_record_json": json.dumps(raw_record, sort_keys=True, separators=(",", ":")),
        "decoded_json": entry.model_dump_json(
            by_alias=False,
            exclude_none=False,
            round_trip=True,
        ),
        "cached_at": _now_iso(),
    }


def deleted_entry(record: dict[str, Any]) -> LibraryEntryModel:
    name = record_name(record)
    try:
        identity = parse_library_identity(name)
    except LibraryIdentityError as exc:
        raise LibraryCacheError(f"CloudKit deleted record has invalid identity {name}.") from exc
    deleted_at = deleted_timestamp(record)
    return LibraryEntryTombstone(
        kind="tombstone",
        identity=identity.raw,
        entry_type=identity.entry_type,
        tmdb_id=identity.tmdb_id,
        parent_series_id=identity.parent_series_id,
        season_number=identity.season_number,
        deleted_at=deleted_at,
    )


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


def decoded_entry(row: sqlite3.Row) -> LibraryEntryModel:
    try:
        return validate_library_entry_json(str(row["decoded_json"]))
    except ValueError as exc:
        raise LibraryCacheError("Cached library entry is corrupt.") from exc


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cloudkit_record_payload(record: CloudKitRecord) -> dict[str, Any]:
    return record.to_cloudkit_payload()
