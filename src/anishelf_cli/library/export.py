from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from anishelf_cli.models import ExportFormat
from anishelf_cli.models.domain import TMDbSummaryIdentity
from anishelf_cli.models.transport.tmdb import details_link

CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "entry_type",
    "tmdb_id",
    "parent_series_id",
    "season_number",
    "watch_status",
    "score",
    "favorite",
    "on_display",
    "date_saved",
    "date_started",
    "date_finished",
    "episode_progress",
    "notes",
    "on_air_date",
    "genres",
    "tmdb_url",
    "homepage",
)

_FREE_TEXT_COLUMNS = ("title", "notes", "genres")
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

_FORMATS_BY_SUFFIX = {
    ".json": ExportFormat.JSON,
    ".jsonl": ExportFormat.JSONL,
    ".ndjson": ExportFormat.JSONL,
    ".csv": ExportFormat.CSV,
}


def export_format_for_path(path: Path) -> ExportFormat | None:
    return _FORMATS_BY_SUFFIX.get(path.suffix.lower())


def render_export(
    payload: Mapping[str, Any],
    export_format: ExportFormat,
    *,
    display_titles: Mapping[str, str],
) -> str:
    """Render a `lib export` JSON payload in the requested file format."""
    entries: Sequence[Mapping[str, Any]] = payload["entries"]
    if export_format is ExportFormat.JSON:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if export_format is ExportFormat.JSONL:
        return "".join(
            json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries
        )
    return _render_csv(entries, display_titles)


def write_export_file(path: Path, content: str) -> None:
    """Write an export atomically and readable only by the current user.

    Exports hold private library data, so they never pass through a
    world-readable state, and a failed write never leaves a truncated file.
    """
    directory = path.parent if str(path.parent) else Path(".")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _render_csv(
    entries: Sequence[Mapping[str, Any]],
    display_titles: Mapping[str, str],
) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for entry in entries:
        writer.writerow(_csv_row(entry, display_titles))
    return buffer.getvalue()


def _csv_row(entry: Mapping[str, Any], display_titles: Mapping[str, str]) -> dict[str, str]:
    metadata: Mapping[str, Any] = entry.get("metadata") or {}
    identity = str(entry.get("id", ""))
    row = {
        "id": identity,
        "title": display_titles.get(identity) or metadata.get("name") or "",
        "entry_type": entry.get("entry_type"),
        "tmdb_id": entry.get("tmdb_id"),
        "parent_series_id": entry.get("parent_series_id"),
        "season_number": entry.get("season_number"),
        "watch_status": entry.get("watch_status"),
        "score": entry.get("score"),
        "favorite": entry.get("favorite"),
        "on_display": entry.get("on_display"),
        "date_saved": entry.get("date_saved"),
        "date_started": entry.get("date_started"),
        "date_finished": entry.get("date_finished"),
        "episode_progress": "; ".join(
            f"S{item['season_number']}:E{item['watched_through_episode']}"
            for item in entry.get("episode_progresses") or ()
        ),
        "notes": entry.get("notes"),
        "on_air_date": metadata.get("on_air_date"),
        "genres": "; ".join(
            str(genre["name"]) for genre in metadata.get("genres") or () if genre.get("name")
        ),
        "tmdb_url": _tmdb_url(entry),
        "homepage": metadata.get("link_to_details"),
    }
    cells = {key: _csv_value(value) for key, value in row.items()}
    for key in _FREE_TEXT_COLUMNS:
        cells[key] = _neutralize_formula(cells[key])
    return cells


def _tmdb_url(entry: Mapping[str, Any]) -> str | None:
    entry_type = entry.get("entry_type")
    if entry_type not in {"movie", "series", "season"} or entry.get("tmdb_id") is None:
        return None
    return details_link(
        TMDbSummaryIdentity(
            entry_type=entry_type,
            tmdb_id=entry["tmdb_id"],
            parent_series_id=entry.get("parent_series_id"),
            season_number=entry.get("season_number"),
        )
    )


def _neutralize_formula(value: str) -> str:
    # Titles and genres come from TMDb, so a cell starting with a formula trigger
    # could run as a formula when the CSV is opened in a spreadsheet.
    if value.startswith(_FORMULA_TRIGGERS):
        return f"'{value}"
    return value


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
