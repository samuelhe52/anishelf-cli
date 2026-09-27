from __future__ import annotations

from enum import StrEnum

from anishelf_cli.models.common import AniShelfBaseModel
from anishelf_cli.models.identity import (
    LibraryIdentity,
    LibraryIdentityError,
    library_identity_from_fields,
    parse_library_identity,
)


class MetadataDepth(StrEnum):
    NONE = "none"
    SUMMARY = "summary"
    DETAILS = "details"
    FULL = "full"


class LibraryListSort(StrEnum):
    SAVED = "saved"
    UPDATED = "updated"
    TITLE = "title"
    SCORE = "score"
    STARTED = "started"
    FINISHED = "finished"
    TYPE = "type"
    WATCH_STATUS = "watch-status"
    AIR_DATE = "air-date"


class HumanOutputStyle(StrEnum):
    TABLE = "table"
    LIST = "list"


class LibraryWatchStatus(StrEnum):
    PLAN_TO_WATCH = "planToWatch"
    WATCHING = "watching"
    WATCHED = "watched"
    DROPPED = "dropped"


class LibraryEntryType(StrEnum):
    MOVIE = "movie"
    SERIES = "series"
    SEASON = "season"


class HydrationDepth(StrEnum):
    """Depths the metadata cache can be hydrated to (a subset of MetadataDepth)."""

    DETAILS = "details"
    FULL = "full"


class ExportFormat(StrEnum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"


class TMDbMetadataLanguage(StrEnum):
    EN = "en"
    JA = "ja"
    ZH = "zh"


class CallbackStrategy(StrEnum):
    MANUAL_PASTE = "manual-paste"
    LOOPBACK = "loopback"


class SecretBackend(StrEnum):
    SYSTEM = "system"
    PLAINTEXT_FILE = "plaintext-file"


class AppState(AniShelfBaseModel):
    json_output: bool = False
    verbose: bool = False


__all__ = [
    "AniShelfBaseModel",
    "AppState",
    "CallbackStrategy",
    "ExportFormat",
    "HumanOutputStyle",
    "HydrationDepth",
    "LibraryEntryType",
    "LibraryIdentity",
    "LibraryIdentityError",
    "LibraryListSort",
    "LibraryWatchStatus",
    "MetadataDepth",
    "SecretBackend",
    "TMDbMetadataLanguage",
    "library_identity_from_fields",
    "parse_library_identity",
]
