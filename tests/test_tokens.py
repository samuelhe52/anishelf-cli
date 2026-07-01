from __future__ import annotations

import pytest
from keyring.errors import PasswordDeleteError, PasswordSetError
from tests.support import MemorySecretStore

from anishelf_cli import secrets as secrets_module
from anishelf_cli.cloudkit import api_token as cloudkit_api_token_module
from anishelf_cli.cloudkit.api_token import (
    EMBEDDED_PUBLIC_TOKEN_VERSION,
    MissingCloudKitAPITokenError,
    resolve_cloudkit_api_token,
)
from anishelf_cli.cloudkit.app_auth_transform import (
    restore_transformed_hex,
    transform_hex,
)
from anishelf_cli.secrets import (
    KeyringSecretStore,
    SecretStorageUnavailableError,
    store_cloudkit_web_auth_token,
    tmdb_api_key_secret,
)
from anishelf_cli.tmdb.tokens import resolve_tmdb_api_token


class FailingSecretStore:
    def get_password(self, service: str, account: str) -> str | None:
        raise SecretStorageUnavailableError("no secure backend")

    def set_password(self, service: str, account: str, password: str) -> None:
        raise SecretStorageUnavailableError("no secure backend")

    def delete_password(self, service: str, account: str) -> None:
        raise SecretStorageUnavailableError("no secure backend")


class AvailableKeyring:
    priority = 1


def test_cloudkit_api_token_prefers_process_env_over_embedded(monkeypatch) -> None:
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "env-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN_VERSION", "v1")

    token = resolve_cloudkit_api_token()

    assert token.value == "env-token"
    assert token.source == "env"
    assert token.version == "v1"
    assert token.is_public is False


def test_cloudkit_api_token_uses_embedded_when_env_absent(monkeypatch) -> None:
    monkeypatch.delenv("ANI_CLOUDKIT_API_TOKEN", raising=False)
    monkeypatch.delenv("ANI_CLOUDKIT_API_TOKEN_VERSION", raising=False)

    token = resolve_cloudkit_api_token()

    assert token.value
    assert token.value == cloudkit_api_token_module._embedded_public_token()
    assert token.source == "embedded-public"
    assert token.version == EMBEDDED_PUBLIC_TOKEN_VERSION
    assert token.is_public is True


def test_cloudkit_app_auth_transform_round_trips_hex_fixture() -> None:
    fixture = "0123456789abcdef"
    transformed = transform_hex(fixture, key="test-key")

    assert transformed != fixture
    assert restore_transformed_hex(transformed, key="test-key") == fixture


def test_cloudkit_api_token_reports_clear_build_error_without_env_or_embedded(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANI_CLOUDKIT_API_TOKEN", raising=False)
    monkeypatch.setattr(
        cloudkit_api_token_module,
        "_EMBEDDED_PUBLIC_TOKEN_TRANSFORMED_FRAGMENTS",
        (),
    )

    with pytest.raises(MissingCloudKitAPITokenError, match="not configured in this build"):
        resolve_cloudkit_api_token()


def test_tmdb_api_key_prefers_env_then_keychain(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = tmdb_api_key_secret()
    store.set_password(descriptor.service, descriptor.account, "keychain-token")

    keychain_token = resolve_tmdb_api_token(store)
    assert keychain_token.value == "keychain-token"
    assert keychain_token.source_label == "keychain"

    monkeypatch.setenv("ANI_TMDB_API_KEY", "process-token")
    process_token = resolve_tmdb_api_token(store)
    assert process_token.value == "process-token"
    assert process_token.source_label == "env:ANI_TMDB_API_KEY"


def test_cloudkit_web_auth_storage_fails_closed_without_secure_backend() -> None:
    with pytest.raises(SecretStorageUnavailableError):
        store_cloudkit_web_auth_token("web-auth-token", FailingSecretStore())


def test_keyring_delete_password_ignores_missing_macos_item(monkeypatch) -> None:
    class MissingItemError(Exception):
        pass

    monkeypatch.setattr(secrets_module.keyring, "get_password", lambda service, account: "token")

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


def test_keyring_delete_password_ignores_absent_password_without_delete(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("service")

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AvailableKeyring())
    monkeypatch.setattr(secrets_module.keyring, "get_password", lambda service, account: None)
    monkeypatch.setattr(secrets_module.keyring, "delete_password", delete_password)

    KeyringSecretStore().delete_password("service", "account")


def test_keyring_delete_password_ignores_secretservice_missing_message(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("No such password!")

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AvailableKeyring())
    monkeypatch.setattr(secrets_module.keyring, "get_password", lambda service, account: "token")
    monkeypatch.setattr(secrets_module.keyring, "delete_password", delete_password)

    KeyringSecretStore().delete_password("service", "account")


def test_keyring_delete_password_reports_real_delete_failure(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("Can't delete password in keychain: (-128, 'denied')")

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AvailableKeyring())
    monkeypatch.setattr(secrets_module.keyring, "get_password", lambda service, account: "token")
    monkeypatch.setattr(secrets_module.keyring, "delete_password", delete_password)

    with pytest.raises(SecretStorageUnavailableError, match="Can't delete password in keychain"):
        KeyringSecretStore().delete_password("service", "account")


def test_keyring_set_password_reports_real_write_failure(monkeypatch) -> None:
    def set_password(service: str, account: str, password: str) -> None:
        _ = service, account, password
        raise PasswordSetError("Can't store password on keychain: (-25244, 'Invalid attempt')")

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AvailableKeyring())
    monkeypatch.setattr(secrets_module.keyring, "set_password", set_password)

    with pytest.raises(SecretStorageUnavailableError, match="Can't store password on keychain"):
        KeyringSecretStore().set_password("service", "account", "token")
