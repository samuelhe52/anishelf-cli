from __future__ import annotations

import pytest

from anishelf_cli.library.entries import EpisodeProgress, LibraryEntry


def test_library_entry_rejects_unknown_kind() -> None:
    payload = _snapshot_payload(kind="snapshott")

    with pytest.raises(ValueError, match="Unsupported library entry kind: snapshott."):
        LibraryEntry.from_payload(payload)


def test_library_entry_to_payload_rejects_unknown_kind() -> None:
    entry = LibraryEntry(
        identity="movie:55",
        kind="snapshott",
        entry_type="movie",
        tmdb_id=55,
    )

    with pytest.raises(ValueError, match="Unsupported library entry kind: snapshott."):
        entry.to_payload()


def test_library_entry_normalizes_legacy_deleted_kind_to_tombstone() -> None:
    entry = LibraryEntry.from_payload(
        {
            "identity": "movie:55",
            "kind": "deleted",
            "entry_type": "movie",
            "tmdb_id": 55,
            "deleted_at": "2026-07-01T00:00:00Z",
        }
    )

    assert entry.kind == "tombstone"
    assert entry.to_payload()["kind"] == "tombstone"


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

    with pytest.raises(ValueError, match=message):
        LibraryEntry.from_payload(payload)


def test_library_entry_rejects_invalid_metadata_shape() -> None:
    payload = _snapshot_payload(metadata=["bad"])

    with pytest.raises(ValueError, match="Library entry metadata value is invalid."):
        LibraryEntry.from_payload(payload)


def test_library_entry_rejects_invalid_episode_progresses_shape() -> None:
    payload = _snapshot_payload(episode_progresses="bad")

    with pytest.raises(
        ValueError,
        match="Library entry episode_progresses value is invalid.",
    ):
        LibraryEntry.from_payload(payload)


def test_library_entry_rejects_invalid_episode_progress_item_shape() -> None:
    payload = _snapshot_payload(episode_progresses=["bad"])

    with pytest.raises(
        ValueError,
        match="Library entry episode_progresses item is invalid.",
    ):
        LibraryEntry.from_payload(payload)


def test_episode_progress_rejects_invalid_updated_at() -> None:
    with pytest.raises(ValueError, match="Library entry updated_at value is invalid."):
        EpisodeProgress.from_payload(
            {
                "season_number": 1,
                "watched_through_episode": 3,
                "updated_at": 123,
            }
        )


def _snapshot_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "identity": "movie:55",
        "kind": "snapshot",
        "entry_type": "movie",
        "tmdb_id": 55,
        "episode_progresses": [],
    }
    payload.update(overrides)
    return payload
