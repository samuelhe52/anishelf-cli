from __future__ import annotations

from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "anishelf-cli"
APP_AUTHOR = "samuelhe"

DEFAULT_CONTAINER = "iCloud.com.samuelhe.MyAnimeList"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_DATABASE = "private"
DEFAULT_PROFILE = "default"
DEFAULT_ANISHELF_SOURCE = Path("~/projects/AniShelf").expanduser()

KEYCHAIN_SERVICE_CLOUDKIT_API_TOKEN = "anishelf-cli.cloudkit-api-token"
KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN = "anishelf-cli.cloudkit-web-auth-token"
KEYCHAIN_SERVICE_TMDB_READ_ACCESS_TOKEN = "anishelf-cli.tmdb-read-access-token"
KEYCHAIN_SERVICE_TMDB_API_KEY = "anishelf-cli.tmdb-api-key"

dirs = PlatformDirs(appname=APP_NAME, appauthor=APP_AUTHOR)


def config_dir() -> Path:
    return Path(dirs.user_config_dir)


def cache_dir() -> Path:
    return Path(dirs.user_cache_dir)


def data_dir() -> Path:
    return Path(dirs.user_data_dir)


def profile_config_path(profile: str) -> Path:
    return config_dir() / "profiles" / f"{profile}.json"


def profile_lock_path(profile: str) -> Path:
    return data_dir() / "locks" / f"{profile}.lock"

