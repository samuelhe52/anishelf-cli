from __future__ import annotations

import sys
from dataclasses import replace
from typing import Annotated

import httpx
import typer

from anishelf_cli import config
from anishelf_cli.cache.store import (
    LibraryCacheError,
    LibraryCacheNotAvailableError,
    LibraryCacheScope,
    LibraryCacheStore,
)
from anishelf_cli.cache.sync import (
    LibraryCacheRefreshResult,
    LibraryCacheSync,
    fetch_metadata_summaries,
)
from anishelf_cli.cli.common import json_output_requested, state_from_context
from anishelf_cli.cloudkit.api_token import MissingCloudKitAPITokenError, resolve_cloudkit_api_token
from anishelf_cli.cloudkit.executor import CloudKitExecutor, CloudKitWhoamiError
from anishelf_cli.core.output import (
    HumanSection,
    HumanTable,
    HumanTableColumn,
    emit_error,
    emit_human_blocks,
    emit_json,
    emit_placeholder,
)
from anishelf_cli.library import (
    LibraryRecordDecodeError,
    has_any_found_item,
    library_get_cache_envelope,
    valid_lookup_record_names,
)
from anishelf_cli.library.records import WATCH_STATUS_VALUES
from anishelf_cli.models import CallbackStrategy, LibraryListSort, MetadataDepth
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    default_secret_store,
    set_secret,
    tmdb_api_key_secret,
)
from anishelf_cli.tmdb.client import TMDbClient
from anishelf_cli.tmdb.tokens import MissingTMDbAPITokenError, resolve_tmdb_api_token

config_app = typer.Typer(
    help="Configuration commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
library_app = typer.Typer(
    help="AniShelf library commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
tmdb_app = typer.Typer(
    help="Global TMDb discovery commands.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
library_lock_factory = None

MetadataOption = Annotated[
    MetadataDepth | None,
    typer.Option(
        "--metadata",
        help=(
            "Include TMDb metadata. Bare --metadata uses the default summary level; "
            "explicit values are none, summary, details, or full. Use none to "
            "disable TMDb requests."
        ),
        show_default=False,
    ),
]


def _make_http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _config_payload() -> dict[str, object]:
    api_token = resolve_cloudkit_api_token()
    return {
        "cloudkit": {
            "container": config.DEFAULT_CONTAINER,
            "environment": config.DEFAULT_ENVIRONMENT,
            "database": config.DEFAULT_DATABASE,
            "app_auth_source": api_token.source,
            "app_auth_version": api_token.version,
        },
        "callback": {
            "strategy": CallbackStrategy.MANUAL_PASTE,
        },
        "tmdb": {
            "api_key_envs": list(config.DEFAULT_TMDB_API_KEY_ENVS),
        },
        "paths": {
            "config_dir": str(config.config_dir()),
            "cache_dir": str(config.cache_dir()),
            "data_dir": str(config.data_dir()),
        },
    }


@config_app.command("show", help="Show effective configuration and local paths.")
def config_show(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    payload = _config_payload()
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    cloudkit = payload["cloudkit"]
    callback = payload["callback"]
    tmdb = payload["tmdb"]
    paths = payload["paths"]
    if not (
        isinstance(cloudkit, dict)
        and isinstance(callback, dict)
        and isinstance(tmdb, dict)
        and isinstance(paths, dict)
    ):
        raise RuntimeError("config payload was not initialized correctly")

    app_auth = str(cloudkit["app_auth_source"])
    if cloudkit["app_auth_version"]:
        app_auth += f", version {cloudkit['app_auth_version']}"

    emit_human_blocks(
        [
            HumanSection(
                "CloudKit",
                (
                    ("Container", cloudkit["container"]),
                    ("Environment", cloudkit["environment"]),
                    ("Database", cloudkit["database"]),
                    ("App auth", app_auth),
                ),
            ),
            HumanSection(
                "Callback",
                (("Strategy", callback["strategy"]),),
            ),
            HumanSection(
                "TMDb",
                (("API key envs", ", ".join(config.DEFAULT_TMDB_API_KEY_ENVS)),),
            ),
            HumanSection(
                "Paths",
                (
                    ("Config", paths["config_dir"]),
                    ("Cache", paths["cache_dir"]),
                    ("Data", paths["data_dir"]),
                ),
            ),
        ]
    )


@config_app.command("set-tmdb-api-key", help="Store a TMDb API key in the secure credential store.")
def config_set_tmdb_api_key(
    ctx: typer.Context,
    from_stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read the API key from stdin instead of prompting."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    token = (
        sys.stdin.read().strip()
        if from_stdin
        else typer.prompt(
            "TMDb API key",
            hide_input=True,
        )
    )
    try:
        set_secret(tmdb_api_key_secret(), token, default_secret_store())
    except (SecretStorageUnavailableError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    _emit_secret_saved(json_output_requested(ctx, json_output), "tmdb-api-key")


def _emit_secret_saved(json_output: bool, secret_type: str) -> None:
    payload = {
        "secret_type": secret_type,
        "status": "stored",
        "storage": "keychain",
    }
    if json_output:
        emit_json(payload)
        return
    typer.echo(f"Stored {secret_type} in Keychain.")


@library_app.command("get", help="Read AniShelf library entries by semantic identity.")
def library_get(
    ctx: typer.Context,
    identities: Annotated[list[str], typer.Argument(help="AniShelf identities.")],
    metadata: MetadataOption = None,
    sync: Annotated[
        bool,
        typer.Option(
            "--sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = False,
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
    cached_entries: dict[str, dict[str, object]] = {}
    if lookup_record_names:
        store, _ = _library_read_store(sync=sync)
        cached_entries = store.get_entries_by_identity(lookup_record_names)
        if live_meta:
            _refresh_metadata_for_entries(store, list(cached_entries.values()))
        if metadata_depth is not MetadataDepth.NONE:
            cached_entries = {
                str(entry["identity"]): entry
                for entry in store.attach_metadata_summary(list(cached_entries.values()))
            }

    envelope = library_get_cache_envelope(identities, cached_entries)
    if json_output_requested(ctx, json_output):
        emit_json(envelope)
    else:
        _emit_library_get_human(envelope)

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
    store, refresh_result = _initialize_library_store(require_missing_cache=True)
    payload = {
        "summary": {
            "cache": {
                "mode": "updated",
                "updated": True,
                "rebuilt": refresh_result.rebuilt,
                "pages": refresh_result.pages,
                "records": refresh_result.records,
                "metadata_requested": refresh_result.metadata_requested,
                "metadata_hydrated": refresh_result.metadata_hydrated,
                "metadata_errors": refresh_result.metadata_errors,
                "container": store.scope.container,
                "environment": store.scope.environment,
                "database": store.scope.database,
                "zone": store.scope.zone,
                "user_record_name": store.scope.user_record_name,
            }
        }
    }
    if json_output_requested(ctx, json_output):
        emit_json(payload)
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
    store, refresh_result = _initialize_library_store(require_existing_cache=True)
    payload = {
        "summary": {
            "cache": {
                "mode": "updated",
                "updated": True,
                "rebuilt": refresh_result.rebuilt,
                "pages": refresh_result.pages,
                "records": refresh_result.records,
                "metadata_requested": refresh_result.metadata_requested,
                "metadata_hydrated": refresh_result.metadata_hydrated,
                "metadata_errors": refresh_result.metadata_errors,
                "container": store.scope.container,
                "environment": store.scope.environment,
                "database": store.scope.database,
                "zone": store.scope.zone,
                "user_record_name": store.scope.user_record_name,
            }
        }
    }
    if json_output_requested(ctx, json_output):
        emit_json(payload)
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
    payload = _library_status_payload()
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return

    summary = payload["summary"]
    cache = payload["cache"]
    active = payload["active"]
    if not (
        isinstance(summary, dict)
        and isinstance(cache, dict)
        and isinstance(active, dict)
    ):
        raise RuntimeError("library status payload was not initialized correctly")

    active_scope = active.get("scope")
    active_user = None
    if isinstance(active_scope, dict):
        active_user = active_scope.get("user_record_name")
    metadata = active.get("metadata")
    metadata_hydrated = None
    metadata_missing = None
    metadata_state = "empty"
    if isinstance(metadata, dict):
        metadata_hydrated = metadata.get("hydrated_entries")
        metadata_missing = metadata.get("missing_entries")
        if summary.get("initialized"):
            if metadata.get("ready"):
                metadata_state = "complete"
            elif metadata_hydrated:
                metadata_state = "partial"

    emit_human_blocks(
        [
            HumanSection(
                "Library cache",
                (
                    ("Initialized", "yes" if summary.get("initialized") else "no"),
                    ("Entries", active.get("entries")),
                    ("Sync token", "present" if active.get("has_sync_token") else "missing"),
                    ("Metadata", metadata_state),
                    ("Metadata hydrated", metadata_hydrated),
                    ("Metadata missing", metadata_missing),
                    ("Active user", active_user),
                    ("Scope count", summary.get("scope_count")),
                    ("Cache files", summary.get("cache_files")),
                    ("Lock files", summary.get("lock_files")),
                    ("Cache path", cache.get("path")),
                    ("Lock path", cache.get("lock_path")),
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
    payload = {
        "status": "cleared",
        "removed": removed,
        "paths": {
            "cache_dir": str(LibraryCacheStore.library_cache_root()),
            "lock_dir": str(LibraryCacheStore.library_lock_root()),
        },
    }
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return

    emit_human_blocks(
        [
            HumanSection(
                "Library cache clear",
                (
                    ("Status", "cleared"),
                    ("Cache files", removed["cache_files"]),
                    ("Lock files", removed["lock_files"]),
                    ("Cache dir", str(LibraryCacheStore.library_cache_root())),
                    ("Lock dir", str(LibraryCacheStore.library_lock_root())),
                ),
            )
        ]
    )


def _emit_library_get_human(envelope: dict[str, object]) -> None:
    items = envelope.get("items")
    summary = envelope.get("summary")
    blocks: list[HumanSection] = []

    if isinstance(summary, dict):
        blocks.append(
            HumanSection(
                "Library entries",
                (
                    ("Requested", summary.get("requested")),
                    ("Found", summary.get("found")),
                    ("Errors", summary.get("errors")),
                ),
            )
        )

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            blocks.append(_library_get_item_section(item))

    emit_human_blocks(blocks)


def _library_get_item_section(item: dict[str, object]) -> HumanSection:
    identity = str(item.get("identity") or "unknown identity")
    status = str(item.get("status") or "unknown")
    if status != "found":
        error = item.get("error")
        code = ""
        message = ""
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or "")
        return HumanSection(
            identity,
            (
                ("Status", status),
                ("Error", code),
                ("Detail", message),
            ),
        )

    entry = item.get("entry")
    if not isinstance(entry, dict):
        return HumanSection(identity, (("Status", "decode-error"),))
    metadata = _entry_metadata(entry)
    title = _metadata_name(metadata)

    if entry.get("kind") == "tombstone":
        return HumanSection(
            title or identity,
            (
                ("Status", status),
                ("Identity", identity),
                ("Kind", entry.get("kind")),
                ("Type", entry.get("entry_type")),
                ("TMDb ID", entry.get("tmdb_id")),
                ("Parent series", entry.get("parent_series_id")),
                ("Season", entry.get("season_number")),
                ("Deleted", entry.get("deleted_at")),
                ("Schema", entry.get("schema_version")),
            ),
        )

    return HumanSection(
        title or identity,
        (
            ("Status", status),
            ("Identity", identity),
            ("Title", title),
            ("Original title", _metadata_original_name(metadata)),
            ("Overview", _truncate_text(_metadata_field(metadata, "overview"), limit=220)),
            ("Kind", entry.get("kind")),
            ("Type", entry.get("entry_type")),
            ("TMDb ID", entry.get("tmdb_id")),
            ("Parent series", entry.get("parent_series_id")),
            ("Season", entry.get("season_number")),
            ("Watch status", entry.get("watch_status")),
            ("Score", entry.get("score")),
            ("Favorite", entry.get("favorite")),
            ("On display", entry.get("on_display")),
            ("Date saved", _compact_date(entry.get("date_saved"))),
            ("Date started", entry.get("date_started")),
            ("Date finished", entry.get("date_finished")),
            ("Date tracking", entry.get("is_date_tracking_enabled")),
            ("Poster", _metadata_field(metadata, "poster_path")),
            ("Custom poster", entry.get("custom_poster_path")),
            ("Episode progress", _format_episode_progresses(entry.get("episode_progresses"))),
            ("Library updated", entry.get("library_updated_at")),
            ("Tracking updated", entry.get("tracking_updated_at")),
            ("Notes", _truncate_text(_optional_human_text(entry.get("notes")), limit=160)),
            ("Schema", entry.get("schema_version")),
        ),
    )


def _format_episode_progresses(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return None

    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        season = item.get("season_number")
        episode = item.get("watched_through_episode")
        updated_at = item.get("updated_at")
        label = f"S{season}:E{episode}"
        if updated_at:
            label += f" ({updated_at})"
        parts.append(label)
    return ", ".join(parts) if parts else None


def _optional_human_text(value: object) -> object:
    if value == "":
        return None
    return value


@library_app.command("list", help="List cached AniShelf library entries.")
def library_list(
    ctx: typer.Context,
    metadata: MetadataOption = None,
    sync: Annotated[
        bool,
        typer.Option(
            "--sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = False,
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
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    _validate_watch_status(watch_status)
    store, refresh_result = _library_read_store(sync=sync)
    if sort is LibraryListSort.TITLE:
        _require_complete_tmdb_summary_metadata(
            store,
            action="sort library entries by title",
            hint="Run `ani library refresh-meta` after configuring a TMDb API key.",
        )
    entries = store.list_entries_filtered(
        include_tombstones=False,
        watch_status=watch_status,
        hidden=True if hidden else None,
        favorite=True if favorite else None,
        on_display=on_display,
        sort=sort.value,
        limit=None if sort is LibraryListSort.TITLE else limit,
    )
    entries = _entries_for_metadata_depth(store, entries, metadata_depth)
    entries = _sort_entries_after_metadata(entries, sort)
    if sort is LibraryListSort.TITLE and limit is not None:
        entries = entries[:limit]
    payload = _library_entries_payload(entries, store, refresh_result)
    metadata_payload = _metadata_payload(metadata_depth)
    payload["metadata"] = metadata_payload
    payload["filters"] = _library_list_filters_payload(
        watch_status=watch_status,
        hidden=hidden,
        favorite=favorite,
        on_display=on_display,
        sort=sort,
        limit=limit,
    )
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    _emit_library_list_human(entries)


@library_app.command("search", help="Search cached library entries by title.")
def library_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
    metadata: MetadataOption = None,
    sync: Annotated[
        bool,
        typer.Option(
            "--sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    store, refresh_result = _library_read_store(sync=sync)
    _require_complete_tmdb_summary_metadata(
        store,
        action="search cached library entries by title",
        hint="Run `ani library refresh-meta` after configuring a TMDb API key.",
    )
    entries = store.search_entries_by_title(title)
    entries = _entries_for_metadata_depth(store, entries, metadata_depth)
    payload = _library_entries_payload(entries, store, refresh_result)
    payload["metadata"] = _metadata_payload(metadata_depth)
    payload["query"] = {
        "title": title,
    }
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    _emit_library_search_human(title, entries)


@library_app.command("export", help="Export cached AniShelf library entries.")
def library_export(
    ctx: typer.Context,
    metadata: MetadataOption = None,
    sync: Annotated[
        bool,
        typer.Option(
            "--sync",
            help="Sync the initialized local library cache from CloudKit before reading.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    metadata_depth = _metadata_depth(metadata)
    _reject_reserved_metadata_depth(metadata_depth)
    store, refresh_result = _library_read_store(sync=sync)
    entries = store.list_entries(include_tombstones=False)
    entries = _entries_for_metadata_depth(store, entries, metadata_depth)
    payload = _library_entries_payload(entries, store, refresh_result)
    payload["metadata"] = _metadata_payload(metadata_depth)
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    _emit_library_export_human(payload)


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
    entries = store.list_entries(include_tombstones=False)
    refresh_result = _refresh_metadata_for_entries(store, entries)
    payload = {
        "summary": {
            "entries": len(entries),
            "metadata": refresh_result,
            "cache": {
                "container": store.scope.container,
                "environment": store.scope.environment,
                "database": store.scope.database,
                "zone": store.scope.zone,
                "user_record_name": store.scope.user_record_name,
            },
        }
    }
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return

    emit_human_blocks(
        [
            HumanSection(
                "Library metadata refresh",
                (
                    ("Entries", len(entries)),
                    ("Requested", refresh_result["requested"]),
                    ("Hydrated", refresh_result["hydrated"]),
                    ("Errors", refresh_result["errors"]),
                    ("User", store.scope.user_record_name),
                ),
            )
        ]
    )


def _library_read_store(
    *,
    sync: bool,
) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult | None]:
    if sync:
        store, refresh_result = _initialize_library_store(require_existing_cache=True)
        return store, refresh_result
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


def _library_status_payload() -> dict[str, object]:
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

    return {
        "summary": {
            "initialized": bool(active["initialized"]),
            "scope_count": len(scopes),
            "cache_files": len(cache_files),
            "lock_files": len(lock_files),
        },
        "active": active,
        "scopes": [scope.key_payload() for scope in scopes],
        "cache": {
            "path": str(cache_root),
            "lock_path": str(lock_root),
        },
    }


def _initialize_library_store(
    *,
    require_missing_cache: bool = False,
    require_existing_cache: bool = False,
) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult]:
    try:
        api_token = resolve_cloudkit_api_token()
        with _make_http_client() as client:
            executor = CloudKitExecutor(
                client=client,
                api_token_resolver=lambda: api_token,
                secret_store=default_secret_store(),
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
            tmdb_client = _tmdb_summary_client_or_none()
            with store.locked():
                refresh_result = LibraryCacheSync(
                    store=store,
                    executor=executor,
                    tmdb_client=None,
                ).refresh()
            if tmdb_client is not None and refresh_result.metadata_targets:
                metadata_result = _refresh_metadata_targets(
                    store,
                    tmdb_client,
                    list(refresh_result.metadata_targets),
                )
                refresh_result = replace(
                    refresh_result,
                    metadata_requested=metadata_result["requested"],
                    metadata_hydrated=metadata_result["hydrated"],
                    metadata_errors=metadata_result["errors"],
                )
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


def _library_entries_payload(
    entries: list[dict[str, object]],
    store: LibraryCacheStore,
    refresh_result: LibraryCacheRefreshResult | None,
) -> dict[str, object]:
    return {
        "summary": {
            "entries": len(entries),
            "cache": {
                "mode": "cached" if refresh_result is None else "updated",
                "updated": refresh_result is not None,
                "rebuilt": None if refresh_result is None else refresh_result.rebuilt,
                "pages": None if refresh_result is None else refresh_result.pages,
                "records": None if refresh_result is None else refresh_result.records,
                "metadata_requested": None
                if refresh_result is None
                else refresh_result.metadata_requested,
                "metadata_hydrated": None
                if refresh_result is None
                else refresh_result.metadata_hydrated,
                "metadata_errors": None
                if refresh_result is None
                else refresh_result.metadata_errors,
                "container": store.scope.container,
                "environment": store.scope.environment,
                "database": store.scope.database,
                "zone": store.scope.zone,
                "user_record_name": store.scope.user_record_name,
            },
        },
        "entries": entries,
    }


def _metadata_depth(value: MetadataDepth | None) -> MetadataDepth:
    return value or MetadataDepth.SUMMARY


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


def _entries_for_metadata_depth(
    store: LibraryCacheStore,
    entries: list[dict[str, object]],
    metadata_depth: MetadataDepth,
) -> list[dict[str, object]]:
    if metadata_depth is MetadataDepth.NONE:
        return entries
    return store.attach_metadata_summary(entries)


def _require_complete_tmdb_summary_metadata(
    store: LibraryCacheStore,
    *,
    action: str,
    hint: str,
) -> None:
    status = store.metadata_summary_status()
    if bool(status.get("ready")):
        return
    tracked = int(status.get("tracked_entries", 0))
    hydrated = int(status.get("hydrated_entries", 0))
    missing = int(status.get("missing_entries", 0))
    emit_error(
        f"Cannot {action} because TMDb summary metadata is incomplete "
        f"({hydrated}/{tracked} hydrated, {missing} missing). {hint}"
    )
    raise typer.Exit(code=2)


def _refresh_metadata_for_entries(
    store: LibraryCacheStore,
    entries: list[dict[str, object]],
) -> dict[str, int]:
    tmdb_client = _tmdb_summary_client_or_exit()
    targets = store.metadata_summary_targets_for_entries(entries)
    return _refresh_metadata_targets(store, tmdb_client, targets)


def _refresh_metadata_targets(
    store: LibraryCacheStore,
    tmdb_client: TMDbClient,
    targets: list[dict[str, object]],
) -> dict[str, int]:
    summaries, errors = fetch_metadata_summaries(tmdb_client, targets)
    store.upsert_metadata_summaries(summaries)
    if len(targets) == 1 and errors:
        emit_error("TMDb summary metadata request failed.")
    return {
        "requested": len(targets),
        "hydrated": len(summaries),
        "errors": errors,
    }


def _metadata_payload(metadata_depth: MetadataDepth) -> dict[str, object]:
    return {
        "requested": metadata_depth.value,
        "attached": metadata_depth is not MetadataDepth.NONE,
        "source": "cache" if metadata_depth is not MetadataDepth.NONE else None,
    }


def _tmdb_summary_client_or_none() -> TMDbClient | None:
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
    except (MissingTMDbAPITokenError, SecretStorageUnavailableError):
        return None
    return TMDbClient(tmdb_token.value)


def _tmdb_summary_client_or_exit() -> TMDbClient:
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
    except (MissingTMDbAPITokenError, SecretStorageUnavailableError) as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    return TMDbClient(tmdb_token.value)


def _sort_entries_after_metadata(
    entries: list[dict[str, object]],
    sort: LibraryListSort,
) -> list[dict[str, object]]:
    if sort is not LibraryListSort.TITLE:
        return entries
    return sorted(
        entries,
        key=lambda entry: (
            str(_metadata_name(_entry_metadata(entry)) or entry.get("identity") or "").lower(),
            str(entry.get("identity") or ""),
        ),
    )


def _library_list_filters_payload(
    *,
    watch_status: str | None,
    hidden: bool,
    favorite: bool,
    on_display: bool | None,
    sort: LibraryListSort,
    limit: int | None,
) -> dict[str, object]:
    return {
        "watch_status": watch_status,
        "hidden": hidden,
        "favorite": favorite,
        "on_display": on_display,
        "sort": sort.value,
        "limit": limit,
    }


def _human_library_row(entry: dict[str, object]) -> dict[str, object]:
    return {
        "title": _metadata_name(_entry_metadata(entry)) or entry.get("identity"),
        "identity": entry.get("identity"),
        "entry_type": entry.get("entry_type"),
        "watch_status": entry.get("watch_status"),
        "score": entry.get("score"),
        "favorite": entry.get("favorite"),
        "on_display": entry.get("on_display"),
        "saved": _compact_date(entry.get("date_saved")),
    }


def _entry_metadata(entry: dict[str, object]) -> dict[str, object] | None:
    metadata = entry.get("metadata")
    return metadata if isinstance(metadata, dict) else None


def _metadata_name(metadata: dict[str, object] | None) -> str | None:
    return _metadata_field(metadata, "name") or _metadata_original_name(metadata)


def _metadata_original_name(metadata: dict[str, object] | None) -> str | None:
    return _metadata_field(metadata, "original_name")


def _metadata_field(metadata: dict[str, object] | None, key: str) -> str | None:
    if metadata is None:
        return None
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


def _compact_date(value: object) -> object:
    if not isinstance(value, str):
        return value
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return value


def _truncate_text(value: object, *, limit: int) -> object:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _emit_library_list_human(entries: list[dict[str, object]]) -> None:
    rows = [_human_library_row(entry) for entry in entries]
    emit_human_blocks(
        [
            HumanTable(
                "Library entries",
                (
                    HumanTableColumn("title", "Title"),
                    HumanTableColumn("identity", "Identity"),
                    HumanTableColumn("entry_type", "Type"),
                    HumanTableColumn("watch_status", "Status"),
                    HumanTableColumn("score", "Score", "right"),
                    HumanTableColumn("favorite", "Fav"),
                    HumanTableColumn("on_display", "Display"),
                    HumanTableColumn("saved", "Saved"),
                ),
                rows,
                empty_message="No cached library entries.",
            )
        ]
    )


def _emit_library_search_human(title: str, entries: list[dict[str, object]]) -> None:
    rows = [_human_library_row(entry) for entry in entries]
    emit_human_blocks(
        [
            HumanTable(
                f"Library search: {title}",
                (
                    HumanTableColumn("title", "Title"),
                    HumanTableColumn("identity", "Identity"),
                    HumanTableColumn("entry_type", "Type"),
                    HumanTableColumn("watch_status", "Status"),
                    HumanTableColumn("score", "Score", "right"),
                    HumanTableColumn("saved", "Saved"),
                ),
                rows,
                empty_message="No cached library entries matched the title search.",
            )
        ]
    )


def _emit_library_export_human(payload: dict[str, object]) -> None:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("library export payload was not initialized correctly")
    cache = summary.get("cache")
    if not isinstance(cache, dict):
        raise RuntimeError("library export cache payload was not initialized correctly")

    emit_human_blocks(
        [
            HumanSection(
                "Library export",
                (
                    ("Entries", summary.get("entries")),
                    ("Cache", cache.get("mode")),
                    ("User", cache.get("user_record_name")),
                ),
            )
        ]
    )


@tmdb_app.command("search")
def tmdb_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
) -> None:
    _ = title
    emit_placeholder(state_from_context(ctx), "tmdb search")
