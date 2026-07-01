from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.library.identity import (
    LibraryIdentity,
    LibraryIdentityError,
    library_identity_from_fields,
)
from anishelf_cli.models.domain import (
    WATCH_STATUS_VALUES as DOMAIN_WATCH_STATUS_VALUES,
)
from anishelf_cli.models.domain import (
    EpisodeProgress,
    LibraryEntryModel,
    LibraryEntrySnapshot,
    LibraryEntryTombstone,
)
from anishelf_cli.models.transport.cloudkit import CloudKitRecord

LIBRARY_ENTRY_RECORD_TYPE = "LibraryEntry"
SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION = 2
SWIFT_REFERENCE_DATE = datetime(2001, 1, 1, tzinfo=UTC)
WATCH_STATUS_VALUES = DOMAIN_WATCH_STATUS_VALUES

class LibraryRecordDecodeError(ValueError):
    pass


def decode_library_entry_record(record: CloudKitRecord) -> LibraryEntryModel:
    record_name = _record_name(record)
    record_type = nonempty_string_or_none(record.record_type)
    if record_type != LIBRARY_ENTRY_RECORD_TYPE:
        actual_type = record_type or "missing record type"
        raise LibraryRecordDecodeError(
            f"Expected {LIBRARY_ENTRY_RECORD_TYPE} record, got {actual_type}."
        )

    if not record.fields:
        raise LibraryRecordDecodeError("CloudKit record is missing fields.")

    schema_version = _required_int(record, "schemaVersion")
    if schema_version > SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION:
        raise LibraryRecordDecodeError(
            f"Unsupported LibraryEntry schema version {schema_version}; "
            f"maximum supported is {SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION}."
        )

    tmdb_id = _required_int(record, "tmdbID")
    entry_type = _required_string(record, "entryType")
    parent_series_id = _optional_int(record, "parentSeriesID")
    season_number = _optional_int(record, "seasonNumber")
    identity = _validated_identity(
        record_name,
        entry_type,
        tmdb_id,
        parent_series_id,
        season_number,
    )
    deleted_at = _optional_datetime(record, "deletedAt")
    if deleted_at is not None:
        return _build_entry(
            LibraryEntryTombstone,
            kind="tombstone",
            identity=identity.raw,
            schema_version=schema_version,
            tmdb_id=tmdb_id,
            entry_type=entry_type,
            parent_series_id=parent_series_id,
            season_number=season_number,
            deleted_at=deleted_at,
        )

    watch_status = _required_string(record, "watchStatus")
    using_custom_poster = _required_bool(record, "usingCustomPoster")
    custom_poster_path = _custom_poster_path(record) if using_custom_poster else None

    return _build_entry(
        LibraryEntrySnapshot,
        kind="snapshot",
        identity=identity.raw,
        schema_version=schema_version,
        tmdb_id=tmdb_id,
        entry_type=entry_type,
        parent_series_id=parent_series_id,
        season_number=season_number,
        on_display=_required_bool(record, "onDisplay"),
        date_saved=_required_datetime(record, "dateSaved"),
        watch_status=watch_status,
        date_started=_optional_datetime(record, "dateStarted"),
        date_finished=_optional_datetime(record, "dateFinished"),
        is_date_tracking_enabled=_required_bool(record, "isDateTrackingEnabled"),
        score=_optional_int(record, "score"),
        favorite=_required_bool(record, "favorite"),
        notes=_required_string(record, "notes", allow_empty=True),
        using_custom_poster=using_custom_poster,
        custom_poster_path=custom_poster_path,
        episode_progresses=_required_episode_progresses(record, "episodeProgresses"),
        library_updated_at=_optional_datetime(record, "libraryUpdatedAt"),
        tracking_updated_at=_optional_datetime(record, "trackingUpdatedAt"),
    )


def _record_name(record: CloudKitRecord) -> str:
    record_name = _record_name_or_none(record)
    if not record_name:
        raise LibraryRecordDecodeError("CloudKit record is missing recordName.")
    return record_name


def _record_name_or_none(record: CloudKitRecord) -> str | None:
    return record.effective_record_name


def _validated_identity(
    record_name: str,
    entry_type: str,
    tmdb_id: int,
    parent_series_id: int | None,
    season_number: int | None,
) -> LibraryIdentity:
    try:
        return library_identity_from_fields(
            entry_type,
            tmdb_id,
            parent_series_id,
            season_number,
            raw_identity=record_name,
        )
    except LibraryIdentityError as exc:
        if str(exc) == "Library entry identity does not match decoded fields.":
            raise LibraryRecordDecodeError(
                f"CloudKit record identity {record_name} does not match decoded fields."
            ) from exc
        raise LibraryRecordDecodeError(str(exc)) from exc


def _custom_poster_path(record: CloudKitRecord) -> str | None:
    path_from_path = _optional_string_field(record, "customPosterPath")
    if path_from_path:
        path_from_path = _storage_path(path_from_path)

    poster_url = _optional_string_field(record, "customPosterURL")
    if not poster_url:
        return path_from_path
    path_from_url = _storage_path_from_url(poster_url)
    if path_from_url:
        return path_from_url
    if path_from_path is not None:
        return path_from_path
    raise LibraryRecordDecodeError("Invalid customPosterURL value.")


def _storage_path(value: str) -> str:
    return value if value.startswith("/") else f"/{value}"


def _storage_path_from_url(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 4 and path_parts[:2] == ["t", "p"]:
        return "/" + "/".join(path_parts[3:])
    if path_parts:
        return "/" + "/".join(path_parts)
    return None


def _build_entry(
    model_type: type[LibraryEntrySnapshot] | type[LibraryEntryTombstone],
    **payload: object,
) -> LibraryEntryModel:
    try:
        return model_type(**payload)
    except ValidationError as exc:
        first_error = exc.errors(include_url=False)[0]
        raise LibraryRecordDecodeError(str(first_error["msg"])) from exc


def _required_episode_progresses(
    record: CloudKitRecord,
    field: str,
) -> tuple[EpisodeProgress, ...]:
    raw = _required_field_value(record, field)
    if isinstance(raw, list):
        decoded = raw
    elif isinstance(raw, str):
        decoded = _decode_episode_progress_string(raw)
    else:
        raise LibraryRecordDecodeError(f"Invalid {field} value.")

    if not isinstance(decoded, list):
        raise LibraryRecordDecodeError(f"Invalid {field} value.")

    progresses: list[EpisodeProgress] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise LibraryRecordDecodeError(f"Invalid {field} item.")
        progresses.append(
            EpisodeProgress(
                season_number=_int_from_raw(item.get("seasonNumber"), "seasonNumber"),
                watched_through_episode=_int_from_raw(
                    item.get("watchedThroughEpisode"),
                    "watchedThroughEpisode",
                ),
                updated_at=_swift_reference_datetime_from_raw(
                    item.get("updatedAt"),
                    "updatedAt",
                ),
            )
        )
    return tuple(progresses)


def _decode_episode_progress_string(raw: str) -> Any:
    try:
        decoded_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        decoded_bytes = raw.encode()
    try:
        return json.loads(decoded_bytes.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LibraryRecordDecodeError("Corrupt episodeProgresses payload.") from exc


def _required_string(record: CloudKitRecord, field: str, *, allow_empty: bool = False) -> str:
    raw = _required_field_value(record, field)
    if not isinstance(raw, str) or (not allow_empty and not raw):
        raise LibraryRecordDecodeError(f"Invalid {field} value.")
    return raw


def _optional_string_field(record: CloudKitRecord, field: str) -> str | None:
    raw = _optional_field_value(record, field)
    if raw is None:
        return None
    value = nonempty_string_or_none(raw)
    if value is None:
        raise LibraryRecordDecodeError(f"Invalid {field} value.")
    return value


def _required_int(record: CloudKitRecord, field: str) -> int:
    return _int_from_raw(_required_field_value(record, field), field)


def _optional_int(record: CloudKitRecord, field: str) -> int | None:
    raw = _optional_field_value(record, field)
    if raw is None:
        return None
    return _int_from_raw(raw, field)


def _required_bool(record: CloudKitRecord, field: str) -> bool:
    raw = _required_field_value(record, field)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    raise LibraryRecordDecodeError(f"Invalid {field} value.")


def _required_datetime(record: CloudKitRecord, field: str) -> str:
    return _datetime_from_raw(_required_field_value(record, field), field)


def _optional_datetime(record: CloudKitRecord, field: str) -> str | None:
    raw = _optional_field_value(record, field)
    if raw is None:
        return None
    return _datetime_from_raw(raw, field)


def _required_field_value(record: CloudKitRecord, field: str) -> Any:
    raw_field = record.field(field)
    if raw_field is None:
        raise LibraryRecordDecodeError(f"Missing required field {field}.")
    value = raw_field.value
    if value is None:
        raise LibraryRecordDecodeError(f"Missing required field {field}.")
    return value


def _optional_field_value(record: CloudKitRecord, field: str) -> Any:
    raw_field = record.field(field)
    if raw_field is None:
        return None
    return raw_field.value


def _int_from_raw(raw: Any, field: str) -> int:
    if isinstance(raw, bool):
        raise LibraryRecordDecodeError(f"Invalid {field} value.")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    raise LibraryRecordDecodeError(f"Invalid {field} value.")


def _datetime_from_raw(raw: Any, field: str) -> str:
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LibraryRecordDecodeError(f"Invalid {field} value.") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return _iso_z(parsed)
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        timestamp = float(raw)
        if abs(timestamp) > 10_000_000_000:
            timestamp /= 1000
        return _iso_z(datetime.fromtimestamp(timestamp, UTC))
    raise LibraryRecordDecodeError(f"Invalid {field} value.")


def _swift_reference_datetime_from_raw(raw: Any, field: str) -> str:
    if isinstance(raw, str):
        return _datetime_from_raw(raw, field)
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return _iso_z(SWIFT_REFERENCE_DATE + timedelta(seconds=float(raw)))
    raise LibraryRecordDecodeError(f"Invalid {field} value.")


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
