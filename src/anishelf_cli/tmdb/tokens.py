from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from anishelf_cli.models import ProfileConfig, TokenSourceKind
from anishelf_cli.secrets import (
    SecretStore,
    env_file_permission_warning,
    get_secret,
    read_env_file,
    tmdb_api_key_secret,
)


@dataclass(frozen=True, slots=True)
class TMDbAPIToken:
    value: str
    source_label: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


class MissingTMDbAPITokenError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConfiguredTMDbAPITokenProvider:
    profile_name: str
    profile: ProfileConfig
    store: SecretStore | None = None

    def resolve(self) -> TMDbAPIToken:
        source = self.profile.tmdb_token_source

        if source in (TokenSourceKind.AUTO, TokenSourceKind.ENV):
            for env_name in self.profile.tmdb_api_key_envs:
                token = os.environ.get(env_name)
                if token:
                    return TMDbAPIToken(value=token, source_label=f"env:{env_name}")

        if source in (TokenSourceKind.AUTO, TokenSourceKind.ENV_FILE) and self.profile.env_file:
            env_file_token = self._resolve_env_file(self.profile.env_file)
            if env_file_token:
                return env_file_token

        if source in (TokenSourceKind.AUTO, TokenSourceKind.KEYCHAIN):
            token = get_secret(tmdb_api_key_secret(self.profile_name), self.store)
            if token:
                return TMDbAPIToken(value=token, source_label=f"keychain:{self.profile_name}")

        raise MissingTMDbAPITokenError(
            "TMDb API key is not configured. Set ANI_TMDB_API_KEY, TMDB_API_KEY, "
            "configure an env file, or run `ani config set-tmdb-token`."
        )

    def _resolve_env_file(self, path: Path) -> TMDbAPIToken | None:
        if not path.exists():
            return None
        values = read_env_file(path)
        for env_name in self.profile.tmdb_api_key_envs:
            if token := values.get(env_name):
                warning = env_file_permission_warning(path)
                warnings = (warning,) if warning else ()
                return TMDbAPIToken(
                    value=token,
                    source_label=f"env-file:{path}:{env_name}",
                    warnings=warnings,
                )
        return None
