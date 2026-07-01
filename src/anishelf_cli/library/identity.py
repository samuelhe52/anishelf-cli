from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VALID_LIBRARY_ENTRY_TYPES = frozenset({"movie", "series", "season"})
LibraryEntryType = Literal["movie", "series", "season"]


class LibraryIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LibraryIdentity:
    raw: str
    entry_type: LibraryEntryType
    tmdb_id: int
    parent_series_id: int | None = None
    season_number: int | None = None


def parse_library_identity(raw_identity: str) -> LibraryIdentity:
    parts = raw_identity.split(":")
    if len(parts) == 2 and parts[0] in {"movie", "series"}:
        return library_identity_from_fields(
            parts[0],
            _parse_positive_int(parts[1], "tmdbID"),
            raw_identity=raw_identity,
        )

    if len(parts) == 4 and parts[0] == "season":
        return library_identity_from_fields(
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


def library_identity_from_fields(
    entry_type: str,
    tmdb_id: int,
    parent_series_id: int | None = None,
    season_number: int | None = None,
    *,
    raw_identity: str | None = None,
) -> LibraryIdentity:
    normalized_entry_type = _validate_entry_type(entry_type)
    normalized_tmdb_id = _validate_positive_int(tmdb_id, "tmdbID")

    if normalized_entry_type == "season":
        if parent_series_id is None or season_number is None:
            raise LibraryIdentityError("Season identity requires parentSeriesID and seasonNumber.")
        normalized_parent_series_id = _validate_positive_int(
            parent_series_id,
            "parentSeriesID",
        )
        normalized_season_number = _validate_non_negative_int(
            season_number,
            "seasonNumber",
        )
        expected_identity = (
            f"season:{normalized_parent_series_id}:{normalized_season_number}:{normalized_tmdb_id}"
        )
        identity = LibraryIdentity(
            raw=expected_identity,
            entry_type="season",
            tmdb_id=normalized_tmdb_id,
            parent_series_id=normalized_parent_series_id,
            season_number=normalized_season_number,
        )
    else:
        if parent_series_id is not None or season_number is not None:
            raise LibraryIdentityError(
                f"{normalized_entry_type} identity cannot define parentSeriesID or seasonNumber."
            )
        expected_identity = f"{normalized_entry_type}:{normalized_tmdb_id}"
        identity = LibraryIdentity(
            raw=expected_identity,
            entry_type=normalized_entry_type,
            tmdb_id=normalized_tmdb_id,
        )

    if raw_identity is not None and raw_identity != expected_identity:
        raise LibraryIdentityError("Library entry identity does not match decoded fields.")
    return identity


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


def _validate_entry_type(raw: str) -> LibraryEntryType:
    if raw not in VALID_LIBRARY_ENTRY_TYPES:
        valid = ", ".join(sorted(VALID_LIBRARY_ENTRY_TYPES))
        raise LibraryIdentityError(f"entryType must be one of: {valid}.")
    return raw


def _validate_positive_int(raw: int, label: str) -> int:
    value = _validate_int(raw, label)
    if value <= 0:
        raise LibraryIdentityError(f"{label} must be a positive integer.")
    return value


def _validate_non_negative_int(raw: int, label: str) -> int:
    value = _validate_int(raw, label)
    if value < 0:
        raise LibraryIdentityError(f"{label} must be a non-negative integer.")
    return value


def _validate_int(raw: int, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise LibraryIdentityError(f"{label} must be an integer.")
    return raw


def _parse_int(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise LibraryIdentityError(f"{label} must be an integer.") from exc
