from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anishelf_cli.models import MetadataDepth

APP_NAME = "anishelf-cli"
POSIX_APP_DIR = f".{APP_NAME}"

DEFAULT_CONTAINER = "iCloud.com.samuelhe.MyAnimeList"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_DATABASE = "private"
DEFAULT_TMDB_API_KEY_ENVS = ("ANI_TMDB_API_KEY", "TMDB_API_KEY")

KEYCHAIN_ACCOUNT = "anishelf-cli"
KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN = "anishelf-cli.cloudkit-web-auth-token"
KEYCHAIN_SERVICE_TMDB_API_KEY = "anishelf-cli.tmdb-api-key"
USER_CONFIG_FILE = "config.toml"
LIBRARY_DISPLAY_FIELDS = (
    "title",
    "identity",
    "type",
    "status",
    "score",
    "favorite",
    "display",
    "saved",
)


class UserConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LibraryReadDefaults:
    metadata: MetadataDepth = MetadataDepth.SUMMARY
    display_fields: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class UserDefaults:
    library_read: LibraryReadDefaults = LibraryReadDefaults()


def app_dir() -> Path:
    if sys.platform == "win32":
        if local_app_data := os.environ.get("LOCALAPPDATA"):
            return Path(local_app_data).expanduser() / APP_NAME
    return Path.home() / POSIX_APP_DIR


def config_dir() -> Path:
    if override := os.environ.get("ANISHELF_CLI_CONFIG_DIR"):
        return Path(override).expanduser()
    return app_dir()


def cache_dir() -> Path:
    if override := os.environ.get("ANISHELF_CLI_CACHE_DIR"):
        return Path(override).expanduser()
    return app_dir() / "cache"


def data_dir() -> Path:
    if override := os.environ.get("ANISHELF_CLI_DATA_DIR"):
        return Path(override).expanduser()
    return app_dir()


def user_config_file() -> Path:
    return config_dir() / USER_CONFIG_FILE


def load_user_defaults() -> UserDefaults:
    path = user_config_file()
    if not path.exists():
        return UserDefaults()

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UserConfigError(f"Failed to read user defaults from {path}.") from exc
    except tomllib.TOMLDecodeError as exc:
        raise UserConfigError(f"User config file {path} is not valid TOML.") from exc

    if not isinstance(payload, dict):
        raise UserConfigError(f"User config file {path} must contain a TOML table.")
    _reject_unknown_keys(
        payload,
        allowed_keys={"library"},
        path=path,
        scope="top-level config",
    )

    return UserDefaults(
        library_read=_load_library_read_defaults(payload.get("library"), path),
    )


def save_user_defaults(defaults: UserDefaults) -> Path:
    path = user_config_file()
    body = _serialize_user_defaults(defaults)
    if not body:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise UserConfigError(f"Failed to remove user defaults file {path}.") from exc
        return path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise UserConfigError(f"Failed to write user defaults file {path}.") from exc
    return path


def normalize_library_display_fields(
    value: str | list[object] | tuple[object, ...],
) -> tuple[str, ...]:
    raw_values: list[object]
    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",")]
    else:
        raw_values = list(value)

    fields: list[str] = []
    for raw in raw_values:
        if not isinstance(raw, str):
            raise UserConfigError("Display fields must be strings.")
        field = raw.strip().lower()
        if not field:
            continue
        if field not in LIBRARY_DISPLAY_FIELDS:
            valid = ", ".join(LIBRARY_DISPLAY_FIELDS)
            raise UserConfigError(
                f"Invalid display field {field!r}. Expected one of: {valid}."
            )
        if field not in fields:
            fields.append(field)

    if not fields:
        raise UserConfigError("Display fields cannot be empty.")
    return tuple(fields)


def resolve_configured_metadata_depth(value: object, *, path: Path | None = None) -> MetadataDepth:
    candidate = str(value).strip().lower()
    location = f" in {path}" if path is not None else ""
    try:
        depth = MetadataDepth(candidate)
    except ValueError as exc:
        valid = ", ".join((MetadataDepth.NONE.value, MetadataDepth.SUMMARY.value))
        raise UserConfigError(
            f"Invalid metadata default {candidate!r}{location}. Expected one of: {valid}."
        ) from exc
    if depth not in {MetadataDepth.NONE, MetadataDepth.SUMMARY}:
        raise UserConfigError(
            f"Invalid metadata default {candidate!r}{location}. "
            "Details and full are reserved until TMDb detail metadata caching exists."
        )
    return depth


def _load_library_read_defaults(value: object, path: Path) -> LibraryReadDefaults:
    if value is None:
        return LibraryReadDefaults()
    if not isinstance(value, dict):
        raise UserConfigError(f"Library defaults in {path} must be a TOML table.")
    _reject_unknown_keys(
        value,
        allowed_keys={"metadata", "display_fields"},
        path=path,
        scope="library defaults",
    )

    metadata_value = value.get("metadata", MetadataDepth.SUMMARY.value)
    metadata = resolve_configured_metadata_depth(metadata_value, path=path)

    display_fields_value = value.get("display_fields")
    display_fields: tuple[str, ...] | None
    if display_fields_value is None:
        display_fields = None
    elif isinstance(display_fields_value, list):
        display_fields = normalize_library_display_fields(display_fields_value)
    else:
        raise UserConfigError(f"library.display_fields in {path} must be a TOML array.")

    return LibraryReadDefaults(
        metadata=metadata,
        display_fields=display_fields,
    )


def _serialize_user_defaults(defaults: UserDefaults) -> str:
    lines: list[str] = []
    library_lines: list[str] = []
    if defaults.library_read.metadata is not MetadataDepth.SUMMARY:
        library_lines.append(f'metadata = "{defaults.library_read.metadata.value}"')
    if defaults.library_read.display_fields is not None:
        fields = ", ".join(f'"{field}"' for field in defaults.library_read.display_fields)
        library_lines.append(f"display_fields = [{fields}]")

    if library_lines:
        lines.append("[library]")
        lines.extend(library_lines)

    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _reject_unknown_keys(
    payload: dict[str, Any],
    *,
    allowed_keys: set[str],
    path: Path,
    scope: str,
) -> None:
    unknown_keys = sorted(key for key in payload if key not in allowed_keys)
    if not unknown_keys:
        return
    supported = ", ".join(sorted(allowed_keys))
    unknown = ", ".join(repr(key) for key in unknown_keys)
    raise UserConfigError(
        f"Unsupported {scope} key(s) in {path}: {unknown}. Supported keys: {supported}."
    )
