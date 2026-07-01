from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from anishelf_cli.cache import metadata
from anishelf_cli.cache.schema import LibraryCacheError
from anishelf_cli.library import decode_library_entry_record
from anishelf_cli.models.domain import (
    LibraryEntryModel,
    LibraryEntryTombstone,
    TMDbSummaryIdentity,
    validate_library_entry_json,
)
from anishelf_cli.models.identity import (
    LibraryIdentityError,
    parse_library_identity,
)
from anishelf_cli.models.transport.cloudkit import CloudKitRecord

ENTRY_MODEL_COLUMNS = (
    "identity",
    "kind",
    "entry_type",
    "tmdb_id",
    "parent_series_id",
    "season_number",
    "watch_status",
    "score",
    "favorite",
    "on_display",
    "date_saved",
    "date_started",
    "date_finished",
    "is_date_tracking_enabled",
    "notes",
    "using_custom_poster",
    "custom_poster_path",
    "library_updated_at",
    "tracking_updated_at",
    "deleted_at",
    "schema_version",
)
ENTRY_BOOL_COLUMNS = (
    "favorite",
    "on_display",
    "is_date_tracking_enabled",
    "using_custom_poster",
)


def apply_record(db: sqlite3.Connection, table: str, record: CloudKitRecord) -> None:
    raw_record = record.to_cloudkit_payload()
    if record_deleted(record):
        upsert_decoded_entry(
            db,
            table,
            deleted_entry(record),
            raw_record,
            record_change_tag(record),
        )
        return

    decoded = decode_library_entry_record(record)
    upsert_decoded_entry(db, table, decoded, raw_record, record_change_tag(record))


def summary_target_from_record(
    db: sqlite3.Connection,
    table: str,
    record: CloudKitRecord,
) -> TMDbSummaryIdentity | None:
    if record_deleted(record):
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
    change_tag: str | None,
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
        entry_row_params(entry, raw_record, change_tag),
    )


def entry_row_params(
    entry: LibraryEntryModel,
    raw_record: dict[str, Any],
    change_tag: str | None,
) -> dict[str, Any]:
    model_payload = entry.model_dump(
        mode="json",
        by_alias=False,
        exclude_none=False,
        round_trip=True,
    )
    row = {column: model_payload.get(column) for column in ENTRY_MODEL_COLUMNS}
    for column in ENTRY_BOOL_COLUMNS:
        row[column] = optional_bool_int(row[column])
    row.update(
        record_change_tag=change_tag,
        raw_record_json=json.dumps(raw_record, sort_keys=True, separators=(",", ":")),
        decoded_json=json.dumps(model_payload, sort_keys=True, separators=(",", ":")),
        cached_at=_now_iso(),
    )
    return row


def deleted_entry(record: CloudKitRecord) -> LibraryEntryModel:
    name = record_name(record)
    try:
        identity = parse_library_identity(name)
    except LibraryIdentityError as exc:
        raise LibraryCacheError(f"CloudKit deleted record has invalid identity {name}.") from exc
    if identity.raw is None:
        raise LibraryCacheError(f"CloudKit deleted record has invalid identity {name}.")
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


def record_deleted(record: CloudKitRecord) -> bool:
    return record.is_deleted


def record_name(record: CloudKitRecord) -> str:
    if name := record.effective_record_name:
        return name
    raise LibraryCacheError("CloudKit deleted record is missing recordName.")


def record_change_tag(record: CloudKitRecord) -> str | None:
    return record.record_change_tag


def deleted_timestamp(record: CloudKitRecord) -> str:
    timestamp = record.modified_timestamp
    if timestamp is not None:
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
