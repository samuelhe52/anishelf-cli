from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class EpisodeProgress:
    season_number: int
    watched_through_episode: int
    updated_at: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> EpisodeProgress:
        return cls(
            season_number=_required_int(payload, "season_number"),
            watched_through_episode=_required_int(payload, "watched_through_episode"),
            updated_at=_optional_string(payload.get("updated_at")),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "season_number": self.season_number,
            "watched_through_episode": self.watched_through_episode,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class LibraryEntryMetadata:
    payload: dict[str, object]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> LibraryEntryMetadata:
        return cls(payload=dict(payload))

    def to_payload(self) -> dict[str, object]:
        return dict(self.payload)

    def string_field(self, key: str) -> str | None:
        return _optional_string(self.payload.get(key))

    @property
    def name(self) -> str | None:
        return self.string_field("name")

    @property
    def original_name(self) -> str | None:
        return self.string_field("original_name")

    @property
    def overview(self) -> str | None:
        return self.string_field("overview")

    @property
    def title(self) -> str | None:
        return self.name or self.original_name


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    identity: str
    kind: str
    entry_type: str
    tmdb_id: int
    parent_series_id: int | None = None
    season_number: int | None = None
    schema_version: int | None = None
    deleted_at: str | None = None
    on_display: bool | None = None
    date_saved: str | None = None
    watch_status: str | None = None
    date_started: str | None = None
    date_finished: str | None = None
    is_date_tracking_enabled: bool | None = None
    score: int | None = None
    favorite: bool | None = None
    notes: str | None = None
    using_custom_poster: bool | None = None
    custom_poster_path: str | None = None
    episode_progresses: tuple[EpisodeProgress, ...] = ()
    library_updated_at: str | None = None
    tracking_updated_at: str | None = None
    metadata: LibraryEntryMetadata | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> LibraryEntry:
        episode_progresses = payload.get("episode_progresses")
        metadata_payload = payload.get("metadata")
        return cls(
            identity=_required_string(payload, "identity"),
            kind=_required_string(payload, "kind"),
            entry_type=_required_string(payload, "entry_type"),
            tmdb_id=_required_int(payload, "tmdb_id"),
            parent_series_id=_optional_int(payload.get("parent_series_id")),
            season_number=_optional_int(payload.get("season_number")),
            schema_version=_optional_int(payload.get("schema_version")),
            deleted_at=_optional_string(payload.get("deleted_at")),
            on_display=_optional_bool(payload.get("on_display")),
            date_saved=_optional_string(payload.get("date_saved")),
            watch_status=_optional_string(payload.get("watch_status")),
            date_started=_optional_string(payload.get("date_started")),
            date_finished=_optional_string(payload.get("date_finished")),
            is_date_tracking_enabled=_optional_bool(payload.get("is_date_tracking_enabled")),
            score=_optional_int(payload.get("score")),
            favorite=_optional_bool(payload.get("favorite")),
            notes=_string_or_empty(payload.get("notes")),
            using_custom_poster=_optional_bool(payload.get("using_custom_poster")),
            custom_poster_path=_optional_string(payload.get("custom_poster_path")),
            episode_progresses=_episode_progresses(episode_progresses),
            library_updated_at=_optional_string(payload.get("library_updated_at")),
            tracking_updated_at=_optional_string(payload.get("tracking_updated_at")),
            metadata=_metadata_or_none(metadata_payload),
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "identity": self.identity,
            "entry_type": self.entry_type,
            "tmdb_id": self.tmdb_id,
            "parent_series_id": self.parent_series_id,
            "season_number": self.season_number,
        }
        if self.kind == "snapshot":
            payload.update(
                {
                    "schema_version": self.schema_version,
                    "on_display": self.on_display,
                    "date_saved": self.date_saved,
                    "watch_status": self.watch_status,
                    "date_started": self.date_started,
                    "date_finished": self.date_finished,
                    "is_date_tracking_enabled": self.is_date_tracking_enabled,
                    "score": self.score,
                    "favorite": self.favorite,
                    "notes": self.notes,
                    "using_custom_poster": self.using_custom_poster,
                    "custom_poster_path": self.custom_poster_path,
                    "episode_progresses": [
                        progress.to_payload() for progress in self.episode_progresses
                    ],
                    "library_updated_at": self.library_updated_at,
                    "tracking_updated_at": self.tracking_updated_at,
                }
            )
        else:
            payload.update(
                {
                    "schema_version": self.schema_version,
                    "deleted_at": self.deleted_at,
                }
            )
        if self.metadata is not None:
            payload["metadata"] = self.metadata.to_payload()
        return payload

    def with_metadata(self, metadata: LibraryEntryMetadata | None) -> LibraryEntry:
        return replace(self, metadata=metadata)

    def without_metadata(self) -> LibraryEntry:
        return replace(self, metadata=None)

    @property
    def title(self) -> str:
        return self.metadata_title or self.identity

    @property
    def metadata_title(self) -> str | None:
        if self.metadata is None:
            return None
        return self.metadata.title


def _episode_progresses(value: object) -> tuple[EpisodeProgress, ...]:
    if not isinstance(value, list):
        return ()
    progresses: list[EpisodeProgress] = []
    for item in value:
        if isinstance(item, Mapping):
            progresses.append(EpisodeProgress.from_payload(item))
    return tuple(progresses)


def _metadata_or_none(value: object) -> LibraryEntryMetadata | None:
    if not isinstance(value, Mapping):
        return None
    return LibraryEntryMetadata.from_payload(value)


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = _optional_string(payload.get(key))
    if value is None:
        raise ValueError(f"Library entry payload is missing {key}.")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = _optional_int(payload.get(key))
    if value is None:
        raise ValueError(f"Library entry payload is missing {key}.")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or_empty(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError("Library entry notes value is invalid.")


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
