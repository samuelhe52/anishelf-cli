from __future__ import annotations

import pytest

from anishelf_cli.library.entries import EpisodeProgress, validate_library_entry
from anishelf_cli.library.metadata import LibraryEntryMetadata
from anishelf_cli.models.domain import CurrentUser
from anishelf_cli.tmdb.client import TMDbSummaryIdentity


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


def test_library_entry_rejects_unknown_watch_status_string() -> None:
    payload = _snapshot_payload(watch_status="queued")

    with pytest.raises(ValueError, match="watch_status value is invalid"):
        validate_library_entry(payload)


def test_library_entry_rejects_non_season_context_fields() -> None:
    payload = _snapshot_payload(parent_series_id=22, season_number=1)

    with pytest.raises(ValueError, match="movie identity cannot define"):
        validate_library_entry(payload)


def test_library_entry_rejects_season_without_full_context() -> None:
    payload = _snapshot_payload(
        identity="season:22:1:33",
        entry_type="season",
        tmdb_id=33,
        parent_series_id=22,
    )

    with pytest.raises(ValueError, match="Season identity requires"):
        validate_library_entry(payload)


def test_tmdb_summary_identity_rejects_non_season_context_fields() -> None:
    with pytest.raises(ValueError, match="movie identity cannot define"):
        TMDbSummaryIdentity(entry_type="movie", tmdb_id=55, parent_series_id=22, season_number=1)


def test_tmdb_summary_identity_requires_full_season_context() -> None:
    with pytest.raises(ValueError, match="Season identity requires"):
        TMDbSummaryIdentity(entry_type="season", tmdb_id=33, parent_series_id=22)


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
    assert metadata.model_dump(mode="json") == {
        "name": "Alien",
        "original_name": "Alien",
        "genres": [{"id": 878, "name": "Science Fiction"}],
        "vote_average": 8.2,
    }


def test_library_entry_metadata_with_updates_preserves_partial_payload_shape() -> None:
    metadata = LibraryEntryMetadata.model_validate({"name": "Alien"})

    updated = metadata.with_updates(fetched_at="2026-07-02T00:00:00Z")

    assert updated.model_dump(mode="json") == {
        "name": "Alien",
        "fetched_at": "2026-07-02T00:00:00Z",
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
    assert metadata.model_dump(mode="json") == payload


def test_library_entry_metadata_rejects_incomplete_identity_fields() -> None:
    with pytest.raises(ValueError, match="identity fields are incomplete"):
        LibraryEntryMetadata.model_validate({"tmdb_id": 55})


def test_library_entry_metadata_rejects_non_season_context_fields() -> None:
    with pytest.raises(ValueError, match="movie identity cannot define"):
        LibraryEntryMetadata.model_validate(
            {
                "entry_type": "movie",
                "tmdb_id": 55,
                "parent_series_id": 22,
                "season_number": 1,
            }
        )


def test_library_entry_metadata_requires_full_season_context() -> None:
    with pytest.raises(ValueError, match="Season identity requires"):
        LibraryEntryMetadata.model_validate(
            {
                "entry_type": "season",
                "tmdb_id": 33,
                "parent_series_id": 22,
            }
        )


def test_library_entry_metadata_storage_payload_preserves_full_normalized_shape() -> None:
    metadata = LibraryEntryMetadata.model_validate(
        {
            "entry_type": "movie",
            "tmdb_id": 55,
            "genres": [{"id": 878, "name": "Science Fiction"}],
        }
    )

    assert metadata.storage_payload() == {
        "entry_type": "movie",
        "tmdb_id": 55,
        "parent_series_id": None,
        "season_number": None,
        "language": None,
        "name": None,
        "name_translations": {},
        "original_name": None,
        "overview": None,
        "overview_translations": {},
        "poster_path": None,
        "backdrop_path": None,
        "logo_path": None,
        "original_language_code": None,
        "on_air_date": None,
        "status": None,
        "genres": [{"id": 878, "name": "Science Fiction"}],
        "runtime_minutes": None,
        "season_count": None,
        "episode_count": None,
        "vote_average": None,
        "vote_count": None,
        "popularity": None,
        "link_to_details": None,
        "fetched_at": None,
        "source_version": None,
    }


def test_library_entry_metadata_with_updates_preserves_full_payload_shape() -> None:
    metadata = LibraryEntryMetadata.model_validate(
        {
            "entry_type": "movie",
            "tmdb_id": 55,
            "parent_series_id": None,
            "season_number": None,
            "language": None,
            "name": "Alien",
            "name_translations": {},
            "original_name": None,
            "overview": None,
            "overview_translations": {},
            "poster_path": None,
            "backdrop_path": None,
            "logo_path": None,
            "original_language_code": None,
            "on_air_date": None,
            "status": None,
            "genres": [],
            "runtime_minutes": None,
            "season_count": None,
            "episode_count": None,
            "vote_average": None,
            "vote_count": None,
            "popularity": None,
            "link_to_details": None,
            "fetched_at": None,
            "source_version": "tmdbsummary.v2",
        }
    )

    updated = metadata.with_updates(fetched_at="2026-07-02T00:00:00Z")

    assert set(updated.model_dump(mode="json")) == set(metadata.model_dump(mode="json"))
    assert updated.model_dump(mode="json")["fetched_at"] == "2026-07-02T00:00:00Z"


def test_snapshot_library_entry_json_omits_missing_metadata() -> None:
    entry = validate_library_entry(_snapshot_payload())

    payload = entry.model_dump(mode="json")

    assert payload["identity"] == "movie:55"
    assert "metadata" not in payload


def test_snapshot_with_metadata_revalidates_to_typed_model() -> None:
    entry = validate_library_entry(_snapshot_payload())

    updated = entry.with_metadata(
        LibraryEntryMetadata.model_validate(
            {
                "entry_type": "movie",
                "tmdb_id": 55,
                "name": "Alien",
            }
        )
    )

    assert isinstance(updated.metadata, LibraryEntryMetadata)
    assert updated.metadata.name == "Alien"


def test_snapshot_with_metadata_rejects_mismatched_identity() -> None:
    entry = validate_library_entry(_snapshot_payload())
    metadata = LibraryEntryMetadata.model_validate(
        {
            "entry_type": "series",
            "tmdb_id": 22,
            "name": "Alien Nation",
        }
    )

    with pytest.raises(ValueError, match="metadata identity does not match entry"):
        entry.with_metadata(metadata)


def test_snapshot_with_metadata_rejects_raw_dict_payload() -> None:
    entry = validate_library_entry(_snapshot_payload())

    with pytest.raises(TypeError, match="LibraryEntryMetadata instance"):
        entry.with_metadata({"entry_type": "movie", "tmdb_id": 55, "name": "Alien"})  # type: ignore[arg-type]


def test_current_user_json_payload_uses_authenticated_envelope() -> None:
    current_user = CurrentUser(
        user_record_name="_user",
        first_name="Ripley",
        last_name="Scott",
        email="ripley@example.com",
    )

    assert current_user.model_dump(mode="json") == {
        "status": "authenticated",
        "user": {
            "user_record_name": "_user",
            "first_name": "Ripley",
            "last_name": "Scott",
            "email": "ripley@example.com",
        },
    }


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
