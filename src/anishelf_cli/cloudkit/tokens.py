from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from anishelf_cli.models import CloudKitTokenSourceKind, ProfileConfig
from anishelf_cli.secrets import SecretStore, cloudkit_api_token_secret, get_secret


@dataclass(frozen=True, slots=True)
class CloudKitAPIToken:
    value: str
    source_label: str
    token_version: str | None = None


class CloudKitAPITokenProvider(Protocol):
    def resolve(self) -> CloudKitAPIToken:
        """Return the token value plus non-secret source metadata."""


class MissingCloudKitAPITokenError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConfiguredCloudKitAPITokenProvider:
    profile_name: str
    profile: ProfileConfig
    store: SecretStore | None = None

    def resolve(self) -> CloudKitAPIToken:
        source = self.profile.cloudkit_token_source

        if source in (CloudKitTokenSourceKind.AUTO, CloudKitTokenSourceKind.ENV):
            token = os.environ.get(self.profile.cloudkit_api_token_env)
            if token:
                return CloudKitAPIToken(
                    value=token,
                    source_label=f"env:{self.profile.cloudkit_api_token_env}",
                    token_version=os.environ.get(self.profile.cloudkit_api_token_version_env),
                )

        if source in (CloudKitTokenSourceKind.AUTO, CloudKitTokenSourceKind.KEYCHAIN):
            token = get_secret(cloudkit_api_token_secret(self.profile_name), self.store)
            if token:
                return CloudKitAPIToken(
                    value=token,
                    source_label=f"keychain:{self.profile_name}",
                )

        raise MissingCloudKitAPITokenError(
            "CloudKit API token is not configured. Set "
            f"{self.profile.cloudkit_api_token_env} or run `ani config set-cloudkit-token`."
        )
