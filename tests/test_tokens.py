from __future__ import annotations

import pytest
from keyring.errors import PasswordDeleteError, PasswordSetError

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
    PlaintextFileSecretStore,
    SecretStorageUnavailableError,
    store_cloudkit_web_auth_token,
    tmdb_api_key_secret,
)
from anishelf_cli.tmdb.tokens import resolve_tmdb_api_token
from tests.support import MemorySecretStore, isolate_paths


class FailingSecretStore:
    def get_password(self, service: str, account: str) -> str | None:
        raise SecretStorageUnavailableError("no secure backend")

    def set_password(self, service: str, account: str, password: str) -> None:
        raise SecretStorageUnavailableError("no secure backend")

    def delete_password(self, service: str, account: str) -> None:
        raise SecretStorageUnavailableError("no secure backend")


class AvailableKeyring:
    priority = 1

    def __init__(
        self,
        *,
        password: str | None = "token",
        set_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.password = password
        self.set_error = set_error
        self.delete_error = delete_error
        self.calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, account: str) -> str | None:
        _ = service, account
        return self.password

    def set_password(self, service: str, account: str, password: str) -> None:
        if self.set_error is not None:
            raise self.set_error
        self.calls.append((service, account, password))
        self.password = password

    def delete_password(self, service: str, account: str) -> None:
        _ = service, account
        if self.delete_error is not None:
            raise self.delete_error
        self.password = None


class AltKeyring(AvailableKeyring):
    __module__ = "keyrings.alt.file"
    priority = 1


class NativeKeyring(AvailableKeyring):
    __module__ = "keyring.backends.macOS"
    priority = 5


class AltOnlyChainerBackend:
    __module__ = "keyring.backends.chainer"
    priority = 10
    backends = (AltKeyring(),)


class MixedChainerBackend:
    __module__ = "keyring.backends.chainer"
    priority = 10
    backends = (NativeKeyring(), AltKeyring())


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
    monkeypatch.setenv("TMDB_API_KEY", "env-token")
    store = MemorySecretStore()
    store.set_password(tmdb_api_key_secret().service, tmdb_api_key_secret().account, "stored-token")

    resolved = resolve_tmdb_api_token(store=store)

    assert resolved.value == "env-token"
    assert resolved.source_label == "env:TMDB_API_KEY"


def test_tmdb_api_key_falls_back_to_secret_store(monkeypatch) -> None:
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    store = MemorySecretStore()
    store.set_password(tmdb_api_key_secret().service, tmdb_api_key_secret().account, "stored-token")

    resolved = resolve_tmdb_api_token(store=store)

    assert resolved.value == "stored-token"
    assert resolved.source_label == "secret-storage"


def test_tmdb_api_key_raises_when_store_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("TMDB_API_KEY", raising=False)

    with pytest.raises(SecretStorageUnavailableError, match="no secure backend"):
        resolve_tmdb_api_token(store=FailingSecretStore())


def test_store_cloudkit_web_auth_token_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        store_cloudkit_web_auth_token("", store=MemorySecretStore())


def test_system_store_rejects_keyrings_alt_backend(monkeypatch) -> None:
    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: AltOnlyChainerBackend())

    with pytest.raises(
        SecretStorageUnavailableError,
        match=(
            r"Secure credential backend is unavailable; keyring resolved to "
            r"a non-native keyrings\.alt backend"
        ),
    ):
        KeyringSecretStore().get_password("service", "account")


def test_keyring_secret_store_does_not_read_alt_member_in_mixed_chainer(monkeypatch) -> None:
    native = NativeKeyring(password=None)
    alt = AltKeyring(password="plaintext-token")

    class MixedBackend:
        __module__ = "keyring.backends.chainer"
        priority = 10
        backends = (native, alt)

    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: MixedBackend())

    assert KeyringSecretStore().get_password("service", "account") is None


def test_plaintext_file_secret_store_normalizes_invalid_file_errors(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    secret_file = secrets_module.config.plaintext_keyring_file()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text("tmdb-secret-token\n")
    store = PlaintextFileSecretStore()

    for operation in (
        lambda: store.get_password("service", "account"),
        lambda: store.delete_password("service", "account"),
    ):
        with pytest.raises(SecretStorageUnavailableError) as exc_info:
            operation()

        message = str(exc_info.value)
        assert "Plaintext secret storage is unavailable" in message
        assert "tmdb-secret-token" not in message


def test_plaintext_file_secret_store_normalizes_write_errors(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)
    store = PlaintextFileSecretStore()

    def set_password(service: str, account: str, password: str) -> None:
        _ = service, account, password
        raise OSError("cannot write tmdb-secret-token")

    monkeypatch.setattr(store._keyring, "set_password", set_password)

    with pytest.raises(SecretStorageUnavailableError) as exc_info:
        store.set_password("service", "account", "token")

    message = str(exc_info.value)
    assert "Plaintext secret storage is unavailable" in message
    assert "tmdb-secret-token" not in message


def test_plaintext_file_secret_store_normalizes_init_errors(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)
    (tmp_path / "data").write_text("not-a-directory\n")

    with pytest.raises(SecretStorageUnavailableError) as exc_info:
        PlaintextFileSecretStore()

    assert "Plaintext secret storage is unavailable" in str(exc_info.value)


def test_plaintext_file_secret_store_ignores_missing_password(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)
    store = PlaintextFileSecretStore()

    store.delete_password("service", "account")


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

    backend = AvailableKeyring()
    backend.delete_password = delete_password  # type: ignore[method-assign]
    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: backend)

    KeyringSecretStore().delete_password("service", "account")


def test_keyring_delete_password_ignores_absent_password_without_delete(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("service")

    backend = AvailableKeyring(password=None)
    backend.delete_password = delete_password  # type: ignore[method-assign]
    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: backend)

    KeyringSecretStore().delete_password("service", "account")


def test_keyring_delete_password_ignores_secretservice_missing_message(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("No such password!")

    backend = AvailableKeyring()
    backend.delete_password = delete_password  # type: ignore[method-assign]
    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: backend)

    KeyringSecretStore().delete_password("service", "account")


def test_keyring_delete_password_reports_real_delete_failure(monkeypatch) -> None:
    def delete_password(service: str, account: str) -> None:
        _ = service, account
        raise PasswordDeleteError("Can't delete password in keychain: (-128, 'denied')")

    backend = AvailableKeyring()
    backend.delete_password = delete_password  # type: ignore[method-assign]
    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStorageUnavailableError, match="Can't delete password in keychain"):
        KeyringSecretStore().delete_password("service", "account")


def test_keyring_set_password_reports_real_write_failure(monkeypatch) -> None:
    backend = AvailableKeyring(
        set_error=PasswordSetError("Can't store password on keychain: (-25244, 'Invalid attempt')")
    )
    monkeypatch.setattr(secrets_module.keyring, "get_keyring", lambda: backend)

    with pytest.raises(SecretStorageUnavailableError, match="Can't store password on keychain"):
        KeyringSecretStore().set_password("service", "account", "token")
