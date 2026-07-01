from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import typer

from anishelf_cli.cache.scope import LibraryCacheScope
from anishelf_cli.cache.store import (
    LibraryCacheError,
    LibraryCacheNotAvailableError,
    LibraryCacheStore,
)
from anishelf_cli.cache.sync import (
    LibraryCacheProgress,
    LibraryCacheRefreshResult,
    LibraryCacheSync,
    MetadataHydrationResult,
    hydrate_metadata_targets,
)
from anishelf_cli.cloudkit.api_token import MissingCloudKitAPITokenError
from anishelf_cli.cloudkit.executor import CloudKitExecutor, CloudKitWhoamiError, LockFactory
from anishelf_cli.core.output import emit_error, emit_progress
from anishelf_cli.library import LibraryRecordDecodeError
from anishelf_cli.library.entries import LibraryEntry
from anishelf_cli.library.queries import (
    LibraryEntriesResult,
    cache_summary_payload,
)
from anishelf_cli.secrets import SecretStorageUnavailableError, SecretStore
from anishelf_cli.tmdb.client import TMDbClient, TMDbSummaryIdentity


@dataclass(frozen=True, slots=True)
class CacheStatusResult:
    initialized: bool
    active: dict[str, object]
    scopes: list[dict[str, str]]
    cache_path: str
    lock_path: str
    cache_files: int
    lock_files: int

    def to_payload(self) -> dict[str, object]:
        return {
            "summary": {
                "initialized": self.initialized,
                "scope_count": len(self.scopes),
                "cache_files": self.cache_files,
                "lock_files": self.lock_files,
            },
            "active": self.active,
            "scopes": self.scopes,
            "cache": {
                "path": self.cache_path,
                "lock_path": self.lock_path,
            },
        }


@dataclass(frozen=True, slots=True)
class LibraryCommandService:
    make_http_client: Callable[[], httpx.Client]
    secret_store_factory: Callable[[], SecretStore]
    library_lock_factory: LockFactory | None
    tmdb_summary_client_or_none: Callable[[], TMDbClient | None]

    def status(self) -> CacheStatusResult:
        return library_status()

    def initialize_store(
        self,
        *,
        require_missing_cache: bool = False,
        require_existing_cache: bool = False,
        progress_callback: Callable[[LibraryCacheProgress], None] | None = None,
    ) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult]:
        return initialize_library_store(
            make_http_client=self.make_http_client,
            secret_store_factory=self.secret_store_factory,
            library_lock_factory=self.library_lock_factory,
            tmdb_summary_client_or_none=self.tmdb_summary_client_or_none,
            require_missing_cache=require_missing_cache,
            require_existing_cache=require_existing_cache,
            progress_callback=progress_callback,
        )

    def entries_result(
        self,
        entries: list[LibraryEntry],
        store: LibraryCacheStore,
        refresh_result: LibraryCacheRefreshResult | None,
    ) -> LibraryEntriesResult:
        return LibraryEntriesResult(
            entries=entries,
            cache=cache_summary_payload(store, refresh_result),
        )

    def refresh_metadata_targets(
        self,
        store: LibraryCacheStore,
        tmdb_client: TMDbClient,
        targets: list[TMDbSummaryIdentity],
        *,
        emit_progress_updates: bool = False,
    ) -> MetadataHydrationResult:
        return refresh_metadata_targets(
            store,
            tmdb_client,
            targets,
            emit_progress_updates=emit_progress_updates,
        )


def library_status() -> CacheStatusResult:
    scopes = LibraryCacheStore.existing_scopes()
    cache_root = LibraryCacheStore.library_cache_root()
    lock_root = LibraryCacheStore.library_lock_root()
    cache_files = sorted(cache_root.glob("*.sqlite3")) if cache_root.exists() else []
    lock_files = sorted(lock_root.glob("library-cache.*.lock")) if lock_root.exists() else []

    active: dict[str, object] = {
        "initialized": False,
        "entries": 0,
        "has_sync_token": False,
        "scope": None,
    }
    try:
        store = LibraryCacheStore.find_default_scope()
    except LibraryCacheError:
        store = None
    if store is not None:
        with store.locked():
            store.initialize()
            metadata_status = store.metadata_summary_status()
            active = {
                "initialized": store.has_entries(),
                "entries": len(store.list_entries(include_tombstones=False)),
                "has_sync_token": store.read_sync_token() is not None,
                "scope": store.scope.key_payload(),
                "metadata": metadata_status,
            }
    else:
        active["metadata"] = {
            "tracked_entries": 0,
            "hydrated_entries": 0,
            "missing_entries": 0,
            "ready": False,
        }

    return CacheStatusResult(
        initialized=bool(active["initialized"]),
        active=active,
        scopes=[scope.key_payload() for scope in scopes],
        cache_path=str(cache_root),
        lock_path=str(lock_root),
        cache_files=len(cache_files),
        lock_files=len(lock_files),
    )


def initialize_library_store(
    *,
    make_http_client: Callable[[], AbstractContextManager[httpx.Client]],
    secret_store_factory: Callable[[], SecretStore],
    library_lock_factory: Callable[[Path], AbstractContextManager[Any]] | None,
    tmdb_summary_client_or_none: Callable[[], TMDbClient | None],
    require_missing_cache: bool = False,
    require_existing_cache: bool = False,
    progress_callback: Callable[[LibraryCacheProgress], None] | None = None,
) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult]:
    try:
        from anishelf_cli.cloudkit.api_token import resolve_cloudkit_api_token

        api_token = resolve_cloudkit_api_token()
        with make_http_client() as client:
            executor = CloudKitExecutor(
                client=client,
                api_token_resolver=lambda: api_token,
                secret_store=secret_store_factory(),
                lock_factory=library_lock_factory,
            )
            current_user = executor.get_current_user()
            store = LibraryCacheStore.for_scope(
                LibraryCacheScope.default_for_user(current_user.user_record_name)
            )
            store.initialize()
            cache_has_entries = store.has_entries()
            if require_missing_cache and cache_has_entries:
                raise LibraryCacheError(
                    "Local library cache already exists. Run `ani library sync` instead."
                )
            if require_existing_cache and not cache_has_entries:
                raise LibraryCacheNotAvailableError(
                    "No local library cache is available. Run `ani library init` first."
                )
            tmdb_client = tmdb_summary_client_or_none()
            refresh_result = LibraryCacheSync(
                store=store,
                executor=executor,
                tmdb_client=tmdb_client,
                collect_metadata_targets=tmdb_client is not None,
                progress_callback=progress_callback,
            ).refresh()
            return store, refresh_result
    except (
        CloudKitWhoamiError,
        MissingCloudKitAPITokenError,
        LibraryCacheError,
        LibraryRecordDecodeError,
        SecretStorageUnavailableError,
    ) as exc:
        emit_error(str(exc), redactor=getattr(exc, "redactor", None))
        raise typer.Exit(code=2) from exc


def refresh_metadata_targets(
    store: LibraryCacheStore,
    tmdb_client: TMDbClient,
    targets: list[TMDbSummaryIdentity],
    *,
    emit_progress_updates: bool = False,
) -> MetadataHydrationResult:
    progress_callback = emit_library_cache_progress if emit_progress_updates else None
    result = hydrate_metadata_targets(
        store,
        tmdb_client,
        targets,
        progress_callback=progress_callback,
    )
    if result.requested == 1 and result.errors:
        emit_error("TMDb summary metadata request failed.")
    return result


def emit_library_cache_progress(progress: LibraryCacheProgress) -> None:
    if progress.phase == "rebuild-started":
        emit_progress("Starting local library cache rebuild from CloudKit.")
        return
    if progress.phase == "sync-started":
        emit_progress("Starting local library cache sync from CloudKit.")
        return
    if progress.phase == "page-fetched" and progress.page is not None:
        emit_progress(
            f"Fetched page {progress.page}: "
            f"{progress.records_in_page or 0} records "
            f"({progress.records_total or 0} total)."
        )
        return
    if progress.phase == "metadata-started":
        emit_progress(
            f"Hydrating TMDb summary metadata for {progress.metadata_requested or 0} entries."
        )
        return
    if (
        progress.phase == "metadata-progress"
        and progress.metadata_requested is not None
        and progress.metadata_completed is not None
    ):
        emit_progress(
            "TMDb summary metadata "
            f"{progress.metadata_completed}/{progress.metadata_requested} complete "
            f"({progress.metadata_errors or 0} errors)."
        )
