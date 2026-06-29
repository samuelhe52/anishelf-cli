from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from filelock import FileLock

from anishelf_cli import config
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken, resolve_cloudkit_api_token
from anishelf_cli.cloudkit.auth import database_endpoint_url, successor_web_auth_token
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.secrets import (
    SecretStorageUnavailableError,
    SecretStore,
    delete_cloudkit_web_auth_token,
    load_cloudkit_web_auth_token,
    store_cloudkit_web_auth_token,
)

DEFAULT_PROFILE_ID = "default"

API_TOKEN_PARAM = "ckAPIToken"
WEB_AUTH_TOKEN_PARAM = "ckWebAuthToken"
AUTH_FAILURE_CODES = {
    "AUTHENTICATION_FAILED",
    "AUTHENTICATION_REQUIRED",
}

TokenResolver = Callable[[], CloudKitAPIToken]
LockFactory = Callable[[Path], AbstractContextManager[Any]]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_record_name: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None

    @property
    def display_name(self) -> str | None:
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) if parts else None

    def to_json_payload(self) -> dict[str, object]:
        return {
            "status": "authenticated",
            "user": {
                "user_record_name": self.user_record_name,
                "first_name": self.first_name,
                "last_name": self.last_name,
                "email": self.email,
            },
        }


class CloudKitWhoamiError(RuntimeError):
    def __init__(self, message: str, *, redactor: SecretRedactor | None = None) -> None:
        super().__init__(message)
        self.redactor = redactor or SecretRedactor()


class CloudKitNotLoggedInError(CloudKitWhoamiError):
    pass


class CloudKitAuthenticationFailedError(CloudKitWhoamiError):
    pass


class CloudKitRequestFailedError(CloudKitWhoamiError):
    pass


class CloudKitUnexpectedResponseError(CloudKitWhoamiError):
    pass


@dataclass(slots=True)
class CloudKitExecutor:
    client: httpx.Client
    api_token_resolver: TokenResolver = resolve_cloudkit_api_token
    secret_store: SecretStore | None = None
    profile_id: str = DEFAULT_PROFILE_ID
    lock_factory: LockFactory | None = None
    lock_timeout_seconds: float = -1.0

    def get_current_user(self) -> CurrentUser:
        redactor = SecretRedactor()
        api_token = self.api_token_resolver()
        redactor.register(api_token.value, "cloudkit-api-token")

        with self._token_lock():
            web_auth_token = self._load_web_auth_token(redactor)
            response = self._request_current_user(api_token, web_auth_token, redactor)
            payload = self._parse_response(response, redactor)

            successor_token = successor_web_auth_token(payload)
            redactor.register(successor_token, "cloudkit-successor-web-auth-token")

            if _is_authentication_failure(response, payload):
                self._clear_web_auth_token_after_auth_failure(redactor)
                raise CloudKitAuthenticationFailedError(
                    "CloudKit authentication failed. Cleared stored login; run `ani auth login`.",
                    redactor=redactor,
                )

            if response.is_error:
                raise CloudKitRequestFailedError(
                    _cloudkit_failure_message("CloudKit whoami request failed", response, payload),
                    redactor=redactor,
                )

            if successor_token:
                self._store_successor_web_auth_token(successor_token, redactor)

            return _current_user_from_payload(payload, redactor)

    def lock_path(self) -> Path:
        lock_dir = config.data_dir() / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        return lock_dir / f"cloudkit-web-auth-token.{_safe_lock_name(self.profile_id)}.lock"

    def _token_lock(self) -> AbstractContextManager[Any]:
        lock_path = self.lock_path()
        if self.lock_factory is not None:
            return self.lock_factory(lock_path)
        return FileLock(str(lock_path), timeout=self.lock_timeout_seconds)

    def _load_web_auth_token(self, redactor: SecretRedactor) -> str:
        try:
            web_auth_token = load_cloudkit_web_auth_token(self.secret_store)
        except SecretStorageUnavailableError as exc:
            raise CloudKitRequestFailedError(
                "Secure credential backend is unavailable.",
                redactor=redactor,
            ) from exc

        redactor.register(web_auth_token, "cloudkit-web-auth-token")
        if not web_auth_token:
            raise CloudKitNotLoggedInError(
                "Not logged in to CloudKit. Run `ani auth login`.",
                redactor=redactor,
            )
        return web_auth_token

    def _request_current_user(
        self,
        api_token: CloudKitAPIToken,
        web_auth_token: str,
        redactor: SecretRedactor,
    ) -> httpx.Response:
        try:
            return self.client.get(
                database_endpoint_url("users/current"),
                params={
                    API_TOKEN_PARAM: api_token.value,
                    WEB_AUTH_TOKEN_PARAM: web_auth_token,
                },
            )
        except httpx.HTTPError as exc:
            raise CloudKitRequestFailedError(
                "CloudKit whoami request failed.",
                redactor=redactor,
            ) from exc

    def _parse_response(
        self,
        response: httpx.Response,
        redactor: SecretRedactor,
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            if response.status_code in (401, 403):
                self._clear_web_auth_token_after_auth_failure(redactor)
                raise CloudKitAuthenticationFailedError(
                    "CloudKit authentication failed. Cleared stored login; run `ani auth login`.",
                    redactor=redactor,
                ) from exc
            raise CloudKitUnexpectedResponseError(
                f"CloudKit whoami returned a non-JSON response (HTTP {response.status_code}).",
                redactor=redactor,
            ) from exc

        if not isinstance(payload, dict):
            raise CloudKitUnexpectedResponseError(
                f"CloudKit whoami returned an unexpected response (HTTP {response.status_code}).",
                redactor=redactor,
            )
        return payload

    def _store_successor_web_auth_token(
        self,
        successor_token: str,
        redactor: SecretRedactor,
    ) -> None:
        try:
            store_cloudkit_web_auth_token(successor_token, self.secret_store)
        except SecretStorageUnavailableError as exc:
            raise CloudKitRequestFailedError(
                "CloudKit returned updated auth state, but secure storage could not save it.",
                redactor=redactor,
            ) from exc

    def _clear_web_auth_token_after_auth_failure(self, redactor: SecretRedactor) -> None:
        try:
            delete_cloudkit_web_auth_token(self.secret_store)
        except SecretStorageUnavailableError as exc:
            raise CloudKitRequestFailedError(
                "CloudKit authentication failed, but secure storage could not clear the "
                "stored login. Run `ani auth login` after resolving secure storage.",
                redactor=redactor,
            ) from exc


def _current_user_from_payload(
    payload: dict[str, Any],
    redactor: SecretRedactor,
) -> CurrentUser:
    user_record_name = _optional_string(payload.get("userRecordName"))
    if not user_record_name:
        raise CloudKitUnexpectedResponseError(
            "CloudKit whoami response did not include userRecordName.",
            redactor=redactor,
        )

    return CurrentUser(
        user_record_name=user_record_name,
        first_name=_optional_string(payload.get("firstName")),
        last_name=_optional_string(payload.get("lastName")),
        email=_optional_string(payload.get("email")),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_authentication_failure(response: httpx.Response, payload: dict[str, Any]) -> bool:
    code = payload.get("serverErrorCode")
    if isinstance(code, str) and code in AUTH_FAILURE_CODES:
        return True
    return response.status_code in (401, 403) and code is None


def _cloudkit_failure_message(
    prefix: str,
    response: httpx.Response,
    payload: dict[str, Any],
) -> str:
    details = [f"HTTP {response.status_code}"]
    if code := _optional_string(payload.get("serverErrorCode")):
        details.append(code)
    if reason := _optional_string(payload.get("reason")):
        details.append(reason)
    return f"{prefix} ({': '.join(details)})."


def _safe_lock_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value)
    return safe or DEFAULT_PROFILE_ID
