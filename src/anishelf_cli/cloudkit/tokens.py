from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CloudKitAPIToken:
    value: str
    source_label: str
    token_version: str | None = None


class CloudKitAPITokenProvider(Protocol):
    def resolve(self) -> CloudKitAPIToken:
        """Return the token value plus non-secret source metadata."""

