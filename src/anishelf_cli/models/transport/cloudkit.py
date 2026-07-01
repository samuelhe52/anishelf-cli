from __future__ import annotations

from typing import Any

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import CurrentUser


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


class CloudKitRecordID(AniShelfBaseModel):
    record_name: StrictStr | None = Field(
        default=None,
        validation_alias="recordName",
        serialization_alias="recordName",
    )
    zone_id: dict[str, Any] | None = Field(
        default=None,
        validation_alias="zoneID",
        serialization_alias="zoneID",
    )


class CloudKitField(AniShelfBaseModel):
    type: StrictStr | None = None
    value: Any = None

    @model_validator(mode="before")
    @classmethod
    def _wrap_raw_value(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return value
        return {"value": value}


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
    created: dict[str, Any] | None = None
    modified: dict[str, Any] | None = None
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
        if not isinstance(self.modified, dict):
            return None
        timestamp = self.modified.get("timestamp")
        if isinstance(timestamp, bool):
            return None
        if isinstance(timestamp, int | float):
            return timestamp
        return None

    def field(self, name: str) -> CloudKitField | None:
        return self.fields.get(name)

    def field_value(self, name: str) -> Any | None:
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
    zone_id: dict[str, Any] | None = Field(
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
