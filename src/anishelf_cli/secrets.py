from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError, NoKeyringError

from anishelf_cli import config


class SecretStorageUnavailableError(RuntimeError):
    """Raised when the OS secure credential backend is unavailable."""


class SecretStore(Protocol):
    def get_password(self, service: str, account: str) -> str | None: ...

    def set_password(self, service: str, account: str, password: str) -> None: ...

    def delete_password(self, service: str, account: str) -> None: ...


class KeyringSecretStore:
    def _ensure_available(self) -> None:
        try:
            backend = keyring.get_keyring()
            priority = getattr(backend, "priority", 0)
        except KeyringError as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc

        if priority <= 0:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable")

    def get_password(self, service: str, account: str) -> str | None:
        self._ensure_available()
        try:
            return keyring.get_password(service, account)
        except (KeyringError, NoKeyringError) as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc

    def set_password(self, service: str, account: str, password: str) -> None:
        self._ensure_available()
        try:
            keyring.set_password(service, account, password)
        except (KeyringError, NoKeyringError) as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc

    def delete_password(self, service: str, account: str) -> None:
        self._ensure_available()
        try:
            keyring.delete_password(service, account)
        except keyring.errors.PasswordDeleteError as exc:
            if _is_missing_password_delete_error(exc):
                return
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc
        except (KeyringError, NoKeyringError) as exc:
            raise SecretStorageUnavailableError("Secure credential backend is unavailable") from exc


def _is_missing_password_delete_error(exc: keyring.errors.PasswordDeleteError) -> bool:
    cause = exc.__cause__
    if cause is not None:
        cause_message = " ".join(str(arg).lower() for arg in cause.args)
        if "-25300" in cause_message and "item not found" in cause_message:
            return True

    message = str(exc).lower()
    return "-25300" in message and "item not found" in message


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


def tmdb_api_key_secret(profile: str) -> SecretDescriptor:
    return SecretDescriptor(
        service=config.KEYCHAIN_SERVICE_TMDB_API_KEY,
        account=profile,
        label="TMDb API key",
    )


def default_secret_store() -> SecretStore:
    return KeyringSecretStore()


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


def env_file_permission_warning(path: Path) -> str | None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None

    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return f"Env file {path} is readable or writable by group/other users."
    return None


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def store_cloudkit_web_auth_token(
    profile: str,
    token: str,
    store: SecretStore | None = None,
) -> None:
    set_secret(cloudkit_web_auth_token_secret(profile), token, store)


def load_cloudkit_web_auth_token(
    profile: str,
    store: SecretStore | None = None,
) -> str | None:
    return get_secret(cloudkit_web_auth_token_secret(profile), store)


def delete_cloudkit_web_auth_token(profile: str, store: SecretStore | None = None) -> None:
    delete_secret(cloudkit_web_auth_token_secret(profile), store)
