from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from anishelf_cli import config
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken
from anishelf_cli.core.output import emit_verbose
from anishelf_cli.core.redaction import SecretRedactor

APPLE_CLOUDKIT_API_BASE_URL = "https://api.apple-cloudkit.com"
CK_WEB_AUTH_TOKEN_QUERY_KEY = "ckWebAuthToken"

# Verified against Apple's archived CloudKit Web Services reference on 2026-06-29:
# - database v1 endpoints are rooted at /database/1/{container}/{environment}/{operation}
# - API-token auth appends ckAPIToken and, after sign-in, ckWebAuthToken
# - missing user auth returns AUTHENTICATION_REQUIRED with redirectURL
# - returned web-auth tokens are single-round-trip and replaced by a response token
# The official docs confirm the callback query key. They do not name the exact
# successor response key; those keys are kept as local observed fixture evidence.
CLOUDKIT_AUTH_BEHAVIOR_FIXTURE: dict[str, Any] = {
    "docs": [
        "https://developer.apple.com/library/archive/documentation/DataManagement/"
        "Conceptual/CloudKitWebServicesReference/SettingUpWebServices.html",
        "https://developer.apple.com/library/archive/documentation/DataManagement/"
        "Conceptual/CloudKitWebServicesReference/GetCurrentUser.html",
    ],
    "login_probe_endpoint": ("GET /database/1/{container}/{environment}/{database}/users/current"),
    "login_probe_query": {"ckAPIToken": "<api-token>"},
    "auth_required_response": {
        "serverErrorCode": "AUTHENTICATION_REQUIRED",
        "reason": "request needs authorization",
        "redirectURL": "<apple-sign-in-url>",
    },
    "callback_query": {"ckWebAuthToken": "<web-auth-token>"},
    "observed_successor_token_response_keys": ("webAuthToken", "ckWebAuthToken"),
}


@dataclass(frozen=True, slots=True)
class LoginInitiation:
    endpoint_url: str
    redirect_url: str


class CloudKitAuthError(RuntimeError):
    pass


class CloudKitLoginInitiationError(CloudKitAuthError):
    pass


class MalformedCallbackURLError(CloudKitAuthError):
    pass


class LoopbackLoginTimeoutError(CloudKitAuthError):
    pass


class LoopbackLoginSetupError(CloudKitAuthError):
    pass


BrowserOpener = Callable[[str], bool]


def database_endpoint_url(operation_subpath: str) -> str:
    parts = [
        APPLE_CLOUDKIT_API_BASE_URL,
        "database",
        "1",
        config.DEFAULT_CONTAINER,
        config.DEFAULT_ENVIRONMENT,
        config.DEFAULT_DATABASE,
        operation_subpath.strip("/"),
    ]
    return "/".join(part.strip("/") for part in parts)


def initiate_login(
    api_token: CloudKitAPIToken,
    client: httpx.Client,
) -> LoginInitiation:
    endpoint_url = database_endpoint_url("users/current")
    redactor = SecretRedactor()
    redactor.register(api_token.value, "cloudkit-api-token")
    emit_verbose(
        f"CloudKit request -> GET {endpoint_url} params={json.dumps({'ckAPIToken': api_token.value}, sort_keys=True)}",
        redactor=redactor,
    )
    try:
        response = client.get(endpoint_url, params={"ckAPIToken": api_token.value})
    except httpx.HTTPError as exc:
        emit_verbose(
            f"CloudKit transport error <- GET {endpoint_url}: {exc.__class__.__name__}: {exc}",
            redactor=redactor,
        )
        raise CloudKitLoginInitiationError("CloudKit login initiation request failed") from exc

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        emit_verbose(
            f"CloudKit response <- HTTP {response.status_code} GET {response.request.url} non-json",
            redactor=redactor,
        )
        raise CloudKitLoginInitiationError(
            "CloudKit login initiation returned a non-JSON response"
        ) from exc

    emit_verbose(
        _cloudkit_login_response_log(response, payload),
        redactor=redactor,
    )
    if not isinstance(payload, dict):
        raise CloudKitLoginInitiationError(
            "CloudKit login initiation returned an unexpected response"
        )

    redirect_url = payload.get("redirectURL")
    if payload.get("serverErrorCode") != "AUTHENTICATION_REQUIRED" or not isinstance(
        redirect_url,
        str,
    ):
        code = payload.get("serverErrorCode")
        suffix = f" ({code})" if isinstance(code, str) else ""
        raise CloudKitLoginInitiationError(
            "CloudKit did not return an authentication redirect URL" + suffix
        )

    return LoginInitiation(endpoint_url=endpoint_url, redirect_url=redirect_url)


def extract_web_auth_token(callback_url: str, *, allow_loopback_http: bool = False) -> str:
    parsed = urlsplit(callback_url)
    if not parsed.scheme or not parsed.netloc:
        raise MalformedCallbackURLError("CloudKit callback URL is malformed or missing a host")
    if parsed.scheme != "https" and not (
        allow_loopback_http
        and parsed.scheme == "http"
        and parsed.hostname is not None
        and _is_loopback_host(parsed.hostname)
    ):
        raise MalformedCallbackURLError(
            "CloudKit callback URL must use HTTPS unless loopback capture is enabled"
        )

    values = parse_qs(parsed.query, keep_blank_values=False)
    if CK_WEB_AUTH_TOKEN_QUERY_KEY not in values and parsed.fragment:
        values = parse_qs(parsed.fragment, keep_blank_values=False)

    tokens = values.get(CK_WEB_AUTH_TOKEN_QUERY_KEY)
    if not tokens or not tokens[0]:
        raise MalformedCallbackURLError("CloudKit callback URL is missing ckWebAuthToken")
    return tokens[0]


def _cloudkit_login_response_log(response: httpx.Response, payload: object) -> str:
    parts = [f"CloudKit response <- HTTP {response.status_code} GET {response.request.url}"]
    if isinstance(payload, dict):
        if code := payload.get("serverErrorCode"):
            parts.append(f"serverErrorCode={code}")
        if reason := payload.get("reason"):
            parts.append(f"reason={reason}")
        parts.append(f"keys={sorted(payload.keys())}")
    else:
        parts.append(f"payload_type={type(payload).__name__}")
    return " ".join(parts)


def successor_web_auth_token(payload: dict[str, Any]) -> str | None:
    for key in CLOUDKIT_AUTH_BEHAVIOR_FIXTURE["observed_successor_token_response_keys"]:
        token = payload.get(key)
        if isinstance(token, str) and token:
            return token
    return None


class _LoopbackCallbackState:
    def __init__(self) -> None:
        self.token: str | None = None
        self.error: MalformedCallbackURLError | None = None


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_loopback_host(host: str) -> None:
    if not _is_loopback_host(host):
        raise LoopbackLoginSetupError(
            "Loopback callback host must be localhost or a loopback IP address"
        )


def capture_loopback_callback(
    redirect_url: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    timeout_seconds: float = 120.0,
    browser_open: BrowserOpener,
) -> str:
    _validate_loopback_host(host)
    state = _LoopbackCallbackState()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            callback_url = f"http://{self.headers.get('Host', f'{host}:{port}')}{self.path}"
            try:
                state.token = extract_web_auth_token(
                    callback_url,
                    allow_loopback_http=True,
                )
                self.send_response(200)
                body = b"CloudKit login captured. You can close this window."
            except MalformedCallbackURLError as exc:
                state.error = exc
                self.send_response(400)
                body = b"CloudKit login callback was missing required data."

            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            _ = format, args

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except (OSError, OverflowError, ValueError) as exc:
        raise LoopbackLoginSetupError(
            f"Could not start loopback callback listener on {host}:{port}"
        ) from exc

    server.timeout = timeout_seconds
    try:
        browser_open(redirect_url)
        server.handle_request()
    finally:
        server.server_close()

    if state.token:
        return state.token
    if state.error:
        raise state.error
    raise LoopbackLoginTimeoutError("Timed out waiting for CloudKit loopback callback")
