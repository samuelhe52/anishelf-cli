from __future__ import annotations

import json
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
from anishelf_cli.core.coercion import nonempty_string_or_none
from anishelf_cli.core.output import emit_verbose
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
ANI_SHELF_LIBRARY_ZONE_NAME = "AniShelfLibrary"
AUTH_FAILURE_CODES = {
    "AUTHENTICATION_FAILED",
    "AUTHENTICATION_REQUIRED",
}

TokenResolver = Callable[[], CloudKitAPIToken]
LockFactory = Callable[[Path], AbstractContextManager[Any]]
QueryParamValue = str | int | float | bool | None


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


@dataclass(frozen=True, slots=True)
class ZoneChangesPage:
    records: list[dict[str, Any]]
    sync_token: str
    more_coming: bool


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


class CloudKitChangeTokenExpiredError(CloudKitWhoamiError):
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
        payload = self.authenticated_request(
            "GET",
            "users/current",
            error_context="CloudKit whoami request",
            response_description="CloudKit whoami",
        )
        return _current_user_from_payload(payload, SecretRedactor())

    def lookup_records(
        self,
        record_names: list[str],
        *,
        zone_name: str = ANI_SHELF_LIBRARY_ZONE_NAME,
    ) -> dict[str, Any]:
        request_payload = {
            "records": [{"recordName": record_name} for record_name in record_names],
            "zoneID": {"zoneName": zone_name},
        }
        return self.authenticated_request(
            "POST",
            "records/lookup",
            json_payload=request_payload,
            error_context="CloudKit library lookup request",
            response_description="CloudKit library lookup",
        )

    def fetch_zone_changes(
        self,
        *,
        sync_token: str | None,
        zone_name: str = ANI_SHELF_LIBRARY_ZONE_NAME,
        results_limit: int = 400,
        desired_record_types: list[str] | None = None,
    ) -> ZoneChangesPage:
        zone_request: dict[str, Any] = {
            "zoneID": {"zoneName": zone_name},
        }
        if sync_token:
            zone_request["syncToken"] = sync_token

        request_payload: dict[str, Any] = {
            "zones": [zone_request],
            "resultsLimit": results_limit,
        }
        if desired_record_types:
            request_payload["desiredRecordTypes"] = desired_record_types

        payload = self.authenticated_request(
            "POST",
            "changes/zone",
            json_payload=request_payload,
            error_context="CloudKit zone changes request",
            response_description="CloudKit zone changes",
        )
        return _zone_changes_page_from_payload(payload, SecretRedactor())

    def authenticated_request(
        self,
        method: str,
        operation_subpath: str,
        *,
        params: dict[str, QueryParamValue] | None = None,
        json_payload: dict[str, Any] | None = None,
        error_context: str = "CloudKit request",
        response_description: str = "CloudKit response",
    ) -> dict[str, Any]:
        redactor = SecretRedactor()
        api_token = self.api_token_resolver()
        redactor.register(api_token.value, "cloudkit-api-token")

        with self._token_lock():
            web_auth_token = self._load_web_auth_token(redactor)
            response = self._request_authenticated(
                method,
                operation_subpath,
                api_token,
                web_auth_token,
                redactor,
                params=params,
                json_payload=json_payload,
                error_context=error_context,
            )
            payload = self._parse_response(response, redactor, response_description)

            successor_token = successor_web_auth_token(payload)
            redactor.register(successor_token, "cloudkit-successor-web-auth-token")

            if _is_authentication_failure(response, payload):
                self._clear_web_auth_token_after_auth_failure(redactor)
                raise CloudKitAuthenticationFailedError(
                    "CloudKit authentication failed. Cleared stored login; run `ani auth login`.",
                    redactor=redactor,
                )

            if _is_change_token_expired_payload(response, payload):
                raise CloudKitChangeTokenExpiredError(
                    _cloudkit_failure_message(f"{error_context} failed", response, payload),
                    redactor=redactor,
                )

            if response.is_error:
                raise CloudKitRequestFailedError(
                    _cloudkit_failure_message(f"{error_context} failed", response, payload),
                    redactor=redactor,
                )

            if successor_token:
                self._store_successor_web_auth_token(successor_token, redactor)

            return payload

    def lock_path(self) -> Path:
        return cloudkit_web_auth_token_lock_path(self.profile_id)

    def _token_lock(self) -> AbstractContextManager[Any]:
        return cloudkit_web_auth_token_lock(
            profile_id=self.profile_id,
            lock_factory=self.lock_factory,
            lock_timeout_seconds=self.lock_timeout_seconds,
        )

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

    def _request_authenticated(
        self,
        method: str,
        operation_subpath: str,
        api_token: CloudKitAPIToken,
        web_auth_token: str,
        redactor: SecretRedactor,
        *,
        params: dict[str, QueryParamValue] | None,
        json_payload: dict[str, Any] | None,
        error_context: str,
    ) -> httpx.Response:
        request_params: dict[str, QueryParamValue] = {
            API_TOKEN_PARAM: api_token.value,
            WEB_AUTH_TOKEN_PARAM: web_auth_token,
        }
        if params:
            request_params.update(params)
        endpoint_url = database_endpoint_url(operation_subpath)
        message = (
            f"CloudKit request -> {method.upper()} {endpoint_url} "
            f"params={json.dumps(request_params, sort_keys=True)}"
        )
        if json_payload is not None:
            message += f" json={json.dumps(json_payload, sort_keys=True)}"
        emit_verbose(message, redactor=redactor)
        try:
            response = self.client.request(
                method,
                endpoint_url,
                params=request_params,
                json=json_payload,
            )
            emit_verbose(
                "CloudKit response <- "
                f"HTTP {response.status_code} {method.upper()} {response.request.url}",
                redactor=redactor,
            )
            return response
        except httpx.HTTPError as exc:
            emit_verbose(
                "CloudKit transport error <- "
                f"{method.upper()} {endpoint_url}: {exc.__class__.__name__}: {exc}",
                redactor=redactor,
            )
            raise CloudKitRequestFailedError(
                f"{error_context} failed.",
                redactor=redactor,
            ) from exc

    def _parse_response(
        self,
        response: httpx.Response,
        redactor: SecretRedactor,
        response_description: str,
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            message = (
                f"{response_description} returned a non-JSON response "
                f"(HTTP {response.status_code})."
            )
            raise CloudKitUnexpectedResponseError(
                message,
                redactor=redactor,
            ) from exc

        if not isinstance(payload, dict):
            message = (
                f"{response_description} returned an unexpected response "
                f"(HTTP {response.status_code})."
            )
            raise CloudKitUnexpectedResponseError(
                message,
                redactor=redactor,
            )
        emit_verbose(
            _cloudkit_payload_log(response, payload),
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
    user_record_name = nonempty_string_or_none(payload.get("userRecordName"))
    if not user_record_name:
        raise CloudKitUnexpectedResponseError(
            "CloudKit whoami response did not include userRecordName.",
            redactor=redactor,
        )

    return CurrentUser(
        user_record_name=user_record_name,
        first_name=nonempty_string_or_none(payload.get("firstName")),
        last_name=nonempty_string_or_none(payload.get("lastName")),
        email=nonempty_string_or_none(payload.get("email")),
    )


def _zone_changes_page_from_payload(
    payload: dict[str, Any],
    redactor: SecretRedactor,
) -> ZoneChangesPage:
    zones = payload.get("zones")
    if not isinstance(zones, list) or len(zones) != 1 or not isinstance(zones[0], dict):
        raise CloudKitUnexpectedResponseError(
            "CloudKit zone changes response did not include one zone result.",
            redactor=redactor,
        )

    zone_result = zones[0]
    if code := nonempty_string_or_none(zone_result.get("serverErrorCode")):
        message = _zone_error_message(code, zone_result)
        if _is_change_token_expired_code(code):
            raise CloudKitChangeTokenExpiredError(message, redactor=redactor)
        raise CloudKitRequestFailedError(message, redactor=redactor)

    records = zone_result.get("records")
    if not isinstance(records, list):
        raise CloudKitUnexpectedResponseError(
            "CloudKit zone changes response did not include records.",
            redactor=redactor,
        )
    if not all(isinstance(record, dict) for record in records):
        raise CloudKitUnexpectedResponseError(
            "CloudKit zone changes response included an invalid record.",
            redactor=redactor,
        )

    sync_token = nonempty_string_or_none(zone_result.get("syncToken"))
    if not sync_token:
        raise CloudKitUnexpectedResponseError(
            "CloudKit zone changes response did not include syncToken.",
            redactor=redactor,
        )

    return ZoneChangesPage(
        records=records,
        sync_token=sync_token,
        more_coming=bool(zone_result.get("moreComing")),
    )


def _is_authentication_failure(response: httpx.Response, payload: dict[str, Any]) -> bool:
    _ = response
    code = payload.get("serverErrorCode")
    return isinstance(code, str) and code in AUTH_FAILURE_CODES


def _is_change_token_expired_payload(response: httpx.Response, payload: dict[str, Any]) -> bool:
    if not response.is_error:
        return False
    code = payload.get("serverErrorCode")
    return isinstance(code, str) and _is_change_token_expired_code(code)


def cloudkit_web_auth_token_lock_path(profile_id: str = DEFAULT_PROFILE_ID) -> Path:
    lock_dir = config.data_dir() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"cloudkit-web-auth-token.{_safe_lock_name(profile_id)}.lock"


def cloudkit_web_auth_token_lock(
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    lock_factory: LockFactory | None = None,
    lock_timeout_seconds: float = -1.0,
) -> AbstractContextManager[Any]:
    lock_path = cloudkit_web_auth_token_lock_path(profile_id)
    if lock_factory is not None:
        return lock_factory(lock_path)
    return FileLock(str(lock_path), timeout=lock_timeout_seconds)


def _cloudkit_failure_message(
    prefix: str,
    response: httpx.Response,
    payload: dict[str, Any],
) -> str:
    details = [f"HTTP {response.status_code}"]
    if code := nonempty_string_or_none(payload.get("serverErrorCode")):
        details.append(code)
    if reason := nonempty_string_or_none(payload.get("reason")):
        details.append(reason)
    return f"{prefix} ({': '.join(details)})."


def _cloudkit_payload_log(response: httpx.Response, payload: dict[str, Any]) -> str:
    parts = [
        "CloudKit payload <- "
        f"HTTP {response.status_code} {response.request.method} {response.request.url}"
    ]
    if code := nonempty_string_or_none(payload.get("serverErrorCode")):
        parts.append(f"serverErrorCode={code}")
    if reason := nonempty_string_or_none(payload.get("reason")):
        parts.append(f"reason={reason}")
    zones = payload.get("zones")
    if isinstance(zones, list) and len(zones) == 1 and isinstance(zones[0], dict):
        zone = zones[0]
        records = zone.get("records")
        if isinstance(records, list):
            parts.append(f"records={len(records)}")
        if "moreComing" in zone:
            parts.append(f"moreComing={bool(zone.get('moreComing'))}")
    else:
        parts.append(f"keys={sorted(payload.keys())}")
    return " ".join(parts)


def _safe_lock_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value)
    return safe or DEFAULT_PROFILE_ID


def _zone_error_message(code: str, zone_result: dict[str, Any]) -> str:
    details = [code]
    if reason := nonempty_string_or_none(zone_result.get("reason")):
        details.append(reason)
    return f"CloudKit zone changes request failed ({': '.join(details)})."


def _is_change_token_expired_code(code: str) -> bool:
    normalized = code.upper().replace("-", "_")
    return normalized in {
        "CHANGE_TOKEN_EXPIRED",
        "SERVER_CHANGE_TOKEN_EXPIRED",
        "CKERROR_CHANGE_TOKEN_EXPIRED",
    }
