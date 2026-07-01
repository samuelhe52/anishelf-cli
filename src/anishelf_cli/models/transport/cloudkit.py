from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypeVar

from pydantic import (
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import CurrentUser

SWIFT_REFERENCE_DATE = datetime(2001, 1, 1, tzinfo=UTC)
ModelT = TypeVar("ModelT", bound=AniShelfBaseModel)


def _int_from_raw(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Invalid integer value.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError("Invalid integer value.")


def _bool_from_raw(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError("Invalid boolean value.")


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_raw(value: object) -> str:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid datetime value.") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return _iso_z(parsed)
    if isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = float(value)
        if abs(timestamp) > 10_000_000_000:
            timestamp /= 1000
        return _iso_z(datetime.fromtimestamp(timestamp, UTC))
    raise ValueError("Invalid datetime value.")


def _swift_reference_datetime_from_raw(value: object) -> str:
    if isinstance(value, str):
        return _datetime_from_raw(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return _iso_z(SWIFT_REFERENCE_DATE + timedelta(seconds=float(value)))
    raise ValueError("Invalid datetime value.")


CloudKitIntValue = Annotated[StrictInt, BeforeValidator(_int_from_raw)]
CloudKitBoolValue = Annotated[StrictBool, BeforeValidator(_bool_from_raw)]
CloudKitDateTimeValue = Annotated[StrictStr, BeforeValidator(_datetime_from_raw)]
CloudKitSwiftReferenceDateTimeValue = Annotated[
    StrictStr,
    BeforeValidator(_swift_reference_datetime_from_raw),
]


class CloudKitCurrentUserResponse(AniShelfBaseModel):
    user_record_name: StrictStr = Field(
        validation_alias="userRecordName",
        serialization_alias="userRecordName",
    )
    first_name: StrictStr | None = Field(
        default=None,
        validation_alias="firstName",
        serialization_alias="firstName",
    )
    last_name: StrictStr | None = Field(
        default=None,
        validation_alias="lastName",
        serialization_alias="lastName",
    )
    email: StrictStr | None = None
    web_auth_token: StrictStr | None = Field(
        default=None,
        validation_alias="webAuthToken",
        serialization_alias="webAuthToken",
    )

    def to_domain(self) -> CurrentUser:
        return CurrentUser(
            user_record_name=self.user_record_name,
            first_name=nonempty_string_or_none(self.first_name),
            last_name=nonempty_string_or_none(self.last_name),
            email=nonempty_string_or_none(self.email),
        )


class _CloudKitTypedNestedModel(AniShelfBaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        populate_by_name=False,
        str_strip_whitespace=False,
    )


class CloudKitZoneID(_CloudKitTypedNestedModel):
    zone_name: StrictStr | None = Field(
        default=None,
        validation_alias="zoneName",
        serialization_alias="zoneName",
    )
    owner_record_name: StrictStr | None = Field(
        default=None,
        validation_alias="ownerRecordName",
        serialization_alias="ownerRecordName",
    )


class CloudKitRecordID(AniShelfBaseModel):
    record_name: StrictStr | None = Field(
        default=None,
        validation_alias="recordName",
        serialization_alias="recordName",
    )
    zone_id: CloudKitZoneID | None = Field(
        default=None,
        validation_alias="zoneID",
        serialization_alias="zoneID",
    )


class CloudKitField(AniShelfBaseModel):
    type: StrictStr | None = None
    value: JsonValue = None

    @model_validator(mode="before")
    @classmethod
    def _wrap_raw_value(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return value
        return {"value": value}


class CloudKitUserTimestamp(_CloudKitTypedNestedModel):
    timestamp: StrictInt | StrictFloat | None = None
    user_record_name: StrictStr | None = Field(
        default=None,
        validation_alias="userRecordName",
        serialization_alias="userRecordName",
    )
    device_id: StrictStr | None = Field(
        default=None,
        validation_alias="deviceID",
        serialization_alias="deviceID",
    )


class CloudKitEpisodeProgressPayload(AniShelfBaseModel):
    season_number: CloudKitIntValue = Field(
        validation_alias="seasonNumber",
        serialization_alias="seasonNumber",
    )
    watched_through_episode: CloudKitIntValue = Field(
        validation_alias="watchedThroughEpisode",
        serialization_alias="watchedThroughEpisode",
    )
    updated_at: CloudKitSwiftReferenceDateTimeValue = Field(
        validation_alias="updatedAt",
        serialization_alias="updatedAt",
    )


class _CloudKitFieldPayloadModel(AniShelfBaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=False,
        str_strip_whitespace=False,
    )


class CloudKitLibraryEntryCommonFields(_CloudKitFieldPayloadModel):
    schema_version: CloudKitIntValue = Field(
        validation_alias="schemaVersion",
        serialization_alias="schemaVersion",
    )
    tmdb_id: CloudKitIntValue = Field(
        validation_alias="tmdbID",
        serialization_alias="tmdbID",
    )
    entry_type: StrictStr = Field(
        validation_alias="entryType",
        serialization_alias="entryType",
    )
    parent_series_id: CloudKitIntValue | None = Field(
        default=None,
        validation_alias="parentSeriesID",
        serialization_alias="parentSeriesID",
    )
    season_number: CloudKitIntValue | None = Field(
        default=None,
        validation_alias="seasonNumber",
        serialization_alias="seasonNumber",
    )
    deleted_at: CloudKitDateTimeValue | None = Field(
        default=None,
        validation_alias="deletedAt",
        serialization_alias="deletedAt",
    )


class CloudKitLibraryEntrySnapshotFields(CloudKitLibraryEntryCommonFields):
    on_display: CloudKitBoolValue = Field(
        validation_alias="onDisplay",
        serialization_alias="onDisplay",
    )
    date_saved: CloudKitDateTimeValue = Field(
        validation_alias="dateSaved",
        serialization_alias="dateSaved",
    )
    watch_status: StrictStr = Field(
        validation_alias="watchStatus",
        serialization_alias="watchStatus",
    )
    date_started: CloudKitDateTimeValue | None = Field(
        default=None,
        validation_alias="dateStarted",
        serialization_alias="dateStarted",
    )
    date_finished: CloudKitDateTimeValue | None = Field(
        default=None,
        validation_alias="dateFinished",
        serialization_alias="dateFinished",
    )
    is_date_tracking_enabled: CloudKitBoolValue = Field(
        validation_alias="isDateTrackingEnabled",
        serialization_alias="isDateTrackingEnabled",
    )
    score: CloudKitIntValue | None = None
    favorite: CloudKitBoolValue = Field(
        validation_alias="favorite",
        serialization_alias="favorite",
    )
    notes: StrictStr
    using_custom_poster: CloudKitBoolValue = Field(
        validation_alias="usingCustomPoster",
        serialization_alias="usingCustomPoster",
    )
    custom_poster_path: StrictStr | None = Field(
        default=None,
        validation_alias="customPosterPath",
        serialization_alias="customPosterPath",
    )
    custom_poster_url: StrictStr | None = Field(
        default=None,
        validation_alias="customPosterURL",
        serialization_alias="customPosterURL",
    )
    episode_progresses: tuple[CloudKitEpisodeProgressPayload, ...] = Field(
        default=(),
        validation_alias="episodeProgresses",
        serialization_alias="episodeProgresses",
    )
    library_updated_at: CloudKitDateTimeValue | None = Field(
        default=None,
        validation_alias="libraryUpdatedAt",
        serialization_alias="libraryUpdatedAt",
    )
    tracking_updated_at: CloudKitDateTimeValue | None = Field(
        default=None,
        validation_alias="trackingUpdatedAt",
        serialization_alias="trackingUpdatedAt",
    )

    @field_validator("custom_poster_path", "custom_poster_url")
    @classmethod
    def _validate_optional_nonempty_string(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None or nonempty_string_or_none(value) is not None:
            return value
        raise ValueError("Invalid string value.")

    @field_validator("episode_progresses", mode="before")
    @classmethod
    def _decode_episode_progresses(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, list | tuple):
            return value
        if isinstance(value, str):
            try:
                decoded_bytes = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                decoded_bytes = value.encode()
            try:
                decoded = json.loads(decoded_bytes.decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Corrupt episodeProgresses payload.") from exc
            if not isinstance(decoded, list):
                raise ValueError("Invalid episodeProgresses value.")
            return decoded
        raise ValueError("Invalid episodeProgresses value.")


class CloudKitLibraryEntryTombstoneFields(CloudKitLibraryEntryCommonFields):
    deleted_at: CloudKitDateTimeValue = Field(
        validation_alias="deletedAt",
        serialization_alias="deletedAt",
    )


class CloudKitRecord(AniShelfBaseModel):
    record_name: StrictStr | None = Field(
        default=None,
        validation_alias="recordName",
        serialization_alias="recordName",
    )
    record_id: CloudKitRecordID | None = Field(
        default=None,
        validation_alias="recordID",
        serialization_alias="recordID",
    )
    record_type: StrictStr | None = Field(
        default=None,
        validation_alias="recordType",
        serialization_alias="recordType",
    )
    record_change_tag: StrictStr | None = Field(
        default=None,
        validation_alias="recordChangeTag",
        serialization_alias="recordChangeTag",
    )
    created: CloudKitUserTimestamp | None = None
    modified: CloudKitUserTimestamp | None = None
    deleted: StrictBool | None = None
    fields: dict[str, CloudKitField] = Field(default_factory=dict)
    server_error_code: StrictStr | None = Field(
        default=None,
        validation_alias="serverErrorCode",
        serialization_alias="serverErrorCode",
    )
    reason: StrictStr | None = None

    @property
    def effective_record_name(self) -> str | None:
        if name := nonempty_string_or_none(self.record_name):
            return name
        if self.record_id is not None:
            return nonempty_string_or_none(self.record_id.record_name)
        return None

    @property
    def is_deleted(self) -> bool:
        return self.deleted is True

    @property
    def modified_timestamp(self) -> int | float | None:
        if self.modified is None:
            return None
        return self.modified.timestamp

    def field_values(self) -> dict[str, JsonValue]:
        return {name: field.value for name, field in self.fields.items()}

    def validate_fields(self, model_type: type[ModelT]) -> ModelT:
        return model_type.model_validate(self.field_values())

    def field(self, name: str) -> CloudKitField | None:
        return self.fields.get(name)

    def field_value(self, name: str) -> JsonValue:
        field = self.field(name)
        if field is None:
            return None
        return field.value

    def to_cloudkit_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class CloudKitLookupResponse(AniShelfBaseModel):
    records: tuple[CloudKitRecord, ...] = ()
    server_error_code: StrictStr | None = Field(
        default=None,
        validation_alias="serverErrorCode",
        serialization_alias="serverErrorCode",
    )
    reason: StrictStr | None = None

    @field_validator("records", mode="before")
    @classmethod
    def _default_records(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    def results_by_record_name(self) -> dict[str, CloudKitRecord]:
        results: dict[str, CloudKitRecord] = {}
        for record in self.records:
            if record_name := record.effective_record_name:
                results[record_name] = record
        return results


class CloudKitZoneResult(AniShelfBaseModel):
    zone_id: CloudKitZoneID | None = Field(
        default=None,
        validation_alias="zoneID",
        serialization_alias="zoneID",
    )
    records: tuple[CloudKitRecord, ...] = ()
    sync_token: StrictStr | None = Field(
        default=None,
        validation_alias="syncToken",
        serialization_alias="syncToken",
    )
    more_coming: StrictBool = Field(
        default=False,
        validation_alias="moreComing",
        serialization_alias="moreComing",
    )
    server_error_code: StrictStr | None = Field(
        default=None,
        validation_alias="serverErrorCode",
        serialization_alias="serverErrorCode",
    )
    reason: StrictStr | None = None

    @field_validator("records", mode="before")
    @classmethod
    def _default_records(cls, value: object) -> object:
        if value is None:
            return ()
        return value


class CloudKitZoneChangesResponse(AniShelfBaseModel):
    zones: tuple[CloudKitZoneResult, ...]
    server_error_code: StrictStr | None = Field(
        default=None,
        validation_alias="serverErrorCode",
        serialization_alias="serverErrorCode",
    )
    reason: StrictStr | None = None


class ZoneChangesPage(AniShelfBaseModel):
    records: tuple[CloudKitRecord, ...]
    sync_token: StrictStr
    more_coming: StrictBool
