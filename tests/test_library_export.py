from __future__ import annotations

import csv
import io
import json
import stat
from pathlib import Path

from anishelf_cli.cli.root import app
from anishelf_cli.library.export import (
    CSV_COLUMNS,
    export_format_for_path,
    render_export,
    write_export_file,
)
from anishelf_cli.models import ExportFormat
from tests.support import (
    create_seeded_cache_store,
    live_record,
    metadata_summary,
    runner,
)


def _payload(*entries: dict[str, object]) -> dict[str, object]:
    return {"entries": list(entries), "summary": {"entries": len(entries)}}


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "season:22:2:33",
        "entry_type": "season",
        "tmdb_id": 33,
        "parent_series_id": 22,
        "season_number": 2,
        "watch_status": "watching",
        "score": None,
        "favorite": True,
        "on_display": False,
        "date_saved": "2026-05-01T00:00:00Z",
        "date_started": None,
        "date_finished": None,
        "episode_progresses": [
            {"season_number": 2, "watched_through_episode": 3, "updated_at": None}
        ],
        "notes": "line one\nline two",
    }
    entry.update(overrides)
    return entry


def test_csv_export_flattens_entries_with_stable_columns() -> None:
    content = render_export(
        _payload(
            _entry(
                metadata={
                    "name": "Season 2",
                    "on_air_date": "2024-01-05",
                    "genres": [{"id": 16, "name": "Animation"}, {"id": 18, "name": "Drama"}],
                    "link_to_details": "https://example.test/show",
                }
            )
        ),
        ExportFormat.CSV,
        display_titles={"season:22:2:33": "Cowboy Bebop"},
    )

    rows = list(csv.DictReader(io.StringIO(content)))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert rows[0] == {
        "id": "season:22:2:33",
        "title": "Cowboy Bebop",
        "entry_type": "season",
        "tmdb_id": "33",
        "parent_series_id": "22",
        "season_number": "2",
        "watch_status": "watching",
        "score": "",
        "favorite": "true",
        "on_display": "false",
        "date_saved": "2026-05-01T00:00:00Z",
        "date_started": "",
        "date_finished": "",
        "episode_progress": "S2:E3",
        "notes": "line one\nline two",
        "on_air_date": "2024-01-05",
        "genres": "Animation; Drama",
        "tmdb_url": "https://www.themoviedb.org/tv/22/season/2",
        "homepage": "https://example.test/show",
    }


def test_csv_export_neutralizes_spreadsheet_formulas_in_free_text() -> None:
    content = render_export(
        _payload(_entry(notes='=HYPERLINK("https://evil.test")', metadata=None)),
        ExportFormat.CSV,
        display_titles={"season:22:2:33": "+SUM(A1)"},
    )

    row = next(csv.DictReader(io.StringIO(content)))
    assert row["title"] == "'+SUM(A1)"
    assert row["notes"] == '\'=HYPERLINK("https://evil.test")'
    assert row["id"] == "season:22:2:33"


def test_jsonl_export_writes_one_entry_per_line() -> None:
    content = render_export(
        _payload(_entry(id="movie:1"), _entry(id="movie:2")),
        ExportFormat.JSONL,
        display_titles={},
    )

    lines = content.splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["movie:1", "movie:2"]


def test_export_format_is_inferred_from_known_suffixes() -> None:
    assert export_format_for_path(Path("lib.CSV")) is ExportFormat.CSV
    assert export_format_for_path(Path("lib.ndjson")) is ExportFormat.JSONL
    assert export_format_for_path(Path("lib.json")) is ExportFormat.JSON
    assert export_format_for_path(Path("lib.txt")) is None


def test_write_export_file_is_private_and_replaces_existing_files(tmp_path) -> None:
    path = tmp_path / "library.json"
    path.write_text("old")

    write_export_file(path, "new\n")

    assert path.read_text() == "new\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [child.name for child in tmp_path.iterdir()] == ["library.json"]


def _seed(monkeypatch, tmp_path) -> None:
    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("movie:55", "movie", 55),
        live_record("series:22", "series", 22),
    )
    store.upsert_metadata_summary(metadata_summary("movie", 55, name="Alien"))
    store.upsert_metadata_summary(metadata_summary("series", 22, name="Cowboy Bebop"))


def test_library_export_streams_requested_format_to_stdout(tmp_path, monkeypatch) -> None:
    _seed(monkeypatch, tmp_path)

    jsonl = runner.invoke(app, ["lib", "export", "--format", "jsonl"])
    csv_result = runner.invoke(app, ["lib", "export", "--format", "csv"])

    assert jsonl.exit_code == 0, jsonl.output
    assert sorted(json.loads(line)["id"] for line in jsonl.stdout.splitlines()) == [
        "movie:55",
        "series:22",
    ]
    assert csv_result.exit_code == 0, csv_result.output
    titles = {row["id"]: row["title"] for row in csv.DictReader(io.StringIO(csv_result.stdout))}
    assert titles == {"movie:55": "Alien", "series:22": "Cowboy Bebop"}


def test_library_export_writes_output_file_and_reports_it(tmp_path, monkeypatch) -> None:
    _seed(monkeypatch, tmp_path)
    human_path = tmp_path / "library.csv"
    json_path = tmp_path / "library.json"

    human = runner.invoke(app, ["lib", "export", "-o", str(human_path)])
    machine = runner.invoke(app, ["--json", "lib", "export", "--output", str(json_path)])

    assert human.exit_code == 0, human.output
    assert "Format   csv" in human.stdout
    assert human_path.read_text().startswith("id,title,")
    assert machine.exit_code == 0, machine.output
    result = json.loads(machine.stdout)
    assert result["format"] == "json"
    assert result["entries"] == 2
    assert result["path"] == str(json_path)
    assert len(json.loads(json_path.read_text())["entries"]) == 2


def test_library_export_rejects_json_flag_with_streamed_non_json_format(
    tmp_path,
    monkeypatch,
) -> None:
    _seed(monkeypatch, tmp_path)

    result = runner.invoke(app, ["--json", "lib", "export", "--format", "csv"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--json prints the JSON envelope" in result.stderr


def test_library_export_without_format_keeps_human_summary(tmp_path, monkeypatch) -> None:
    _seed(monkeypatch, tmp_path)

    result = runner.invoke(app, ["lib", "export"])

    assert result.exit_code == 0, result.output
    assert "Library export" in result.stdout
    assert "Entries  2" in result.stdout
