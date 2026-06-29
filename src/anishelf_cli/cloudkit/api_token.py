from __future__ import annotations

import os
from dataclasses import dataclass

from anishelf_cli.cloudkit.app_auth_transform import restore_transformed_hex

ANI_CLOUDKIT_API_TOKEN_ENV = "ANI_CLOUDKIT_API_TOKEN"
ANI_CLOUDKIT_API_TOKEN_VERSION_ENV = "ANI_CLOUDKIT_API_TOKEN_VERSION"

EMBEDDED_PUBLIC_TOKEN_VERSION = "2026-06-29"
_EMBEDDED_PUBLIC_TOKEN_TRANSFORM_KEY = "anishelf-cli-cloudkit"
_EMBEDDED_PUBLIC_TOKEN_TRANSFORMED_FRAGMENTS = (
    "39b8358b63d4cb8c",
    "2ec018ae00d3f789",
    "de579ded18779801",
    "53a912dbe5efde44",
)


@dataclass(frozen=True, slots=True)
class CloudKitAPIToken:
    value: str
    source: str
    version: str | None = None
    is_public: bool = False


class MissingCloudKitAPITokenError(RuntimeError):
    pass


def resolve_cloudkit_api_token() -> CloudKitAPIToken:
    if token := os.environ.get(ANI_CLOUDKIT_API_TOKEN_ENV):
        return CloudKitAPIToken(
            value=token,
            source="env",
            version=os.environ.get(ANI_CLOUDKIT_API_TOKEN_VERSION_ENV),
            is_public=False,
        )

    embedded_token = _embedded_public_token()
    if not embedded_token:
        raise MissingCloudKitAPITokenError(
            "Embedded CloudKit app auth is not configured in this build."
        )

    return CloudKitAPIToken(
        value=embedded_token,
        source="embedded-public",
        version=EMBEDDED_PUBLIC_TOKEN_VERSION,
        is_public=True,
    )


def _embedded_public_token() -> str:
    return restore_transformed_hex(
        "".join(_EMBEDDED_PUBLIC_TOKEN_TRANSFORMED_FRAGMENTS),
        key=_EMBEDDED_PUBLIC_TOKEN_TRANSFORM_KEY,
    )
