from __future__ import annotations

import binascii
import configparser
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import keyring
from keyring.errors import KeyringError, NoKeyringError
from keyrings.alt.file import PlaintextKeyring  # type: ignore[import-untyped]

from anishelf_cli import config
from anishelf_cli.models import SecretBackend


class SecretStorageUnavailableError(RuntimeError):
    """Raised when the OS secure credential backend is unavailable."""


_PLAINTEXT_FILE_UNAVAILABLE_MESSAGE = (
    "Plaintext secret storage is unavailable; plaintext keyring file is unreadable or invalid"
)
_PLAINTEXT_FILE_EXCEPTIONS = (
    binascii.Error,
    configparser.Error,
    KeyringError,
    OSError,
    UnicodeError,
)


class SecretStore(Protocol):
    def get_password(self, service: str, account: str) -> str | None: ...

    def set_password(self, service: str, account: str, password: str) -> None: ...

    def delete_password(self, service: str, account: str) -> None: ...


class _KeyringBackend(Protocol):
    priority: float

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class KeyringSecretStore:
    def _backend(self) -> _KeyringBackend:
        try:
            backend = keyring.get_keyring()
        except KeyringError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc

        return _system_keyring_backend(backend)

    def get_password(self, service: str, account: str) -> str | None:
        backend = self._backend()
        try:
            return backend.get_password(service, account)
        except NotImplementedError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except NoKeyringError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except KeyringError as exc:
            raise SecretStorageUnavailableError(str(exc)) from exc

    def set_password(self, service: str, account: str, password: str) -> None:
        backend = self._backend()
        try:
            backend.set_password(service, account, password)
        except NotImplementedError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except NoKeyringError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except KeyringError as exc:
            raise SecretStorageUnavailableError(str(exc)) from exc

    def delete_password(self, service: str, account: str) -> None:
        backend = self._backend()
        try:
            if backend.get_password(service, account) is None:
                return
        except NotImplementedError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except NoKeyringError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except KeyringError as exc:
            raise SecretStorageUnavailableError(str(exc)) from exc

        try:
            backend.delete_password(service, account)
        except keyring.errors.PasswordDeleteError as exc:
            if _is_missing_password_delete_error(exc):
                return
            raise SecretStorageUnavailableError(str(exc)) from exc
        except NotImplementedError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except NoKeyringError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except KeyringError as exc:
            raise SecretStorageUnavailableError(str(exc)) from exc


class _AniShelfPlaintextKeyring(PlaintextKeyring):  # type: ignore[misc]
    @property
    def file_path(self) -> str:
        return str(config.plaintext_keyring_file())


class PlaintextFileSecretStore:
    def __init__(self) -> None:
        try:
            config.plaintext_keyring_file().parent.mkdir(parents=True, exist_ok=True)
            self._keyring = _AniShelfPlaintextKeyring()
        except _PLAINTEXT_FILE_EXCEPTIONS as exc:
            raise _plaintext_file_unavailable_error() from exc

    def get_password(self, service: str, account: str) -> str | None:
        try:
            return cast(str | None, self._keyring.get_password(service, account))
        except _PLAINTEXT_FILE_EXCEPTIONS as exc:
            raise _plaintext_file_unavailable_error() from exc

    def set_password(self, service: str, account: str, password: str) -> None:
        try:
            self._keyring.set_password(service, account, password)
        except _PLAINTEXT_FILE_EXCEPTIONS as exc:
            raise _plaintext_file_unavailable_error() from exc

    def delete_password(self, service: str, account: str) -> None:
        try:
            self._keyring.delete_password(service, account)
        except keyring.errors.PasswordDeleteError as exc:
            if _is_missing_password_delete_error(exc):
                return
            raise SecretStorageUnavailableError(str(exc)) from exc
        except _PLAINTEXT_FILE_EXCEPTIONS as exc:
            raise _plaintext_file_unavailable_error() from exc


def _plaintext_file_unavailable_error() -> SecretStorageUnavailableError:
    return SecretStorageUnavailableError(_PLAINTEXT_FILE_UNAVAILABLE_MESSAGE)


def _is_missing_password_delete_error(exc: keyring.errors.PasswordDeleteError) -> bool:
    cause = exc.__cause__
    if cause is not None:
        cause_message = " ".join(str(arg).lower() for arg in cause.args)
        if "-25300" in cause_message and "item not found" in cause_message:
            return True

    message = str(exc).lower()
    return (
        ("-25300" in message and "item not found" in message)
        or "no such password" in message
        or "password not found" in message
    )


def _system_keyring_backend(backend: object) -> _KeyringBackend:
    candidates = _effective_keyring_backends(backend)
    for candidate in candidates:
        if not _is_keyrings_alt_backend(candidate) and getattr(candidate, "priority", 0) > 0:
            return cast(_KeyringBackend, candidate)

    if any(_is_keyrings_alt_backend(candidate) for candidate in candidates):
        raise SecretStorageUnavailableError(
            "Secure credential backend is unavailable; keyring resolved to "
            "a non-native keyrings.alt backend"
        )
    raise SecretStorageUnavailableError("Secure credential backend is unavailable")


def _effective_keyring_backends(backend: object) -> tuple[object, ...]:
    if not _is_chainer_backend(backend):
        return (backend,)

    backends = tuple(getattr(backend, "backends", ()))
    flattened: list[object] = []
    for candidate in backends:
        flattened.extend(_effective_keyring_backends(candidate))
    return tuple(flattened)


def _is_chainer_backend(backend: object) -> bool:
    return backend.__class__.__module__.startswith("keyring.backends.chainer")


def _is_keyrings_alt_backend(backend: object) -> bool:
    backend_module = backend.__class__.__module__
    return backend_module.startswith("keyrings.alt.")


@dataclass(frozen=True, slots=True)
class SecretDescriptor:
    service: str
    account: str
    label: str


def cloudkit_web_auth_token_secret() -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_CLOUDKIT_WEB_AUTH_TOKEN,
        account=config.KEYCHAIN_ACCOUNT,
        label="CloudKit web auth token",
    )


def tmdb_api_key_secret() -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_TMDB_API_KEY,
        account=config.KEYCHAIN_ACCOUNT,
        label="TMDb API key",
    )


def default_secret_store() -> SecretStore:
    try:
        backend = config.load_secret_defaults().backend
    except config.UserConfigError as exc:
        raise SecretStorageUnavailableError(str(exc)) from exc
    return secret_store_for_backend(backend)


def secret_store_for_backend(backend: SecretBackend) -> SecretStore:
    if backend is SecretBackend.SYSTEM:
        return KeyringSecretStore()
    if backend is SecretBackend.PLAINTEXT_FILE:
        return PlaintextFileSecretStore()
    raise ValueError(f"Unsupported secret backend {backend!r}")


def configured_secret_backend() -> SecretBackend:
    try:
        return config.load_secret_defaults().backend
    except config.UserConfigError as exc:
        raise SecretStorageUnavailableError(str(exc)) from exc


def secret_backend_storage_label(
    backend: SecretBackend | None = None,
) -> Literal["system", "plaintext-file"]:
    resolved = backend if backend is not None else configured_secret_backend()
    if resolved is SecretBackend.SYSTEM:
        return "system"
    if resolved is SecretBackend.PLAINTEXT_FILE:
        return "plaintext-file"
    raise ValueError(f"Unsupported secret backend {resolved!r}")


def get_secret(descriptor: SecretDescriptor, store: SecretStore | None = None) -> str | None:
    backend = store or default_secret_store()
    return backend.get_password(descriptor.service, descriptor.account)


def set_secret(
    descriptor: SecretDescriptor,
    value: str,
    store: SecretStore | None = None,
) -> None:
    if not value:
        raise ValueError(f"{descriptor.label} cannot be empty")
    backend = store or default_secret_store()
    backend.set_password(descriptor.service, descriptor.account, value)


def delete_secret(descriptor: SecretDescriptor, store: SecretStore | None = None) -> None:
    backend = store or default_secret_store()
    backend.delete_password(descriptor.service, descriptor.account)


def store_cloudkit_web_auth_token(
    token: str,
    store: SecretStore | None = None,
) -> None:
    set_secret(cloudkit_web_auth_token_secret(), token, store)


def load_cloudkit_web_auth_token(
    store: SecretStore | None = None,
) -> str | None:
    return get_secret(cloudkit_web_auth_token_secret(), store)


def delete_cloudkit_web_auth_token(store: SecretStore | None = None) -> None:
    delete_secret(cloudkit_web_auth_token_secret(), store)
