from __future__ import annotations

import pytest

from anishelf_cli.models.identity import (
    LibraryIdentity,
    LibraryIdentityError,
    library_identity_from_fields,
    parse_library_identity,
)


def test_library_identity_model_derives_raw_from_fields() -> None:
    identity = LibraryIdentity.from_fields("season", 33, parent_series_id=22, season_number=1)

    assert identity.raw == "season:22:1:33"
    assert identity.model_dump(mode="json") == {
        "raw": "season:22:1:33",
        "entry_type": "season",
        "tmdb_id": 33,
        "parent_series_id": 22,
        "season_number": 1,
    }


def test_parse_library_identity_returns_model_identity() -> None:
    identity = parse_library_identity("movie:55")

    assert isinstance(identity, LibraryIdentity)
    assert identity.raw == "movie:55"
    assert identity.entry_type == "movie"
    assert identity.tmdb_id == 55
    assert identity.parent_series_id is None
    assert identity.season_number is None


def test_library_identity_from_fields_rejects_non_season_context() -> None:
    with pytest.raises(
        LibraryIdentityError,
        match=r"movie identity cannot define parentSeriesID or seasonNumber\.",
    ):
        library_identity_from_fields("movie", 55, parent_series_id=22, season_number=1)


def test_parse_library_identity_rejects_invalid_shape() -> None:
    with pytest.raises(LibraryIdentityError, match="Expected identity in one of these forms"):
        parse_library_identity("movie")
