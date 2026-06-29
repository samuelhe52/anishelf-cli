from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from anishelf_cli.config import profile_config_path
from anishelf_cli.models import ProfileConfig


def load_profile(profile: str) -> ProfileConfig:
    path = profile_config_path(profile)
    if not path.exists():
        return ProfileConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return ProfileConfig.model_validate(data)


def update_profile(profile: str, updates: dict[str, Any]) -> tuple[ProfileConfig, Path]:
    config = load_profile(profile)
    non_empty_updates = {key: value for key, value in updates.items() if value is not None}
    next_config = ProfileConfig.model_validate(config.model_dump() | non_empty_updates)
    return next_config, save_profile(profile, next_config)


def save_profile(profile: str, config: ProfileConfig) -> Path:
    path = profile_config_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    return path
