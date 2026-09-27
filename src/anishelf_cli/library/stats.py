from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from anishelf_cli import config
from anishelf_cli.library.queries import LibraryQueryStore, visible_snapshots
from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.domain import LibraryEntryMetadata, LibraryEntryModel
from anishelf_cli.models.output import (
    LibraryEntriesCacheResult,
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


def build_library_stats_result(
    store: LibraryQueryStore,
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
        genres=top_genres(entries),
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
    """Count finished entries per calendar year, newest year first."""
    years = Counter(
        finished[:4]
        for entry in entries
        if (finished := _date_finished(entry)) is not None and finished[:4].isdigit()
    )
    return dict(sorted(years.items(), reverse=True))


def top_genres(entries: Sequence[LibraryEntryModel]) -> tuple[LibraryStatsGenreResult, ...] | None:
    """Most common genres, or None when no entry has genre metadata attached."""
    counts: Counter[str] = Counter()
    has_genre_metadata = False
    for entry in entries:
        metadata = getattr(entry, "metadata", None)
        if not isinstance(metadata, LibraryEntryMetadata) or not metadata.genres:
            continue
        has_genre_metadata = True
        counts.update({genre.name for genre in metadata.genres if genre.name})
    if not has_genre_metadata:
        return None
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        LibraryStatsGenreResult(name=name, entries=count)
        for name, count in ranked[:TOP_GENRE_LIMIT]
    )


def _watch_status(entry: LibraryEntryModel) -> str | None:
    return getattr(entry, "watch_status", None)


def _score(entry: LibraryEntryModel) -> int | None:
    score = getattr(entry, "score", None)
    return score if isinstance(score, int) else None


def _date_finished(entry: LibraryEntryModel) -> str | None:
    value = getattr(entry, "date_finished", None)
    return value if isinstance(value, str) else None
