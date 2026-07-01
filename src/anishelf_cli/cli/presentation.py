from __future__ import annotations

from anishelf_cli.core.output import (
    HumanSection,
    HumanTable,
    HumanTableColumn,
    emit_human_blocks,
)
from anishelf_cli.models.domain import (
    EpisodeProgress,
    LibraryEntryMetadata,
    LibraryEntryModel,
    LibraryEntryTombstone,
)
from anishelf_cli.models.output import (
    LibraryEntriesCacheResult,
    LibraryGetEnvelope,
    LibraryGetItemErrorResult,
    LibraryGetItemFound,
    TMDbSearchMatchResult,
    TMDbSearchOutputResult,
    TMDbSearchQueryResult,
    TMDbSearchResultsResult,
    TMDbSearchSummaryResult,
)
from anishelf_cli.models.tmdb import (
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


def render_library_get(envelope: LibraryGetEnvelope) -> None:
    blocks: list[HumanSection] = []

    blocks.append(
        HumanSection(
            "Library entries",
            (
                ("Requested", envelope.summary.requested),
                ("Found", envelope.summary.found),
                ("Errors", envelope.summary.errors),
            ),
        )
    )

    for item in envelope.items:
        blocks.append(_library_get_item_section(item))

    emit_human_blocks(blocks)


def _library_get_item_section(
    item: LibraryGetItemFound | LibraryGetItemErrorResult,
) -> HumanSection:
    identity = item.identity
    if isinstance(item, LibraryGetItemErrorResult):
        return HumanSection(
            identity,
            (
                ("Status", item.status),
                ("Error", item.error.code),
                ("Detail", item.error.message),
            ),
        )

    entry_model = item.entry
    title = entry_model.metadata_title

    if isinstance(entry_model, LibraryEntryTombstone):
        return HumanSection(
            title or identity,
            (
                ("Status", item.status),
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

    metadata = entry_model.metadata
    return HumanSection(
        title or identity,
        (
            ("Status", item.status),
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
            ("Poster", metadata.poster_path if metadata is not None else None),
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


def _human_library_row(entry: LibraryEntryModel) -> dict[str, object]:
    return {
        "title": entry.title,
        "identity": entry.identity,
        "type": entry.entry_type,
        "status": getattr(entry, "watch_status", None),
        "score": getattr(entry, "score", None),
        "favorite": getattr(entry, "favorite", None),
        "display": getattr(entry, "on_display", None),
        "saved": _compact_date(getattr(entry, "date_saved", None)),
    }


def _metadata_original_name(metadata: LibraryEntryMetadata | None) -> str | None:
    if metadata is None:
        return None
    return metadata.original_name


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
    entries: list[LibraryEntryModel],
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
    entries: list[LibraryEntryModel],
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


def render_library_export_result(
    entries: list[LibraryEntryModel],
    cache: LibraryEntriesCacheResult,
) -> None:
    emit_human_blocks(
        [
            HumanSection(
                "Library export",
                (
                    ("Entries", len(entries)),
                    ("Cache", cache.mode),
                    ("User", cache.user_record_name),
                ),
            )
        ]
    )


def tmdb_search_payload(
    query: TMDbTitleSearchQuery,
    result: TMDbTitleSearchResult,
) -> TMDbSearchOutputResult:
    movies = tuple(TMDbSearchMatchResult.from_match(match) for match in result.movies)
    series = tuple(TMDbSearchMatchResult.from_match(match) for match in result.series)
    return TMDbSearchOutputResult(
        query=TMDbSearchQueryResult(
            mode=query.mode,
            type=query.entry_type,
            title=query.title,
            year=query.year,
        ),
        summary=TMDbSearchSummaryResult(
            movies=len(movies),
            series=len(series),
            total=len(movies) + len(series),
        ),
        results=TMDbSearchResultsResult(
            movies=movies,
            series=series,
        ),
    )


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
