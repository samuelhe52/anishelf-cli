from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from anishelf_cli.cache.schema import (
    TMDB_LEGACY_SUMMARY_SOURCE_VERSION,
    TMDB_SUMMARY_SOURCE_VERSION,
    LibraryCacheError,
)
from anishelf_cli.core.coercion import nonempty_string_or_none as optional_string
from anishelf_cli.models.domain import LibraryEntryMetadata, LibraryEntryModel, TMDbSummaryIdentity
from anishelf_cli.models.identity import LibraryIdentityError, library_identity_from_fields


def metadata_row(row: sqlite3.Row) -> LibraryEntryMetadata:
    try:
        return LibraryEntryMetadata.model_validate_json(str(row["metadata_json"]))
    except ValidationError as exc:
        raise LibraryCacheError("Cached TMDb metadata summary is corrupt.") from exc
    except ValueError as exc:
        raise LibraryCacheError("Cached TMDb metadata summary is corrupt.") from exc


def upsert_metadata_summary(db: sqlite3.Connection, summary: LibraryEntryMetadata) -> None:
    stored_summary = summary.with_updates(
        fetched_at=summary.fetched_at or _now_iso(),
        source_version=TMDB_SUMMARY_SOURCE_VERSION,
    )
    db.execute(
        """
        INSERT INTO tmdb_metadata_summary (
            metadata_key,
            entry_type,
            tmdb_id,
            parent_series_id,
            season_number,
            language,
            name,
            name_translations_json,
            original_name,
            overview,
            overview_translations_json,
            poster_path,
            backdrop_path,
            logo_path,
            original_language_code,
            on_air_date,
            link_to_details,
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
            :name,
            :name_translations_json,
            :original_name,
            :overview,
            :overview_translations_json,
            :poster_path,
            :backdrop_path,
            :logo_path,
            :original_language_code,
            :on_air_date,
            :link_to_details,
            :fetched_at,
            :source_version,
            :metadata_json
        )
        ON CONFLICT(metadata_key, language) DO UPDATE SET
            language = excluded.language,
            name = excluded.name,
            name_translations_json = excluded.name_translations_json,
            original_name = excluded.original_name,
            overview = excluded.overview,
            overview_translations_json = excluded.overview_translations_json,
            poster_path = excluded.poster_path,
            backdrop_path = excluded.backdrop_path,
            logo_path = excluded.logo_path,
            original_language_code = excluded.original_language_code,
            on_air_date = excluded.on_air_date,
            link_to_details = excluded.link_to_details,
            fetched_at = excluded.fetched_at,
            source_version = excluded.source_version,
            metadata_json = excluded.metadata_json
        """,
        metadata_summary_params(stored_summary),
    )


def metadata_summary_params(summary: LibraryEntryMetadata) -> dict[str, Any]:
    payload = summary.storage_payload()
    return {
        "metadata_key": metadata_key_from_summary(summary),
        "entry_type": summary.entry_type,
        "tmdb_id": summary.tmdb_id,
        "parent_series_id": summary.parent_series_id,
        "season_number": summary.season_number,
        "language": payload["language"] or "",
        "name": payload["name"],
        "name_translations_json": _stable_json(payload["name_translations"]),
        "original_name": payload["original_name"],
        "overview": payload["overview"],
        "overview_translations_json": _stable_json(payload["overview_translations"]),
        "poster_path": payload["poster_path"],
        "backdrop_path": payload["backdrop_path"],
        "logo_path": payload["logo_path"],
        "original_language_code": payload["original_language_code"],
        "on_air_date": payload["on_air_date"],
        "link_to_details": payload["link_to_details"],
        "fetched_at": payload["fetched_at"],
        "source_version": payload["source_version"],
        "metadata_json": _stable_json(payload),
    }


def canonical_metadata_source_version(value: object) -> str | None:
    source_version = optional_string(value)
    if source_version is None:
        return None
    if source_version in {TMDB_SUMMARY_SOURCE_VERSION, "tmdb.http.summary.v2"}:
        return TMDB_SUMMARY_SOURCE_VERSION
    if source_version in {TMDB_LEGACY_SUMMARY_SOURCE_VERSION, "tmdb.http.summary.v1"}:
        return TMDB_LEGACY_SUMMARY_SOURCE_VERSION
    return source_version


def metadata_summary_state(
    db: sqlite3.Connection,
    target: TMDbSummaryIdentity,
) -> Literal["current", "missing", "outdated"]:
    row = db.execute(
        """
        SELECT source_version
        FROM tmdb_metadata_summary
        WHERE metadata_key = ? AND language = ''
        """,
        (metadata_key_from_target(target),),
    ).fetchone()
    if row is None:
        return "missing"
    source_version = canonical_metadata_source_version(row["source_version"])
    if source_version == TMDB_SUMMARY_SOURCE_VERSION:
        return "current"
    return "outdated"


def metadata_summary_exists(db: sqlite3.Connection, target: TMDbSummaryIdentity) -> bool:
    row = db.execute(
        """
        SELECT 1 FROM tmdb_metadata_summary
        WHERE metadata_key = ? AND language = ''
        """,
        (metadata_key_from_target(target),),
    ).fetchone()
    return row is not None


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


def placeholders(values: set[int] | list[str] | list[dict[str, Any]]) -> str:
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
