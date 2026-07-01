from __future__ import annotations

from typing import cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.models.domain import (
    WATCH_STATUS_VALUES as DOMAIN_WATCH_STATUS_VALUES,
)
from anishelf_cli.models.domain import (
    EpisodeProgress,
    LibraryEntryModel,
    LibraryEntrySnapshot,
    LibraryEntryTombstone,
)
from anishelf_cli.models.identity import (
    LibraryIdentity,
    LibraryIdentityError,
    library_identity_from_fields,
)
from anishelf_cli.models.transport.cloudkit import (
    CloudKitLibraryEntryCommonFields,
    CloudKitLibraryEntrySnapshotFields,
    CloudKitLibraryEntryTombstoneFields,
    CloudKitRecord,
)

LIBRARY_ENTRY_RECORD_TYPE = "LibraryEntry"
SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION = 2
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

    common_fields = _validated_record_fields(record, CloudKitLibraryEntryCommonFields)
    if common_fields.schema_version > SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION:
        raise LibraryRecordDecodeError(
            f"Unsupported LibraryEntry schema version {common_fields.schema_version}; "
            f"maximum supported is {SUPPORTED_LIBRARY_ENTRY_SCHEMA_VERSION}."
        )

    identity = _validated_identity(
        record_name,
        common_fields.entry_type,
        common_fields.tmdb_id,
        common_fields.parent_series_id,
        common_fields.season_number,
    )
    if common_fields.deleted_at is not None:
        tombstone_fields = _validated_record_fields(record, CloudKitLibraryEntryTombstoneFields)
        return _tombstone_entry_from_cloudkit_fields(identity, tombstone_fields)

    snapshot_fields = _validated_record_fields(record, CloudKitLibraryEntrySnapshotFields)
    return _snapshot_entry_from_cloudkit_fields(identity, snapshot_fields)


def _snapshot_entry_from_cloudkit_fields(
    identity: LibraryIdentity,
    fields: CloudKitLibraryEntrySnapshotFields,
) -> LibraryEntrySnapshot:
    return cast(
        LibraryEntrySnapshot,
        _build_entry(
            LibraryEntrySnapshot,
            kind="snapshot",
            identity=_identity_raw(identity),
            schema_version=fields.schema_version,
            entry_type=fields.entry_type,
            tmdb_id=fields.tmdb_id,
            parent_series_id=fields.parent_series_id,
            season_number=fields.season_number,
            on_display=fields.on_display,
            date_saved=fields.date_saved,
            watch_status=fields.watch_status,
            date_started=fields.date_started,
            date_finished=fields.date_finished,
            is_date_tracking_enabled=fields.is_date_tracking_enabled,
            score=fields.score,
            favorite=fields.favorite,
            notes=fields.notes,
            using_custom_poster=fields.using_custom_poster,
            custom_poster_path=(
                _custom_poster_path(fields) if fields.using_custom_poster else None
            ),
            episode_progresses=_episode_progresses_from_cloudkit_fields(fields),
            library_updated_at=fields.library_updated_at,
            tracking_updated_at=fields.tracking_updated_at,
        ),
    )


def _tombstone_entry_from_cloudkit_fields(
    identity: LibraryIdentity,
    fields: CloudKitLibraryEntryTombstoneFields,
) -> LibraryEntryTombstone:
    return cast(
        LibraryEntryTombstone,
        _build_entry(
            LibraryEntryTombstone,
            kind="tombstone",
            identity=_identity_raw(identity),
            schema_version=fields.schema_version,
            entry_type=fields.entry_type,
            tmdb_id=fields.tmdb_id,
            parent_series_id=fields.parent_series_id,
            season_number=fields.season_number,
            deleted_at=fields.deleted_at,
        ),
    )


def _episode_progresses_from_cloudkit_fields(
    fields: CloudKitLibraryEntrySnapshotFields,
) -> tuple[EpisodeProgress, ...]:
    return tuple(
        EpisodeProgress(
            season_number=progress.season_number,
            watched_through_episode=progress.watched_through_episode,
            updated_at=progress.updated_at,
        )
        for progress in fields.episode_progresses
    )


def _identity_raw(identity: LibraryIdentity) -> str:
    if identity.raw is None:
        raise LibraryRecordDecodeError("CloudKit record identity is missing its canonical value.")
    return identity.raw


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


def _validated_record_fields[CloudKitFieldModelT: CloudKitLibraryEntryCommonFields](
    record: CloudKitRecord,
    model_type: type[CloudKitFieldModelT],
) -> CloudKitFieldModelT:
    try:
        return record.validate_fields(model_type)
    except ValidationError as exc:
        raise _library_record_decode_error(exc) from exc


def _custom_poster_path(fields: CloudKitLibraryEntrySnapshotFields) -> str | None:
    path_from_path = fields.custom_poster_path
    if path_from_path:
        path_from_path = _storage_path(path_from_path)

    poster_url = fields.custom_poster_url
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
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise _library_record_decode_error(exc) from exc


def _library_record_decode_error(exc: ValidationError) -> LibraryRecordDecodeError:
    first_error = exc.errors(include_url=False)[0]
    field = first_error["loc"][0] if first_error.get("loc") else None
    message = _normalized_error_message(str(first_error["msg"]))
    if message == "Field required" and isinstance(field, str):
        return LibraryRecordDecodeError(f"Missing required field {field}.")
    if message == "Corrupt episodeProgresses payload.":
        return LibraryRecordDecodeError(message)
    if message.startswith("Library entry "):
        return LibraryRecordDecodeError(message)
    if isinstance(field, str):
        return LibraryRecordDecodeError(f"Invalid {field} value.")
    return LibraryRecordDecodeError(message)


def _normalized_error_message(message: str) -> str:
    prefix = "Value error, "
    if message.startswith(prefix):
        return message[len(prefix) :]
    return message
