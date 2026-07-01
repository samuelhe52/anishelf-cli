from __future__ import annotations

import pytest

from anishelf_cli.library.entries import EpisodeProgress, validate_library_entry
from anishelf_cli.library.metadata import LibraryEntryMetadata


def test_library_entry_rejects_unknown_kind() -> None:
    payload = _snapshot_payload(kind="snapshott")

    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_library_entry_adapter_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        validate_library_entry(
            {
                "identity": "movie:55",
                "kind": "snapshott",
                "entry_type": "movie",
                "tmdb_id": 55,
            }
        )


def test_library_entry_rejects_legacy_deleted_kind() -> None:
    with pytest.raises(ValueError):
        validate_library_entry(
            {
                "identity": "movie:55",
                "kind": "deleted",
                "entry_type": "movie",
                "tmdb_id": 55,
                "deleted_at": "2026-07-01T00:00:00Z",
            }
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("parent_series_id", "55", "Library entry parent_series_id value is invalid."),
        ("season_number", "1", "Library entry season_number value is invalid."),
        ("schema_version", "2", "Library entry schema_version value is invalid."),
        ("deleted_at", 123, "Library entry deleted_at value is invalid."),
        ("on_display", "yes", "Library entry boolean value is invalid."),
        ("date_saved", 123, "Library entry date_saved value is invalid."),
        ("watch_status", 123, "Library entry watch_status value is invalid."),
        ("date_started", 123, "Library entry date_started value is invalid."),
        ("date_finished", 123, "Library entry date_finished value is invalid."),
        (
            "is_date_tracking_enabled",
            "yes",
            "Library entry boolean value is invalid.",
        ),
        ("score", "8", "Library entry score value is invalid."),
        ("favorite", "true", "Library entry boolean value is invalid."),
        ("using_custom_poster", 1, "Library entry boolean value is invalid."),
        ("custom_poster_path", 123, "Library entry custom_poster_path value is invalid."),
        (
            "library_updated_at",
            123,
            "Library entry library_updated_at value is invalid.",
        ),
        (
            "tracking_updated_at",
            123,
            "Library entry tracking_updated_at value is invalid.",
        ),
    ],
)
def test_library_entry_rejects_invalid_optional_scalar(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _snapshot_payload(**{field: value})

    _ = message
    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_library_entry_rejects_invalid_metadata_shape() -> None:
    payload = _snapshot_payload(metadata=["bad"])

    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_library_entry_rejects_invalid_episode_progresses_shape() -> None:
    payload = _snapshot_payload(episode_progresses="bad")

    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_library_entry_rejects_invalid_episode_progress_item_shape() -> None:
    payload = _snapshot_payload(episode_progresses=["bad"])

    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_episode_progress_rejects_invalid_updated_at() -> None:
    with pytest.raises(ValueError):
        EpisodeProgress.model_validate(
            {
                "season_number": 1,
                "watched_through_episode": 3,
                "updated_at": 123,
            }
        )


def test_snapshot_library_entry_requires_snapshot_fields() -> None:
    payload = _snapshot_payload()
    del payload["date_saved"]

    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_tombstone_library_entry_rejects_snapshot_fields() -> None:
    payload = {
        "identity": "movie:55",
        "kind": "tombstone",
        "entry_type": "movie",
        "tmdb_id": 55,
        "deleted_at": "2026-07-01T00:00:00Z",
        "favorite": True,
    }

    with pytest.raises(ValueError):
        validate_library_entry(payload)


def test_library_entry_metadata_uses_typed_fields_and_preserves_partial_payload_shape() -> None:
    metadata = LibraryEntryMetadata.model_validate(
        {
            "name": "Alien",
            "original_name": "Alien",
            "genres": [{"id": 878, "name": "Science Fiction"}],
            "vote_average": 8.2,
        }
    )

    assert metadata.name == "Alien"
    assert metadata.original_name == "Alien"
    assert metadata.title == "Alien"
    assert metadata.genre_ids == (878,)
    assert [genre.model_dump(mode="json") for genre in metadata.genres] == [
        {"id": 878, "name": "Science Fiction"}
    ]
    assert metadata.vote_average == 8.2
    assert metadata.model_dump(mode="json", include=metadata.model_fields_set) == {
        "name": "Alien",
        "original_name": "Alien",
        "genres": [{"id": 878, "name": "Science Fiction"}],
        "vote_average": 8.2,
    }


def test_library_entry_metadata_round_trips_normalized_summary_payload() -> None:
    payload = {
        "entry_type": "series",
        "tmdb_id": 22,
        "parent_series_id": None,
        "season_number": None,
        "language": None,
        "name": "Alien Nation",
        "name_translations": {"ja": "エイリアン・ネイション"},
        "original_name": "Alien Nation",
        "overview": "A sci-fi police series.",
        "overview_translations": {},
        "poster_path": "/series.jpg",
        "backdrop_path": "/backdrop.jpg",
        "logo_path": None,
        "original_language_code": "en",
        "on_air_date": "1989-09-18",
        "status": "Ended",
        "genres": [{"id": 18, "name": "Drama"}],
        "runtime_minutes": None,
        "season_count": 1,
        "episode_count": 22,
        "vote_average": 7.4,
        "vote_count": 120,
        "popularity": 8.8,
        "link_to_details": "https://www.themoviedb.org/tv/22",
        "fetched_at": "2026-06-30T00:00:00Z",
        "source_version": "tmdbsummary.v2",
    }

    metadata = LibraryEntryMetadata.model_validate(payload)

    assert metadata.entry_type == "series"
    assert metadata.tmdb_id == 22
    assert metadata.name_translation_map == {"ja": "エイリアン・ネイション"}
    assert metadata.status == "Ended"
    assert metadata.genre_ids == (18,)
    assert metadata.season_count == 1
    assert metadata.episode_count == 22
    assert metadata.model_dump(mode="json", include=metadata.model_fields_set) == payload


def _snapshot_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "identity": "movie:55",
        "kind": "snapshot",
        "entry_type": "movie",
        "tmdb_id": 55,
        "schema_version": 2,
        "on_display": True,
        "date_saved": "2026-05-01T00:00:00Z",
        "watch_status": "watched",
        "is_date_tracking_enabled": False,
        "favorite": False,
        "notes": "",
        "using_custom_poster": False,
        "episode_progresses": [],
    }
    payload.update(overrides)
    return payload
