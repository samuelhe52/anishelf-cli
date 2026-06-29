from __future__ import annotations

from dataclasses import dataclass

from anishelf_cli.config import DEFAULT_CONTAINER


@dataclass(frozen=True, slots=True)
class SchemaSnapshot:
    container: str = DEFAULT_CONTAINER
    zone: str = "AniShelfLibrary"
    entry_record_type: str = "LibraryEntry"
    settings_record_type: str = "LibrarySettings"
    settings_record_name: str = "userDefaults"

