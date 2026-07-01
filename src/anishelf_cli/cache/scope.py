from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from anishelf_cli import config
from anishelf_cli.cache import schema
from anishelf_cli.cloudkit.executor import ANI_SHELF_LIBRARY_ZONE_NAME


@dataclass(frozen=True, slots=True)
class LibraryCacheScope:
    container: str
    environment: str
    database: str
    zone: str
    user_record_name: str

    @classmethod
    def default_for_user(cls, user_record_name: str) -> LibraryCacheScope:
        return cls(
            container=config.DEFAULT_CONTAINER,
            environment=config.DEFAULT_ENVIRONMENT,
            database=config.DEFAULT_DATABASE,
            zone=ANI_SHELF_LIBRARY_ZONE_NAME,
            user_record_name=user_record_name,
        )

    def key_payload(self) -> dict[str, str]:
        return {
            "container": self.container,
            "environment": self.environment,
            "database": self.database,
            "zone": self.zone,
            "user_record_name": self.user_record_name,
        }

    def cache_key(self) -> str:
        encoded = json.dumps(self.key_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


def write_scope_metadata(db: sqlite3.Connection, cache_scope: LibraryCacheScope) -> None:
    for key, value in cache_scope.key_payload().items():
        schema.write_meta(db, f"scope.{key}", value)


def scope_from_existing_database(path: Path) -> LibraryCacheScope | None:
    try:
        db = sqlite3.connect(path)
    except sqlite3.Error:
        return None
    db.row_factory = sqlite3.Row
    try:
        with db:
            values = {
                key: schema.read_meta(db, f"scope.{key}")
                for key in ("container", "environment", "database", "zone", "user_record_name")
            }
    except sqlite3.Error:
        return None
    finally:
        db.close()
    if not all(values.values()):
        return None
    return LibraryCacheScope(
        container=str(values["container"]),
        environment=str(values["environment"]),
        database=str(values["database"]),
        zone=str(values["zone"]),
        user_record_name=str(values["user_record_name"]),
    )
