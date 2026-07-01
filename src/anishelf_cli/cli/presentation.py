from __future__ import annotations

from anishelf_cli.core.output import (
    HumanSection,
    HumanTable,
    HumanTableColumn,
    emit_human_blocks,
)
from anishelf_cli.library.entries import EpisodeProgress, LibraryEntry, LibraryEntryMetadata
from anishelf_cli.tmdb.client import (
    TMDbTitleSearchMatch,
    TMDbTitleSearchQuery,
    TMDbTitleSearchResult,
)

_TMDB_SEARCH_ALL = "all"

LIBRARY_LIST_DEFAULT_FIELDS = (
    "title",
    "identity",
    "type",
    "status",
    "score",
    "favorite",
    "display",
    "saved",
)
LIBRARY_SEARCH_DEFAULT_FIELDS = (
    "title",
    "identity",
    "type",
    "status",
    "score",
    "saved",
)
DISPLAY_FIELD_COLUMNS = {
    "title": HumanTableColumn("title", "Title"),
    "identity": HumanTableColumn("identity", "Identity"),
    "type": HumanTableColumn("type", "Type"),
    "status": HumanTableColumn("status", "Status"),
    "score": HumanTableColumn("score", "Score", "right"),
    "favorite": HumanTableColumn("favorite", "Fav"),
    "display": HumanTableColumn("display", "Display"),
    "saved": HumanTableColumn("saved", "Saved"),
}


def render_library_get(envelope: dict[str, object]) -> None:
    items = envelope.get("items")
    summary = envelope.get("summary")
    blocks: list[HumanSection] = []

    if isinstance(summary, dict):
        blocks.append(
            HumanSection(
                "Library entries",
                (
                    ("Requested", summary.get("requested")),
                    ("Found", summary.get("found")),
                    ("Errors", summary.get("errors")),
                ),
            )
        )

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            blocks.append(_library_get_item_section(item))

    emit_human_blocks(blocks)


def _library_get_item_section(item: dict[str, object]) -> HumanSection:
    identity = str(item.get("identity") or "unknown identity")
    status = str(item.get("status") or "unknown")
    if status != "found":
        error = item.get("error")
        code = ""
        message = ""
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or "")
        return HumanSection(
            identity,
            (
                ("Status", status),
                ("Error", code),
                ("Detail", message),
            ),
        )

    entry = item.get("entry")
    if not isinstance(entry, dict):
        return HumanSection(identity, (("Status", "decode-error"),))
    try:
        entry_model = LibraryEntry.from_payload(entry)
    except ValueError:
        return HumanSection(identity, (("Status", "decode-error"),))
    metadata = entry_model.metadata
    title = entry_model.metadata_title

    if entry_model.kind == "tombstone":
        return HumanSection(
            title or identity,
            (
                ("Status", status),
                ("Identity", identity),
                ("Kind", entry_model.kind),
                ("Type", entry_model.entry_type),
                ("TMDb ID", entry_model.tmdb_id),
                ("Parent series", entry_model.parent_series_id),
                ("Season", entry_model.season_number),
                ("Deleted", entry_model.deleted_at),
                ("Schema", entry_model.schema_version),
            ),
        )

    return HumanSection(
        title or identity,
        (
            ("Status", status),
            ("Identity", identity),
            ("Title", title),
            ("Original title", _metadata_original_name(metadata)),
            (
                "Overview",
                _truncate_text(
                    metadata.overview if metadata is not None else None,
                    limit=220,
                ),
            ),
            ("Kind", entry_model.kind),
            ("Type", entry_model.entry_type),
            ("TMDb ID", entry_model.tmdb_id),
            ("Parent series", entry_model.parent_series_id),
            ("Season", entry_model.season_number),
            ("Watch status", entry_model.watch_status),
            ("Score", entry_model.score),
            ("Favorite", entry_model.favorite),
            ("On display", entry_model.on_display),
            ("Date saved", _compact_date(entry_model.date_saved)),
            ("Date started", entry_model.date_started),
            ("Date finished", entry_model.date_finished),
            ("Date tracking", entry_model.is_date_tracking_enabled),
            ("Poster", _metadata_field(metadata, "poster_path")),
            ("Custom poster", entry_model.custom_poster_path),
            ("Episode progress", _format_episode_progresses(entry_model.episode_progresses)),
            ("Library updated", entry_model.library_updated_at),
            ("Tracking updated", entry_model.tracking_updated_at),
            ("Notes", _truncate_text(_optional_human_text(entry_model.notes), limit=160)),
            ("Schema", entry_model.schema_version),
        ),
    )


def _format_episode_progresses(value: tuple[EpisodeProgress, ...]) -> str | None:
    if not value:
        return None

    parts: list[str] = []
    for item in value:
        label = f"S{item.season_number}:E{item.watched_through_episode}"
        if item.updated_at:
            label += f" ({item.updated_at})"
        parts.append(label)
    return ", ".join(parts) if parts else None


def _optional_human_text(value: object) -> object:
    if value == "":
        return None
    return value


def _human_library_row(entry: LibraryEntry) -> dict[str, object]:
    return {
        "title": entry.title,
        "identity": entry.identity,
        "type": entry.entry_type,
        "status": entry.watch_status,
        "score": entry.score,
        "favorite": entry.favorite,
        "display": entry.on_display,
        "saved": _compact_date(entry.date_saved),
    }


def _metadata_original_name(metadata: LibraryEntryMetadata | None) -> str | None:
    return _metadata_field(metadata, "original_name")


def _metadata_field(metadata: LibraryEntryMetadata | None, key: str) -> str | None:
    if metadata is None:
        return None
    return metadata.string_field(key)


def _compact_date(value: object) -> object:
    if not isinstance(value, str):
        return value
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value


def _truncate_text(value: object, *, limit: int) -> object:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def render_library_list(
    entries: list[LibraryEntry],
    *,
    fields: tuple[str, ...],
) -> None:
    rows = [_human_library_row(entry) for entry in entries]
    emit_human_blocks(
        [
            HumanTable(
                "Library entries",
                _columns_for_display_fields(fields),
                rows,
                empty_message="No cached library entries.",
            )
        ]
    )


def render_library_search(
    title: str,
    entries: list[LibraryEntry],
    *,
    fields: tuple[str, ...],
) -> None:
    rows = [_human_library_row(entry) for entry in entries]
    emit_human_blocks(
        [
            HumanTable(
                f"Library search: {title}",
                _columns_for_display_fields(fields),
                rows,
                empty_message="No cached library entries matched the title search.",
            )
        ]
    )


def _columns_for_display_fields(fields: tuple[str, ...]) -> tuple[HumanTableColumn, ...]:
    return tuple(DISPLAY_FIELD_COLUMNS[field] for field in fields)


def render_library_export(payload: dict[str, object]) -> None:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("library export payload was not initialized correctly")
    cache = summary.get("cache")
    if not isinstance(cache, dict):
        raise RuntimeError("library export cache payload was not initialized correctly")

    emit_human_blocks(
        [
            HumanSection(
                "Library export",
                (
                    ("Entries", summary.get("entries")),
                    ("Cache", cache.get("mode")),
                    ("User", cache.get("user_record_name")),
                ),
            )
        ]
    )


def tmdb_search_payload(
    query: TMDbTitleSearchQuery,
    result: TMDbTitleSearchResult,
) -> dict[str, object]:
    movies = [_tmdb_search_match_payload(match) for match in result.movies]
    series = [_tmdb_search_match_payload(match) for match in result.series]
    query_payload: dict[str, object] = {
        "mode": query.mode,
        "type": query.entry_type,
    }
    if query.title is not None:
        query_payload["title"] = query.title
    if query.year is not None:
        query_payload["year"] = query.year
    return {
        "query": query_payload,
        "summary": {
            "movies": len(movies),
            "series": len(series),
            "total": len(movies) + len(series),
        },
        "results": {
            "movies": movies,
            "series": series,
        },
    }


def _tmdb_search_match_payload(match: TMDbTitleSearchMatch) -> dict[str, object]:
    return {
        "entry_type": match.entry_type,
        "tmdb_id": match.tmdb_id,
        "title": match.title,
        "original_title": match.original_title,
        "release_date": match.release_date,
        "original_language_code": match.original_language_code,
        "overview": match.overview,
        "poster_path": match.poster_path,
        "details_url": match.details_url,
    }


def render_tmdb_search(query: TMDbTitleSearchQuery, result: TMDbTitleSearchResult) -> None:
    summary_rows: list[tuple[str, object | None]] = [
        ("Mode", query.mode),
    ]
    if query.title is not None:
        summary_rows.append(("Query", query.title))
    if query.entry_type != _TMDB_SEARCH_ALL:
        summary_rows.append(("Type", query.entry_type))
    if query.year is not None:
        summary_rows.append(("Year", query.year))
    summary_rows.extend(
        [
            ("Movies", len(result.movies)),
            ("Series", len(result.series)),
            ("Total", len(result.movies) + len(result.series)),
        ]
    )
    blocks: list[HumanSection | HumanTable] = [
        HumanSection(
            "TMDb search",
            tuple(summary_rows),
        )
    ]

    if result.movies:
        blocks.append(_tmdb_search_table("Movies", result.movies))
    if result.series:
        blocks.append(_tmdb_search_table("Series", result.series))
    if not result.movies and not result.series:
        blocks.append(
            HumanTable(
                "Results",
                (
                    HumanTableColumn("tmdb_id", "TMDb ID", "right"),
                    HumanTableColumn("title", "Title"),
                    HumanTableColumn("release_date", "Date"),
                    HumanTableColumn("original_language_code", "Lang"),
                ),
                (),
                empty_message="No TMDb titles matched the query.",
            )
        )

    emit_human_blocks(blocks)


def _tmdb_search_table(
    title: str,
    matches: tuple[TMDbTitleSearchMatch, ...],
) -> HumanTable:
    return HumanTable(
        title,
        (
            HumanTableColumn("tmdb_id", "TMDb ID", "right"),
            HumanTableColumn("title", "Title"),
            HumanTableColumn("release_date", "Date"),
            HumanTableColumn("original_language_code", "Lang"),
        ),
        [_human_tmdb_search_row(match) for match in matches],
    )


def _human_tmdb_search_row(match: TMDbTitleSearchMatch) -> dict[str, object]:
    return {
        "tmdb_id": match.tmdb_id,
        "title": match.title or match.original_title or f"{match.entry_type}:{match.tmdb_id}",
        "release_date": _compact_date(match.release_date),
        "original_language_code": match.original_language_code,
    }


def normalized_tmdb_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = title.strip()
    return normalized or None
