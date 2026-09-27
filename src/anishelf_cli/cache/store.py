from __future__ import annotations

import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from anishelf_cli import config
from anishelf_cli.cache import metadata, records, schema
from anishelf_cli.cache.scope import LibraryCacheScope, scope_from_existing_database
from anishelf_cli.cloudkit.executor import ANI_SHELF_LIBRARY_ZONE_NAME
from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.domain import LibraryEntryMetadata, LibraryEntryModel, TMDbSummaryIdentity
from anishelf_cli.models.output import CacheMetadataStatusResult, RemovedCacheFilesResult
from anishelf_cli.models.transport.cloudkit import ZoneChangesPage

LibraryCacheError = schema.LibraryCacheError
LibraryCacheNotAvailableError = schema.LibraryCacheNotAvailableError


_IDENTITY_LOOKUP_CHUNK_SIZE = 500


@dataclass(frozen=True, slots=True)
class LibraryCacheStore:
    scope: LibraryCacheScope
    path: Path
    lock_path: Path

    @classmethod
    def for_scope(cls, scope: LibraryCacheScope) -> LibraryCacheStore:
        cache_key = scope.cache_key()
        cache_root = config.cache_dir() / "library"
        lock_root = config.data_dir() / "locks"
        cache_root.mkdir(parents=True, exist_ok=True)
        lock_root.mkdir(parents=True, exist_ok=True)
        return cls(
            scope=scope,
            path=cache_root / f"{cache_key}.sqlite3",
            lock_path=lock_root / f"library-cache.{cache_key}.lock",
        )

    @classmethod
    def library_cache_root(cls) -> Path:
        return config.cache_dir() / "library"

    @classmethod
    def library_lock_root(cls) -> Path:
        return config.data_dir() / "locks"

    @classmethod
    def find_default_scope(cls) -> LibraryCacheStore:
        candidates: list[LibraryCacheStore] = []
        for scope in cls.existing_scopes():
            if (
                scope.container == config.DEFAULT_CONTAINER
                and scope.environment == config.DEFAULT_ENVIRONMENT
                and scope.database == config.DEFAULT_DATABASE
                and scope.zone == ANI_SHELF_LIBRARY_ZONE_NAME
            ):
                candidates.append(cls.for_scope(scope))

        if not candidates:
            raise LibraryCacheNotAvailableError(
                "No local library cache is available. Run `ani lib init` first."
            )
        if len(candidates) > 1:
            raise LibraryCacheNotAvailableError(
                "Multiple user-scoped library caches are available. Run `ani lib init` "
                "to select the authenticated user."
            )
        return candidates[0]

    @classmethod
    def existing_scopes(cls) -> list[LibraryCacheScope]:
        cache_root = cls.library_cache_root()
        scopes: list[LibraryCacheScope] = []
        for path in sorted(cache_root.glob("*.sqlite3")) if cache_root.exists() else []:
            scope = scope_from_existing_database(path)
            if scope is not None:
                scopes.append(scope)
        return scopes

    @classmethod
    def remove_all_local_caches(cls) -> RemovedCacheFilesResult:
        cache_root = cls.library_cache_root()
        lock_root = cls.library_lock_root()
        return RemovedCacheFilesResult(
            cache_files=schema.remove_matching_files(cache_root, "*.sqlite3"),
            lock_files=schema.remove_matching_files(lock_root, "library-cache.*.lock"),
        )

    @contextmanager
    def locked(self) -> Generator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.lock_path)):
            yield

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            schema.initialize_schema(db)
            from anishelf_cli.cache.scope import write_scope_metadata

            write_scope_metadata(db, self.scope)

    def read_sync_token(self) -> str | None:
        with self._connect_initialized() as db:
            return schema.read_meta(db, schema.ZONE_SYNC_TOKEN_META_KEY)

    def has_entries(self, *, read_only: bool = False) -> bool:
        connection = self._connect_read_only() if read_only else self._connect_initialized()
        with connection as db:
            row = db.execute("SELECT 1 FROM library_entries LIMIT 1").fetchone()
            return row is not None

    def begin_rebuild(self) -> None:
        with self._connect_initialized() as db:
            db.execute("BEGIN")
            db.execute("DROP TABLE IF EXISTS library_entries_stage")
            db.execute(schema.entries_table_sql("library_entries_stage"))
            schema.create_entries_indexes(
                db,
                "library_entries_stage",
                "idx_library_entries_stage",
            )
            db.execute(
                "DELETE FROM cache_meta WHERE key = ?",
                (schema.REBUILD_SYNC_TOKEN_META_KEY,),
            )
            db.commit()

    def apply_page(self, page: ZoneChangesPage, *, staging: bool) -> None:
        table = "library_entries_stage" if staging else "library_entries"
        token_key = (
            schema.REBUILD_SYNC_TOKEN_META_KEY if staging else schema.ZONE_SYNC_TOKEN_META_KEY
        )
        with self._connect_initialized() as db:
            db.execute("BEGIN")
            try:
                for record in page.records:
                    records.apply_record(db, table, record)
                schema.write_meta(db, token_key, page.sync_token)
                db.commit()
            except Exception:
                db.rollback()
                raise

    def apply_page_and_collect_new_summary_targets(
        self,
        page: ZoneChangesPage,
        *,
        staging: bool,
        metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        metadata_depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[TMDbSummaryIdentity]:
        table = "library_entries_stage" if staging else "library_entries"
        token_key = (
            schema.REBUILD_SYNC_TOKEN_META_KEY if staging else schema.ZONE_SYNC_TOKEN_META_KEY
        )
        new_targets: list[TMDbSummaryIdentity] = []
        with self._connect_initialized() as db:
            db.execute("BEGIN")
            try:
                for record in page.records:
                    target = records.summary_target_from_record(db, table, record)
                    records.apply_record(db, table, record)
                    if (
                        target is not None
                        and metadata.metadata_summary_state(
                            db,
                            target,
                            language=metadata_language,
                            depth=metadata_depth,
                        )
                        != "current"
                    ):
                        new_targets.append(target)
                schema.write_meta(db, token_key, page.sync_token)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return metadata.dedupe_summary_targets(new_targets)

    def finish_rebuild(self) -> None:
        with self._connect_initialized() as db:
            sync_token = schema.read_meta(db, schema.REBUILD_SYNC_TOKEN_META_KEY)
            if not sync_token:
                raise LibraryCacheError("Cannot finish library cache rebuild without a sync token.")
            db.execute("BEGIN")
            db.execute("DELETE FROM library_entries")
            db.execute("INSERT INTO library_entries SELECT * FROM library_entries_stage")
            schema.write_meta(db, schema.ZONE_SYNC_TOKEN_META_KEY, sync_token)
            db.execute(
                "DELETE FROM cache_meta WHERE key = ?",
                (schema.REBUILD_SYNC_TOKEN_META_KEY,),
            )
            db.execute("DROP TABLE library_entries_stage")
            db.commit()

    def list_entry_models(self, *, include_tombstones: bool = False) -> list[LibraryEntryModel]:
        where = "" if include_tombstones else "WHERE kind = 'snapshot'"
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM library_entries
                {where}
                ORDER BY date_saved DESC NULLS LAST, identity ASC
                """
            ).fetchall()
        return self._entry_models_from_rows(rows)

    def list_entry_models_filtered(
        self,
        *,
        include_tombstones: bool = False,
        watch_statuses: Sequence[str] | None = None,
        entry_types: Sequence[str] | None = None,
        hidden: bool | None = None,
        favorite: bool | None = None,
        on_display: bool | None = None,
        sort: str = "updated",
        reverse: bool = False,
        limit: int | None = None,
    ) -> list[LibraryEntryModel]:
        where_parts: list[str] = []
        params: list[Any] = []
        if not include_tombstones:
            where_parts.append("kind = 'snapshot'")
        if watch_statuses:
            where_parts.append(f"watch_status IN ({metadata.placeholders(watch_statuses)})")
            params.extend(watch_statuses)
        if entry_types:
            where_parts.append(f"entry_type IN ({metadata.placeholders(entry_types)})")
            params.extend(entry_types)
        if hidden is not None:
            where_parts.append("kind = 'snapshot'")
            where_parts.append("on_display = ?")
            params.append(0 if hidden else 1)
        if favorite is not None:
            where_parts.append("kind = 'snapshot'")
            where_parts.append("favorite = ?")
            params.append(1 if favorite else 0)
        if on_display is not None:
            where_parts.append("kind = 'snapshot'")
            where_parts.append("on_display = ?")
            params.append(1 if on_display else 0)

        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        order_by = schema.list_order_by(sort, reverse=reverse)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(limit)

        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM library_entries
                {where}
                {order_by}
                {limit_clause}
                """,
                params,
            ).fetchall()
        return self._entry_models_from_rows(rows)

    def get_entry_models_by_identity(self, identities: list[str]) -> dict[str, LibraryEntryModel]:
        unique_identities = list(dict.fromkeys(identities))
        if not unique_identities:
            return {}
        rows: list[sqlite3.Row] = []
        with self._connect_initialized() as db:
            # Chunk the IN clause to stay under SQLite's bound-variable limit for
            # large batches read from stdin.
            for start in range(0, len(unique_identities), _IDENTITY_LOOKUP_CHUNK_SIZE):
                chunk = unique_identities[start : start + _IDENTITY_LOOKUP_CHUNK_SIZE]
                rows.extend(
                    db.execute(
                        f"""
                        SELECT decoded_json
                        FROM library_entries
                        WHERE kind = 'snapshot'
                        AND identity IN ({metadata.placeholders(chunk)})
                        """,
                        chunk,
                    ).fetchall()
                )
        entries = self._entry_models_from_rows(rows)
        return {entry.identity: entry for entry in entries}

    def search_cached_entry_models(
        self,
        *,
        movie_ids: set[int],
        series_ids: set[int],
        read_only: bool = False,
    ) -> list[LibraryEntryModel]:
        query_parts: list[str] = []
        params: list[int] = []
        if movie_ids:
            query_parts.append(
                "SELECT decoded_json, date_saved, identity "
                "FROM library_entries "
                "WHERE kind = 'snapshot' "
                f"AND entry_type = 'movie' AND tmdb_id IN ({metadata.placeholders(movie_ids)})"
            )
            params.extend(sorted(movie_ids))
        if series_ids:
            query_parts.append(
                "SELECT decoded_json, date_saved, identity "
                "FROM library_entries "
                "WHERE kind = 'snapshot' "
                f"AND entry_type = 'series' AND tmdb_id IN ({metadata.placeholders(series_ids)})"
            )
            params.extend(sorted(series_ids))
            query_parts.append(
                "SELECT decoded_json, date_saved, identity "
                "FROM library_entries "
                "WHERE kind = 'snapshot' "
                f"AND entry_type = 'season' "
                f"AND parent_series_id IN ({metadata.placeholders(series_ids)})"
            )
            params.extend(sorted(series_ids))
        if not query_parts:
            return []

        connection = self._connect_read_only() if read_only else self._connect_initialized()
        with connection as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM ({" UNION ALL ".join(query_parts)})
                ORDER BY date_saved DESC NULLS LAST, identity ASC
                """,
                params,
            ).fetchall()
        return self._entry_models_from_rows(rows)

    def search_entry_models(
        self,
        query: str,
        *,
        metadata_language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    ) -> list[LibraryEntryModel]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        pattern = f"%{normalized_query.lower()}%"
        with self._connect_initialized() as db:
            rows = db.execute(
                """
                WITH entry_search_rows AS (
                    SELECT
                        library_entries.identity,
                        library_entries.date_saved,
                        library_entries.decoded_json,
                        entry_tmdb_metadata_items.name AS entry_name,
                        entry_tmdb_metadata_items.name_translations_json
                            AS entry_name_translations,
                        entry_tmdb_metadata_items.overview AS entry_overview,
                        entry_tmdb_metadata_items.overview_translations_json
                            AS entry_overview_translations,
                        entry_tmdb_metadata_items.on_air_date AS entry_on_air_date,
                        parent_tmdb_metadata_items.name AS parent_name,
                        parent_tmdb_metadata_items.name_translations_json
                            AS parent_name_translations,
                        parent_tmdb_metadata_items.overview AS parent_overview,
                        parent_tmdb_metadata_items.overview_translations_json
                            AS parent_overview_translations,
                        library_entries.notes
                    FROM library_entries
                    LEFT JOIN tmdb_metadata_items AS entry_tmdb_metadata_items
                        ON entry_tmdb_metadata_items.metadata_key = CASE
                            WHEN library_entries.entry_type = 'season' THEN
                                'season:' || library_entries.parent_series_id || ':' ||
                                library_entries.season_number || ':' || library_entries.tmdb_id
                            ELSE
                                library_entries.entry_type || ':' || library_entries.tmdb_id
                        END
                        AND entry_tmdb_metadata_items.language = ?
                    LEFT JOIN tmdb_metadata_items AS parent_tmdb_metadata_items
                        ON library_entries.entry_type = 'season'
                        AND parent_tmdb_metadata_items.metadata_key =
                            'series:' || library_entries.parent_series_id
                        AND parent_tmdb_metadata_items.language = ?
                    WHERE library_entries.kind = 'snapshot'
                ),
                candidate_matches AS (
                    SELECT identity, date_saved, decoded_json, 1 AS priority
                    FROM entry_search_rows
                    WHERE LOWER(COALESCE(entry_name, identity)) LIKE ?

                    UNION ALL
                    SELECT identity, date_saved, decoded_json, 2 AS priority
                    FROM entry_search_rows
                    WHERE EXISTS (
                        SELECT 1
                        FROM json_each(COALESCE(entry_name_translations, '{}'))
                        WHERE LOWER(json_each.value) LIKE ?
                    )

                    UNION ALL
                    SELECT identity, date_saved, decoded_json, 3 AS priority
                    FROM entry_search_rows
                    WHERE LOWER(COALESCE(parent_overview, '')) LIKE ?
                        OR LOWER(COALESCE(parent_name, '')) LIKE ?
                        OR EXISTS (
                            SELECT 1
                            FROM json_each(COALESCE(parent_name_translations, '{}'))
                            WHERE LOWER(json_each.value) LIKE ?
                        )
                        OR EXISTS (
                            SELECT 1
                            FROM json_each(COALESCE(parent_overview_translations, '{}'))
                            WHERE LOWER(json_each.value) LIKE ?
                        )

                    UNION ALL
                    SELECT identity, date_saved, decoded_json, 4 AS priority
                    FROM entry_search_rows
                    WHERE LOWER(COALESCE(entry_overview, '')) LIKE ?

                    UNION ALL
                    SELECT identity, date_saved, decoded_json, 5 AS priority
                    FROM entry_search_rows
                    WHERE EXISTS (
                        SELECT 1
                        FROM json_each(COALESCE(entry_overview_translations, '{}'))
                        WHERE LOWER(json_each.value) LIKE ?
                    )

                    UNION ALL
                    SELECT identity, date_saved, decoded_json, 6 AS priority
                    FROM entry_search_rows
                    WHERE LOWER(COALESCE(notes, '')) LIKE ?

                    UNION ALL
                    SELECT identity, date_saved, decoded_json, 7 AS priority
                    FROM entry_search_rows
                    WHERE LOWER(COALESCE(entry_on_air_date, '')) LIKE ?
                ),
                ranked_matches AS (
                    SELECT
                        identity,
                        decoded_json,
                        date_saved,
                        MIN(priority) AS priority
                    FROM candidate_matches
                    GROUP BY identity
                )
                SELECT decoded_json
                FROM ranked_matches
                ORDER BY priority ASC, date_saved DESC NULLS LAST, identity ASC
                """,
                (
                    metadata_language,
                    metadata_language,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                ),
            ).fetchall()
        return self._entry_models_from_rows(rows)

    def upsert_metadata_summary(self, summary: LibraryEntryMetadata) -> None:
        with self._connect_initialized() as db:
            metadata.upsert_metadata_summary(db, summary)

    def upsert_metadata_summaries(
        self,
        summaries: list[LibraryEntryMetadata],
        *,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> None:
        if not summaries:
            return
        with self._connect_initialized() as db:
            for summary in summaries:
                metadata.upsert_metadata_item(db, summary, depth=depth)

    def metadata_summary_targets_for_entries(
        self,
        entries: list[LibraryEntryModel],
    ) -> list[TMDbSummaryIdentity]:
        targets = [metadata.metadata_target_from_entry(entry) for entry in entries]
        return metadata.dedupe_summary_targets([target for target in targets if target is not None])

    def missing_metadata_summary_targets(
        self,
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[TMDbSummaryIdentity]:
        return self._metadata_summary_targets_by_state(
            {"missing"},
            language=language,
            depth=depth,
        )

    def outdated_metadata_summary_targets(
        self,
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[TMDbSummaryIdentity]:
        return self._metadata_summary_targets_by_state(
            {"outdated"},
            language=language,
            depth=depth,
        )

    def incomplete_metadata_summary_targets(
        self,
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[TMDbSummaryIdentity]:
        return self._metadata_summary_targets_by_state(
            {"missing", "outdated"},
            language=language,
            depth=depth,
        )

    def metadata_summary_status(
        self,
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> CacheMetadataStatusResult:
        return self.metadata_status_for_entries(
            self.list_entry_models(include_tombstones=False),
            language=language,
            depth=depth,
        )

    def metadata_status_for_entries(
        self,
        entries: list[LibraryEntryModel],
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> CacheMetadataStatusResult:
        tracked = self.metadata_summary_targets_for_entries(entries)
        if not tracked:
            return CacheMetadataStatusResult(
                tracked_entries=0,
                hydrated_entries=0,
                missing_entries=0,
                ready=True,
            )
        with self._connect_initialized() as db:
            missing_count = sum(
                1
                for target in tracked
                if metadata.metadata_summary_state(db, target, language=language, depth=depth)
                != "current"
            )
        return CacheMetadataStatusResult(
            tracked_entries=len(tracked),
            hydrated_entries=len(tracked) - missing_count,
            missing_entries=missing_count,
            ready=missing_count == 0,
        )

    def attach_metadata_summary_models(
        self,
        entries: list[LibraryEntryModel],
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[LibraryEntryModel]:
        if not entries:
            return []
        metadata_keys = [metadata.metadata_key_from_entry(entry) for entry in entries]
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT metadata_depth, metadata_json
                FROM tmdb_metadata_items
                WHERE metadata_key IN ({metadata.placeholders(metadata_keys)})
                AND language = ?
                """,
                [*metadata_keys, language],
            ).fetchall()
        metadata_by_key: dict[str, LibraryEntryMetadata] = {}
        for row in rows:
            if not metadata.metadata_depth_satisfies(row["metadata_depth"], depth):
                continue
            summary = metadata.metadata_row(row)
            metadata_by_key[metadata.metadata_key_from_summary(summary)] = summary.project(
                depth.value
            )
        return [
            entry.with_metadata(metadata_by_key.get(metadata.metadata_key_from_entry(entry)))
            for entry in entries
        ]

    def display_titles_for_entries(
        self,
        entries: list[LibraryEntryModel],
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    ) -> dict[str, str]:
        entry_keys_by_identity = {
            entry.identity: metadata.metadata_key_from_entry(entry) for entry in entries
        }
        parent_keys_by_identity = {
            entry.identity: parent_key
            for entry in entries
            if (parent_key := self._parent_series_metadata_key(entry)) is not None
        }
        if not entry_keys_by_identity and not parent_keys_by_identity:
            return {}

        metadata_keys = sorted(
            set(entry_keys_by_identity.values()) | set(parent_keys_by_identity.values())
        )
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT metadata_key, metadata_json
                FROM tmdb_metadata_items
                WHERE metadata_key IN ({metadata.placeholders(metadata_keys)})
                AND language = ?
                """,
                [*metadata_keys, language],
            ).fetchall()

        metadata_by_key = {str(row["metadata_key"]): metadata.metadata_row(row) for row in rows}
        display_titles: dict[str, str] = {}
        for identity, entry_key in entry_keys_by_identity.items():
            entry_title = metadata_by_key.get(entry_key)
            if entry_title is not None and entry_title.title is not None:
                display_titles[identity] = entry_title.title
        for identity, parent_key in parent_keys_by_identity.items():
            parent_title = metadata_by_key.get(parent_key)
            if parent_title is not None and parent_title.title is not None:
                display_titles[identity] = parent_title.title
        return display_titles

    def parent_series_titles_for_entries(
        self,
        entries: list[LibraryEntryModel],
        *,
        language: str = config.DEFAULT_TMDB_METADATA_LANGUAGE,
    ) -> dict[str, str]:
        parent_keys_by_identity = {
            entry.identity: parent_key
            for entry in entries
            if (parent_key := self._parent_series_metadata_key(entry)) is not None
        }
        if not parent_keys_by_identity:
            return {}

        metadata_keys = sorted(set(parent_keys_by_identity.values()))
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT metadata_key, metadata_json
                FROM tmdb_metadata_items
                WHERE metadata_key IN ({metadata.placeholders(metadata_keys)})
                AND language = ?
                """,
                [*metadata_keys, language],
            ).fetchall()

        metadata_by_key = {str(row["metadata_key"]): metadata.metadata_row(row) for row in rows}
        parent_titles: dict[str, str] = {}
        for identity, parent_key in parent_keys_by_identity.items():
            parent_title = metadata_by_key.get(parent_key)
            if parent_title is not None and parent_title.title is not None:
                parent_titles[identity] = parent_title.title
        return parent_titles

    def _metadata_summary_targets_by_state(
        self,
        states: set[str],
        *,
        language: str,
        depth: MetadataDepth = MetadataDepth.SUMMARY,
    ) -> list[TMDbSummaryIdentity]:
        entries = self.list_entry_models(include_tombstones=False)
        if not entries:
            return []
        targets: list[TMDbSummaryIdentity] = []
        with self._connect_initialized() as db:
            for entry in entries:
                target = metadata.metadata_target_from_entry(entry)
                if (
                    target is not None
                    and metadata.metadata_summary_state(
                        db,
                        target,
                        language=language,
                        depth=depth,
                    )
                    in states
                ):
                    targets.append(target)
        return metadata.dedupe_summary_targets(targets)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _entry_models_from_rows(self, rows: list[sqlite3.Row]) -> list[LibraryEntryModel]:
        return [records.decoded_entry(row) for row in rows]

    def _parent_series_metadata_key(self, entry: LibraryEntryModel) -> str | None:
        if entry.entry_type != "season" or entry.parent_series_id is None:
            return None
        return metadata.metadata_key_from_target(
            TMDbSummaryIdentity(entry_type="series", tmdb_id=entry.parent_series_id)
        )

    @contextmanager
    def _connect_read_only(self) -> Generator[sqlite3.Connection]:
        """Open an existing cache without creating, migrating, or resetting it.

        Best-effort readers outside the `lib` commands use this so they never
        write to the cache or hold its lock; any mismatch reads as unavailable.
        """
        if not self.path.exists():
            raise LibraryCacheNotAvailableError("No local library cache is available.")
        db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            if schema.read_meta(db, "schema_version") != schema.CACHE_SCHEMA_VERSION:
                raise LibraryCacheNotAvailableError(
                    "The local library cache uses a different schema version."
                )
            yield db
        finally:
            db.close()

    @contextmanager
    def _connect_initialized(self) -> Generator[sqlite3.Connection]:
        self.initialize()
        with self._connect() as db:
            yield db
