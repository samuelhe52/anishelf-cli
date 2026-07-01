from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, NoReturn

import httpx
import typer

from anishelf_cli import config
from anishelf_cli.cache.store import (
    LibraryCacheError,
    LibraryCacheNotAvailableError,
    LibraryCacheStore,
)
from anishelf_cli.cache.sync import (
    LibraryCacheProgress,
    LibraryCacheRefreshResult,
    MetadataHydrationResult,
)
from anishelf_cli.cli.common import json_output_requested
from anishelf_cli.cli.library_service import (
    LibraryCommandService,
    emit_library_cache_progress,
)
from anishelf_cli.cli.library_service import (
    library_status as service_library_status,
)
from anishelf_cli.cli.options import FieldListOption, MetadataOption
from anishelf_cli.cli.presentation import (
    LIBRARY_LIST_DEFAULT_FIELDS,
    LIBRARY_SEARCH_DEFAULT_FIELDS,
    render_library_export_result,
    render_library_get,
    render_library_list,
    render_library_search,
)
from anishelf_cli.core.output import HumanSection, emit_error, emit_human_blocks, emit_json
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
from anishelf_cli.models import LibraryListSort, MetadataDepth
from anishelf_cli.models.domain import LibraryEntryModel
from anishelf_cli.models.output import (
    CacheStatusResult,
    ClearedCachePathsResult,
    LibraryCacheUpdateResult,
    LibraryCacheUpdateSummaryResult,
    LibraryClearCacheResult,
    LibraryEntriesCacheResult,
    LibraryRefreshMetadataCacheResult,
    LibraryRefreshMetadataResult,
    LibraryRefreshMetadataSummaryResult,
    MetadataHydrationSummaryResult,
    RemovedCacheFilesResult,
)
from anishelf_cli.secrets import SecretStorageUnavailableError, default_secret_store
from anishelf_cli.tmdb.client import TMDbClient, TMDbSummaryIdentity
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, resolve_tmdb_api_token

library_app = typer.Typer(
    help="AniShelf library commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
library_lock_factory = None


def _make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


@library_app.command("get", help="Read AniShelf library entries by semantic identity.")
def library_get(
    ctx: typer.Context,
    identities: Annotated[list[str], typer.Argument(help="AniShelf identities.")],
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
            help="Fetch fresh TMDb summary metadata for the requested entries.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    lookup_record_names = valid_lookup_record_names(identities)
    cached_entries: dict[str, LibraryEntryModel] = {}
    if lookup_record_names:
        store, _ = _library_read_store(sync=sync)
        cached_entries = store.get_entry_models_by_identity(lookup_record_names)
        if live_meta:
            _refresh_metadata_for_entries(store, list(cached_entries.values()))
        if metadata_depth is not MetadataDepth.NONE:
            cached_entries = {
                entry.identity: entry
                for entry in attach_metadata_for_depth(
                    store,
                    list(cached_entries.values()),
                    metadata_depth,
                )
            }

    envelope = library_get_cache_envelope(identities, cached_entries)
    if json_output_requested(ctx, json_output):
        emit_json(envelope.model_dump(mode="json"))
    else:
        render_library_get(envelope)

    if not has_any_found_item(envelope):
        raise typer.Exit(code=1)


@library_app.command("init", help="Initialize the local library cache from CloudKit.")
def library_init(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
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
        typer.Option("--json", help="Emit machine-readable JSON."),
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
        typer.Option("--json", help="Emit machine-readable JSON."),
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

    emit_human_blocks(
        [
            HumanSection(
                "Library cache",
                (
                    ("Initialized", "yes" if status.initialized else "no"),
                    ("Entries", status.active.entries),
                    ("Sync token", "present" if status.active.has_sync_token else "missing"),
                    ("Metadata", metadata_state),
                    ("Metadata hydrated", metadata_hydrated),
                    ("Metadata missing", metadata_missing),
                    ("Active user", active_user),
                    ("Scope count", len(status.scopes)),
                    ("Cache files", status.cache_files),
                    ("Lock files", status.lock_files),
                    ("Cache path", status.cache_path),
                    ("Lock path", status.lock_path),
                ),
            )
        ]
    )


@library_app.command("clear-cache", help="Clear all local library cache files.")
def library_clear_cache(
    ctx: typer.Context,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the confirmation prompt."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
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
        removed=RemovedCacheFilesResult.model_validate(removed),
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
    watch_status: Annotated[
        str | None,
        typer.Option("--watch-status", help="Filter by watch status."),
    ] = None,
    hidden: Annotated[
        bool,
        typer.Option("--hidden", help="Show only entries hidden from display."),
    ] = False,
    favorite: Annotated[
        bool,
        typer.Option("--favorite", help="Show only favorite entries."),
    ] = False,
    on_display: Annotated[
        bool | None,
        typer.Option(
            "--on-display/--not-on-display",
            help="Filter by display visibility.",
        ),
    ] = None,
    sort: Annotated[
        LibraryListSort,
        typer.Option("--sort", help="Sort by saved, updated, or title."),
    ] = LibraryListSort.SAVED,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Limit the number of entries returned."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _reject_fields_with_json(ctx, json_output, fields)
    machine_output = json_output_requested(ctx, json_output)
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    _validate_watch_status(watch_status)
    store, refresh_result = _library_read_store(sync=sync)
    try:
        result = build_library_list_result(
            store,
            metadata_depth=metadata_depth,
            cache=cache_summary_payload(store, refresh_result),
            watch_status=watch_status,
            hidden=hidden,
            favorite=favorite,
            on_display=on_display,
            sort=sort,
            limit=limit,
        )
    except MetadataCompletenessError as exc:
        _exit_metadata_completeness(exc)
    payload = result.model_dump(mode="json")
    if machine_output:
        emit_json(payload)
        return
    render_library_list(
        store.attach_metadata_summary_models(list(result.entries)),
        fields=_resolve_display_fields(fields, command_default=LIBRARY_LIST_DEFAULT_FIELDS),
    )


@library_app.command("search", help="Search cached library entries by title.")
def library_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
    metadata: MetadataOption = None,
    sync: Annotated[
        bool | None,
        typer.Option(
            "--sync/--no-sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = None,
    fields: FieldListOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _reject_fields_with_json(ctx, json_output, fields)
    machine_output = json_output_requested(ctx, json_output)
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    store, refresh_result = _library_read_store(sync=sync)
    try:
        result = build_library_search_result(
            store,
            title=title,
            metadata_depth=metadata_depth,
            cache=cache_summary_payload(store, refresh_result),
        )
    except MetadataCompletenessError as exc:
        _exit_metadata_completeness(exc)
    payload = result.model_dump(mode="json")
    if machine_output:
        emit_json(payload)
        return
    render_library_search(
        title,
        store.attach_metadata_summary_models(list(result.entries)),
        fields=_resolve_display_fields(fields, command_default=LIBRARY_SEARCH_DEFAULT_FIELDS),
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    store, refresh_result = _library_read_store(sync=sync)
    result = build_library_export_result(
        store,
        metadata_depth=metadata_depth,
        cache=cache_summary_payload(store, refresh_result),
    )
    payload = result.model_dump(mode="json")
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    render_library_export_result(list(result.entries), result.cache)


@library_app.command(
    "refresh-meta",
    help="Refresh cached TMDb summary metadata for the local library.",
)
def library_refresh_meta(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    store = _library_store_for_read()
    entries = store.list_entry_models(include_tombstones=False)
    refresh_result = _refresh_metadata_for_entries(store, entries)
    payload = LibraryRefreshMetadataResult(
        summary=LibraryRefreshMetadataSummaryResult(
            entries=len(entries),
            metadata=MetadataHydrationSummaryResult.model_validate(
                refresh_result.model_dump(mode="json")
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
                    "No local library cache entries are available. Run `ani library init` first."
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
        tmdb_summary_client_or_none=_tmdb_summary_client_or_none,
    )


def _metadata_depth(value: MetadataDepth | None) -> MetadataDepth:
    if value is not None:
        return value
    return _user_defaults_or_exit().library_read.metadata


def _sync_requested(value: bool | None) -> bool:
    if value is not None:
        return value
    return False


def _reject_reserved_metadata_depth(metadata_depth: MetadataDepth) -> None:
    if metadata_depth in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        emit_error(
            f"--metadata {metadata_depth.value} is reserved until TMDb detail metadata "
            "caching exists."
        )
        raise typer.Exit(code=2)


def _validate_watch_status(watch_status: str | None) -> None:
    if watch_status is None or watch_status in WATCH_STATUS_VALUES:
        return
    valid = ", ".join(sorted(WATCH_STATUS_VALUES))
    emit_error(f"Invalid watch status {watch_status!r}. Expected one of: {valid}.")
    raise typer.Exit(code=2)


def _refresh_metadata_for_entries(
    store: LibraryCacheStore,
    entries: list[LibraryEntryModel],
) -> MetadataHydrationResult:
    tmdb_client = _tmdb_summary_client_or_exit()
    targets = store.metadata_summary_targets_for_entries(entries)
    return _refresh_metadata_targets(store, tmdb_client, targets)


def _refresh_metadata_targets(
    store: LibraryCacheStore,
    tmdb_client: TMDbClient,
    targets: list[TMDbSummaryIdentity],
    *,
    emit_progress_updates: bool = False,
) -> MetadataHydrationResult:
    return (
        _library_command_service()
        .refresh_metadata_targets(
            store,
            tmdb_client,
            targets,
            emit_progress_updates=emit_progress_updates,
        )
    )


def _tmdb_summary_client_or_none() -> TMDbClient | None:
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
    except MissingTMDbAPITokenError:
        return None
    return TMDbClient(tmdb_token.value)


def _tmdb_summary_client_or_exit() -> TMDbClient:
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
    except (MissingTMDbAPITokenError, SecretStorageUnavailableError) as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    return TMDbClient(tmdb_token.value)


def _emit_library_cache_progress(progress: LibraryCacheProgress) -> None:
    emit_library_cache_progress(progress)


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


def _reject_fields_with_json(
    ctx: typer.Context,
    json_output: bool,
    fields: str | None,
) -> None:
    if fields is None or not json_output_requested(ctx, json_output):
        return
    emit_error("--fields only applies to human table output.")
    raise typer.Exit(code=2)


def _user_defaults_or_exit() -> config.UserDefaults:
    try:
        return config.load_user_defaults()
    except config.UserConfigError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
