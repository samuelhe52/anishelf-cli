from __future__ import annotations

import sys
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
from anishelf_cli.cache.sync import LibraryCacheRefreshResult, LibraryCacheSync
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
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.library import (
    LibraryRecordDecodeError,
    has_any_found_item,
    library_get_envelope,
    valid_lookup_record_names,
)
from anishelf_cli.models import CallbackStrategy, MetadataDepth
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    default_secret_store,
    set_secret,
    tmdb_api_key_secret,
)
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError
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
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _ = metadata
    lookup_record_names = valid_lookup_record_names(identities)
    lookup_payload: dict[str, object] | None = None

    if lookup_record_names:
        try:
            api_token = resolve_cloudkit_api_token()
            with _make_http_client() as client:
                lookup_payload = CloudKitExecutor(
                    client=client,
                    api_token_resolver=lambda: api_token,
                    secret_store=default_secret_store(),
                    lock_factory=library_lock_factory,
                ).lookup_records(lookup_record_names)
        except (CloudKitWhoamiError, MissingCloudKitAPITokenError) as exc:
            emit_error(str(exc), redactor=getattr(exc, "redactor", None))
            raise typer.Exit(code=2) from exc

    envelope = library_get_envelope(identities, lookup_payload)
    if json_output_requested(ctx, json_output):
        emit_json(envelope)
    else:
        _emit_library_get_human(envelope)

    if not has_any_found_item(envelope):
        raise typer.Exit(code=1)


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

    if entry.get("kind") == "tombstone":
        return HumanSection(
            identity,
            (
                ("Status", status),
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
        identity,
        (
            ("Status", status),
            ("Kind", entry.get("kind")),
            ("Type", entry.get("entry_type")),
            ("TMDb ID", entry.get("tmdb_id")),
            ("Parent series", entry.get("parent_series_id")),
            ("Season", entry.get("season_number")),
            ("Watch status", entry.get("watch_status")),
            ("Score", entry.get("score")),
            ("Favorite", entry.get("favorite")),
            ("On display", entry.get("on_display")),
            ("Date saved", entry.get("date_saved")),
            ("Date started", entry.get("date_started")),
            ("Date finished", entry.get("date_finished")),
            ("Date tracking", entry.get("is_date_tracking_enabled")),
            ("Custom poster", entry.get("custom_poster_path")),
            ("Episode progress", _format_episode_progresses(entry.get("episode_progresses"))),
            ("Library updated", entry.get("library_updated_at")),
            ("Tracking updated", entry.get("tracking_updated_at")),
            ("Notes", _optional_human_text(entry.get("notes"))),
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
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Read the existing local cache without refreshing."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _ = metadata
    store, refresh_result = _library_store_for_read(offline=offline)
    entries = store.list_entries(include_tombstones=False)
    payload = _library_entries_payload(entries, store, refresh_result, include_tombstones=False)
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    _emit_library_list_human(entries)


@library_app.command("search", help="Search cached library entries by TMDb title search.")
def library_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
    metadata: MetadataOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _ = metadata
    store, refresh_result = _library_store_for_read(offline=False)
    try:
        tmdb_token = resolve_tmdb_api_token(default_secret_store())
        search_result = TMDbClient(tmdb_token.value).search_title(title)
    except MissingTMDbAPITokenError as exc:
        emit_error(str(exc))
        raise typer.Exit(code=2) from exc
    except (SecretStorageUnavailableError, TMDbRequestError) as exc:
        redactor = SecretRedactor()
        if "tmdb_token" in locals():
            redactor.register(tmdb_token.value, "tmdb-api-key")
        emit_error(str(exc), redactor=redactor)
        raise typer.Exit(code=2) from exc

    entries = store.search_cached_entries(
        movie_ids=search_result.movie_ids,
        series_ids=search_result.series_ids,
    )
    payload = _library_entries_payload(entries, store, refresh_result, include_tombstones=False)
    payload["query"] = {
        "title": title,
        "tmdb_movie_ids": sorted(search_result.movie_ids),
        "tmdb_series_ids": sorted(search_result.series_ids),
    }
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    _emit_library_search_human(title, entries)


@library_app.command("export", help="Export cached AniShelf library entries.")
def library_export(
    ctx: typer.Context,
    metadata: MetadataOption = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Read the existing local cache without refreshing."),
    ] = False,
    include_tombstones: Annotated[
        bool,
        typer.Option("--include-tombstones", help="Include locally cached tombstone rows."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    _ = metadata
    store, refresh_result = _library_store_for_read(offline=offline)
    entries = store.list_entries(include_tombstones=include_tombstones)
    payload = _library_entries_payload(entries, store, refresh_result, include_tombstones)
    if json_output_requested(ctx, json_output):
        emit_json(payload)
        return
    _emit_library_export_human(payload)


def _library_store_for_read(
    *,
    offline: bool,
) -> tuple[LibraryCacheStore, LibraryCacheRefreshResult | None]:
    try:
        if offline:
            store = LibraryCacheStore.find_default_scope()
            with store.locked():
                store.initialize()
                if not store.has_entries():
                    raise LibraryCacheNotAvailableError(
                        "No local library cache entries are available. "
                        "Run without --offline to refresh first."
                    )
                return store, None

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
            with store.locked():
                refresh_result = LibraryCacheSync(store=store, executor=executor).refresh()
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
    include_tombstones: bool,
) -> dict[str, object]:
    tombstones = sum(1 for entry in entries if entry.get("kind") != "snapshot")
    return {
        "summary": {
            "entries": len(entries),
            "tombstones": tombstones,
            "include_tombstones": include_tombstones,
            "cache": {
                "mode": "offline" if refresh_result is None else "refreshed",
                "rebuilt": None if refresh_result is None else refresh_result.rebuilt,
                "pages": None if refresh_result is None else refresh_result.pages,
                "records": None if refresh_result is None else refresh_result.records,
                "container": store.scope.container,
                "environment": store.scope.environment,
                "database": store.scope.database,
                "zone": store.scope.zone,
                "user_record_name": store.scope.user_record_name,
            },
        },
        "entries": entries,
    }


def _emit_library_list_human(entries: list[dict[str, object]]) -> None:
    emit_human_blocks(
        [
            HumanTable(
                "Library entries",
                (
                    HumanTableColumn("identity", "Identity"),
                    HumanTableColumn("entry_type", "Type"),
                    HumanTableColumn("watch_status", "Status"),
                    HumanTableColumn("score", "Score", "right"),
                    HumanTableColumn("favorite", "Fav"),
                    HumanTableColumn("on_display", "Display"),
                    HumanTableColumn("date_saved", "Saved"),
                ),
                entries,
                empty_message="No cached library entries.",
            )
        ]
    )


def _emit_library_search_human(title: str, entries: list[dict[str, object]]) -> None:
    emit_human_blocks(
        [
            HumanTable(
                f"Library search: {title}",
                (
                    HumanTableColumn("identity", "Identity"),
                    HumanTableColumn("entry_type", "Type"),
                    HumanTableColumn("watch_status", "Status"),
                    HumanTableColumn("score", "Score", "right"),
                    HumanTableColumn("date_saved", "Saved"),
                ),
                entries,
                empty_message="No cached library entries matched the TMDb title search.",
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
                    ("Tombstones", summary.get("tombstones")),
                    ("Cache", cache.get("mode")),
                    ("User", cache.get("user_record_name")),
                ),
            )
        ]
    )


@library_app.command("changes")
def library_changes(
    ctx: typer.Context,
    metadata: MetadataOption = None,
) -> None:
    _ = metadata
    emit_placeholder(state_from_context(ctx), "library changes")


@tmdb_app.command("search")
def tmdb_search(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title")],
) -> None:
    _ = title
    emit_placeholder(state_from_context(ctx), "tmdb search")
