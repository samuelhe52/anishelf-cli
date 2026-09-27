from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol

from anishelf_cli import config
from anishelf_cli.library.queries import LibraryQueryStore, visible_snapshots
from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.domain import LibraryEntryMetadata, LibraryEntryModel
from anishelf_cli.models.output import (
    LibraryEntriesCacheResult,
    LibraryStatsGenreCoverageResult,
    LibraryStatsGenreResult,
    LibraryStatsResult,
    LibraryStatsScoresResult,
    LibraryStatsSummaryResult,
)

ENTRY_TYPES = ("movie", "series", "season")
WATCH_STATUSES = ("planToWatch", "watching", "watched", "dropped")
# AniShelf's `AnimeEntry.validScoreRange` is 1...5.
SCORE_VALUES = (1, 2, 3, 4, 5)
TOP_GENRE_LIMIT = 10


class LibraryStatsStore(LibraryQueryStore, Protocol):
    def series_genre_names(
        self,
        series_ids: set[int],
        *,
        language: str,
    ) -> dict[int, tuple[str, ...]]: ...


def build_library_stats_result(
    store: LibraryStatsStore,
    *,
    cache: LibraryEntriesCacheResult,
    show_hidden: bool,
    metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
) -> LibraryStatsResult:
    entries = store.list_entry_models(include_tombstones=False)
    if not show_hidden:
        entries = visible_snapshots(entries)
    # Genres live at details depth; entries hydrated shallower simply contribute none.
    entries = store.attach_metadata_summary_models(
        entries,
        language=metadata_language,
        depth=MetadataDepth.DETAILS,
    )
    titles = genre_titles(
        entries,
        store.series_genre_names(
            {
                entry.parent_series_id
                for entry in entries
                if entry.entry_type == "season" and entry.parent_series_id is not None
            },
            language=metadata_language,
        ),
    )
    return LibraryStatsResult(
        summary=LibraryStatsSummaryResult(
            entries=len(entries),
            favorites=sum(1 for entry in entries if getattr(entry, "favorite", False)),
            show_hidden=show_hidden,
            cache=cache,
        ),
        types=count_by_type(entries),
        watch_status=count_by_watch_status(entries),
        scores=score_summary(entries),
        finished_by_year=finished_by_year(entries),
        genres=top_genres(titles),
        genre_coverage=LibraryStatsGenreCoverageResult(
            titles=len(titles),
            titles_with_genres=sum(1 for genres in titles.values() if genres),
        ),
    )


def count_by_type(entries: Sequence[LibraryEntryModel]) -> dict[str, int]:
    counts = Counter(entry.entry_type for entry in entries)
    return {entry_type: counts.get(entry_type, 0) for entry_type in ENTRY_TYPES}


def count_by_watch_status(entries: Sequence[LibraryEntryModel]) -> dict[str, int]:
    counts = Counter(_watch_status(entry) for entry in entries)
    return {status: counts.get(status, 0) for status in WATCH_STATUSES}


def score_summary(entries: Sequence[LibraryEntryModel]) -> LibraryStatsScoresResult:
    scores = [score for entry in entries if (score := _score(entry)) is not None]
    distribution = Counter(scores)
    return LibraryStatsScoresResult(
        scored=len(scores),
        unscored=len(entries) - len(scores),
        average=round(sum(scores) / len(scores), 2) if scores else None,
        distribution={str(value): distribution.get(value, 0) for value in SCORE_VALUES},
    )


def finished_by_year(entries: Sequence[LibraryEntryModel]) -> dict[str, int]:
    """Count entries with a finish date per (UTC) calendar year."""
    years = Counter(
        finished[:4]
        for entry in entries
        if (finished := _date_finished(entry)) is not None and finished[:4].isdigit()
    )
    return dict(sorted(years.items(), reverse=True))


def genre_titles(
    entries: Sequence[LibraryEntryModel],
    series_genres: Mapping[int, tuple[str, ...]],
) -> dict[tuple[str, int], tuple[str, ...]]:
    """Group entries into titles and resolve each title's genre names.

    TMDb seasons carry no genres, so a series and its saved seasons form one title
    whose genres come from the series metadata; a movie is its own title.
    """
    titles: dict[tuple[str, int], tuple[str, ...]] = {}
    for entry in entries:
        if entry.entry_type == "season" and entry.parent_series_id is not None:
            key = ("series", entry.parent_series_id)
            genres = series_genres.get(entry.parent_series_id, ())
        else:
            key = (entry.entry_type, entry.tmdb_id)
            genres = _entry_genres(entry)
        if genres or key not in titles:
            titles[key] = titles.get(key) or genres
    return titles


def top_genres(
    titles: Mapping[tuple[str, int], tuple[str, ...]],
) -> tuple[LibraryStatsGenreResult, ...] | None:
    """Most common genres across titles, or None when no title has genres."""
    counts: Counter[str] = Counter()
    for genres in titles.values():
        counts.update(set(genres))
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        LibraryStatsGenreResult(name=name, titles=count) for name, count in ranked[:TOP_GENRE_LIMIT]
    )


def _entry_genres(entry: LibraryEntryModel) -> tuple[str, ...]:
    metadata = getattr(entry, "metadata", None)
    if not isinstance(metadata, LibraryEntryMetadata):
        return ()
    return tuple(genre.name for genre in metadata.genres if genre.name)


def _watch_status(entry: LibraryEntryModel) -> str | None:
    return getattr(entry, "watch_status", None)


def _score(entry: LibraryEntryModel) -> int | None:
    # AniShelf treats scores outside its valid range as unscored.
    score = getattr(entry, "score", None)
    return score if isinstance(score, int) and score in SCORE_VALUES else None


def _date_finished(entry: LibraryEntryModel) -> str | None:
    value = getattr(entry, "date_finished", None)
    return value if isinstance(value, str) else None
