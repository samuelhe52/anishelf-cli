from __future__ import annotations

import os
from dataclasses import dataclass, field

from anishelf_cli.config import DEFAULT_TMDB_API_KEY_ENVS
from anishelf_cli.secrets import (
    SecretStore,
    get_secret,
    tmdb_api_key_secret,
)


@dataclass(frozen=True, slots=True)
class TMDbAPIToken:
    value: str
    source_label: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


class MissingTMDbAPITokenError(RuntimeError):
    pass


def resolve_tmdb_api_token(store: SecretStore | None = None) -> TMDbAPIToken:
    for env_name in DEFAULT_TMDB_API_KEY_ENVS:
        token = os.environ.get(env_name)
        if token:
            return TMDbAPIToken(value=token, source_label=f"env:{env_name}")

    token = get_secret(tmdb_api_key_secret(), store)
    if token:
        return TMDbAPIToken(value=token, source_label="keychain")

    raise MissingTMDbAPITokenError(
        "TMDb API key is not configured. Set ANI_TMDB_API_KEY, TMDB_API_KEY, "
        "or run `ani config set-tmdb-api-key`."
    )
