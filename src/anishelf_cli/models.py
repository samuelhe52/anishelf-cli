from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from anishelf_cli.config import (
    DEFAULT_CONTAINER,
    DEFAULT_DATABASE,
    DEFAULT_ENVIRONMENT,
    DEFAULT_TMDB_API_KEY_ENVS,
)


class MetadataDepth(StrEnum):
    NONE = "none"
    SUMMARY = "summary"
    DETAILS = "details"
    FULL = "full"


class CallbackStrategy(StrEnum):
    MANUAL_PASTE = "manual-paste"
    LOOPBACK = "loopback"


class TokenSourceKind(StrEnum):
    ENV = "env"
    ENV_FILE = "env-file"
    KEYCHAIN = "keychain"
    AUTO = "auto"


class ProfileConfig(BaseModel):
    container: str = DEFAULT_CONTAINER
    environment: str = DEFAULT_ENVIRONMENT
    database: str = DEFAULT_DATABASE
    callback_strategy: CallbackStrategy = CallbackStrategy.MANUAL_PASTE
    tmdb_token_source: TokenSourceKind = TokenSourceKind.AUTO
    tmdb_api_key_envs: tuple[str, ...] = DEFAULT_TMDB_API_KEY_ENVS
    env_file: Path | None = None


class AppState(BaseModel):
    profile: str
    json_output: bool = False
    verbosity: int = 0
    metadata_depth: MetadataDepth | None = None
