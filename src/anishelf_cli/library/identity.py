from __future__ import annotations

from dataclasses import dataclass


class LibraryIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LibraryIdentity:
    raw: str
    entry_type: str
    tmdb_id: int
    parent_series_id: int | None = None
    season_number: int | None = None


def parse_library_identity(raw_identity: str) -> LibraryIdentity:
    parts = raw_identity.split(":")
    if len(parts) == 2 and parts[0] in {"movie", "series"}:
        tmdb_id = _parse_positive_int(parts[1], "tmdbID")
        return LibraryIdentity(raw_identity, parts[0], tmdb_id)

    if len(parts) == 4 and parts[0] == "season":
        parent_series_id = _parse_positive_int(parts[1], "parentSeriesID")
        season_number = _parse_non_negative_int(parts[2], "seasonNumber")
        tmdb_id = _parse_positive_int(parts[3], "tmdbID")
        return LibraryIdentity(raw_identity, "season", tmdb_id, parent_series_id, season_number)

    raise LibraryIdentityError(
        "Expected identity in one of these forms: movie:<tmdbID>, series:<tmdbID>, "
        "season:<parentSeriesID>:<seasonNumber>:<tmdbID>."
    )


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
