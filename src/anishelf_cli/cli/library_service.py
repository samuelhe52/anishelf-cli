from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import typer

from anishelf_cli import config
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
from anishelf_cli.core.logging import get_logger
from anishelf_cli.core.output import emit_error, emit_progress
from anishelf_cli.library import LibraryRecordDecodeError
from anishelf_cli.library.queries import cache_summary_payload
from anishelf_cli.models import MetadataDepth
from anishelf_cli.models.domain import (
    LibraryEntryModel,
    LibraryEntrySnapshot,
    TMDbSummaryIdentity,
)
from anishelf_cli.models.output import (
    CacheActiveResult,
    CacheMetadataStatusResult,
    CacheScopeResult,
    CacheStatusResult,
    LibraryEntriesResult,
)
from anishelf_cli.secrets import SecretStorageUnavailableError, SecretStore
from anishelf_cli.tmdb.client import TMDbClient

logger = get_logger(__name__)


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
        entries: list[LibraryEntryModel],
        store: LibraryCacheStore,
        refresh_result: LibraryCacheRefreshResult | None,
    ) -> LibraryEntriesResult:
        return LibraryEntriesResult(
            entries=tuple(entries),
            cache=cache_summary_payload(store, refresh_result),
        )

    def refresh_metadata_targets(
        self,
        store: LibraryCacheStore,
        tmdb_client: TMDbClient,
        targets: list[TMDbSummaryIdentity],
        *,
        depth: MetadataDepth,
        emit_progress_updates: bool = False,
    ) -> MetadataHydrationResult:
        return refresh_metadata_targets(
            store,
            tmdb_client,
            targets,
            depth=depth,
            emit_progress_updates=emit_progress_updates,
        )


def library_status() -> CacheStatusResult:
    metadata_language = _preferred_metadata_language()
    scopes = LibraryCacheStore.existing_scopes()
    cache_root = LibraryCacheStore.library_cache_root()
    lock_root = LibraryCacheStore.library_lock_root()
    cache_files = sorted(cache_root.glob("*.sqlite3")) if cache_root.exists() else []
    lock_files = sorted(lock_root.glob("library-cache.*.lock")) if lock_root.exists() else []

    active = CacheActiveResult(
        initialized=False,
        entries=0,
        visible_entries=0,
        hidden_entries=0,
        has_sync_token=False,
        scope=None,
        metadata=CacheMetadataStatusResult(
            tracked_entries=0,
            hydrated_entries=0,
            missing_entries=0,
            ready=False,
        ),
    )
    try:
        store = LibraryCacheStore.find_default_scope()
    except LibraryCacheError:
        store = None
    if store is not None:
        logger.debug("Library status -> active cache path=%s", store.path)
        with store.locked():
            store.initialize()
            entries = store.list_entry_models(include_tombstones=False)
            visible_entries, hidden_entries = _entry_visibility_counts(entries)
            active = CacheActiveResult(
                initialized=store.has_entries(),
                entries=len(entries),
                visible_entries=visible_entries,
                hidden_entries=hidden_entries,
                has_sync_token=store.read_sync_token() is not None,
                scope=CacheScopeResult.model_validate(store.scope.key_payload()),
                metadata=store.metadata_summary_status(language=metadata_language),
            )
    else:
        logger.debug("Library status -> no active cache")

    return CacheStatusResult(
        initialized=active.initialized,
        active=active,
        scopes=tuple(CacheScopeResult.model_validate(scope.key_payload()) for scope in scopes),
        cache_path=str(cache_root),
        lock_path=str(lock_root),
        cache_files=len(cache_files),
        lock_files=len(lock_files),
    )


def _entry_visibility_counts(entries: list[LibraryEntryModel]) -> tuple[int, int]:
    visible_entries = 0
    hidden_entries = 0
    for entry in entries:
        if isinstance(entry, LibraryEntrySnapshot) and not entry.on_display:
            hidden_entries += 1
        else:
            visible_entries += 1
    return visible_entries, hidden_entries


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
        logger.debug(
            "CloudKit app token -> resolved source=%s version=%s",
            api_token.source,
            api_token.version or "unknown",
        )
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
            logger.debug("Library cache scope -> path=%s", store.path)
            store.initialize()
            cache_has_entries = store.has_entries()
            logger.debug(
                "Library cache initialize -> requireMissing=%s requireExisting=%s hasEntries=%s",
                require_missing_cache,
                require_existing_cache,
                cache_has_entries,
            )
            if require_missing_cache and cache_has_entries:
                raise LibraryCacheError(
                    "Local library cache already exists. Run `ani lib sync` instead."
                )
            if require_existing_cache and not cache_has_entries:
                raise LibraryCacheNotAvailableError(
                    "No local library cache is available. Run `ani lib init` first."
                )
            tmdb_client = tmdb_summary_client_or_none()
            logger.debug("TMDb summary hydration source -> enabled=%s", tmdb_client is not None)
            refresh_result = LibraryCacheSync(
                store=store,
                executor=executor,
                metadata_language=_preferred_metadata_language(),
                metadata_depth=_preferred_hydration_depth(),
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
    depth: MetadataDepth,
    emit_progress_updates: bool = False,
) -> MetadataHydrationResult:
    progress_callback = emit_library_cache_progress if emit_progress_updates else None
    logger.debug(
        "TMDb metadata refresh -> requested targets=%s progress=%s",
        len(targets),
        emit_progress_updates,
    )
    result = hydrate_metadata_targets(
        store,
        tmdb_client,
        targets,
        depth=depth,
        progress_callback=progress_callback,
    )
    if result.requested == 1 and result.errors:
        emit_error(
            result.error_messages[0] if result.error_messages else "TMDb metadata request failed."
        )
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
        emit_progress(f"Hydrating TMDb metadata for {progress.metadata_requested or 0} entries.")
        return
    if (
        progress.phase == "metadata-progress"
        and progress.metadata_requested is not None
        and progress.metadata_completed is not None
    ):
        emit_progress(
            "TMDb metadata "
            f"{progress.metadata_completed}/{progress.metadata_requested} complete "
            f"({progress.metadata_errors or 0} errors)."
        )


def _preferred_metadata_language() -> str:
    return config.load_user_defaults().tmdb.metadata_language


def _preferred_hydration_depth() -> MetadataDepth:
    return config.load_user_defaults().tmdb.hydration_depth
