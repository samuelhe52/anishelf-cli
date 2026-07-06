from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anishelf_cli.models import HumanOutputStyle, MetadataDepth, SecretBackend, TMDbMetadataLanguage

APP_NAME = "anishelf-cli"
POSIX_APP_DIR = f".{APP_NAME}"

DEFAULT_CONTAINER = "iCloud.com.samuelhe.MyAnimeList"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_DATABASE = "private"
DEFAULT_TMDB_API_KEY_ENVS = ("ANI_TMDB_API_KEY", "TMDB_API_KEY")
DEFAULT_TMDB_METADATA_LANGUAGE = TMDbMetadataLanguage.EN.value
DEFAULT_TMDB_HYDRATION_DEPTH = MetadataDepth.DETAILS
DEFAULT_SECRET_BACKEND = SecretBackend.SYSTEM
TMDB_HTTP_LANGUAGE_TAGS = {
    TMDbMetadataLanguage.EN.value: "en-US",
    TMDbMetadataLanguage.JA.value: "ja-JP",
    TMDbMetadataLanguage.ZH.value: "zh-CN",
}

KEYCHAIN_ACCOUNT = "anishelf-cli"
KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN = "anishelf-cli.cloudkit-web-auth-token"
KEYCHAIN_SERVICE_TMDB_API_KEY = "anishelf-cli.tmdb-api-key"
USER_CONFIG_FILE = "config.toml"
PLAINTEXT_KEYRING_FILE = "plaintext-keyring.cfg"
_TABLE_HEADER_RE = re.compile(r"^\s*\[(?!\[)\s*([A-Za-z0-9_-]+)\s*\]\s*(?:#.*)?$")
_ANY_TABLE_HEADER_RE = re.compile(r"^\s*\[")
_SECRETS_BACKEND_RE = re.compile(r"^\s*backend\s*=")
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
class SecretDefaults:
    backend: SecretBackend = DEFAULT_SECRET_BACKEND


@dataclass(frozen=True, slots=True)
class UserDefaults:
    library_read: LibraryReadDefaults = LibraryReadDefaults()
    tmdb: TMDbDefaults = TMDbDefaults()
    secrets: SecretDefaults = SecretDefaults()


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


def plaintext_keyring_file() -> Path:
    return data_dir() / PLAINTEXT_KEYRING_FILE


def load_user_defaults() -> UserDefaults:
    path = user_config_file()
    payload = _load_user_config_payload(path)
    _reject_unknown_keys(
        payload,
        allowed_keys={"library", "secrets", "tmdb"},
        path=path,
        scope="top-level config",
    )

    return UserDefaults(
        library_read=_load_library_read_defaults(payload.get("library"), path),
        tmdb=_load_tmdb_defaults(payload.get("tmdb"), path),
        secrets=_load_secret_defaults(payload.get("secrets"), path),
    )


def load_secret_defaults() -> SecretDefaults:
    path = user_config_file()
    payload = _load_user_config_payload(path)
    _reject_unknown_keys(
        payload,
        allowed_keys={"library", "secrets", "tmdb"},
        path=path,
        scope="top-level config",
    )
    return _load_secret_defaults(payload.get("secrets"), path)


def save_user_defaults(defaults: UserDefaults) -> Path:
    path = user_config_file()
    if path.exists():
        load_user_defaults()
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


def save_secret_defaults(defaults: SecretDefaults) -> Path:
    path = user_config_file()
    if not path.exists():
        body = _serialize_secret_defaults(defaults)
        if not body:
            return path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        except OSError as exc:
            raise UserConfigError(f"Failed to write user defaults file {path}.") from exc
        return path

    try:
        raw_body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UserConfigError(f"Failed to read user defaults from {path}.") from exc

    payload = _parse_user_config_payload(raw_body, path)
    _reject_unknown_keys(
        payload,
        allowed_keys={"library", "secrets", "tmdb"},
        path=path,
        scope="top-level config",
    )
    _load_secret_defaults(payload.get("secrets"), path)
    body = _patch_secret_defaults(
        raw_body,
        defaults,
        path=path,
        has_existing_secret_defaults=payload.get("secrets") is not None,
    )
    if not body:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise UserConfigError(f"Failed to remove user defaults file {path}.") from exc
        return path

    try:
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
    candidate = str(value).strip().lower()
    location = f" in {path}" if path is not None else ""
    if candidate not in TMDB_HTTP_LANGUAGE_TAGS:
        valid = ", ".join(TMDB_HTTP_LANGUAGE_TAGS)
        raise UserConfigError(
            f"Invalid TMDb metadata language {candidate!r}{location}. Expected one of: {valid}."
        )
    return candidate


def tmdb_http_language_tag(language: str) -> str:
    return TMDB_HTTP_LANGUAGE_TAGS[resolve_configured_tmdb_language(language)]


def resolve_configured_secret_backend(
    value: object,
    *,
    path: Path | None = None,
) -> SecretBackend:
    candidate = str(value).strip().lower()
    location = f" in {path}" if path is not None else ""
    try:
        return SecretBackend(candidate)
    except ValueError as exc:
        valid = ", ".join(backend.value for backend in SecretBackend)
        raise UserConfigError(
            f"Invalid secrets backend {candidate!r}{location}. Expected one of: {valid}."
        ) from exc


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


def _load_secret_defaults(value: object, path: Path) -> SecretDefaults:
    if value is None:
        return SecretDefaults()
    if not isinstance(value, dict):
        raise UserConfigError(f"Secret defaults in {path} must be a TOML table.")
    _reject_unknown_keys(
        value,
        allowed_keys={"backend"},
        path=path,
        scope="secret defaults",
    )

    backend_value = value.get("backend", DEFAULT_SECRET_BACKEND.value)
    return SecretDefaults(
        backend=resolve_configured_secret_backend(backend_value, path=path),
    )


def _load_user_config_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UserConfigError(f"Failed to read user defaults from {path}.") from exc
    return _parse_user_config_payload(body, path)


def _parse_user_config_payload(body: str, path: Path) -> dict[str, Any]:
    try:
        payload = tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise UserConfigError(f"User config file {path} is not valid TOML.") from exc

    if not isinstance(payload, dict):
        raise UserConfigError(f"User config file {path} must contain a TOML table.")
    return payload


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

    secret_body = _serialize_secret_defaults(defaults.secrets)
    if secret_body:
        if lines:
            lines.append("")
        lines.extend(secret_body.rstrip("\n").split("\n"))

    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _serialize_secret_defaults(defaults: SecretDefaults) -> str:
    secret_lines: list[str] = []
    if defaults.backend is not DEFAULT_SECRET_BACKEND:
        secret_lines.append(f'backend = "{defaults.backend.value}"')

    if not secret_lines:
        return ""
    return "\n".join(("[secrets]", *secret_lines)) + "\n"


def _patch_secret_defaults(
    body: str,
    defaults: SecretDefaults,
    *,
    path: Path,
    has_existing_secret_defaults: bool,
) -> str:
    lines = body.splitlines()
    section_start, section_end = _find_toml_table(lines, "secrets")
    backend_line = f'backend = "{defaults.backend.value}"'
    if has_existing_secret_defaults and section_start is None:
        raise UserConfigError(
            f"Secret defaults in {path} must use a secrets TOML table before ani can modify them."
        )

    if defaults.backend is not DEFAULT_SECRET_BACKEND:
        if section_start is None or section_end is None:
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend(("[secrets]", backend_line))
            return _format_toml_lines(lines)

        for index in range(section_start + 1, section_end):
            if _SECRETS_BACKEND_RE.match(lines[index]):
                lines[index] = backend_line
                return _format_toml_lines(lines)
        lines.insert(section_start + 1, backend_line)
        return _format_toml_lines(lines)

    if section_start is None or section_end is None:
        return _format_toml_lines(lines)

    lines = lines[:section_start] + lines[section_end:]
    return _format_toml_lines(lines)


def _find_toml_table(lines: list[str], name: str) -> tuple[int | None, int | None]:
    section_start: int | None = None
    for index, line in enumerate(lines):
        match = _TABLE_HEADER_RE.match(line)
        if match is not None and match.group(1) == name:
            section_start = index
            break
    if section_start is None:
        return None, None

    section_end = len(lines)
    for index in range(section_start + 1, len(lines)):
        if _ANY_TABLE_HEADER_RE.match(lines[index]):
            section_end = index
            break
    return section_start, section_end


def _format_toml_lines(lines: list[str]) -> str:
    while lines and lines[-1] == "":
        lines.pop()
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
