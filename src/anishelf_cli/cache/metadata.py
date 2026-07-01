from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal

from anishelf_cli.cache.schema import (
    TMDB_LEGACY_SUMMARY_SOURCE_VERSION,
    TMDB_SUMMARY_SOURCE_VERSION,
    LibraryCacheError,
)
from anishelf_cli.core.coercion import nonempty_string_or_none as optional_string
from anishelf_cli.tmdb.client import TMDbSummaryIdentity


def metadata_row(row: sqlite3.Row) -> dict[str, Any]:
    value = json.loads(str(row["metadata_json"]))
    if not isinstance(value, dict):
        raise LibraryCacheError("Cached TMDb metadata summary is corrupt.")
    return normalized_metadata_summary(value)


def upsert_metadata_summary(db: sqlite3.Connection, summary: dict[str, Any]) -> None:
    metadata = normalized_metadata_summary(summary, for_storage=True)
    metadata.setdefault("fetched_at", _now_iso())
    metadata.setdefault("source_version", TMDB_SUMMARY_SOURCE_VERSION)
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
        metadata_summary_params(metadata),
    )


def metadata_summary_params(summary: dict[str, Any]) -> dict[str, Any]:
    metadata_json = _metadata_json(summary)
    return {
        "metadata_key": metadata_key_from_entry(summary),
        "entry_type": summary["entry_type"],
        "tmdb_id": summary["tmdb_id"],
        "parent_series_id": summary.get("parent_series_id"),
        "season_number": summary.get("season_number"),
        "language": summary.get("language") or "",
        "name": summary.get("name"),
        "name_translations_json": json.dumps(
            summary.get("name_translations") or {},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "original_name": summary.get("original_name"),
        "overview": summary.get("overview"),
        "overview_translations_json": json.dumps(
            summary.get("overview_translations") or {},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "poster_path": summary.get("poster_path"),
        "backdrop_path": summary.get("backdrop_path"),
        "logo_path": summary.get("logo_path"),
        "original_language_code": summary.get("original_language_code"),
        "on_air_date": summary.get("on_air_date"),
        "link_to_details": summary.get("link_to_details"),
        "fetched_at": summary["fetched_at"],
        "source_version": summary["source_version"],
        "metadata_json": metadata_json,
    }


def normalized_metadata_summary(
    summary: dict[str, Any],
    *,
    for_storage: bool = False,
) -> dict[str, Any]:
    source_version = (
        TMDB_SUMMARY_SOURCE_VERSION
        if for_storage
        else canonical_metadata_source_version(summary.get("source_version"))
    )
    if source_version is None:
        source_version = TMDB_SUMMARY_SOURCE_VERSION
    return {
        "entry_type": str(summary["entry_type"]),
        "tmdb_id": int(summary["tmdb_id"]),
        "parent_series_id": metadata_optional_int(summary.get("parent_series_id")),
        "season_number": metadata_optional_int(summary.get("season_number")),
        "language": optional_string(summary.get("language")),
        "name": optional_string(summary.get("name")),
        "name_translations": metadata_translation_map(summary.get("name_translations")),
        "original_name": optional_string(summary.get("original_name")),
        "overview": optional_string(summary.get("overview")),
        "overview_translations": metadata_translation_map(summary.get("overview_translations")),
        "poster_path": optional_string(summary.get("poster_path")),
        "backdrop_path": optional_string(summary.get("backdrop_path")),
        "logo_path": optional_string(summary.get("logo_path")),
        "original_language_code": optional_string(summary.get("original_language_code")),
        "on_air_date": optional_string(summary.get("on_air_date")),
        "status": optional_string(summary.get("status")),
        "genres": metadata_genres(summary.get("genres")),
        "runtime_minutes": metadata_optional_int(summary.get("runtime_minutes")),
        "season_count": metadata_optional_int(summary.get("season_count")),
        "episode_count": metadata_optional_int(summary.get("episode_count")),
        "vote_average": metadata_optional_float(summary.get("vote_average")),
        "vote_count": metadata_optional_int(summary.get("vote_count")),
        "popularity": metadata_optional_float(summary.get("popularity")),
        "link_to_details": optional_string(summary.get("link_to_details")),
        "fetched_at": optional_string(summary.get("fetched_at")) or _now_iso(),
        "source_version": source_version,
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


def metadata_lookup_params(entries: list[dict[str, Any]]) -> list[Any]:
    return [metadata_key_from_entry(entry) for entry in entries]


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


def metadata_key_from_summary(summary: dict[str, Any]) -> str:
    return metadata_key_from_entry(summary)


def metadata_key_from_entry(entry: dict[str, Any]) -> str:
    entry_type = str(entry["entry_type"])
    tmdb_id = int(entry["tmdb_id"])
    if entry_type == "season":
        parent_series_id = entry.get("parent_series_id")
        season_number = entry.get("season_number")
        if parent_series_id is None or season_number is None:
            raise LibraryCacheError("Season metadata is missing parent series context.")
        return f"season:{int(parent_series_id)}:{int(season_number)}:{tmdb_id}"
    return f"{entry_type}:{tmdb_id}"


def metadata_key_from_target(target: TMDbSummaryIdentity) -> str:
    if target.entry_type == "season":
        if target.parent_series_id is None or target.season_number is None:
            raise LibraryCacheError("Season metadata is missing parent series context.")
        return f"season:{target.parent_series_id}:{target.season_number}:{target.tmdb_id}"
    return f"{target.entry_type}:{target.tmdb_id}"


def metadata_target_from_entry(entry: dict[str, Any]) -> TMDbSummaryIdentity | None:
    entry_type = optional_string(entry.get("entry_type"))
    tmdb_id = metadata_optional_int(entry.get("tmdb_id"))
    if entry_type is None or tmdb_id is None:
        return None
    return TMDbSummaryIdentity(
        entry_type=entry_type,
        tmdb_id=tmdb_id,
        parent_series_id=metadata_optional_int(entry.get("parent_series_id")),
        season_number=metadata_optional_int(entry.get("season_number")),
    )


def placeholders(values: set[int] | list[str] | list[dict[str, Any]]) -> str:
    return ", ".join("?" for _ in values)
def metadata_optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value)
    return None


def _metadata_json(summary: dict[str, Any]) -> str:
    metadata = {
        "entry_type": summary["entry_type"],
        "tmdb_id": summary["tmdb_id"],
        "parent_series_id": summary.get("parent_series_id"),
        "season_number": summary.get("season_number"),
        "language": summary.get("language") or None,
        "name": summary.get("name"),
        "name_translations": summary.get("name_translations") or {},
        "original_name": summary.get("original_name"),
        "overview": summary.get("overview"),
        "overview_translations": summary.get("overview_translations") or {},
        "poster_path": summary.get("poster_path"),
        "backdrop_path": summary.get("backdrop_path"),
        "logo_path": summary.get("logo_path"),
        "original_language_code": summary.get("original_language_code"),
        "on_air_date": summary.get("on_air_date"),
        "status": summary.get("status"),
        "genres": summary.get("genres") or [],
        "runtime_minutes": summary.get("runtime_minutes"),
        "season_count": summary.get("season_count"),
        "episode_count": summary.get("episode_count"),
        "vote_average": summary.get("vote_average"),
        "vote_count": summary.get("vote_count"),
        "popularity": summary.get("popularity"),
        "link_to_details": summary.get("link_to_details"),
        "fetched_at": summary["fetched_at"],
        "source_version": summary["source_version"],
    }
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


def metadata_translation_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    translations: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        text = optional_string(item)
        if text is not None:
            translations[key] = text
    return translations


def metadata_genres(value: object) -> list[dict[str, int | str]]:
    if not isinstance(value, list):
        return []

    genres: list[dict[str, int | str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        genre_id = metadata_optional_int(item.get("id"))
        name = optional_string(item.get("name"))
        if genre_id is None or name is None:
            continue
        genres.append({"id": genre_id, "name": name})
    return genres


def metadata_optional_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        return float(value)
    return None


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
