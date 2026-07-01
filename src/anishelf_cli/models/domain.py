from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_serializer,
)
from pydantic.functional_validators import field_validator, model_validator

from anishelf_cli.core.coercion import nonempty_string_or_none, strict_int_or_none
from anishelf_cli.models.common import AniShelfBaseModel

SNAPSHOT_KIND = "snapshot"
TOMBSTONE_KIND = "tombstone"
VALID_LIBRARY_ENTRY_KINDS = frozenset({SNAPSHOT_KIND, TOMBSTONE_KIND})


class EpisodeProgress(AniShelfBaseModel):
    season_number: StrictInt
    watched_through_episode: StrictInt
    updated_at: StrictStr | None = None

    @field_validator("season_number", "watched_through_episode")
    @classmethod
    def _validate_progress_int(cls, value: int, info: Any) -> int:
        if strict_int_or_none(value) is None:
            raise ValueError(f"Library entry {info.field_name} value is invalid.")
        return value

    @field_validator("updated_at")
    @classmethod
    def _validate_updated_at(cls, value: str | None) -> str | None:
        if value is not None and not isinstance(value, str):
            raise ValueError("Library entry updated_at value is invalid.")
        return value


class LibraryEntryMetadataGenre(AniShelfBaseModel):
    id: StrictInt
    name: StrictStr

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: int) -> int:
        if strict_int_or_none(value) is None:
            raise ValueError("Library entry metadata genre id value is invalid.")
        return value

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if nonempty_string_or_none(value) is None:
            raise ValueError("Library entry metadata genre name value is invalid.")
        return value


class LibraryEntryMetadata(AniShelfBaseModel):
    """Typed TMDb metadata attached to a library entry."""

    entry_type: StrictStr | None = None
    tmdb_id: StrictInt | None = None
    parent_series_id: StrictInt | None = None
    season_number: StrictInt | None = None
    language: StrictStr | None = None
    name: StrictStr | None = None
    name_translations: tuple[tuple[str, str], ...] = ()
    original_name: StrictStr | None = None
    overview: StrictStr | None = None
    overview_translations: tuple[tuple[str, str], ...] = ()
    poster_path: StrictStr | None = None
    backdrop_path: StrictStr | None = None
    logo_path: StrictStr | None = None
    original_language_code: StrictStr | None = None
    on_air_date: StrictStr | None = None
    status: StrictStr | None = None
    genre_ids: tuple[StrictInt, ...] = ()
    genres: tuple[LibraryEntryMetadataGenre, ...] = ()
    runtime_minutes: StrictInt | None = None
    season_count: StrictInt | None = None
    episode_count: StrictInt | None = None
    vote_average: StrictFloat | StrictInt | None = None
    vote_count: StrictInt | None = None
    popularity: StrictFloat | StrictInt | None = None
    link_to_details: StrictStr | None = None
    fetched_at: StrictStr | None = None
    source_version: StrictStr | None = None

    @field_validator(
        "entry_type",
        "language",
        "name",
        "original_name",
        "overview",
        "poster_path",
        "backdrop_path",
        "logo_path",
        "original_language_code",
        "on_air_date",
        "status",
        "link_to_details",
        "fetched_at",
        "source_version",
    )
    @classmethod
    def _validate_optional_nonempty_string(
        cls,
        value: str | None,
        info: Any,
    ) -> str | None:
        if value is None:
            return None
        if nonempty_string_or_none(value) is None:
            raise ValueError(f"Library entry metadata {info.field_name} value is invalid.")
        return value

    @field_validator(
        "tmdb_id",
        "parent_series_id",
        "season_number",
        "runtime_minutes",
        "season_count",
        "episode_count",
        "vote_count",
    )
    @classmethod
    def _validate_optional_int(cls, value: int | None, info: Any) -> int | None:
        if value is None:
            return None
        if strict_int_or_none(value) is None:
            raise ValueError(f"Library entry metadata {info.field_name} value is invalid.")
        return value

    @field_validator("genre_ids", mode="before")
    @classmethod
    def _validate_genre_ids(cls, value: object) -> object:
        if value is None:
            return ()
        return value

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

    @field_validator("genres", mode="before")
    @classmethod
    def _validate_genres(cls, value: object) -> object:
        if value is None:
            return ()
        return value

    @model_validator(mode="after")
    def _derive_genre_ids(self) -> Self:
        if "genre_ids" not in self.model_fields_set and self.genres:
            object.__setattr__(self, "genre_ids", tuple(genre.id for genre in self.genres))
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

    def output_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", include=self.model_fields_set)

    def storage_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"genre_ids"})


class _LibraryEntryBase(AniShelfBaseModel):
    identity: StrictStr
    entry_type: StrictStr
    tmdb_id: StrictInt
    parent_series_id: StrictInt | None = None
    season_number: StrictInt | None = None
    schema_version: StrictInt | None = None

    @field_validator("identity", "entry_type")
    @classmethod
    def _validate_required_string(cls, value: str, info: Any) -> str:
        if nonempty_string_or_none(value) is None:
            raise ValueError(f"Library entry {info.field_name} value is invalid.")
        return value

    @field_validator("tmdb_id")
    @classmethod
    def _validate_required_int(cls, value: int, info: Any) -> int:
        if strict_int_or_none(value) is None:
            raise ValueError(f"Library entry {info.field_name} value is invalid.")
        return value

    @field_validator("parent_series_id", "season_number", "schema_version")
    @classmethod
    def _validate_optional_int(cls, value: int | None, info: Any) -> int | None:
        if value is None:
            return None
        if strict_int_or_none(value) is None:
            raise ValueError(f"Library entry {info.field_name} value is invalid.")
        return value

    @model_validator(mode="after")
    def _validate_identity_fields(self) -> Self:
        from anishelf_cli.library.identity import LibraryIdentityError, parse_library_identity

        try:
            identity = parse_library_identity(self.identity)
        except LibraryIdentityError as exc:
            raise ValueError(str(exc)) from exc
        if (
            identity.entry_type != self.entry_type
            or identity.tmdb_id != self.tmdb_id
            or identity.parent_series_id != self.parent_series_id
            or identity.season_number != self.season_number
        ):
            raise ValueError("Library entry identity does not match decoded fields.")
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

    def output_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class LibraryEntrySnapshot(_LibraryEntryBase):
    kind: Literal["snapshot"] = "snapshot"
    on_display: StrictBool
    date_saved: StrictStr
    watch_status: StrictStr
    date_started: StrictStr | None = None
    date_finished: StrictStr | None = None
    is_date_tracking_enabled: StrictBool
    score: StrictInt | None = None
    favorite: StrictBool
    notes: StrictStr
    using_custom_poster: StrictBool
    custom_poster_path: StrictStr | None = None
    episode_progresses: tuple[EpisodeProgress, ...] = ()
    library_updated_at: StrictStr | None = None
    tracking_updated_at: StrictStr | None = None
    metadata: LibraryEntryMetadata | None = None

    @field_validator(
        "date_saved",
        "watch_status",
        "date_started",
        "date_finished",
        "custom_poster_path",
        "library_updated_at",
        "tracking_updated_at",
    )
    @classmethod
    def _validate_optional_snapshot_string(
        cls,
        value: str | None,
        info: Any,
    ) -> str | None:
        if value is None:
            return None
        if nonempty_string_or_none(value) is None:
            raise ValueError(f"Library entry {info.field_name} value is invalid.")
        return value

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Library entry notes value is invalid.")
        return value

    @field_validator("episode_progresses", mode="before")
    @classmethod
    def _validate_episode_progresses(cls, value: object) -> object:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("Library entry episode_progresses value is invalid.")
        return value

    @field_serializer("metadata", when_used="json")
    def _serialize_metadata(
        self,
        metadata: LibraryEntryMetadata | None,
    ) -> dict[str, object] | None:
        if metadata is None:
            return None
        return metadata.output_payload()

    def with_metadata(self, metadata: LibraryEntryMetadata | None) -> LibraryEntrySnapshot:
        return self.model_copy(update={"metadata": metadata})

    def without_metadata(self) -> LibraryEntrySnapshot:
        return self.with_metadata(None)

    def output_payload(self) -> dict[str, object]:
        payload = super().output_payload()
        if self.metadata is None:
            payload.pop("metadata", None)
        return payload


class LibraryEntryTombstone(_LibraryEntryBase):
    kind: Literal["tombstone"] = "tombstone"
    deleted_at: StrictStr

    @field_validator("deleted_at")
    @classmethod
    def _validate_deleted_at(cls, value: str) -> str:
        if nonempty_string_or_none(value) is None:
            raise ValueError("Library entry deleted_at value is invalid.")
        return value

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

    def to_json_payload(self) -> dict[str, object]:
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


def validate_library_entry(value: object) -> LibraryEntryModel:
    return LibraryEntryAdapter.validate_python(value)


def validate_library_entry_json(value: str | bytes) -> LibraryEntryModel:
    return LibraryEntryAdapter.validate_json(value)
