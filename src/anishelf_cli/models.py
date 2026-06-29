from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class MetadataDepth(StrEnum):
    NONE = "none"
    SUMMARY = "summary"
    DETAILS = "details"
    FULL = "full"


class CallbackStrategy(StrEnum):
    MANUAL_PASTE = "manual-paste"
    LOOPBACK = "loopback"


class AppState(BaseModel):
    json_output: bool = False
    metadata_depth: MetadataDepth | None = None
