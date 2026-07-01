from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from anishelf_cli import config
from anishelf_cli.cache import metadata, records, schema
from anishelf_cli.cache.scope import LibraryCacheScope, scope_from_existing_database
from anishelf_cli.cloudkit.executor import ANI_SHELF_LIBRARY_ZONE_NAME, ZoneChangesPage
from anishelf_cli.tmdb.client import TMDbSummaryIdentity

LibraryCacheError = schema.LibraryCacheError
LibraryCacheNotAvailableError = schema.LibraryCacheNotAvailableError


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
                "No local library cache is available. Run `ani library init` first."
            )
        if len(candidates) > 1:
            raise LibraryCacheNotAvailableError(
                "Multiple user-scoped library caches are available. Run `ani library init` "
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
    def remove_all_local_caches(cls) -> dict[str, int]:
        cache_root = cls.library_cache_root()
        lock_root = cls.library_lock_root()
        return {
            "cache_files": schema.remove_matching_files(cache_root, "*.sqlite3"),
            "lock_files": schema.remove_matching_files(lock_root, "library-cache.*.lock"),
        }

    @contextmanager
    def locked(self) -> Iterator[None]:
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

    def has_entries(self) -> bool:
        with self._connect_initialized() as db:
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
                    if target is not None and not metadata.metadata_summary_exists(db, target):
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

    def list_entries(self, *, include_tombstones: bool = False) -> list[dict[str, Any]]:
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
        return [records.decoded_row(row) for row in rows]

    def list_entries_filtered(
        self,
        *,
        include_tombstones: bool = False,
        watch_status: str | None = None,
        hidden: bool | None = None,
        favorite: bool | None = None,
        on_display: bool | None = None,
        sort: str = "saved",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if not include_tombstones:
            where_parts.append("kind = 'snapshot'")
        if watch_status is not None:
            where_parts.append("watch_status = ?")
            params.append(watch_status)
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
        order_by = schema.list_order_by(sort)
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
        return [records.decoded_row(row) for row in rows]

    def get_entries_by_identity(self, identities: list[str]) -> dict[str, dict[str, Any]]:
        if not identities:
            return {}
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM library_entries
                WHERE kind = 'snapshot'
                AND identity IN ({metadata.placeholders(identities)})
                """,
                identities,
            ).fetchall()
        entries = [records.decoded_row(row) for row in rows]
        return {str(entry["identity"]): entry for entry in entries}

    def search_cached_entries(
        self,
        *,
        movie_ids: set[int],
        series_ids: set[int],
    ) -> list[dict[str, Any]]:
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

        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT decoded_json
                FROM ({" UNION ALL ".join(query_parts)})
                ORDER BY date_saved DESC NULLS LAST, identity ASC
                """,
                params,
            ).fetchall()
        return [records.decoded_row(row) for row in rows]

    def search_entries_by_title(self, title: str) -> list[dict[str, Any]]:
        query = title.strip()
        if not query:
            return []

        pattern = f"%{query.lower()}%"
        with self._connect_initialized() as db:
            rows = db.execute(
                """
                SELECT library_entries.decoded_json
                FROM library_entries
                LEFT JOIN tmdb_metadata_summary
                    ON tmdb_metadata_summary.metadata_key = CASE
                        WHEN library_entries.entry_type = 'season' THEN
                            'season:' || library_entries.parent_series_id || ':' ||
                            library_entries.season_number || ':' || library_entries.tmdb_id
                        ELSE
                            library_entries.entry_type || ':' || library_entries.tmdb_id
                    END
                    AND tmdb_metadata_summary.language = ''
                WHERE library_entries.kind = 'snapshot'
                    AND (
                        LOWER(library_entries.identity) LIKE ?
                        OR LOWER(COALESCE(tmdb_metadata_summary.name, '')) LIKE ?
                        OR LOWER(COALESCE(tmdb_metadata_summary.original_name, '')) LIKE ?
                    )
                ORDER BY library_entries.date_saved DESC NULLS LAST, library_entries.identity ASC
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        return [records.decoded_row(row) for row in rows]

    def upsert_metadata_summary(self, summary: dict[str, Any]) -> None:
        with self._connect_initialized() as db:
            metadata.upsert_metadata_summary(db, summary)

    def upsert_metadata_summaries(self, summaries: list[dict[str, Any]]) -> None:
        if not summaries:
            return
        with self._connect_initialized() as db:
            for summary in summaries:
                metadata.upsert_metadata_summary(db, summary)

    def metadata_summary_targets_for_entries(
        self,
        entries: list[dict[str, Any]],
    ) -> list[TMDbSummaryIdentity]:
        targets = [
            metadata.metadata_target_from_entry(entry)
            for entry in entries
            if entry.get("kind") == "snapshot"
        ]
        return metadata.dedupe_summary_targets([target for target in targets if target is not None])

    def missing_metadata_summary_targets(self) -> list[TMDbSummaryIdentity]:
        return self._metadata_summary_targets_by_state({"missing"})

    def outdated_metadata_summary_targets(self) -> list[TMDbSummaryIdentity]:
        return self._metadata_summary_targets_by_state({"outdated"})

    def incomplete_metadata_summary_targets(self) -> list[TMDbSummaryIdentity]:
        return self._metadata_summary_targets_by_state({"missing", "outdated"})

    def metadata_summary_status(self) -> dict[str, int | bool]:
        entries = self.list_entries(include_tombstones=False)
        if not entries:
            return {
                "tracked_entries": 0,
                "hydrated_entries": 0,
                "missing_entries": 0,
                "ready": True,
            }

        tracked = self.metadata_summary_targets_for_entries(entries)
        missing = self.incomplete_metadata_summary_targets()
        tracked_count = len(tracked)
        missing_count = len(missing)
        return {
            "tracked_entries": tracked_count,
            "hydrated_entries": tracked_count - missing_count,
            "missing_entries": missing_count,
            "ready": missing_count == 0,
        }

    def attach_metadata_summary(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not entries:
            return []
        with self._connect_initialized() as db:
            rows = db.execute(
                f"""
                SELECT metadata_json
                FROM tmdb_metadata_summary
                WHERE metadata_key IN ({metadata.placeholders(entries)})
                AND language = ''
                """,
                metadata.metadata_lookup_params(entries),
            ).fetchall()
        metadata_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            summary = metadata.metadata_row(row)
            metadata_by_key[metadata.metadata_key_from_summary(summary)] = summary
        attached: list[dict[str, Any]] = []
        for entry in entries:
            clone = dict(entry)
            clone["metadata"] = metadata_by_key.get(metadata.metadata_key_from_entry(entry))
            attached.append(clone)
        return attached

    def _metadata_summary_targets_by_state(
        self,
        states: set[str],
    ) -> list[TMDbSummaryIdentity]:
        entries = self.list_entries(include_tombstones=False)
        if not entries:
            return []
        targets: list[TMDbSummaryIdentity] = []
        with self._connect_initialized() as db:
            for entry in entries:
                target = metadata.metadata_target_from_entry(entry)
                if target is not None and metadata.metadata_summary_state(db, target) in states:
                    targets.append(target)
        return metadata.dedupe_summary_targets(targets)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def _connect_initialized(self) -> Iterator[sqlite3.Connection]:
        self.initialize()
        with self._connect() as db:
            yield db
