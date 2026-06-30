from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


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


class AppState(BaseModel):
    json_output: bool = False
