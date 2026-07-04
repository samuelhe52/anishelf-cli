from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anishelf_cli.models import HumanOutputStyle, MetadataDepth

APP_NAME = "anishelf-cli"
POSIX_APP_DIR = f".{APP_NAME}"

DEFAULT_CONTAINER = "iCloud.com.samuelhe.MyAnimeList"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_DATABASE = "private"
DEFAULT_TMDB_API_KEY_ENVS = ("ANI_TMDB_API_KEY", "TMDB_API_KEY")
DEFAULT_TMDB_METADATA_LANGUAGE = "en-US"
DEFAULT_TMDB_HYDRATION_DEPTH = MetadataDepth.DETAILS

KEYCHAIN_ACCOUNT = "anishelf-cli"
KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN = "anishelf-cli.cloudkit-web-auth-token"
KEYCHAIN_SERVICE_TMDB_API_KEY = "anishelf-cli.tmdb-api-key"
USER_CONFIG_FILE = "config.toml"
LIBRARY_DISPLAY_FIELDS = (
    "title",
    "id",
    "type",
    "status",
    "score",
    "favorite",
    "display",
    "updated",
    "saved",
)


class UserConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LibraryReadDefaults:
    metadata: MetadataDepth = MetadataDepth.SUMMARY
    display_fields: tuple[str, ...] | None = None
    output_style: HumanOutputStyle = HumanOutputStyle.TABLE
    show_hidden: bool = False


@dataclass(frozen=True, slots=True)
class TMDbDefaults:
    metadata_language: str = DEFAULT_TMDB_METADATA_LANGUAGE
    hydration_depth: MetadataDepth = DEFAULT_TMDB_HYDRATION_DEPTH


@dataclass(frozen=True, slots=True)
class UserDefaults:
    library_read: LibraryReadDefaults = LibraryReadDefaults()
    tmdb: TMDbDefaults = TMDbDefaults()


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
        allowed_keys={"library", "tmdb"},
        path=path,
        scope="top-level config",
    )

    return UserDefaults(
        library_read=_load_library_read_defaults(payload.get("library"), path),
        tmdb=_load_tmdb_defaults(payload.get("tmdb"), path),
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
            raise UserConfigError(f"Invalid display field {field!r}. Expected one of: {valid}.")
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
            "Library output defaults accept none or summary. Use tmdb.hydration_depth "
            "or --hydration-depth for details/full cache hydration."
        )
    return depth


def resolve_configured_hydration_depth(value: object, *, path: Path | None = None) -> MetadataDepth:
    candidate = str(value).strip().lower()
    location = f" in {path}" if path is not None else ""
    try:
        depth = MetadataDepth(candidate)
    except ValueError as exc:
        valid = ", ".join((MetadataDepth.DETAILS.value, MetadataDepth.FULL.value))
        raise UserConfigError(
            f"Invalid TMDb hydration depth {candidate!r}{location}. Expected one of: {valid}."
        ) from exc
    if depth not in {MetadataDepth.DETAILS, MetadataDepth.FULL}:
        raise UserConfigError(
            f"Invalid TMDb hydration depth {candidate!r}{location}. Expected details or full."
        )
    return depth


def resolve_configured_output_style(
    value: object,
    *,
    path: Path | None = None,
) -> HumanOutputStyle:
    candidate = str(value).strip().lower()
    location = f" in {path}" if path is not None else ""
    try:
        return HumanOutputStyle(candidate)
    except ValueError as exc:
        valid = ", ".join(style.value for style in HumanOutputStyle)
        raise UserConfigError(
            f"Invalid output style {candidate!r}{location}. Expected one of: {valid}."
        ) from exc


def resolve_configured_tmdb_language(
    value: object,
    *,
    path: Path | None = None,
) -> str:
    candidate = str(value).strip()
    location = f" in {path}" if path is not None else ""
    if not candidate:
        raise UserConfigError(f"Invalid TMDb metadata language{location}: value cannot be empty.")
    if any(character.isspace() for character in candidate):
        raise UserConfigError(
            f"Invalid TMDb metadata language {candidate!r}{location}. "
            "Use a BCP 47-style TMDb language tag such as en-US or ja-JP."
        )
    if len(candidate) > 35:
        raise UserConfigError(
            f"Invalid TMDb metadata language {candidate!r}{location}. Value is too long."
        )
    return candidate


def _load_library_read_defaults(value: object, path: Path) -> LibraryReadDefaults:
    if value is None:
        return LibraryReadDefaults()
    if not isinstance(value, dict):
        raise UserConfigError(f"Library defaults in {path} must be a TOML table.")
    _reject_unknown_keys(
        value,
        allowed_keys={"metadata", "display_fields", "output_style", "show_hidden"},
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

    output_style_value = value.get("output_style", HumanOutputStyle.TABLE.value)
    output_style = resolve_configured_output_style(output_style_value, path=path)

    show_hidden_value = value.get("show_hidden", False)
    if not isinstance(show_hidden_value, bool):
        raise UserConfigError(f"library.show_hidden in {path} must be a TOML boolean.")

    return LibraryReadDefaults(
        metadata=metadata,
        display_fields=display_fields,
        output_style=output_style,
        show_hidden=show_hidden_value,
    )


def _load_tmdb_defaults(value: object, path: Path) -> TMDbDefaults:
    if value is None:
        return TMDbDefaults()
    if not isinstance(value, dict):
        raise UserConfigError(f"TMDb defaults in {path} must be a TOML table.")
    _reject_unknown_keys(
        value,
        allowed_keys={"metadata_language", "hydration_depth"},
        path=path,
        scope="TMDb defaults",
    )

    language_value = value.get("metadata_language", DEFAULT_TMDB_METADATA_LANGUAGE)
    hydration_depth_value = value.get(
        "hydration_depth",
        DEFAULT_TMDB_HYDRATION_DEPTH.value,
    )
    return TMDbDefaults(
        metadata_language=resolve_configured_tmdb_language(language_value, path=path),
        hydration_depth=resolve_configured_hydration_depth(hydration_depth_value, path=path),
    )


def _serialize_user_defaults(defaults: UserDefaults) -> str:
    lines: list[str] = []
    library_lines: list[str] = []
    if defaults.library_read.metadata is not MetadataDepth.SUMMARY:
        library_lines.append(f'metadata = "{defaults.library_read.metadata.value}"')
    if defaults.library_read.display_fields is not None:
        fields = ", ".join(f'"{field}"' for field in defaults.library_read.display_fields)
        library_lines.append(f"display_fields = [{fields}]")
    if defaults.library_read.output_style is not HumanOutputStyle.TABLE:
        library_lines.append(f'output_style = "{defaults.library_read.output_style.value}"')
    if defaults.library_read.show_hidden:
        library_lines.append("show_hidden = true")

    if library_lines:
        lines.append("[library]")
        lines.extend(library_lines)

    tmdb_lines: list[str] = []
    if defaults.tmdb.metadata_language != DEFAULT_TMDB_METADATA_LANGUAGE:
        tmdb_lines.append(f'metadata_language = "{defaults.tmdb.metadata_language}"')
    if defaults.tmdb.hydration_depth is not DEFAULT_TMDB_HYDRATION_DEPTH:
        tmdb_lines.append(f'hydration_depth = "{defaults.tmdb.hydration_depth.value}"')

    if tmdb_lines:
        if lines:
            lines.append("")
        lines.append("[tmdb]")
        lines.extend(tmdb_lines)

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
