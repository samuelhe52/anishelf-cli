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


class CallbackStrategy(StrEnum):
    MANUAL_PASTE = "manual-paste"
    LOOPBACK = "loopback"


class AppState(AniShelfBaseModel):
    json_output: bool = False
    verbose: bool = False


__all__ = [
    "AniShelfBaseModel",
    "AppState",
    "CallbackStrategy",
    "LibraryIdentity",
    "LibraryIdentityError",
    "LibraryListSort",
    "MetadataDepth",
    "library_identity_from_fields",
    "parse_library_identity",
]
