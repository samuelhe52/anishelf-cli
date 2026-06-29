from __future__ import annotations

from dataclasses import dataclass

from anishelf_cli import config


@dataclass(frozen=True, slots=True)
class SecretDescriptor:
    service: str
    account: str
    label: str


def cloudkit_api_token_secret(profile: str) -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_CLOUDKIT_API_TOKEN,
        account=profile,
        label="CloudKit API token",
    )


def cloudkit_web_auth_token_secret(profile: str) -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN,
        account=profile,
        label="CloudKit web auth token",
    )


def tmdb_read_access_token_secret(profile: str) -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_TMDB_READ_ACCESS_TOKEN,
        account=profile,
        label="TMDb read access token",
    )


def tmdb_api_key_secret(profile: str) -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_TMDB_API_KEY,
        account=profile,
        label="TMDb API key",
    )

