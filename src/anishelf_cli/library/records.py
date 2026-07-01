from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.library.identity import LibraryIdentity

LIBRARY_ENTRY_RECORD_TYPE = "LibraryEntry"
SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION = 2
SWIFT_REFERENCE_DATE = datetime(2001, 1, 1, tzinfo=UTC)

WATCH_STATUS_VALUES = {"planToWatch", "watching", "watched", "dropped"}


class LibraryRecordDecodeError(ValueError):
    pass


def decode_library_entry_record(record: dict[str, Any]) -> dict[str, Any]:
    record_name = _record_name(record)
    record_type = nonempty_string_or_none(record.get("recordType"))
    if record_type != LIBRARY_ENTRY_RECORD_TYPE:
        actual_type = record_type or "missing record type"
        raise LibraryRecordDecodeError(
            f"Expected {LIBRARY_ENTRY_RECORD_TYPE} record, got {actual_type}."
        )

    fields = record.get("fields")
    if not isinstance(fields, dict):
        raise LibraryRecordDecodeError("CloudKit record is missing fields.")

    schema_version = _required_int(fields, "schemaVersion")
    if schema_version > SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION:
        raise LibraryRecordDecodeError(
            f"Unsupported LibraryEntry schema version {schema_version}; "
            f"maximum supported is {SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION}."
        )

    tmdb_id = _required_int(fields, "tmdbID")
    entry_type = _required_string(fields, "entryType")
    parent_series_id = _optional_int(fields, "parentSeriesID")
    season_number = _optional_int(fields, "seasonNumber")
    identity = _validated_identity(
        record_name,
        entry_type,
        tmdb_id,
        parent_series_id,
        season_number,
    )
    deleted_at = _optional_datetime(fields, "deletedAt")
    if deleted_at is not None:
        return {
            "kind": "tombstone",
            "identity": identity.raw,
            "schema_version": schema_version,
            "tmdb_id": tmdb_id,
            "entry_type": entry_type,
            "parent_series_id": parent_series_id,
            "season_number": season_number,
            "deleted_at": deleted_at,
        }

    watch_status = _required_string(fields, "watchStatus")
    if watch_status not in WATCH_STATUS_VALUES:
        raise LibraryRecordDecodeError(f"Invalid watchStatus value: {watch_status}.")

    using_custom_poster = _required_bool(fields, "usingCustomPoster")
    custom_poster_path = _custom_poster_path(fields) if using_custom_poster else None

    return {
        "kind": "snapshot",
        "identity": identity.raw,
        "schema_version": schema_version,
        "tmdb_id": tmdb_id,
        "entry_type": entry_type,
        "parent_series_id": parent_series_id,
        "season_number": season_number,
        "on_display": _required_bool(fields, "onDisplay"),
        "date_saved": _required_datetime(fields, "dateSaved"),
        "watch_status": watch_status,
        "date_started": _optional_datetime(fields, "dateStarted"),
        "date_finished": _optional_datetime(fields, "dateFinished"),
        "is_date_tracking_enabled": _required_bool(fields, "isDateTrackingEnabled"),
        "score": _optional_int(fields, "score"),
        "favorite": _required_bool(fields, "favorite"),
        "notes": _required_string(fields, "notes", allow_empty=True),
        "using_custom_poster": using_custom_poster,
        "custom_poster_path": custom_poster_path,
        "episode_progresses": _required_episode_progresses(fields, "episodeProgresses"),
        "library_updated_at": _optional_datetime(fields, "libraryUpdatedAt"),
        "tracking_updated_at": _optional_datetime(fields, "trackingUpdatedAt"),
    }


def _record_name(record: dict[str, Any]) -> str:
    record_name = _record_name_or_none(record)
    if not record_name:
        raise LibraryRecordDecodeError("CloudKit record is missing recordName.")
    return record_name


def _record_name_or_none(record: dict[str, Any]) -> str | None:
    if record_name := nonempty_string_or_none(record.get("recordName")):
        return record_name
    record_id = record.get("recordID")
    if isinstance(record_id, dict):
        return nonempty_string_or_none(record_id.get("recordName"))
    return None


def _validated_identity(
    record_name: str,
    entry_type: str,
    tmdb_id: int,
    parent_series_id: int | None,
    season_number: int | None,
) -> LibraryIdentity:
    if entry_type == "movie":
        if parent_series_id is not None or season_number is not None:
            raise LibraryRecordDecodeError(f"Invalid identity fields for {record_name}.")
        expected = f"movie:{tmdb_id}"
    elif entry_type == "series":
        if parent_series_id is not None or season_number is not None:
            raise LibraryRecordDecodeError(f"Invalid identity fields for {record_name}.")
        expected = f"series:{tmdb_id}"
    elif entry_type == "season":
        if parent_series_id is None or season_number is None:
            raise LibraryRecordDecodeError(f"Invalid identity fields for {record_name}.")
        expected = f"season:{parent_series_id}:{season_number}:{tmdb_id}"
    else:
        raise LibraryRecordDecodeError(f"Invalid entryType value: {entry_type}.")

    if expected != record_name:
        raise LibraryRecordDecodeError(
            f"CloudKit record identity {record_name} does not match decoded fields."
        )
    return LibraryIdentity(record_name, entry_type, tmdb_id, parent_series_id, season_number)


def _custom_poster_path(fields: dict[str, Any]) -> str | None:
    path_from_path = _optional_string_field(fields, "customPosterPath")
    if path_from_path:
        path_from_path = _storage_path(path_from_path)

    poster_url = _optional_string_field(fields, "customPosterURL")
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


def _required_episode_progresses(fields: dict[str, Any], field: str) -> list[dict[str, Any]]:
    raw = _required_field_value(fields, field)
    if isinstance(raw, list):
        decoded = raw
    elif isinstance(raw, str):
        decoded = _decode_episode_progress_string(raw)
    else:
        raise LibraryRecordDecodeError(f"Invalid {field} value.")

    if not isinstance(decoded, list):
        raise LibraryRecordDecodeError(f"Invalid {field} value.")

    progresses: list[dict[str, Any]] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise LibraryRecordDecodeError(f"Invalid {field} item.")
        progresses.append(
            {
                "season_number": _int_from_raw(item.get("seasonNumber"), "seasonNumber"),
                "watched_through_episode": _int_from_raw(
                    item.get("watchedThroughEpisode"),
                    "watchedThroughEpisode",
                ),
                "updated_at": _swift_reference_datetime_from_raw(
                    item.get("updatedAt"),
                    "updatedAt",
                ),
            }
        )
    return progresses


def _decode_episode_progress_string(raw: str) -> Any:
    try:
        decoded_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        decoded_bytes = raw.encode()
    try:
        return json.loads(decoded_bytes.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LibraryRecordDecodeError("Corrupt episodeProgresses payload.") from exc


def _required_string(fields: dict[str, Any], field: str, *, allow_empty: bool = False) -> str:
    raw = _required_field_value(fields, field)
    if not isinstance(raw, str) or (not allow_empty and not raw):
        raise LibraryRecordDecodeError(f"Invalid {field} value.")
    return raw


def _optional_string_field(fields: dict[str, Any], field: str) -> str | None:
    raw = _optional_field_value(fields, field)
    if raw is None:
        return None
    value = nonempty_string_or_none(raw)
    if value is None:
        raise LibraryRecordDecodeError(f"Invalid {field} value.")
    return value


def _required_int(fields: dict[str, Any], field: str) -> int:
    return _int_from_raw(_required_field_value(fields, field), field)


def _optional_int(fields: dict[str, Any], field: str) -> int | None:
    raw = _optional_field_value(fields, field)
    if raw is None:
        return None
    return _int_from_raw(raw, field)


def _required_bool(fields: dict[str, Any], field: str) -> bool:
    raw = _required_field_value(fields, field)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    raise LibraryRecordDecodeError(f"Invalid {field} value.")


def _required_datetime(fields: dict[str, Any], field: str) -> str:
    return _datetime_from_raw(_required_field_value(fields, field), field)


def _optional_datetime(fields: dict[str, Any], field: str) -> str | None:
    raw = _optional_field_value(fields, field)
    if raw is None:
        return None
    return _datetime_from_raw(raw, field)


def _required_field_value(fields: dict[str, Any], field: str) -> Any:
    if field not in fields:
        raise LibraryRecordDecodeError(f"Missing required field {field}.")
    value = _field_value(fields[field])
    if value is None:
        raise LibraryRecordDecodeError(f"Missing required field {field}.")
    return value


def _optional_field_value(fields: dict[str, Any], field: str) -> Any:
    if field not in fields:
        return None
    return _field_value(fields[field])


def _field_value(raw: Any) -> Any:
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw


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
