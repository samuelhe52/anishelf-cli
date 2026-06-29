from __future__ import annotations

import json
from pathlib import Path

from anishelf_cli.config import profile_config_path
from anishelf_cli.models import ProfileConfig


def load_profile(profile: str) -> ProfileConfig:
    path = profile_config_path(profile)
    if not path.exists():
        return ProfileConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return ProfileConfig.model_validate(data)


def save_profile(profile: str, config: ProfileConfig) -> Path:
    path = profile_config_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    return path

