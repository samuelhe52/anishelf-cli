from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_serializer,
    model_serializer,
)
from pydantic.functional_validators import field_validator, model_validator

from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.models.common import (
    AniShelfBaseModel,
    EmptyTupleForNone,
    NonEmptyStr,
)
from anishelf_cli.models.identity import library_identity_from_fields

SNAPSHOT_KIND = "snapshot"
TOMBSTONE_KIND = "tombstone"
VALID_LIBRARY_ENTRY_KINDS = frozenset({SNAPSHOT_KIND, TOMBSTONE_KIND})
WATCH_STATUS_VALUES = frozenset({"planToWatch", "watching", "watched", "dropped"})


class EpisodeProgress(AniShelfBaseModel):
    season_number: StrictInt
    watched_through_episode: StrictInt
    updated_at: StrictStr | None = None


class LibraryEntryMetadataGenre(AniShelfBaseModel):
    id: StrictInt
    name: NonEmptyStr


class LibraryEntryMetadata(AniShelfBaseModel):
    """Typed TMDb metadata attached to a library entry."""

    entry_type: NonEmptyStr | None = None
    tmdb_id: StrictInt | None = None
    parent_series_id: StrictInt | None = None
    season_number: StrictInt | None = None
    language: NonEmptyStr | None = None
    name: NonEmptyStr | None = None
    name_translations: tuple[tuple[str, str], ...] = ()
    original_name: NonEmptyStr | None = None
    overview: NonEmptyStr | None = None
    overview_translations: tuple[tuple[str, str], ...] = ()
    poster_path: NonEmptyStr | None = None
    backdrop_path: NonEmptyStr | None = None
    logo_path: NonEmptyStr | None = None
    original_language_code: NonEmptyStr | None = None
    on_air_date: NonEmptyStr | None = None
    status: NonEmptyStr | None = None
    genre_ids: Annotated[tuple[StrictInt, ...], EmptyTupleForNone] = ()
    genres: Annotated[tuple[LibraryEntryMetadataGenre, ...], EmptyTupleForNone] = ()
    runtime_minutes: StrictInt | None = None
    season_count: StrictInt | None = None
    episode_count: StrictInt | None = None
    vote_average: StrictFloat | StrictInt | None = None
    vote_count: StrictInt | None = None
    popularity: StrictFloat | StrictInt | None = None
    link_to_details: NonEmptyStr | None = None
    fetched_at: NonEmptyStr | None = None
    source_version: NonEmptyStr | None = None

    @field_validator("name_translations", "overview_translations", mode="before")
    @classmethod
    def _validate_translations(cls, value: object, info: Any) -> tuple[tuple[str, str], ...]:
        if value is None:
            return ()
        items: Iterable[tuple[object, object]]
        if isinstance(value, dict):
            items = value.items()
        elif isinstance(value, list | tuple):
            items = value
        else:
            raise ValueError(f"Library entry {info.field_name} value is invalid.")

        normalized: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, list | tuple) or len(item) != 2:
                raise ValueError(f"Library entry {info.field_name} value is invalid.")
            key, raw_value = item
            if not isinstance(key, str):
                raise ValueError(f"Library entry {info.field_name} value is invalid.")
            parsed = nonempty_string_or_none(raw_value)
            if parsed is None:
                raise ValueError(f"Library entry {info.field_name} value is invalid.")
            normalized.append((key, parsed))
        return tuple(normalized)

    @model_validator(mode="after")
    def _derive_genre_ids(self) -> Self:
        if "genre_ids" not in self.model_fields_set and self.genres:
            object.__setattr__(self, "genre_ids", tuple(genre.id for genre in self.genres))
        return self

    @model_validator(mode="after")
    def _validate_identity_context(self) -> Self:
        identity_fields = (
            self.entry_type,
            self.tmdb_id,
            self.parent_series_id,
            self.season_number,
        )
        if all(field is None for field in identity_fields):
            return self
        if self.entry_type is None or self.tmdb_id is None:
            raise ValueError("Library entry metadata identity fields are incomplete.")
        try:
            library_identity_from_fields(
                self.entry_type,
                self.tmdb_id,
                self.parent_series_id,
                self.season_number,
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @field_serializer("name_translations", "overview_translations", when_used="json")
    def _serialize_translations(self, value: tuple[tuple[str, str], ...]) -> dict[str, str]:
        return dict(value)

    @property
    def title(self) -> str | None:
        return self.name or self.original_name

    @property
    def name_translation_map(self) -> dict[str, str]:
        return dict(self.name_translations)

    @property
    def overview_translation_map(self) -> dict[str, str]:
        return dict(self.overview_translations)

    @model_serializer(mode="wrap", when_used="json")
    def _serialize_output(
        self,
        handler: SerializerFunctionWrapHandler,
        info: SerializationInfo,
    ) -> dict[str, object]:
        payload = cast(dict[str, object], handler(self))
        if isinstance(info.context, dict) and info.context.get("storage_payload"):
            return payload
        return {
            field_name: payload[field_name]
            for field_name in self.__class__.model_fields
            if field_name in self.model_fields_set and field_name in payload
        }

    def storage_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"genre_ids"},
            context={"storage_payload": True},
        )

    def with_updates(self, **updates: object) -> LibraryEntryMetadata:
        payload = {
            field_name: getattr(self, field_name)
            for field_name in self.model_fields_set
            if field_name in self.__class__.model_fields
        }
        payload.update(updates)
        return self.__class__.model_validate(payload)


class _LibraryEntryBase(AniShelfBaseModel):
    identity: NonEmptyStr
    entry_type: NonEmptyStr
    tmdb_id: StrictInt
    parent_series_id: StrictInt | None = None
    season_number: StrictInt | None = None
    schema_version: StrictInt | None = None

    @model_validator(mode="after")
    def _validate_identity_fields(self) -> Self:
        from anishelf_cli.models.identity import LibraryIdentityError

        try:
            library_identity_from_fields(
                self.entry_type,
                self.tmdb_id,
                self.parent_series_id,
                self.season_number,
                raw_identity=self.identity,
            )
        except LibraryIdentityError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @property
    def title(self) -> str:
        return self.metadata_title or self.identity

    @property
    def metadata_title(self) -> str | None:
        metadata = getattr(self, "metadata", None)
        if isinstance(metadata, LibraryEntryMetadata):
            return metadata.title
        return None


class LibraryEntrySnapshot(_LibraryEntryBase):
    kind: Literal["snapshot"] = "snapshot"
    on_display: StrictBool
    date_saved: NonEmptyStr
    watch_status: StrictStr
    date_started: NonEmptyStr | None = None
    date_finished: NonEmptyStr | None = None
    is_date_tracking_enabled: StrictBool
    score: StrictInt | None = None
    favorite: StrictBool
    notes: StrictStr
    using_custom_poster: StrictBool
    custom_poster_path: NonEmptyStr | None = None
    episode_progresses: Annotated[tuple[EpisodeProgress, ...], EmptyTupleForNone] = ()
    library_updated_at: NonEmptyStr | None = None
    tracking_updated_at: NonEmptyStr | None = None
    metadata: LibraryEntryMetadata | None = None

    @field_validator("watch_status")
    @classmethod
    def _validate_watch_status(cls, value: str) -> str:
        if value not in WATCH_STATUS_VALUES:
            valid = ", ".join(sorted(WATCH_STATUS_VALUES))
            raise ValueError(
                f"Library entry watch_status value is invalid. Expected one of: {valid}."
            )
        return value

    @model_validator(mode="after")
    def _validate_metadata_identity(self) -> Self:
        if self.metadata is None:
            return self
        metadata_identity = (
            self.metadata.entry_type,
            self.metadata.tmdb_id,
            self.metadata.parent_series_id,
            self.metadata.season_number,
        )
        if all(field is None for field in metadata_identity):
            return self
        entry_identity = (
            self.entry_type,
            self.tmdb_id,
            self.parent_series_id,
            self.season_number,
        )
        if metadata_identity != entry_identity:
            raise ValueError("Library entry metadata identity does not match entry.")
        return self

    def with_metadata(self, metadata: LibraryEntryMetadata | None) -> LibraryEntrySnapshot:
        if metadata is not None and not isinstance(metadata, LibraryEntryMetadata):
            raise TypeError("Library entry metadata must be a LibraryEntryMetadata instance.")
        payload = self.model_dump(mode="python", round_trip=True)
        payload["metadata"] = metadata
        return self.__class__.model_validate(payload)

    def without_metadata(self) -> LibraryEntrySnapshot:
        return self.with_metadata(None)

    @model_serializer(mode="wrap", when_used="json")
    def _serialize_output(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        payload = cast(dict[str, object], handler(self))
        if self.metadata is None:
            payload.pop("metadata", None)
        return payload


class LibraryEntryTombstone(_LibraryEntryBase):
    kind: Literal["tombstone"] = "tombstone"
    deleted_at: NonEmptyStr

    def with_metadata(self, metadata: LibraryEntryMetadata | None) -> LibraryEntryTombstone:
        if metadata is not None:
            raise ValueError("Tombstone library entries cannot define metadata.")
        return self

    def without_metadata(self) -> LibraryEntryTombstone:
        return self


LibraryEntry = Annotated[
    LibraryEntrySnapshot | LibraryEntryTombstone,
    Field(discriminator="kind"),
]
LibraryEntryAdapter: TypeAdapter[LibraryEntry] = TypeAdapter(LibraryEntry)
LibraryEntryModel = LibraryEntrySnapshot | LibraryEntryTombstone


class CurrentUser(AniShelfBaseModel):
    user_record_name: StrictStr
    first_name: StrictStr | None = None
    last_name: StrictStr | None = None
    email: StrictStr | None = None

    @property
    def display_name(self) -> str | None:
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) if parts else None

    @model_serializer(mode="plain", when_used="json")
    def _serialize_output(self) -> dict[str, object]:
        return {
            "status": "authenticated",
            "user": {
                "user_record_name": self.user_record_name,
                "first_name": self.first_name,
                "last_name": self.last_name,
                "email": self.email,
            },
        }


class TMDbSummaryIdentity(AniShelfBaseModel):
    entry_type: StrictStr
    tmdb_id: StrictInt
    parent_series_id: StrictInt | None = None
    season_number: StrictInt | None = None

    @model_validator(mode="after")
    def _validate_entry_context(self) -> Self:
        from anishelf_cli.models.identity import LibraryIdentityError

        try:
            library_identity_from_fields(
                self.entry_type,
                self.tmdb_id,
                self.parent_series_id,
                self.season_number,
            )
        except LibraryIdentityError as exc:
            raise ValueError(str(exc)) from exc
        return self


def validate_library_entry(value: object) -> LibraryEntryModel:
    return LibraryEntryAdapter.validate_python(value)


def validate_library_entry_json(value: str | bytes) -> LibraryEntryModel:
    return LibraryEntryAdapter.validate_json(value)
