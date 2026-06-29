from __future__ import annotations

import os

import pytest
from keyring.errors import PasswordDeleteError

from anishelf_cli import secrets as secrets_module
from anishelf_cli.cloudkit.tokens import ConfiguredCloudKitAPITokenProvider
from anishelf_cli.models import ProfileConfig
from anishelf_cli.secrets import (
    KeyringSecretStore,
    SecretStorageUnavailableError,
    cloudkit_api_token_secret,
    store_cloudkit_web_auth_token,
    tmdb_api_key_secret,
)
from anishelf_cli.tmdb.tokens import ConfiguredTMDbAPITokenProvider


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.values[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


class FailingSecretStore:
    def get_password(self, service: str, account: str) -> str | None:
        raise SecretStorageUnavailableError("no secure backend")

    def set_password(self, service: str, account: str, password: str) -> None:
        raise SecretStorageUnavailableError("no secure backend")

    def delete_password(self, service: str, account: str) -> None:
        raise SecretStorageUnavailableError("no secure backend")


class AvailableKeyring:
    priority = 1


def test_cloudkit_api_token_prefers_process_env_over_keychain(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_api_token_secret("default")
    store.set_password(descriptor.service, descriptor.account, "keychain-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "env-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN_VERSION", "v1")

    token = ConfiguredCloudKitAPITokenProvider("default", ProfileConfig(), store).resolve()

    assert token.value == "env-token"
    assert token.source_label == "env:ANI_CLOUDKIT_API_TOKEN"
    assert token.token_version == "v1"
    assert store.values[(descriptor.service, descriptor.account)] == "keychain-token"


def test_tmdb_token_prefers_env_then_env_file_then_keychain(tmp_path, monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = tmdb_api_key_secret("default")
    store.set_password(descriptor.service, descriptor.account, "keychain-token")
    env_file = tmp_path / "tokens.env"
    env_file.write_text("TMDB_API_KEY=env-file-token\n", encoding="utf-8")
    profile = ProfileConfig(env_file=env_file)

    env_file_token = ConfiguredTMDbAPITokenProvider("default", profile, store).resolve()
    assert env_file_token.value == "env-file-token"
    assert env_file_token.source_label.endswith(":TMDB_API_KEY")

    monkeypatch.setenv("ANI_TMDB_API_KEY", "process-token")
    process_token = ConfiguredTMDbAPITokenProvider("default", profile, store).resolve()
    assert process_token.value == "process-token"
    assert process_token.source_label == "env:ANI_TMDB_API_KEY"


def test_tmdb_env_file_reports_broad_permissions(tmp_path) -> None:
    env_file = tmp_path / "tokens.env"
    env_file.write_text("ANI_TMDB_API_KEY=env-file-token\n", encoding="utf-8")
    os.chmod(env_file, 0o644)
    profile = ProfileConfig(env_file=env_file)

    token = ConfiguredTMDbAPITokenProvider("default", profile, MemorySecretStore()).resolve()

    assert token.value == "env-file-token"
    assert token.warnings


def test_cloudkit_web_auth_storage_fails_closed_without_secure_backend() -> None:
    with pytest.raises(SecretStorageUnavailableError):
        store_cloudkit_web_auth_token("default", "web-auth-token", FailingSecretStore())


def test_keyring_delete_password_ignores_missing_macos_item(monkeypatch) -> None:
    class MissingItemError(Exception):
        pass

    def delete_password(service: str, account: str) -> None:
        _ = service, account
        try:
            raise MissingItemError(-25300, "Item not found")
        except MissingItemError as exc:
            raise PasswordDeleteError(
                "Can't delete password in keychain: (-25300, 'Item not found')"
            ) from exc

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AvailableKeyring())
    monkeypatch.setattr(secrets_module.keyring, "delete_password", delete_password)

    KeyringSecretStore().delete_password("service", "account")


def test_keyring_delete_password_reports_real_delete_failure(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("Can't delete password in keychain: (-128, 'denied')")

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AvailableKeyring())
    monkeypatch.setattr(secrets_module.keyring, "delete_password", delete_password)

    with pytest.raises(SecretStorageUnavailableError):
        KeyringSecretStore().delete_password("service", "account")
