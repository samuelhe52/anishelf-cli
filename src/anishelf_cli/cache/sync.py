from __future__ import annotations

from dataclasses import dataclass

from anishelf_cli.cache.store import LibraryCacheStore
from anishelf_cli.cloudkit.executor import CloudKitChangeTokenExpiredError, CloudKitExecutor
from anishelf_cli.library import LIBRARY_ENTRY_RECORD_TYPE


@dataclass(frozen=True, slots=True)
class LibraryCacheRefreshResult:
    rebuilt: bool
    pages: int
    records: int


@dataclass(slots=True)
class LibraryCacheSync:
    store: LibraryCacheStore
    executor: CloudKitExecutor

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
        next_token: str | None = sync_token
        while True:
            page = self.executor.fetch_zone_changes(
                sync_token=next_token,
                desired_record_types=[LIBRARY_ENTRY_RECORD_TYPE],
            )
            self.store.apply_page(page, staging=False)
            pages += 1
            records += len(page.records)
            next_token = page.sync_token
            if not page.more_coming:
                return LibraryCacheRefreshResult(rebuilt=False, pages=pages, records=records)

    def _rebuild(self) -> LibraryCacheRefreshResult:
        self.store.begin_rebuild()
        pages = 0
        records = 0
        next_token: str | None = None
        while True:
            page = self.executor.fetch_zone_changes(
                sync_token=next_token,
                desired_record_types=[LIBRARY_ENTRY_RECORD_TYPE],
            )
            self.store.apply_page(page, staging=True)
            pages += 1
            records += len(page.records)
            next_token = page.sync_token
            if not page.more_coming:
                self.store.finish_rebuild()
                return LibraryCacheRefreshResult(rebuilt=True, pages=pages, records=records)
