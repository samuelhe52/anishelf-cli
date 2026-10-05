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
    "is_rewatching",
    "rewatch_count",
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

# Typed columns hold only ids, scores, and counts, which never start with a formula trigger.
_TYPED_COLUMNS = frozenset(
    {"tmdb_id", "parent_series_id", "season_number", "score", "rewatch_count"}
)
# ASCII triggers plus their full-width forms (U+FF1D, U+FF0B, U+FF0D, U+FF20),
# which some spreadsheets also evaluate.
_FORMULA_TRIGGERS = (
    "=",
    "+",
    "-",
    "@",
    "\t",
    "\r",
    "\n",
    "\uff1d",
    "\uff0b",
    "\uff0d",
    "\uff20",
)

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


def write_export_file(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write an export atomically and readable only by the current user.

    Exports hold private library data, so they never pass through a
    world-readable state, and an interrupted write never replaces the target
    with a truncated file. A symlinked target is replaced by a regular file
    rather than written through.
    """
    fd, temp_name = tempfile.mkstemp(prefix=".ani-export.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def file_encoding_for_format(export_format: ExportFormat) -> str:
    # Excel only detects UTF-8 in a CSV file with a byte order mark; without one,
    # Japanese and Chinese titles open as mojibake. Streams stay BOM-free.
    return "utf-8-sig" if export_format is ExportFormat.CSV else "utf-8"


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
        "is_rewatching": entry.get("is_rewatching"),
        "rewatch_count": entry.get("rewatch_count"),
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
    return {
        key: _csv_value(value) if key in _TYPED_COLUMNS else _neutralize_formula(_csv_value(value))
        for key, value in row.items()
    }


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
    # Titles, genres, and homepages come from TMDb, which anyone can edit, so a cell
    # starting with a formula trigger could run as a formula in a spreadsheet.
    # Spreadsheets ignore leading spaces, so check past them too.
    if value.lstrip(" ").startswith(_FORMULA_TRIGGERS):
        return f"'{value}"
    return value


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
