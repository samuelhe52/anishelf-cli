from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, NoReturn, cast

import httpx
import typer

from anishelf_cli import config
from anishelf_cli.cache import metadata as cache_metadata
from anishelf_cli.cache.store import (
    LibraryCacheError,
    LibraryCacheNotAvailableError,
    LibraryCacheStore,
)
from anishelf_cli.cache.sync import (
    LibraryCacheProgress,
    LibraryCacheRefreshResult,
    MetadataHydrationResult,
    fetch_metadata_summaries,
)
from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.library_service import (
    LibraryCommandService,
    emit_library_cache_progress,
)
from anishelf_cli.cli.library_service import (
    library_status as service_library_status,
)
from anishelf_cli.cli.options import FieldListOption, MetadataOption, OutputStyleOption
from anishelf_cli.cli.presentation import (
    LIBRARY_LIST_DEFAULT_FIELDS,
    LIBRARY_SEARCH_DEFAULT_FIELDS,
    render_library_export_result,
    render_library_get,
    render_library_list,
    render_library_search,
)
from anishelf_cli.core.logging import get_logger
from anishelf_cli.core.output import (
    HumanSection,
    emit_error,
    emit_human_blocks,
    emit_json,
    emit_warning,
)
from anishelf_cli.library import (
    has_any_found_item,
    library_get_cache_envelope,
    valid_lookup_record_names,
)
from anishelf_cli.library.queries import (
    MetadataCompletenessError,
    attach_metadata_for_depth,
    build_library_export_result,
    build_library_list_result,
    build_library_search_result,
    cache_summary_payload,
)
from anishelf_cli.library.records import WATCH_STATUS_VALUES
from anishelf_cli.models import (
    HumanOutputStyle,
    LibraryEntryType,
    LibraryListSort,
    LibraryWatchStatus,
    MetadataDepth,
    TMDbMetadataLanguage,
)
from anishelf_cli.models.domain import LibraryEntryModel, TMDbSummaryIdentity
from anishelf_cli.models.output import (
    CacheStatusResult,
    ClearedCachePathsResult,
    LibraryCacheUpdateResult,
    LibraryCacheUpdateSummaryResult,
    LibraryClearCacheResult,
    LibraryEntriesCacheResult,
    LibraryEntriesResult,
    LibraryRefreshMetadataCacheResult,
    LibraryRefreshMetadataResult,
    LibraryRefreshMetadataSummaryResult,
    MetadataHydrationSummaryResult,
)
from anishelf_cli.secrets import SecretStorageUnavailableError, default_secret_store
from anishelf_cli.tmdb.client import TMDbClient
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, resolve_tmdb_api_token

library_app = typer.Typer(
    help="AniShelf library commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
library_lock_factory = None
logger = get_logger(__name__)
_LIBRARY_GET_ENTRY_FIELDS_TO_DROP = frozenset(
    {
        "kind",
        "using_custom_poster",
        "custom_poster_path",
        "library_updated_at",
        "tracking_updated_at",
        "schema_version",
    }
)
_LIBRARY_GET_METADATA_FIELDS_TO_DROP: frozenset[str] = frozenset()


def _make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


@library_app.command("get", help="Read AniShelf library entries by AniShelf id.")
def library_get(
    ctx: typer.Context,
    identities: Annotated[list[str], typer.Argument(help="AniShelf ids.")],
    metadata: MetadataOption = None,
    sync: Annotated[
        bool | None,
        typer.Option(
            "--sync/--no-sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = None,
    live_meta: Annotated[
        bool,
        typer.Option(
            "--live-meta",
            help="Fetch fresh TMDb metadata for the requested entries.",
        ),
    ] = False,
    tmdb_language: Annotated[
        TMDbMetadataLanguage | None,
        typer.Option(
            "--tmdb-language",
            help=(
                "Fetch metadata for this request in a different language without "
                "updating the cache."
            ),
            show_default=False,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _metadata_depth(metadata)
    preferred_language = _preferred_metadata_language()
    request_language = _metadata_language(tmdb_language, preferred_language=preferred_language)
    ad_hoc_language = request_language != preferred_language
    lookup_record_names = valid_lookup_record_names(identities)
    cached_entries: dict[str, LibraryEntryModel] = {}
    store: LibraryCacheStore | None = None
    if lookup_record_names:
        store, _ = _library_read_store(sync=sync)
        cached_entries = store.get_entry_models_by_identity(lookup_record_names)
        if live_meta and not ad_hoc_language and metadata_depth is not MetadataDepth.NONE:
            # Refresh the persisted row at hydration depth so live-meta can replace
            # existing details/full cache rows, then project the requested output depth below.
            _refresh_metadata_for_entries(
                store,
                list(cached_entries.values()),
                depth=_metadata_hydration_depth_for_output(metadata_depth),
            )
        if metadata_depth is not MetadataDepth.NONE and ad_hoc_language:
            cached_entries = {
                entry.identity: entry
                for entry in _attach_live_metadata_for_entries(
                    list(cached_entries.values()),
                    language=request_language,
                    depth=metadata_depth,
                )
            }
        elif metadata_depth is not MetadataDepth.NONE:
            cached_entries = {
                entry.identity: entry
                for entry in attach_metadata_for_depth(
                    store,
                    list(cached_entries.values()),
                    metadata_depth,
                    metadata_language=preferred_language,
                )
            }

    envelope = library_get_cache_envelope(identities, cached_entries)
    if json_output_requested(ctx, json_output):
        payload = envelope.model_dump(mode="json")
        if store is not None:
            _add_parent_series_titles_to_get_payload(
                payload,
                _parent_series_titles_for_entries(
                    store,
                    list(cached_entries.values()),
                    language=request_language,
                    preferred_language=preferred_language,
                    metadata_depth=metadata_depth,
                ),
            )
        _sanitize_library_get_payload(payload)
        emit_json(payload)
    else:
        display_titles = (
            _display_titles_for_entries(
                store,
                list(cached_entries.values()),
                language=request_language,
                preferred_language=preferred_language,
                metadata_depth=metadata_depth,
            )
            if store is not None
            else {}
        )
        render_library_get(envelope, display_titles=display_titles, metadata_depth=metadata_depth)

    if not has_any_found_item(envelope):
        raise typer.Exit(code=1)


@library_app.command("init", help="Initialize the local library cache from CloudKit.")
def library_init(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    machine_output = json_output_requested(ctx, json_output)
    store, refresh_result = _initialize_library_store(
        require_missing_cache=True,
        progress_callback=_emit_library_cache_progress,
    )
    payload = _library_cache_update_result(store, refresh_result)
    if machine_output:
        emit_json(payload.model_dump(mode="json"))
        return
    emit_human_blocks(
        [
            HumanSection(
                "Library init",
                (
                    ("Cache", "updated"),
                    ("User", store.scope.user_record_name),
                    ("Entries fetched", refresh_result.records),
                    ("Pages", refresh_result.pages),
                    ("Metadata requested", refresh_result.metadata_requested),
                    ("Metadata hydrated", refresh_result.metadata_hydrated),
                    ("Metadata errors", refresh_result.metadata_errors),
                ),
            )
        ]
    )


@library_app.command("sync", help="Sync an initialized local library cache from CloudKit.")
def library_sync(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    store, refresh_result = _initialize_library_store(
        require_existing_cache=True,
        progress_callback=_emit_library_cache_progress,
    )
    payload = _library_cache_update_result(store, refresh_result)
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json"))
        return
    emit_human_blocks(
        [
            HumanSection(
                "Library sync",
                (
                    ("Cache", "updated"),
                    ("User", store.scope.user_record_name),
                    ("Entries fetched", refresh_result.records),
                    ("Pages", refresh_result.pages),
                    ("Metadata requested", refresh_result.metadata_requested),
                    ("Metadata hydrated", refresh_result.metadata_hydrated),
                    ("Metadata errors", refresh_result.metadata_errors),
                ),
            )
        ]
    )


@library_app.command("status", help="Show local library cache status.")
def library_status(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    status = service_library_status()
    if json_output_requested(ctx, json_output):
        emit_json(status.model_dump(mode="json"))
        return

    active_user = status.active.scope.user_record_name if status.active.scope is not None else None
    metadata = status.active.metadata
    metadata_hydrated = metadata.hydrated_entries
    metadata_missing = metadata.missing_entries
    metadata_state = "empty"
    if status.initialized:
        if metadata.ready:
            metadata_state = "complete"
        elif metadata_hydrated:
            metadata_state = "partial"
    rows: list[tuple[str, object]] = [
        ("Initialized", "yes" if status.initialized else "no"),
        ("Entries", status.active.entries),
        ("Visible entries", status.active.visible_entries),
        ("Hidden entries", status.active.hidden_entries),
        ("Sync token", "present" if status.active.has_sync_token else "missing"),
        ("Metadata", metadata_state),
        ("Metadata hydrated", metadata_hydrated),
        ("Metadata missing", metadata_missing),
        ("Active cache user", active_user),
        ("Scope count", len(status.scopes)),
        ("Cache files", status.cache_files),
        ("Lock files", status.lock_files),
        ("Cache path", status.cache_path),
        ("Lock path", status.lock_path),
    ]
    if not status.initialized:
        rows.append(("Next step", "ani lib init"))

    emit_human_blocks(
        [
            HumanSection(
                "Library cache",
                rows,
            )
        ]
    )


@library_app.command("clear-cache", help="Clear all local library cache files.")
def library_clear_cache(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    if not yes:
        confirmed = typer.confirm(
            "Delete all local library cache files for every cached user?",
            default=False,
        )
        if not confirmed:
            emit_error("Aborted local library cache clear.")
            raise typer.Exit(code=1)

    removed = LibraryCacheStore.remove_all_local_caches()
    payload = LibraryClearCacheResult(
        status="cleared",
        removed=removed,
        paths=ClearedCachePathsResult(
            cache_dir=str(LibraryCacheStore.library_cache_root()),
            lock_dir=str(LibraryCacheStore.library_lock_root()),
        ),
    )
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json"))
        return

    emit_human_blocks(
        [
            HumanSection(
                "Library cache clear",
                (
                    ("Status", "cleared"),
                    ("Cache files", payload.removed.cache_files),
                    ("Lock files", payload.removed.lock_files),
                    ("Cache dir", str(LibraryCacheStore.library_cache_root())),
                    ("Lock dir", str(LibraryCacheStore.library_lock_root())),
                ),
            )
        ]
    )


@library_app.command("list", help="List cached AniShelf library entries.")
def library_list(
    ctx: typer.Context,
    metadata: MetadataOption = None,
    sync: Annotated[
        bool | None,
        typer.Option(
            "--sync/--no-sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = None,
    fields: FieldListOption = None,
    output_style: OutputStyleOption = None,
    watch_status: Annotated[
        list[LibraryWatchStatus] | None,
        typer.Option(
            "--watch-status",
            "-w",
            help="Filter by watch status. Repeat to match any of several statuses.",
        ),
    ] = None,
    entry_type: Annotated[
        list[LibraryEntryType] | None,
        typer.Option(
            "--type",
            help="Filter by entry type. Repeat to match any of several types.",
        ),
    ] = None,
    show_hidden: Annotated[
        bool,
        typer.Option("--show-hidden", help="Include entries hidden from display."),
    ] = False,
    tmdb_language: Annotated[
        TMDbMetadataLanguage | None,
        typer.Option(
            "--tmdb-language",
            help=(
                "Fetch metadata for this request in a different language without "
                "updating the cache."
            ),
            show_default=False,
        ),
    ] = None,
    favorite: Annotated[
        bool,
        typer.Option("--favorite", help="Show only favorite entries."),
    ] = False,
    sort: Annotated[
        LibraryListSort,
        typer.Option(
            "--sort",
            help=(
                "Sort by saved, updated, title, score, started, finished, type, "
                "watch-status, or air-date."
            ),
        ),
    ] = LibraryListSort.UPDATED,
    reverse: Annotated[
        bool,
        typer.Option("--reverse", "-r", help="Reverse the sort order."),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", min=1, help="Limit the number of entries returned."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _reject_fields_with_json(ctx, json_output, fields)
    _reject_style_with_json(ctx, json_output, output_style)
    machine_output = json_output_requested(ctx, json_output)
    metadata_depth = _metadata_depth(metadata)
    preferred_language = _preferred_metadata_language()
    request_language = _metadata_language(tmdb_language, preferred_language=preferred_language)
    ad_hoc_language = request_language != preferred_language
    watch_statuses = tuple(dict.fromkeys(status.value for status in watch_status or ()))
    entry_types = tuple(dict.fromkeys(kind.value for kind in entry_type or ()))
    resolved_show_hidden = _show_hidden_requested(show_hidden)
    store, refresh_result = _library_read_store(sync=sync)
    try:
        result = build_library_list_result(
            store,
            metadata_depth=metadata_depth,
            cache=cache_summary_payload(store, refresh_result),
            watch_statuses=watch_statuses,
            entry_types=entry_types,
            show_hidden=resolved_show_hidden,
            favorite=favorite,
            sort=sort,
            reverse=reverse,
            limit=limit,
            metadata_language=preferred_language,
            live_metadata=ad_hoc_language and metadata_depth is not MetadataDepth.NONE,
        )
    except MetadataCompletenessError as exc:
        _exit_metadata_completeness(exc)
    _emit_result_warnings(result)
    if ad_hoc_language and metadata_depth is not MetadataDepth.NONE:
        result = _result_with_entries(
            result,
            _attach_live_metadata_for_entries(
                list(result.entries),
                language=request_language,
                depth=metadata_depth,
            ),
        )
    payload = result.model_dump(mode="json")
    if machine_output:
        _add_parent_series_titles_to_entries_payload(
            payload,
            _parent_series_titles_for_entries(
                store,
                list(result.entries),
                language=request_language,
                preferred_language=preferred_language,
                metadata_depth=metadata_depth,
            ),
        )
        emit_json(payload)
        return
    display_entries = list(result.entries)
    render_library_list(
        display_entries,
        fields=_resolve_display_fields(
            fields,
            command_default=_library_table_default_fields(
                LIBRARY_LIST_DEFAULT_FIELDS,
                show_hidden=resolved_show_hidden,
            ),
        ),
        style=_resolve_output_style(output_style),
        display_titles=_display_titles_for_entries(
            store,
            display_entries,
            language=request_language,
            preferred_language=preferred_language,
            metadata_depth=metadata_depth,
        ),
        metadata_depth=metadata_depth,
    )


@library_app.command("search", help="Search cached library entries.")
def library_search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Search query.")],
    metadata: MetadataOption = None,
    sync: Annotated[
        bool | None,
        typer.Option(
            "--sync/--no-sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = None,
    fields: FieldListOption = None,
    output_style: OutputStyleOption = None,
    show_hidden: Annotated[
        bool,
        typer.Option("--show-hidden", help="Include entries hidden from display."),
    ] = False,
    tmdb_language: Annotated[
        TMDbMetadataLanguage | None,
        typer.Option(
            "--tmdb-language",
            help=(
                "Fetch metadata for this request in a different language without "
                "updating the cache."
            ),
            show_default=False,
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", min=1, help="Limit the number of entries returned."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _reject_fields_with_json(ctx, json_output, fields)
    _reject_style_with_json(ctx, json_output, output_style)
    machine_output = json_output_requested(ctx, json_output)
    metadata_depth = _metadata_depth(metadata)
    preferred_language = _preferred_metadata_language()
    request_language = _metadata_language(tmdb_language, preferred_language=preferred_language)
    ad_hoc_language = request_language != preferred_language
    resolved_show_hidden = _show_hidden_requested(show_hidden)
    store, refresh_result = _library_read_store(sync=sync)
    try:
        result = build_library_search_result(
            store,
            query=query,
            metadata_depth=metadata_depth,
            cache=cache_summary_payload(store, refresh_result),
            show_hidden=resolved_show_hidden,
            limit=limit,
            metadata_language=preferred_language,
            live_metadata=ad_hoc_language and metadata_depth is not MetadataDepth.NONE,
        )
    except MetadataCompletenessError as exc:
        _exit_metadata_completeness(exc)
    _emit_result_warnings(result)
    if ad_hoc_language and metadata_depth is not MetadataDepth.NONE:
        result = _result_with_entries(
            result,
            _attach_live_metadata_for_entries(
                list(result.entries),
                language=request_language,
                depth=metadata_depth,
            ),
        )
    payload = result.model_dump(mode="json")
    if machine_output:
        _add_parent_series_titles_to_entries_payload(
            payload,
            _parent_series_titles_for_entries(
                store,
                list(result.entries),
                language=request_language,
                preferred_language=preferred_language,
                metadata_depth=metadata_depth,
            ),
        )
        emit_json(payload)
        return
    display_entries = list(result.entries)
    render_library_search(
        query,
        display_entries,
        fields=_resolve_display_fields(
            fields,
            command_default=_library_table_default_fields(
                LIBRARY_SEARCH_DEFAULT_FIELDS,
                show_hidden=resolved_show_hidden,
            ),
        ),
        style=_resolve_output_style(output_style),
        display_titles=_display_titles_for_entries(
            store,
            display_entries,
            language=request_language,
            preferred_language=preferred_language,
            metadata_depth=metadata_depth,
        ),
        metadata_depth=metadata_depth,
    )


@library_app.command("export", help="Export cached AniShelf library entries.")
def library_export(
    ctx: typer.Context,
    metadata: MetadataOption = None,
    sync: Annotated[
        bool | None,
        typer.Option(
            "--sync/--no-sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = None,
    show_hidden: Annotated[
        bool,
        typer.Option("--show-hidden", help="Include entries hidden from display."),
    ] = False,
    tmdb_language: Annotated[
        TMDbMetadataLanguage | None,
        typer.Option(
            "--tmdb-language",
            help=(
                "Fetch metadata for this request in a different language without "
                "updating the cache."
            ),
            show_default=False,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _metadata_depth(metadata)
    preferred_language = _preferred_metadata_language()
    request_language = _metadata_language(tmdb_language, preferred_language=preferred_language)
    ad_hoc_language = request_language != preferred_language
    store, refresh_result = _library_read_store(sync=sync)
    try:
        result = build_library_export_result(
            store,
            metadata_depth=metadata_depth,
            cache=cache_summary_payload(store, refresh_result),
            show_hidden=_show_hidden_requested(show_hidden),
            metadata_language=preferred_language,
            live_metadata=ad_hoc_language and metadata_depth is not MetadataDepth.NONE,
        )
    except MetadataCompletenessError as exc:
        _exit_metadata_completeness(exc)
    _emit_result_warnings(result)
    if ad_hoc_language and metadata_depth is not MetadataDepth.NONE:
        result = _result_with_entries(
            result,
            _attach_live_metadata_for_entries(
                list(result.entries),
                language=request_language,
                depth=metadata_depth,
            ),
        )
    payload = result.model_dump(mode="json")
    if json_output_requested(ctx, json_output):
        _add_parent_series_titles_to_entries_payload(
            payload,
            _parent_series_titles_for_entries(
                store,
                list(result.entries),
                language=request_language,
                preferred_language=preferred_language,
                metadata_depth=metadata_depth,
            ),
        )
        emit_json(payload)
        return
    render_library_export_result(list(result.entries), result.cache)


@library_app.command(
    "refresh-meta",
    help="Refresh cached TMDb metadata for the local library.",
)
def library_refresh_meta(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _user_defaults_or_exit().tmdb.hydration_depth
    store = _library_store_for_read()
    entries = store.list_entry_models(include_tombstones=False)
    refresh_result = _refresh_metadata_for_entries(store, entries, depth=metadata_depth)
    payload = LibraryRefreshMetadataResult(
        summary=LibraryRefreshMetadataSummaryResult(
            entries=len(entries),
            metadata=MetadataHydrationSummaryResult(
                requested=refresh_result.requested,
                hydrated=refresh_result.hydrated,
                errors=refresh_result.errors,
                depth=metadata_depth.value,
            ),
            cache=LibraryRefreshMetadataCacheResult(
                container=store.scope.container,
                environment=store.scope.environment,
                database=store.scope.database,
                zone=store.scope.zone,
                user_record_name=store.scope.user_record_name,
            ),
        )
    )
    if json_output_requested(ctx, json_output):
        emit_json(payload.model_dump(mode="json"))
        return

    emit_human_blocks(
        [
            HumanSection(
                "Library metadata refresh",
                (
                    ("Entries", len(entries)),
                    ("Depth", metadata_depth.value),
                    ("Requested", refresh_result.requested),
                    ("Hydrated", refresh_result.hydrated),
                    ("Errors", refresh_result.errors),
                    ("User", store.scope.user_record_name),
                ),
            )
        ]
    )


def _library_read_store(
    *,
    sync: bool | None,
) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult | None]:
    if _sync_requested(sync):
        return _initialize_library_store(require_existing_cache=True)
    return _library_store_for_read(), None


def _library_store_for_read() -> LibraryCacheStore:
    try:
        store = LibraryCacheStore.find_default_scope()
        with store.locked():
            store.initialize()
            if not store.has_entries():
                raise LibraryCacheNotAvailableError(
                    "No local library cache entries are available. Run `ani lib init` first."
                )
            return store
    except LibraryCacheError as exc:
        emit_error(str(exc), redactor=getattr(exc, "redactor", None))
        raise typer.Exit(code=2) from exc


def _library_status_payload() -> CacheStatusResult:
    return service_library_status()


def _library_cache_update_result(
    store: LibraryCacheStore,
    refresh_result: LibraryCacheRefreshResult,
) -> LibraryCacheUpdateResult:
    return LibraryCacheUpdateResult(
        summary=LibraryCacheUpdateSummaryResult(
            cache=LibraryEntriesCacheResult(
                mode="updated",
                updated=True,
                rebuilt=refresh_result.rebuilt,
                pages=refresh_result.pages,
                records=refresh_result.records,
                metadata_requested=refresh_result.metadata_requested,
                metadata_hydrated=refresh_result.metadata_hydrated,
                metadata_errors=refresh_result.metadata_errors,
                container=store.scope.container,
                environment=store.scope.environment,
                database=store.scope.database,
                zone=store.scope.zone,
                user_record_name=store.scope.user_record_name,
            )
        )
    )


def _add_parent_series_titles_to_get_payload(
    payload: dict[str, object],
    parent_series_titles: dict[str, str],
) -> None:
    items = payload.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = item.get("entry")
        if isinstance(entry, dict):
            _add_parent_series_title_to_entry_payload(entry, parent_series_titles)


def _sanitize_library_get_payload(payload: dict[str, object]) -> None:
    items = payload.get("items")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = item.get("entry")
        if isinstance(entry, dict):
            _sanitize_library_get_entry_payload(entry)


def _sanitize_library_get_entry_payload(entry: dict[str, object]) -> None:
    for field in _LIBRARY_GET_ENTRY_FIELDS_TO_DROP:
        entry.pop(field, None)
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        for field in _LIBRARY_GET_METADATA_FIELDS_TO_DROP:
            metadata.pop(field, None)
    _compact_library_get_dates(entry)


def _compact_library_get_dates(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(nested, str) and _library_get_date_field(key):
                value[key] = _compact_date_value(nested)
                continue
            _compact_library_get_dates(nested)
        return
    if isinstance(value, list):
        for item in value:
            _compact_library_get_dates(item)


def _library_get_date_field(key: str) -> bool:
    return key.startswith("date_") or key.endswith("_date") or key.endswith("_at")


def _compact_date_value(value: str) -> str:
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value


def _add_parent_series_titles_to_entries_payload(
    payload: dict[str, object],
    parent_series_titles: dict[str, str],
) -> None:
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if isinstance(entry, dict):
            _add_parent_series_title_to_entry_payload(entry, parent_series_titles)


def _add_parent_series_title_to_entry_payload(
    entry: dict[str, object],
    parent_series_titles: dict[str, str],
) -> None:
    metadata = entry.get("metadata")
    if not isinstance(metadata, dict):
        return
    if entry.get("entry_type") != "season":
        cast(dict[str, object], metadata)["parent_series_title"] = None
        return
    identity = entry.get("id")
    parent_series_title = parent_series_titles.get(identity) if isinstance(identity, str) else None
    cast(dict[str, object], metadata)["parent_series_title"] = parent_series_title


def _display_titles_for_entries(
    store: object,
    entries: list[LibraryEntryModel],
    *,
    language: str,
    preferred_language: str,
    metadata_depth: MetadataDepth,
) -> dict[str, str]:
    if metadata_depth is MetadataDepth.NONE:
        language = preferred_language
    if language != preferred_language:
        return _live_parent_series_titles_for_entries(entries, language=language)
    display_titles = getattr(store, "display_titles_for_entries", None)
    if not callable(display_titles):
        return {}
    return cast(
        Callable[..., dict[str, str]],
        display_titles,
    )(entries, language=preferred_language)


def _parent_series_titles_for_entries(
    store: object,
    entries: list[LibraryEntryModel],
    *,
    language: str,
    preferred_language: str,
    metadata_depth: MetadataDepth,
) -> dict[str, str]:
    if metadata_depth is MetadataDepth.NONE:
        return {}
    if language != preferred_language:
        return _live_parent_series_titles_for_entries(entries, language=language)
    parent_titles = getattr(store, "parent_series_titles_for_entries", None)
    if not callable(parent_titles):
        return {}
    return cast(
        Callable[..., dict[str, str]],
        parent_titles,
    )(entries, language=preferred_language)


def _initialize_library_store(
    *,
    require_missing_cache: bool = False,
    require_existing_cache: bool = False,
    progress_callback: Callable[[LibraryCacheProgress], None] | None = None,
) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult]:
    return _library_command_service().initialize_store(
        require_missing_cache=require_missing_cache,
        require_existing_cache=require_existing_cache,
        progress_callback=progress_callback,
    )


def _library_command_service() -> LibraryCommandService:
    return LibraryCommandService(
        make_http_client=_make_http_client,
        secret_store_factory=default_secret_store,
        library_lock_factory=library_lock_factory,
        tmdb_summary_client=_tmdb_summary_client,
    )


def _result_with_entries(
    result: LibraryEntriesResult,
    entries: list[LibraryEntryModel],
) -> LibraryEntriesResult:
    return result.model_copy(update={"entries": tuple(entries)})


def _preferred_metadata_language() -> str:
    return _user_defaults_or_exit().tmdb.metadata_language


def _metadata_language(value: TMDbMetadataLanguage | None, *, preferred_language: str) -> str:
    if value is None:
        logger.debug("TMDb metadata language -> preferred=%s", preferred_language)
        return preferred_language
    language = value.value
    logger.debug(
        "TMDb metadata language -> override=%s preferred=%s",
        language,
        preferred_language,
    )
    return language


def _attach_live_metadata_for_entries(
    entries: list[LibraryEntryModel],
    *,
    language: str,
    depth: MetadataDepth,
) -> list[LibraryEntryModel]:
    targets = cache_metadata.dedupe_summary_targets(
        [
            target
            for entry in entries
            if (target := cache_metadata.metadata_target_from_entry(entry)) is not None
        ]
    )
    logger.debug(
        "Library metadata source -> live language=%s entries=%s targets=%s",
        language,
        len(entries),
        len(targets),
    )
    summaries, error_messages = fetch_metadata_summaries(
        _tmdb_summary_client_or_exit(language=language),
        targets,
        depth=depth,
    )
    if error_messages:
        emit_error(error_messages[0])
        raise typer.Exit(code=2)
    summaries_by_key = {
        cache_metadata.metadata_key_from_summary(summary): summary for summary in summaries
    }
    return [
        entry.with_metadata(
            summary.project(depth.value)
            if (summary := summaries_by_key.get(cache_metadata.metadata_key_from_entry(entry)))
            is not None
            else None
        )
        for entry in entries
    ]


def _live_parent_series_titles_for_entries(
    entries: list[LibraryEntryModel],
    *,
    language: str,
) -> dict[str, str]:
    targets_by_identity = {
        entry.identity: TMDbSummaryIdentity(entry_type="series", tmdb_id=entry.parent_series_id)
        for entry in entries
        if entry.entry_type == "season" and entry.parent_series_id is not None
    }
    if not targets_by_identity:
        return {}
    targets = cache_metadata.dedupe_summary_targets(list(targets_by_identity.values()))
    summaries, error_messages = fetch_metadata_summaries(
        _tmdb_summary_client_or_exit(language=language),
        targets,
    )
    if error_messages:
        return {}
    summaries_by_key = {
        cache_metadata.metadata_key_from_summary(summary): summary for summary in summaries
    }
    display_titles: dict[str, str] = {}
    for identity, target in targets_by_identity.items():
        summary = summaries_by_key.get(cache_metadata.metadata_key_from_target(target))
        if summary is not None and summary.title is not None:
            display_titles[identity] = summary.title
    return display_titles


def _metadata_depth(value: MetadataDepth | None) -> MetadataDepth:
    if value is not None:
        return value
    return _user_defaults_or_exit().library_read.metadata


def _metadata_hydration_depth_for_output(output_depth: MetadataDepth) -> MetadataDepth:
    configured_depth = _user_defaults_or_exit().tmdb.hydration_depth
    if output_depth is MetadataDepth.FULL or configured_depth is MetadataDepth.FULL:
        return MetadataDepth.FULL
    return configured_depth


def _sync_requested(value: bool | None) -> bool:
    if value is not None:
        return value
    return False


def _show_hidden_requested(value: bool) -> bool:
    if value:
        return True
    return _user_defaults_or_exit().library_read.show_hidden


def _validate_watch_status(watch_status: str | None) -> None:
    if watch_status is None or watch_status in WATCH_STATUS_VALUES:
        return
    valid = ", ".join(sorted(WATCH_STATUS_VALUES))
    emit_error(f"Invalid watch status {watch_status!r}. Expected one of: {valid}.")
    raise typer.Exit(code=2)


def _refresh_metadata_for_entries(
    store: LibraryCacheStore,
    entries: list[LibraryEntryModel],
    *,
    depth: MetadataDepth,
) -> MetadataHydrationResult:
    tmdb_client = _tmdb_summary_client_or_exit(language=_preferred_metadata_language())
    targets = store.metadata_summary_targets_for_entries(entries)
    return _refresh_metadata_targets(store, tmdb_client, targets, depth=depth)


def _refresh_metadata_targets(
    store: LibraryCacheStore,
    tmdb_client: TMDbClient,
    targets: list[TMDbSummaryIdentity],
    *,
    depth: MetadataDepth,
    emit_progress_updates: bool = False,
) -> MetadataHydrationResult:
    return _library_command_service().refresh_metadata_targets(
        store,
        tmdb_client,
        targets,
        depth=depth,
        emit_progress_updates=emit_progress_updates,
    )


def _tmdb_summary_client() -> TMDbClient:
    tmdb_token = resolve_tmdb_api_token(default_secret_store())
    client = TMDbClient(tmdb_token.value)
    client.language = _preferred_metadata_language()
    logger.debug(
        "TMDb summary client -> configured source=%s language=%s",
        tmdb_token.source_label,
        client.language,
    )
    return client


def _tmdb_summary_client_or_exit(*, language: str) -> TMDbClient:
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
    except (MissingTMDbAPITokenError, SecretStorageUnavailableError) as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    client = TMDbClient(tmdb_token.value)
    client.language = language
    logger.debug(
        "TMDb summary client -> configured source=%s language=%s",
        tmdb_token.source_label,
        client.language,
    )
    return client


def _emit_library_cache_progress(progress: LibraryCacheProgress) -> None:
    emit_library_cache_progress(progress)


def _emit_result_warnings(result: LibraryEntriesResult) -> None:
    for warning in result.warnings:
        emit_warning(warning)


def _exit_metadata_completeness(exc: MetadataCompletenessError) -> NoReturn:
    emit_error(str(exc))
    raise typer.Exit(code=2) from exc


def _resolve_display_fields(
    value: str | None,
    *,
    command_default: tuple[str, ...],
) -> tuple[str, ...]:
    if value is not None:
        if value.strip().lower() == "default":
            return command_default
        try:
            return config.normalize_library_display_fields(value)
        except config.UserConfigError as exc:
            emit_error(str(exc))
            raise typer.Exit(code=2) from exc

    configured = _user_defaults_or_exit().library_read.display_fields
    if configured is not None:
        return configured
    return command_default


def _library_table_default_fields(
    command_default: tuple[str, ...],
    *,
    show_hidden: bool,
) -> tuple[str, ...]:
    if not show_hidden or "display" in command_default:
        return command_default
    if not command_default:
        return ("display",)
    return (*command_default[:-1], "display", command_default[-1])


def _resolve_output_style(value: HumanOutputStyle | None) -> HumanOutputStyle:
    if value is not None:
        return value

    return _user_defaults_or_exit().library_read.output_style


def _reject_fields_with_json(
    ctx: typer.Context,
    json_output: bool,
    fields: str | None,
) -> None:
    if fields is None or not json_output_requested(ctx, json_output):
        return
    emit_error("--fields only applies to human output.")
    raise typer.Exit(code=2)


def _reject_style_with_json(
    ctx: typer.Context,
    json_output: bool,
    style: str | None,
) -> None:
    if style is None or not json_output_requested(ctx, json_output):
        return
    emit_error("--style only applies to human output.")
    raise typer.Exit(code=2)


def _user_defaults_or_exit() -> config.UserDefaults:
    try:
        return config.load_user_defaults()
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
