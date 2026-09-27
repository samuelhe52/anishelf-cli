from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from anishelf_cli.cache.schema import (
    TMDB_METADATA_SOURCE_VERSION,
    LibraryCacheError,
)
from anishelf_cli.core.coercion import nonempty_string_or_none as optional_string
from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.domain import LibraryEntryMetadata, LibraryEntryModel, TMDbSummaryIdentity
from anishelf_cli.models.identity import LibraryIdentityError, library_identity_from_fields

_DEPTH_RANK = {
    MetadataDepth.SUMMARY: 1,
    MetadataDepth.DETAILS: 2,
    MetadataDepth.FULL: 3,
}


def metadata_row(row: sqlite3.Row) -> LibraryEntryMetadata:
    try:
        return LibraryEntryMetadata.model_validate_json(str(row["metadata_json"]))
    except ValidationError as exc:
        raise LibraryCacheError("Cached TMDb metadata summary is corrupt.") from exc
    except ValueError as exc:
        raise LibraryCacheError("Cached TMDb metadata summary is corrupt.") from exc


def upsert_metadata_item(
    db: sqlite3.Connection,
    summary: LibraryEntryMetadata,
    *,
    depth: MetadataDepth,
) -> None:
    stored_summary = summary.with_updates(
        fetched_at=summary.fetched_at or _now_iso(),
        source_version=TMDB_METADATA_SOURCE_VERSION,
    )
    db.execute(
        """
        INSERT INTO tmdb_metadata_items (
            metadata_key,
            entry_type,
            tmdb_id,
            parent_series_id,
            season_number,
            language,
            metadata_depth,
            name,
            name_translations_json,
            overview,
            overview_translations_json,
            runtime_minutes,
            number_of_seasons,
            number_of_episodes,
            poster_path,
            backdrop_path,
            logo_path,
            original_language_code,
            on_air_date,
            link_to_details,
            episode_run_time_minutes_json,
            genres_json,
            vote_average,
            vote_count,
            popularity,
            status,
            first_air_date,
            last_air_date,
            release_date,
            tagline,
            subtitle,
            season_summaries_json,
            episode_summaries_json,
            fetched_at,
            source_version,
            metadata_json
        )
        VALUES (
            :metadata_key,
            :entry_type,
            :tmdb_id,
            :parent_series_id,
            :season_number,
            :language,
            :metadata_depth,
            :name,
            :name_translations_json,
            :overview,
            :overview_translations_json,
            :runtime_minutes,
            :number_of_seasons,
            :number_of_episodes,
            :poster_path,
            :backdrop_path,
            :logo_path,
            :original_language_code,
            :on_air_date,
            :link_to_details,
            :episode_run_time_minutes_json,
            :genres_json,
            :vote_average,
            :vote_count,
            :popularity,
            :status,
            :first_air_date,
            :last_air_date,
            :release_date,
            :tagline,
            :subtitle,
            :season_summaries_json,
            :episode_summaries_json,
            :fetched_at,
            :source_version,
            :metadata_json
        )
        ON CONFLICT(metadata_key, language) DO UPDATE SET
            language = excluded.language,
            metadata_depth = excluded.metadata_depth,
            name = excluded.name,
            name_translations_json = excluded.name_translations_json,
            overview = excluded.overview,
            overview_translations_json = excluded.overview_translations_json,
            runtime_minutes = excluded.runtime_minutes,
            number_of_seasons = excluded.number_of_seasons,
            number_of_episodes = excluded.number_of_episodes,
            poster_path = excluded.poster_path,
            backdrop_path = excluded.backdrop_path,
            logo_path = excluded.logo_path,
            original_language_code = excluded.original_language_code,
            on_air_date = excluded.on_air_date,
            link_to_details = excluded.link_to_details,
            episode_run_time_minutes_json = excluded.episode_run_time_minutes_json,
            genres_json = excluded.genres_json,
            vote_average = excluded.vote_average,
            vote_count = excluded.vote_count,
            popularity = excluded.popularity,
            status = excluded.status,
            first_air_date = excluded.first_air_date,
            last_air_date = excluded.last_air_date,
            release_date = excluded.release_date,
            tagline = excluded.tagline,
            subtitle = excluded.subtitle,
            season_summaries_json = excluded.season_summaries_json,
            episode_summaries_json = excluded.episode_summaries_json,
            fetched_at = excluded.fetched_at,
            source_version = excluded.source_version,
            metadata_json = excluded.metadata_json
        WHERE :metadata_depth_rank >= CASE tmdb_metadata_items.metadata_depth
            WHEN 'summary' THEN :summary_depth_rank
            WHEN 'details' THEN :details_depth_rank
            WHEN 'full' THEN :full_depth_rank
            ELSE 0
        END
        """,
        metadata_summary_params(stored_summary, depth=depth),
    )


def upsert_metadata_summary(db: sqlite3.Connection, summary: LibraryEntryMetadata) -> None:
    upsert_metadata_item(db, summary, depth=MetadataDepth.SUMMARY)


def metadata_summary_params(
    summary: LibraryEntryMetadata,
    *,
    depth: MetadataDepth = MetadataDepth.SUMMARY,
) -> dict[str, Any]:
    payload = summary.storage_payload()
    return {
        "metadata_key": metadata_key_from_summary(summary),
        "entry_type": summary.entry_type,
        "tmdb_id": summary.tmdb_id,
        "parent_series_id": summary.parent_series_id,
        "season_number": summary.season_number,
        "metadata_depth": depth.value,
        "language": payload["language"],
        "name": payload["name"],
        "name_translations_json": _stable_json(payload["name_translations"]),
        "overview": payload["overview"],
        "overview_translations_json": _stable_json(payload["overview_translations"]),
        "runtime_minutes": payload["runtime_minutes"],
        "number_of_seasons": payload["number_of_seasons"],
        "number_of_episodes": payload["number_of_episodes"],
        "poster_path": payload["poster_path"],
        "backdrop_path": payload["backdrop_path"],
        "logo_path": payload["logo_path"],
        "original_language_code": payload["original_language_code"],
        "on_air_date": payload["on_air_date"],
        "link_to_details": payload["link_to_details"],
        "episode_run_time_minutes_json": _stable_json(payload["episode_run_time_minutes"]),
        "genres_json": _stable_json(payload["genres"]),
        "vote_average": payload["vote_average"],
        "vote_count": payload["vote_count"],
        "popularity": payload["popularity"],
        "status": payload["status"],
        "first_air_date": payload["first_air_date"],
        "last_air_date": payload["last_air_date"],
        "release_date": payload["release_date"],
        "tagline": payload["tagline"],
        "subtitle": payload["subtitle"],
        "season_summaries_json": _stable_json(payload["season_summaries"]),
        "episode_summaries_json": _stable_json(payload["episode_summaries"]),
        "fetched_at": payload["fetched_at"],
        "source_version": payload["source_version"],
        "metadata_depth_rank": _DEPTH_RANK[depth],
        "summary_depth_rank": _DEPTH_RANK[MetadataDepth.SUMMARY],
        "details_depth_rank": _DEPTH_RANK[MetadataDepth.DETAILS],
        "full_depth_rank": _DEPTH_RANK[MetadataDepth.FULL],
        "metadata_json": _stable_json(payload),
    }


def canonical_metadata_source_version(value: object) -> str | None:
    return optional_string(value)


def metadata_summary_state(
    db: sqlite3.Connection,
    target: TMDbSummaryIdentity,
    *,
    language: str,
    depth: MetadataDepth = MetadataDepth.SUMMARY,
) -> Literal["current", "missing", "outdated"]:
    row = db.execute(
        """
        SELECT source_version, metadata_depth
        FROM tmdb_metadata_items
        WHERE metadata_key = ? AND language = ?
        """,
        (metadata_key_from_target(target), language),
    ).fetchone()
    if row is None:
        return "missing"
    source_version = canonical_metadata_source_version(row["source_version"])
    row_depth = _metadata_depth_or_none(row["metadata_depth"])
    if (
        source_version == TMDB_METADATA_SOURCE_VERSION
        and row_depth is not None
        and _DEPTH_RANK[row_depth] >= _DEPTH_RANK[depth]
    ):
        return "current"
    return "outdated"


def metadata_depth_satisfies(value: object, requested_depth: MetadataDepth) -> bool:
    row_depth = _metadata_depth_or_none(value)
    return row_depth is not None and _DEPTH_RANK[row_depth] >= _DEPTH_RANK[requested_depth]


def metadata_summary_exists(
    db: sqlite3.Connection,
    target: TMDbSummaryIdentity,
    *,
    language: str,
) -> bool:
    row = db.execute(
        """
        SELECT 1 FROM tmdb_metadata_items
        WHERE metadata_key = ? AND language = ?
        """,
        (metadata_key_from_target(target), language),
    ).fetchone()
    return row is not None


def _metadata_depth_or_none(value: object) -> MetadataDepth | None:
    try:
        return MetadataDepth(str(value))
    except ValueError:
        return None


def dedupe_summary_targets(targets: list[TMDbSummaryIdentity]) -> list[TMDbSummaryIdentity]:
    seen: set[str] = set()
    deduped: list[TMDbSummaryIdentity] = []
    for target in targets:
        key = metadata_key_from_target(target)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def metadata_key_from_summary(summary: LibraryEntryMetadata) -> str:
    if summary.entry_type is None or summary.tmdb_id is None:
        raise LibraryCacheError("Metadata summary is missing identity fields.")
    return _metadata_key_from_fields(
        summary.entry_type,
        summary.tmdb_id,
        summary.parent_series_id,
        summary.season_number,
    )


def metadata_key_from_entry(entry: LibraryEntryModel) -> str:
    return _metadata_key_from_fields(
        entry.entry_type,
        entry.tmdb_id,
        entry.parent_series_id,
        entry.season_number,
    )


def metadata_key_from_target(target: TMDbSummaryIdentity) -> str:
    return _metadata_key_from_fields(
        target.entry_type,
        target.tmdb_id,
        target.parent_series_id,
        target.season_number,
    )


def metadata_target_from_entry(entry: LibraryEntryModel) -> TMDbSummaryIdentity | None:
    if entry.kind != "snapshot":
        return None
    return TMDbSummaryIdentity(
        entry_type=entry.entry_type,
        tmdb_id=entry.tmdb_id,
        parent_series_id=entry.parent_series_id,
        season_number=entry.season_number,
    )


def placeholders(
    values: set[int] | list[str] | list[dict[str, Any]] | Sequence[str],
) -> str:
    return ", ".join("?" for _ in values)


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _metadata_key_from_fields(
    entry_type: str,
    tmdb_id: int,
    parent_series_id: int | None,
    season_number: int | None,
) -> str:
    try:
        identity = library_identity_from_fields(
            entry_type,
            tmdb_id,
            parent_series_id,
            season_number,
        )
    except LibraryIdentityError as exc:
        raise LibraryCacheError(str(exc)) from exc
    if identity.raw is None:
        raise LibraryCacheError("Metadata identity is missing its canonical raw value.")
    return identity.raw
