from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "anishelf-cli"
APP_AUTHOR = "samuelhe"

DEFAULT_CONTAINER = "iCloud.com.samuelhe.MyAnimeList"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_DATABASE = "private"
DEFAULT_TMDB_API_KEY_ENVS = ("ANI_TMDB_API_KEY", "TMDB_API_KEY")

KEYCHAIN_ACCOUNT = "anishelf-cli"
KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN = "anishelf-cli.cloudkit-web-auth-token"
KEYCHAIN_SERVICE_TMDB_API_KEY = "anishelf-cli.tmdb-api-key"

dirs = PlatformDirs(appname=APP_NAME, appauthor=APP_AUTHOR)


def config_dir() -> Path:
    if override := os.environ.get("ANISHELF_CLI_CONFIG_DIR"):
        return Path(override).expanduser()
    return Path(dirs.user_config_dir)


def cache_dir() -> Path:
    if override := os.environ.get("ANISHELF_CLI_CACHE_DIR"):
        return Path(override).expanduser()
    return Path(dirs.user_cache_dir)


def data_dir() -> Path:
    if override := os.environ.get("ANISHELF_CLI_DATA_DIR"):
        return Path(override).expanduser()
    return Path(dirs.user_data_dir)
