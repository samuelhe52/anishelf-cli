from __future__ import annotations

import json

from anishelf_cli.cli.root import app
from anishelf_cli.models import MetadataDepth
from tests.support import create_seeded_cache_store, live_record, metadata_summary, runner


def _with_genres(entry_type: str, tmdb_id: int, *genres: str):
    summary = metadata_summary(entry_type, tmdb_id, name=f"Title {tmdb_id}")
    return summary.model_copy(
        update={
            "genres": tuple(
                {"id": index, "name": name} for index, name in enumerate(genres, start=1)
            )
        }
    )


def _seed(monkeypatch, tmp_path) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("movie:1", "movie", 1, score=5, watch_status="watched"),
        live_record("series:2", "series", 2, score=3, watch_status="watching"),
        live_record("season:2:1:21", "season", 21, score=None, watch_status="planToWatch"),
        live_record("movie:3", "movie", 3, score=4, watch_status="dropped", on_display=False),
    )
    store.upsert_metadata_summaries(
        [
            _with_genres("movie", 1, "Animation", "Drama"),
            _with_genres("series", 2, "Animation", "Comedy"),
        ],
        depth=MetadataDepth.DETAILS,
    )


def test_library_stats_json_summarizes_visible_entries(tmp_path, monkeypatch) -> None:
    _seed(monkeypatch, tmp_path)

    result = runner.invoke(app, ["--json", "lib", "stats"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["entries"] == 3
    assert payload["summary"]["show_hidden"] is False
    assert payload["types"] == {"movie": 1, "series": 1, "season": 1}
    assert payload["watch_status"] == {
        "planToWatch": 1,
        "watching": 1,
        "watched": 1,
        "dropped": 0,
    }
    assert payload["scores"] == {
        "scored": 2,
        "unscored": 1,
        "average": 4.0,
        "distribution": {"1": 0, "2": 0, "3": 1, "4": 0, "5": 1},
    }
    # live_record finishes every entry on 2026-05-09.
    assert payload["finished_by_year"] == {"2026": 3}
    # series:2 and its season count as one title.
    assert payload["genres"] == [
        {"name": "Animation", "titles": 2},
        {"name": "Comedy", "titles": 1},
        {"name": "Drama", "titles": 1},
    ]
    assert payload["genre_coverage"] == {"titles": 2, "titles_with_genres": 2}


def test_library_stats_show_hidden_includes_hidden_entries(tmp_path, monkeypatch) -> None:
    _seed(monkeypatch, tmp_path)

    result = runner.invoke(app, ["--json", "lib", "stats", "--show-hidden"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["summary"]["entries"] == 4
    assert payload["watch_status"]["dropped"] == 1


def test_library_stats_omits_genres_without_details_metadata(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, live_record("movie:1", "movie", 1))

    machine = runner.invoke(app, ["--json", "lib", "stats"])
    human = runner.invoke(app, ["lib", "stats"])

    assert json.loads(machine.stdout)["genres"] is None
    assert human.exit_code == 0, human.output
    assert "Top genres" not in human.stdout
    assert "Library stats" in human.stdout


def test_library_stats_requires_initialized_cache(tmp_path, monkeypatch) -> None:
    from tests.support import isolate_paths

    isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["lib", "stats"])

    assert result.exit_code == 2
    assert "ani lib init" in result.stderr


def test_library_stats_ignores_scores_outside_anishelf_range(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("movie:1", "movie", 1, score=0),
        live_record("movie:2", "movie", 2, score=10),
        live_record("movie:3", "movie", 3, score=5),
    )

    payload = json.loads(runner.invoke(app, ["--json", "lib", "stats"]).stdout)

    assert payload["scores"]["scored"] == 1
    assert payload["scores"]["unscored"] == 2
    assert payload["scores"]["average"] == 5.0
    assert sum(payload["scores"]["distribution"].values()) == 1


def test_library_stats_seasons_use_cached_parent_series_genres(tmp_path, monkeypatch) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("season:7:1:71", "season", 71),
        live_record("season:7:2:72", "season", 72),
    )
    # The parent series is not saved, but its details metadata is cached.
    store.upsert_metadata_summaries(
        [_with_genres("series", 7, "Animation", "Mystery")],
        depth=MetadataDepth.DETAILS,
    )

    payload = json.loads(runner.invoke(app, ["--json", "lib", "stats"]).stdout)

    assert payload["genres"] == [
        {"name": "Animation", "titles": 1},
        {"name": "Mystery", "titles": 1},
    ]
    assert payload["genre_coverage"] == {"titles": 1, "titles_with_genres": 1}
