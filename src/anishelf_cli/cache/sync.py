from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol

from pydantic import Field

from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cloudkit.executor import CloudKitChangeTokenExpiredError, CloudKitExecutor
from anishelf_cli.library import LIBRARY_ENTRY_RECORD_TYPE
from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.domain import LibraryEntryMetadata
from anishelf_cli.models.output import LibraryEntriesCacheResult
from anishelf_cli.tmdb.client import TMDbRequestError, TMDbSummaryIdentity


class LibraryCacheResultScope(Protocol):
    @property
    def container(self) -> str: ...

    @property
    def environment(self) -> str: ...

    @property
    def database(self) -> str: ...

    @property
    def zone(self) -> str: ...

    @property
    def user_record_name(self) -> str: ...


class LibraryCacheRefreshResult(AniShelfBaseModel):
    rebuilt: bool
    pages: int
    records: int
    metadata_requested: int = 0
    metadata_hydrated: int = 0
    metadata_errors: int = 0
    metadata_targets: tuple[TMDbSummaryIdentity, ...] = Field(
        default_factory=tuple,
        exclude=True,
        repr=False,
    )

    def cache_result(
        self,
        scope: LibraryCacheResultScope,
    ) -> LibraryEntriesCacheResult:
        return LibraryEntriesCacheResult(
            mode="updated",
            updated=True,
            rebuilt=self.rebuilt,
            pages=self.pages,
            records=self.records,
            metadata_requested=self.metadata_requested,
            metadata_hydrated=self.metadata_hydrated,
            metadata_errors=self.metadata_errors,
            container=scope.container,
            environment=scope.environment,
            database=scope.database,
            zone=scope.zone,
            user_record_name=scope.user_record_name,
        )


MAX_METADATA_HYDRATION_WORKERS = 8


class TMDbSummaryClient(Protocol):
    def fetch_summary(self, identity: TMDbSummaryIdentity) -> LibraryEntryMetadata: ...


@dataclass(frozen=True, slots=True)
class LibraryCacheProgress:
    phase: str
    rebuilt: bool | None = None
    page: int | None = None
    records_in_page: int | None = None
    records_total: int | None = None
    metadata_requested: int | None = None
    metadata_completed: int | None = None
    metadata_errors: int | None = None


type LibraryCacheProgressCallback = Callable[[LibraryCacheProgress], None]


class MetadataHydrationResult(AniShelfBaseModel):
    requested: int
    hydrated: int
    errors: int


@dataclass(slots=True)
class LibraryCacheSync:
    store: LibraryCacheStore
    executor: CloudKitExecutor
    tmdb_client: TMDbSummaryClient | None = None
    collect_metadata_targets: bool = True
    metadata_workers: int = MAX_METADATA_HYDRATION_WORKERS
    metadata_target_limit: int | None = None
    progress_callback: LibraryCacheProgressCallback | None = None

    def refresh(self) -> LibraryCacheRefreshResult:
        with self.store.locked():
            self.store.initialize()
            sync_token = self.store.read_sync_token()
            if not sync_token or not self.store.has_entries():
                refresh_result = self._rebuild()
            else:
                try:
                    refresh_result = self._incremental(sync_token)
                except CloudKitChangeTokenExpiredError:
                    refresh_result = self._rebuild()

        targets_to_hydrate = list(refresh_result.metadata_targets)
        if self.tmdb_client is None or not targets_to_hydrate:
            return refresh_result

        hydration_result = hydrate_metadata_targets(
            self.store,
            self.tmdb_client,
            targets_to_hydrate,
            max_workers=self.metadata_workers,
            progress_callback=self.progress_callback,
        )
        return refresh_result.model_copy(
            update={
                "metadata_requested": hydration_result.requested,
                "metadata_hydrated": hydration_result.hydrated,
                "metadata_errors": hydration_result.errors,
            }
        )

    def _incremental(self, sync_token: str) -> LibraryCacheRefreshResult:
        pages = 0
        records = 0
        metadata_targets = (
            self.store.outdated_metadata_summary_targets() if self.collect_metadata_targets else []
        )
        next_token: str | None = sync_token
        self._emit_progress("sync-started", rebuilt=False)
        while True:
            page = self.executor.fetch_zone_changes(
                sync_token=next_token,
                desired_record_types=[LIBRARY_ENTRY_RECORD_TYPE],
            )
            metadata_targets.extend(
                self.store.apply_page_and_collect_new_summary_targets(page, staging=False)
            )
            pages += 1
            records += len(page.records)
            self._emit_progress(
                "page-fetched",
                rebuilt=False,
                page=pages,
                records_in_page=len(page.records),
                records_total=records,
            )
            next_token = page.sync_token
            if not page.more_coming:
                targets_to_hydrate = self._metadata_targets_to_hydrate(
                    metadata_targets,
                    limit_targets=True,
                )
                return LibraryCacheRefreshResult(
                    rebuilt=False,
                    pages=pages,
                    records=records,
                    metadata_targets=tuple(targets_to_hydrate),
                )

    def _rebuild(self) -> LibraryCacheRefreshResult:
        self.store.begin_rebuild()
        pages = 0
        records = 0
        metadata_targets: list[TMDbSummaryIdentity] = []
        next_token: str | None = None
        self._emit_progress("rebuild-started", rebuilt=True)
        while True:
            page = self.executor.fetch_zone_changes(
                sync_token=next_token,
                desired_record_types=[LIBRARY_ENTRY_RECORD_TYPE],
            )
            metadata_targets.extend(
                self.store.apply_page_and_collect_new_summary_targets(page, staging=True)
            )
            pages += 1
            records += len(page.records)
            self._emit_progress(
                "page-fetched",
                rebuilt=True,
                page=pages,
                records_in_page=len(page.records),
                records_total=records,
            )
            next_token = page.sync_token
            if not page.more_coming:
                self.store.finish_rebuild()
                targets_to_hydrate = self._metadata_targets_to_hydrate(
                    metadata_targets,
                    limit_targets=False,
                )
                return LibraryCacheRefreshResult(
                    rebuilt=True,
                    pages=pages,
                    records=records,
                    metadata_targets=tuple(targets_to_hydrate),
                )

    def _metadata_targets_to_hydrate(
        self,
        new_targets: list[TMDbSummaryIdentity],
        *,
        limit_targets: bool,
    ) -> list[TMDbSummaryIdentity]:
        deduped = _dedupe_targets(new_targets)
        if not limit_targets:
            return deduped
        if self.metadata_target_limit is None:
            return deduped
        return deduped[: self.metadata_target_limit]

    def _emit_progress(
        self,
        phase: str,
        *,
        rebuilt: bool | None = None,
        page: int | None = None,
        records_in_page: int | None = None,
        records_total: int | None = None,
        metadata_requested: int | None = None,
        metadata_completed: int | None = None,
        metadata_errors: int | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            LibraryCacheProgress(
                phase=phase,
                rebuilt=rebuilt,
                page=page,
                records_in_page=records_in_page,
                records_total=records_total,
                metadata_requested=metadata_requested,
                metadata_completed=metadata_completed,
                metadata_errors=metadata_errors,
            )
        )


def hydrate_metadata_targets(
    store: LibraryCacheStore,
    tmdb_client: TMDbSummaryClient,
    targets: list[TMDbSummaryIdentity],
    *,
    max_workers: int = MAX_METADATA_HYDRATION_WORKERS,
    progress_callback: LibraryCacheProgressCallback | None = None,
) -> MetadataHydrationResult:
    targets_to_hydrate = _dedupe_targets(targets)
    if not targets_to_hydrate:
        return MetadataHydrationResult(requested=0, hydrated=0, errors=0)

    last_emitted_metadata_completed = 0
    if progress_callback is not None:
        progress_callback(
            LibraryCacheProgress(
                phase="metadata-started",
                metadata_requested=len(targets_to_hydrate),
            )
        )

    def metadata_progress(completed: int, errors: int, requested: int) -> None:
        nonlocal last_emitted_metadata_completed
        should_emit = (
            completed == requested
            or completed == 1
            or completed - last_emitted_metadata_completed >= 25
        )
        if not should_emit:
            return
        last_emitted_metadata_completed = completed
        if progress_callback is None:
            return
        progress_callback(
            LibraryCacheProgress(
                phase="metadata-progress",
                metadata_requested=requested,
                metadata_completed=completed,
                metadata_errors=errors,
            )
        )

    summaries, errors = fetch_metadata_summaries(
        tmdb_client,
        targets_to_hydrate,
        max_workers=max_workers,
        progress_callback=metadata_progress,
    )
    store.upsert_metadata_summaries(summaries)
    return MetadataHydrationResult(
        requested=len(targets_to_hydrate),
        hydrated=len(summaries),
        errors=errors,
    )


def fetch_metadata_summaries(
    tmdb_client: TMDbSummaryClient,
    targets: list[TMDbSummaryIdentity],
    *,
    max_workers: int = MAX_METADATA_HYDRATION_WORKERS,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> tuple[list[LibraryEntryMetadata], int]:
    if not targets:
        return [], 0

    worker_count = max(1, min(max_workers, len(targets)))
    summaries: list[LibraryEntryMetadata] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(tmdb_client.fetch_summary, target) for target in targets]
        for future in as_completed(futures):
            try:
                summaries.append(future.result())
            except TMDbRequestError:
                errors += 1
            if progress_callback is not None:
                progress_callback(len(summaries) + errors, errors, len(targets))

    return summaries, errors


def _dedupe_targets(targets: list[TMDbSummaryIdentity]) -> list[TMDbSummaryIdentity]:
    seen: set[TMDbSummaryIdentity] = set()
    deduped: list[TMDbSummaryIdentity] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        deduped.append(target)
    return deduped
