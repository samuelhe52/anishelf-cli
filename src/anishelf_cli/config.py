from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "anishelf-cli"
POSIX_APP_DIR = f".{APP_NAME}"

DEFAULT_CONTAINER = "iCloud.com.samuelhe.MyAnimeList"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_DATABASE = "private"
DEFAULT_TMDB_API_KEY_ENVS = ("ANI_TMDB_API_KEY", "TMDB_API_KEY")

KEYCHAIN_ACCOUNT = "anishelf-cli"
KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN = "anishelf-cli.cloudkit-web-auth-token"
KEYCHAIN_SERVICE_TMDB_API_KEY = "anishelf-cli.tmdb-api-key"


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
