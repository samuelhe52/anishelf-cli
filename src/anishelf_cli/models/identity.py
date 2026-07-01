from __future__ import annotations

from typing import Literal, Self

from pydantic import ValidationError, field_validator, model_validator

from anishelf_cli.models.common import AniShelfBaseModel, NonEmptyStr

VALID_LIBRARY_ENTRY_TYPES = frozenset({"movie", "series", "season"})
LibraryEntryType = Literal["movie", "series", "season"]


class LibraryIdentityError(ValueError):
    pass


class LibraryIdentity(AniShelfBaseModel):
    raw: NonEmptyStr | None = None
    entry_type: LibraryEntryType
    tmdb_id: int
    parent_series_id: int | None = None
    season_number: int | None = None

    @field_validator("tmdb_id", mode="before")
    @classmethod
    def _validate_tmdb_id(cls, value: object) -> int:
        return _validate_positive_int(value, "tmdbID")

    @field_validator("parent_series_id", mode="before")
    @classmethod
    def _validate_parent_series_id(cls, value: object) -> int | None:
        if value is None:
            return None
        return _validate_positive_int(value, "parentSeriesID")

    @field_validator("season_number", mode="before")
    @classmethod
    def _validate_season_number(cls, value: object) -> int | None:
        if value is None:
            return None
        return _validate_non_negative_int(value, "seasonNumber")

    @model_validator(mode="after")
    def _validate_context_and_raw(self) -> Self:
        expected_raw = _expected_identity(
            self.entry_type,
            self.tmdb_id,
            self.parent_series_id,
            self.season_number,
        )
        if self.raw is None:
            object.__setattr__(self, "raw", expected_raw)
            return self
        if self.raw != expected_raw:
            raise ValueError("Library entry identity does not match decoded fields.")
        return self

    @classmethod
    def parse(cls, raw_identity: str) -> LibraryIdentity:
        parts = raw_identity.split(":")
        if len(parts) == 2 and parts[0] in {"movie", "series"}:
            return cls.from_fields(
                parts[0],
                _parse_positive_int(parts[1], "tmdbID"),
                raw_identity=raw_identity,
            )

        if len(parts) == 4 and parts[0] == "season":
            return cls.from_fields(
                parts[0],
                _parse_positive_int(parts[3], "tmdbID"),
                parent_series_id=_parse_positive_int(parts[1], "parentSeriesID"),
                season_number=_parse_non_negative_int(parts[2], "seasonNumber"),
                raw_identity=raw_identity,
            )

        raise LibraryIdentityError(
            "Expected identity in one of these forms: movie:<tmdbID>, series:<tmdbID>, "
            "season:<parentSeriesID>:<seasonNumber>:<tmdbID>."
        )

    @classmethod
    def from_fields(
        cls,
        entry_type: str,
        tmdb_id: int,
        parent_series_id: int | None = None,
        season_number: int | None = None,
        *,
        raw_identity: str | None = None,
    ) -> LibraryIdentity:
        try:
            return cls.model_validate(
                {
                    "raw": raw_identity,
                    "entry_type": entry_type,
                    "tmdb_id": tmdb_id,
                    "parent_series_id": parent_series_id,
                    "season_number": season_number,
                }
            )
        except ValidationError as exc:
            raise _identity_error_from_validation(exc) from exc


def parse_library_identity(raw_identity: str) -> LibraryIdentity:
    return LibraryIdentity.parse(raw_identity)


def library_identity_from_fields(
    entry_type: str,
    tmdb_id: int,
    parent_series_id: int | None = None,
    season_number: int | None = None,
    *,
    raw_identity: str | None = None,
) -> LibraryIdentity:
    return LibraryIdentity.from_fields(
        entry_type,
        tmdb_id,
        parent_series_id,
        season_number,
        raw_identity=raw_identity,
    )


def _expected_identity(
    entry_type: str,
    tmdb_id: int,
    parent_series_id: int | None,
    season_number: int | None,
) -> str:
    if entry_type == "season":
        if parent_series_id is None or season_number is None:
            raise ValueError("Season identity requires parentSeriesID and seasonNumber.")
        return f"season:{parent_series_id}:{season_number}:{tmdb_id}"
    if parent_series_id is not None or season_number is not None:
        raise ValueError(f"{entry_type} identity cannot define parentSeriesID or seasonNumber.")
    return f"{entry_type}:{tmdb_id}"


def _validate_positive_int(value: object, label: str) -> int:
    parsed = _validate_int(value, label)
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return parsed


def _validate_non_negative_int(value: object, label: str) -> int:
    parsed = _validate_int(value, label)
    if parsed < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    return parsed


def _validate_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer.")
    return value


def _parse_positive_int(raw: str, label: str) -> int:
    value = _parse_int(raw, label)
    if value <= 0:
        raise LibraryIdentityError(f"{label} must be a positive integer.")
    return value


def _parse_non_negative_int(raw: str, label: str) -> int:
    value = _parse_int(raw, label)
    if value < 0:
        raise LibraryIdentityError(f"{label} must be a non-negative integer.")
    return value


def _parse_int(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise LibraryIdentityError(f"{label} must be an integer.") from exc


def _identity_error_from_validation(exc: ValidationError) -> LibraryIdentityError:
    first_error = exc.errors(include_url=False)[0]
    message = str(first_error["msg"])
    prefix = "Value error, "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return LibraryIdentityError(message)


__all__ = [
    "VALID_LIBRARY_ENTRY_TYPES",
    "LibraryEntryType",
    "LibraryIdentity",
    "LibraryIdentityError",
    "library_identity_from_fields",
    "parse_library_identity",
]
