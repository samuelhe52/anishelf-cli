from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Protocol

from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cloudkit.executor import CloudKitChangeTokenExpiredError, CloudKitExecutor
from anishelf_cli.library import LIBRARY_ENTRY_RECORD_TYPE
from anishelf_cli.tmdb.client import TMDbRequestError, TMDbSummaryIdentity


@dataclass(frozen=True, slots=True)
class LibraryCacheRefreshResult:
    rebuilt: bool
    pages: int
    records: int
    metadata_requested: int = 0
    metadata_hydrated: int = 0
    metadata_errors: int = 0
    metadata_targets: tuple[dict[str, Any], ...] = field(
        default_factory=tuple,
        repr=False,
        compare=False,
    )


MAX_METADATA_HYDRATION_WORKERS = 8


class TMDbSummaryClient(Protocol):
    def fetch_summary(self, identity: TMDbSummaryIdentity) -> dict[str, Any]: ...


@dataclass(slots=True)
class LibraryCacheSync:
    store: LibraryCacheStore
    executor: CloudKitExecutor
    tmdb_client: TMDbSummaryClient | None = None
    metadata_workers: int = MAX_METADATA_HYDRATION_WORKERS
    metadata_target_limit: int | None = None

    def refresh(self) -> LibraryCacheRefreshResult:
        self.store.initialize()
        sync_token = self.store.read_sync_token()
        if not sync_token or not self.store.has_entries():
            return self._rebuild()

        try:
            return self._incremental(sync_token)
        except CloudKitChangeTokenExpiredError:
            return self._rebuild()

    def _incremental(self, sync_token: str) -> LibraryCacheRefreshResult:
        pages = 0
        records = 0
        metadata_targets: list[dict[str, Any]] = []
        next_token: str | None = sync_token
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
            next_token = page.sync_token
            if not page.more_coming:
                targets_to_hydrate = self._metadata_targets_to_hydrate(
                    metadata_targets,
                    limit_targets=True,
                )
                hydrated, errors = self._hydrate_metadata_targets(targets_to_hydrate)
                return LibraryCacheRefreshResult(
                    rebuilt=False,
                    pages=pages,
                    records=records,
                    metadata_requested=len(targets_to_hydrate),
                    metadata_hydrated=hydrated,
                    metadata_errors=errors,
                    metadata_targets=tuple(targets_to_hydrate),
                )

    def _rebuild(self) -> LibraryCacheRefreshResult:
        self.store.begin_rebuild()
        pages = 0
        records = 0
        metadata_targets: list[dict[str, Any]] = []
        next_token: str | None = None
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
            next_token = page.sync_token
            if not page.more_coming:
                self.store.finish_rebuild()
                targets_to_hydrate = self._metadata_targets_to_hydrate(
                    metadata_targets,
                    limit_targets=False,
                )
                hydrated, errors = self._hydrate_metadata_targets(targets_to_hydrate)
                return LibraryCacheRefreshResult(
                    rebuilt=True,
                    pages=pages,
                    records=records,
                    metadata_requested=len(targets_to_hydrate),
                    metadata_hydrated=hydrated,
                    metadata_errors=errors,
                    metadata_targets=tuple(targets_to_hydrate),
                )

    def _metadata_targets_to_hydrate(
        self,
        new_targets: list[dict[str, Any]],
        *,
        limit_targets: bool,
    ) -> list[dict[str, Any]]:
        deduped = _dedupe_targets(new_targets)
        if not limit_targets:
            return deduped
        if self.metadata_target_limit is None:
            return deduped
        return deduped[: self.metadata_target_limit]

    def _hydrate_metadata_targets(self, targets: list[dict[str, Any]]) -> tuple[int, int]:
        if self.tmdb_client is None:
            return 0, 0

        summaries, errors = fetch_metadata_summaries(
            self.tmdb_client,
            targets,
            max_workers=self.metadata_workers,
        )
        self.store.upsert_metadata_summaries(summaries)
        return len(summaries), errors


def fetch_metadata_summaries(
    tmdb_client: TMDbSummaryClient,
    targets: list[dict[str, Any]],
    *,
    max_workers: int = MAX_METADATA_HYDRATION_WORKERS,
) -> tuple[list[dict[str, Any]], int]:
    if not targets:
        return [], 0

    worker_count = max(1, min(max_workers, len(targets)))
    summaries: list[dict[str, Any]] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [
            pool.submit(tmdb_client.fetch_summary, _summary_identity_from_target(target))
            for target in targets
        ]
        for future in as_completed(futures):
            try:
                summaries.append(future.result())
            except TMDbRequestError:
                errors += 1

    return summaries, errors


def _summary_identity_from_target(target: dict[str, Any]) -> TMDbSummaryIdentity:
    return TMDbSummaryIdentity(
        entry_type=str(target["entry_type"]),
        tmdb_id=int(target["tmdb_id"]),
        parent_series_id=_optional_int(target.get("parent_series_id")),
        season_number=_optional_int(target.get("season_number")),
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str | bytes | bytearray):
        return int(value)
    raise TypeError(f"Expected integer-compatible metadata field, got {type(value).__name__}.")


def _dedupe_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[object, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for target in targets:
        key = (
            target.get("entry_type"),
            target.get("tmdb_id"),
            target.get("parent_series_id"),
            target.get("season_number"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped
