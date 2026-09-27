from __future__ import annotations

from collections.abc import Mapping

from anishelf_cli.core.output import (
    HumanParagraph,
    HumanSection,
    HumanTable,
    HumanTableColumn,
    emit_human_blocks,
)
from anishelf_cli.models import HumanOutputStyle, MetadataDepth
from anishelf_cli.models.domain import (
    EpisodeProgress,
    LibraryEntryMetadata,
    LibraryEntryMetadataEpisode,
    LibraryEntryMetadataGenre,
    LibraryEntryMetadataSeason,
    LibraryEntryModel,
    LibraryEntryTombstone,
)
from anishelf_cli.models.output import (
    LibraryEntriesCacheResult,
    LibraryExportFileResult,
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
_TABLE_TITLE_MAX_WIDTH = 48
_TABLE_TITLE_MIN_WIDTH = 10
_TABLE_ID_MIN_WIDTH = 12

LIBRARY_LIST_DEFAULT_FIELDS = (
    "title",
    "id",
    "type",
    "status",
    "score",
    "favorite",
    "updated",
)
LIBRARY_SEARCH_DEFAULT_FIELDS = (
    "title",
    "id",
    "type",
    "status",
    "score",
    "saved",
)
DISPLAY_FIELD_COLUMNS = {
    "title": HumanTableColumn(
        "title",
        "Title",
        flexible=True,
        max_width=_TABLE_TITLE_MAX_WIDTH,
        min_width=_TABLE_TITLE_MIN_WIDTH,
    ),
    # Ids are the handle for `ani lib get`: the title shrinks to its floor first and
    # the id is truncated only as a last resort on very narrow terminals.
    "id": HumanTableColumn(
        "id",
        "ID",
        flexible=True,
        min_width=_TABLE_ID_MIN_WIDTH,
        shrink_priority=1,
    ),
    "type": HumanTableColumn("type", "Type"),
    "status": HumanTableColumn("status", "Status"),
    "score": HumanTableColumn("score", "Score", "right"),
    "favorite": HumanTableColumn("favorite", "Fav"),
    "display": HumanTableColumn("display", "Display"),
    "updated": HumanTableColumn("updated", "Updated"),
    "saved": HumanTableColumn("saved", "Saved"),
}


def render_library_get(
    envelope: LibraryGetEnvelope,
    *,
    display_titles: Mapping[str, str] | None = None,
    metadata_depth: MetadataDepth = MetadataDepth.SUMMARY,
) -> None:
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
        blocks.append(
            _library_get_item_section(
                item,
                display_titles=display_titles or {},
                metadata_depth=metadata_depth,
            )
        )

    emit_human_blocks(blocks)


def _library_get_item_section(
    item: LibraryGetItemFound | LibraryGetItemErrorResult,
    *,
    display_titles: Mapping[str, str],
    metadata_depth: MetadataDepth,
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
    title = _display_title(entry_model, display_titles)

    if isinstance(entry_model, LibraryEntryTombstone):
        return HumanSection(
            title or identity,
            (
                ("Status", item.status),
                ("ID", identity),
                ("Type", entry_model.entry_type),
                ("TMDb ID", entry_model.tmdb_id),
                ("Parent series", entry_model.parent_series_id),
                ("Season", entry_model.season_number),
                ("Deleted", _compact_date(entry_model.deleted_at)),
            ),
        )

    return HumanSection(
        title or identity,
        (
            ("Status", item.status),
            ("ID", identity),
            ("Title", title),
            ("Type", entry_model.entry_type),
            ("TMDb ID", entry_model.tmdb_id),
            ("Parent series", entry_model.parent_series_id),
            ("Season", entry_model.season_number),
            ("Watch status", entry_model.watch_status),
            ("Score", entry_model.score),
            ("Favorite", entry_model.favorite),
            ("On display", entry_model.on_display),
            ("Date saved", _compact_date(entry_model.date_saved)),
            ("Date started", _compact_date(entry_model.date_started)),
            ("Date finished", _compact_date(entry_model.date_finished)),
            ("Date tracking", entry_model.is_date_tracking_enabled),
            ("Episode progress", _format_episode_progresses(entry_model.episode_progresses)),
            (
                "Notes",
                _human_block_text(
                    _truncate_text(_optional_human_text(entry_model.notes), limit=160)
                ),
            ),
            *_metadata_rows_for_entry(
                entry_model,
                metadata_depth=metadata_depth,
                display_title=title,
                overview_limit=220,
            ),
        ),
    )


def _format_episode_progresses(value: tuple[EpisodeProgress, ...]) -> str | None:
    if not value:
        return None

    parts: list[str] = []
    for item in value:
        label = f"S{item.season_number}:E{item.watched_through_episode}"
        if item.updated_at:
            label += f" ({_compact_date(item.updated_at)})"
        parts.append(label)
    return ", ".join(parts) if parts else None


def _optional_human_text(value: object) -> object:
    if value == "":
        return None
    return value


def _human_block_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    return HumanParagraph(value)


def _human_library_row(
    entry: LibraryEntryModel,
    *,
    display_titles: Mapping[str, str],
) -> dict[str, object]:
    return {
        "title": _display_table_title(entry, display_titles),
        "id": entry.identity,
        "type": entry.entry_type,
        "status": _human_watch_status(getattr(entry, "watch_status", None)),
        "score": getattr(entry, "score", None),
        "favorite": getattr(entry, "favorite", None),
        "display": getattr(entry, "on_display", None),
        "updated": _compact_date(_library_updated_value(entry)),
        "saved": _compact_date(getattr(entry, "date_saved", None)),
    }


def _library_updated_value(entry: LibraryEntryModel) -> object:
    update_clocks = [
        value
        for value in (
            getattr(entry, "tracking_updated_at", None),
            getattr(entry, "library_updated_at", None),
        )
        if isinstance(value, str)
    ]
    if update_clocks:
        return max(update_clocks)
    return getattr(entry, "date_saved", None)


def _display_title(
    entry: LibraryEntryModel,
    display_titles: Mapping[str, str],
) -> str:
    return display_titles.get(entry.identity) or entry.title


def _display_table_title(
    entry: LibraryEntryModel,
    display_titles: Mapping[str, str],
) -> str:
    title = _display_title(entry, display_titles)
    if (
        entry.entry_type != "season"
        or entry.season_number is None
        or entry.identity not in display_titles
    ):
        return title
    return f"{title} (S{entry.season_number})"


def _season_metadata_title(
    entry_model: LibraryEntryModel,
    *,
    display_title: str,
) -> str | None:
    if entry_model.entry_type != "season":
        return None
    metadata_title = entry_model.metadata_title
    if metadata_title is None or metadata_title == display_title:
        return None
    return metadata_title


def _compact_date(value: object) -> object:
    if not isinstance(value, str):
        return value
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return f"{value[2:4]}/{value[5:7]}/{value[8:10]}"
    return value


def _human_watch_status(value: object) -> object:
    if value == "planToWatch":
        return "planned"
    return value


def _truncate_text(value: object, *, limit: int) -> object:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def render_library_list(
    entries: list[LibraryEntryModel],
    *,
    fields: tuple[str, ...],
    style: HumanOutputStyle = HumanOutputStyle.TABLE,
    display_titles: Mapping[str, str] | None = None,
    metadata_depth: MetadataDepth = MetadataDepth.SUMMARY,
    filtered: bool = False,
) -> None:
    resolved_display_titles = display_titles or {}
    rows = [_human_library_row(entry, display_titles=resolved_display_titles) for entry in entries]
    title = "Library entries"
    empty_message = (
        "No library entries matched the filters." if filtered else "No cached library entries."
    )
    if style is HumanOutputStyle.LIST:
        emit_human_blocks(
            _library_entries_as_sections(
                title,
                fields,
                entries,
                display_titles=resolved_display_titles,
                metadata_depth=metadata_depth,
                empty_message=empty_message,
            )
        )
        return

    emit_human_blocks(
        [
            HumanTable(
                title,
                _columns_for_display_fields(fields),
                rows,
                empty_message=empty_message,
            )
        ]
    )


def render_library_search(
    query: str,
    entries: list[LibraryEntryModel],
    *,
    fields: tuple[str, ...],
    style: HumanOutputStyle = HumanOutputStyle.TABLE,
    display_titles: Mapping[str, str] | None = None,
    metadata_depth: MetadataDepth = MetadataDepth.SUMMARY,
) -> None:
    resolved_display_titles = display_titles or {}
    rows = [_human_library_row(entry, display_titles=resolved_display_titles) for entry in entries]
    block_title = f"Library search: {query}"
    empty_message = "No cached library entries matched the search query."
    if style is HumanOutputStyle.LIST:
        emit_human_blocks(
            _library_entries_as_sections(
                block_title,
                fields,
                entries,
                display_titles=resolved_display_titles,
                metadata_depth=metadata_depth,
                empty_message=empty_message,
            )
        )
        return

    emit_human_blocks(
        [
            HumanTable(
                block_title,
                _columns_for_display_fields(fields),
                rows,
                empty_message=empty_message,
            )
        ]
    )


def _columns_for_display_fields(fields: tuple[str, ...]) -> tuple[HumanTableColumn, ...]:
    return tuple(DISPLAY_FIELD_COLUMNS[field] for field in fields)


def _library_entries_as_sections(
    title: str,
    fields: tuple[str, ...],
    entries: list[LibraryEntryModel],
    *,
    display_titles: Mapping[str, str],
    metadata_depth: MetadataDepth,
    empty_message: str,
) -> list[HumanSection]:
    if not entries:
        return [HumanSection(title, (("Entries", 0), ("Result", empty_message)))]

    sections = [HumanSection(title, (("Entries", len(entries)),))]
    for entry in entries:
        row = _human_library_row(entry, display_titles=display_titles)
        section_title = _library_list_item_title(row, fields)
        sections.append(
            HumanSection(
                section_title,
                (
                    *(
                        (DISPLAY_FIELD_COLUMNS[field].label, row.get(field))
                        for field in fields
                        if field != "title"
                    ),
                    *_metadata_rows_for_entry(
                        entry,
                        metadata_depth=metadata_depth,
                        display_title=_display_title(entry, display_titles),
                        overview_limit=180,
                    ),
                ),
            )
        )
    return sections


def _library_list_item_title(row: Mapping[str, object], fields: tuple[str, ...]) -> str:
    if "title" in fields:
        title = row.get("title")
        if title is not None:
            return str(title)
    identity = row.get("id")
    return str(identity) if identity is not None else "Library entry"


def _metadata_rows_for_entry(
    entry: LibraryEntryModel,
    *,
    metadata_depth: MetadataDepth,
    display_title: str,
    overview_limit: int,
) -> tuple[tuple[str, object], ...]:
    if metadata_depth is MetadataDepth.NONE or isinstance(entry, LibraryEntryTombstone):
        return ()
    metadata = entry.metadata
    if metadata is None:
        return ()

    rows: list[tuple[str, object]] = []
    if season_title := _season_metadata_title(entry, display_title=display_title):
        rows.append(("Season title", season_title))
    rows.extend(_summary_metadata_rows(metadata, overview_limit=overview_limit))
    if metadata_depth in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        rows.extend(_details_metadata_rows(metadata))
    if metadata_depth is MetadataDepth.FULL:
        rows.extend(_full_metadata_rows(metadata))
    return tuple(rows)


def _summary_metadata_rows(
    metadata: LibraryEntryMetadata,
    *,
    overview_limit: int,
) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    if metadata.overview is not None:
        rows.append(
            (
                "Overview",
                _human_block_text(_truncate_text(metadata.overview, limit=overview_limit)),
            )
        )
    rows.extend(
        _present_rows(
            (
                ("On air", _compact_date(metadata.on_air_date)),
                ("Runtime", _format_minutes(metadata.runtime_minutes)),
                ("Seasons", metadata.number_of_seasons),
                ("Episodes", metadata.number_of_episodes),
            )
        )
    )
    return rows


def _details_metadata_rows(metadata: LibraryEntryMetadata) -> list[tuple[str, object]]:
    return _present_rows(
        (
            ("Link", metadata.link_to_details),
            ("TMDb status", metadata.status),
            ("Poster path", metadata.poster_path),
            ("Backdrop path", metadata.backdrop_path),
            ("Logo path", metadata.logo_path),
            ("Genres", _format_genres(metadata.genres)),
            ("Rating", _format_rating(metadata.vote_average, metadata.vote_count)),
            ("Popularity", metadata.popularity),
            ("Original language", metadata.original_language_code),
            ("First air date", _compact_date(metadata.first_air_date)),
            ("Last air date", _compact_date(metadata.last_air_date)),
            ("Release date", _compact_date(metadata.release_date)),
            ("Episode runtime", _format_minutes_list(metadata.episode_run_time_minutes)),
            ("Tagline", metadata.tagline),
            ("Subtitle", metadata.subtitle),
        )
    )


def _full_metadata_rows(metadata: LibraryEntryMetadata) -> list[tuple[str, object]]:
    return _present_rows(
        (
            ("Name translations", _format_translations(metadata.name_translations)),
            ("Overview translations", _format_translations(metadata.overview_translations)),
            ("Season summaries", _format_season_summaries(metadata.season_summaries)),
            ("Episode summaries", _format_episode_summaries(metadata.episode_summaries)),
        )
    )


def _present_rows(rows: tuple[tuple[str, object | None], ...]) -> list[tuple[str, object]]:
    return [(label, value) for label, value in rows if _has_human_value(value)]


def _has_human_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, tuple | list):
        return bool(value)
    return True


def _format_minutes(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{value} min"


def _format_minutes_list(values: tuple[int, ...]) -> str | None:
    if not values:
        return None
    return ", ".join(f"{value} min" for value in values)


def _format_genres(genres: tuple[LibraryEntryMetadataGenre, ...]) -> str | None:
    names = [genre.name for genre in genres if genre.name]
    return ", ".join(names) if names else None


def _format_rating(vote_average: float | None, vote_count: int | None) -> str | None:
    if vote_average is None and vote_count is None:
        return None
    if vote_average is None:
        return f"{vote_count} votes"
    if vote_count is None:
        return f"{vote_average:g}"
    return f"{vote_average:g} ({vote_count} votes)"


def _format_translations(translations: tuple[tuple[str, str], ...]) -> str | None:
    if not translations:
        return None
    return ", ".join(language for language, _ in translations)


def _format_season_summaries(summaries: tuple[LibraryEntryMetadataSeason, ...]) -> str | None:
    if not summaries:
        return None
    total_episodes = sum(summary.episode_count or 0 for summary in summaries)
    if total_episodes:
        return f"{len(summaries)} seasons, {total_episodes} episodes"
    return f"{len(summaries)} seasons"


def _format_episode_summaries(summaries: tuple[LibraryEntryMetadataEpisode, ...]) -> str | None:
    if not summaries:
        return None
    return f"{len(summaries)} episodes"


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


def render_library_export_file_result(result: LibraryExportFileResult) -> None:
    emit_human_blocks(
        [
            HumanSection(
                "Library export",
                (
                    ("Entries", result.entries),
                    ("Format", result.format),
                    ("Output", result.path),
                    ("Cache", result.cache.mode),
                    ("User", result.cache.user_record_name),
                ),
            )
        ]
    )


TMDbLibraryIds = Mapping[tuple[str, int], tuple[str, ...]]


def tmdb_search_payload(
    query: TMDbTitleSearchQuery,
    result: TMDbTitleSearchResult,
    *,
    limit: int | None = None,
    library_ids: TMDbLibraryIds | None = None,
) -> TMDbSearchOutputResult:
    def match_result(match: TMDbTitleSearchMatch) -> TMDbSearchMatchResult:
        return TMDbSearchMatchResult.from_match(
            match,
            library_ids=_match_library_ids(match, library_ids),
        )

    movies = tuple(match_result(match) for match in result.movies)
    series = tuple(match_result(match) for match in result.series)
    in_library = (
        sum(1 for match in (*movies, *series) if match.library_ids)
        if library_ids is not None
        else None
    )
    return TMDbSearchOutputResult(
        query=TMDbSearchQueryResult(
            mode=query.mode,
            type=query.entry_type,
            language=query.language,
            title=query.title,
            year=query.year,
            limit=limit,
        ),
        summary=TMDbSearchSummaryResult(
            movies=len(movies),
            series=len(series),
            total=len(movies) + len(series),
            in_library=in_library,
        ),
        results=TMDbSearchResultsResult(
            movies=movies,
            series=series,
        ),
    )


def _match_library_ids(
    match: TMDbTitleSearchMatch,
    library_ids: TMDbLibraryIds | None,
) -> tuple[str, ...] | None:
    if library_ids is None:
        return None
    return library_ids.get((match.entry_type, match.tmdb_id), ())


def render_tmdb_search(
    query: TMDbTitleSearchQuery,
    result: TMDbTitleSearchResult,
    *,
    library_ids: TMDbLibraryIds | None = None,
) -> None:
    summary_rows: list[tuple[str, object | None]] = [
        ("Mode", query.mode),
    ]
    if query.title is not None:
        summary_rows.append(("Query", query.title))
    if query.entry_type != _TMDB_SEARCH_ALL:
        summary_rows.append(("Type", query.entry_type))
    if query.year is not None:
        summary_rows.append(("Year", query.year))
    summary_rows.append(("Language", query.language))
    summary_rows.extend(
        [
            ("Movies", len(result.movies)),
            ("Series", len(result.series)),
            ("Total", len(result.movies) + len(result.series)),
        ]
    )
    if library_ids is not None:
        summary_rows.append(
            (
                "In library",
                sum(
                    1
                    for match in (*result.movies, *result.series)
                    if _match_library_ids(match, library_ids)
                ),
            )
        )
    blocks: list[HumanSection | HumanTable] = [
        HumanSection(
            "TMDb search",
            tuple(summary_rows),
        )
    ]

    if result.movies:
        blocks.append(_tmdb_search_table("Movies", result.movies, library_ids=library_ids))
    if result.series:
        blocks.append(_tmdb_search_table("Series", result.series, library_ids=library_ids))
    if not result.movies and not result.series:
        blocks.append(
            HumanTable(
                "Results",
                (
                    HumanTableColumn("tmdb_id", "TMDb ID", "right"),
                    HumanTableColumn(
                        "title",
                        "Title",
                        flexible=True,
                        max_width=_TABLE_TITLE_MAX_WIDTH,
                        min_width=_TABLE_TITLE_MIN_WIDTH,
                    ),
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
    *,
    library_ids: TMDbLibraryIds | None = None,
) -> HumanTable:
    columns = [
        HumanTableColumn("tmdb_id", "TMDb ID", "right"),
        HumanTableColumn(
            "title",
            "Title",
            flexible=True,
            max_width=_TABLE_TITLE_MAX_WIDTH,
            min_width=_TABLE_TITLE_MIN_WIDTH,
        ),
        HumanTableColumn("release_date", "Date"),
        HumanTableColumn("original_language_code", "Lang"),
    ]
    if library_ids is not None:
        columns.append(HumanTableColumn("library", "Library"))
    return HumanTable(
        title,
        tuple(columns),
        [_human_tmdb_search_row(match, library_ids=library_ids) for match in matches],
    )


def _human_tmdb_search_row(
    match: TMDbTitleSearchMatch,
    *,
    library_ids: TMDbLibraryIds | None = None,
) -> dict[str, object]:
    return {
        "tmdb_id": match.tmdb_id,
        "title": match.title or match.original_title or f"{match.entry_type}:{match.tmdb_id}",
        "release_date": _compact_date(match.release_date),
        "original_language_code": match.original_language_code,
        "library": _library_marker(_match_library_ids(match, library_ids) or ()),
    }


def _library_marker(ids: tuple[str, ...]) -> str:
    """Summarize saved ids: `yes` for the title itself, `S<n>` for saved seasons."""
    if not ids:
        return "no"
    seasons: list[str] = []
    saved_directly = False
    for identity in ids:
        parts = identity.split(":")
        if parts[0] == "season" and len(parts) == 4:
            seasons.append(f"S{parts[2]}")
        else:
            saved_directly = True
    labels = (["yes"] if saved_directly else []) + sorted(
        dict.fromkeys(seasons),
        key=lambda label: int(label[1:]) if label[1:].isdigit() else 0,
    )
    return ", ".join(labels)


def normalized_tmdb_title(title: str | None) -> str | None:
    if title is None:
        return None
    normalized = title.strip()
    return normalized or None
