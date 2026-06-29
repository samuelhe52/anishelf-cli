from __future__ import annotations

import json
import socket

import httpx
import pytest
from typer.testing import CliRunner

from anishelf_cli.cli import root
from anishelf_cli.cli.root import app
from anishelf_cli.cloudkit.auth import (
    CLOUDKIT_AUTH_BEHAVIOR_FIXTURE,
    LoopbackLoginSetupError,
    LoopbackLoginTimeoutError,
    MalformedCallbackURLError,
    capture_loopback_callback,
    extract_web_auth_token,
    initiate_login,
    successor_web_auth_token,
)
from anishelf_cli.cloudkit.tokens import CloudKitAPIToken
from anishelf_cli.models import ProfileConfig

runner = CliRunner()


def test_login_initiation_calls_private_current_user_with_api_token_only() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            401,
            json={
                "serverErrorCode": "AUTHENTICATION_REQUIRED",
                "reason": "request needs authorization",
                "redirectURL": "https://apple.example/sign-in",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    initiation = initiate_login(
        ProfileConfig(),
        CloudKitAPIToken("api-secret-token", "env:ANI_CLOUDKIT_API_TOKEN"),
        client,
    )

    assert initiation.redirect_url == "https://apple.example/sign-in"
    assert seen_requests[0].url.path.endswith("/production/private/users/current")
    assert seen_requests[0].url.params["ckAPIToken"] == "api-secret-token"
    assert "ckWebAuthToken" not in seen_requests[0].url.params


def test_auth_fixture_captures_documented_redirect_and_successor_shapes() -> None:
    assert "observed_successor_token_response_keys" in CLOUDKIT_AUTH_BEHAVIOR_FIXTURE
    assert CLOUDKIT_AUTH_BEHAVIOR_FIXTURE["auth_required_response"] == {
        "serverErrorCode": "AUTHENTICATION_REQUIRED",
        "reason": "request needs authorization",
        "redirectURL": "<apple-sign-in-url>",
    }
    assert CLOUDKIT_AUTH_BEHAVIOR_FIXTURE["callback_query"] == {
        "ckWebAuthToken": "<web-auth-token>"
    }
    assert successor_web_auth_token({"webAuthToken": "next-token"}) == "next-token"
    assert successor_web_auth_token({"ckWebAuthToken": "next-token"}) == "next-token"


def test_login_http_client_supports_socks_proxy_env(monkeypatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:9999")

    with root._make_http_client() as client:
        assert isinstance(client, httpx.Client)


def test_manual_paste_login_stores_token_without_printing_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root.webbrowser, "open", lambda url: url == "https://apple.example/sign-in")
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        root,
        "store_cloudkit_web_auth_token",
        lambda profile, token: stored.append((profile, token)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            401,
            json={
                "serverErrorCode": "AUTHENTICATION_REQUIRED",
                "redirectURL": "https://apple.example/sign-in",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    callback_url = "https://callback.example/done?ckWebAuthToken=web-secret-token"
    result = runner.invoke(
        app,
        ["--json", "login"],
        input=f"{callback_url}\n",
    )

    assert result.exit_code == 0, result.output
    assert stored == [("default", "web-secret-token")]
    assert json.loads(result.stdout)["status"] == "logged-in"
    combined = result.stdout + result.stderr
    assert "api-secret-token" not in combined
    assert "web-secret-token" not in combined
    assert callback_url not in combined


def test_manual_paste_login_rejects_malformed_callback_without_storing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root.webbrowser, "open", lambda url: True)
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        root,
        "store_cloudkit_web_auth_token",
        lambda profile, token: stored.append((profile, token)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            401,
            json={
                "serverErrorCode": "AUTHENTICATION_REQUIRED",
                "redirectURL": "https://apple.example/sign-in",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    callback_url = "https://callback.example/done?ckWebAuthToken=web-secret-token"
    result = runner.invoke(
        app,
        ["login"],
        input="https://callback.example/done?missing=web-secret-token\n",
    )

    assert result.exit_code == 2
    assert stored == []
    combined = result.stdout + result.stderr
    assert "api-secret-token" not in combined
    assert "web-secret-token" not in combined
    assert callback_url not in combined
    assert "missing ckWebAuthToken" in combined


def test_loopback_capture_times_out_cleanly() -> None:
    with pytest.raises(LoopbackLoginTimeoutError):
        capture_loopback_callback(
            "https://apple.example/sign-in",
            port=0,
            timeout_seconds=0.01,
            browser_open=lambda url: True,
        )


def test_loopback_capture_rejects_non_loopback_host_before_opening_browser() -> None:
    opened: list[str] = []

    with pytest.raises(LoopbackLoginSetupError, match="loopback IP address"):
        capture_loopback_callback(
            "https://apple.example/sign-in",
            host="0.0.0.0",
            port=0,
            timeout_seconds=0.01,
            browser_open=lambda url: opened.append(url) == [],
        )

    assert opened == []


def test_loopback_capture_reports_listener_setup_failure_cleanly() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied_port = sock.getsockname()[1]

        with pytest.raises(LoopbackLoginSetupError, match="Could not start"):
            capture_loopback_callback(
                "https://apple.example/sign-in",
                port=occupied_port,
                timeout_seconds=0.01,
                browser_open=lambda url: False,
            )


def test_loopback_login_timeout_does_not_store_partial_token(monkeypatch) -> None:
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root.webbrowser, "open", lambda url: True)
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        root,
        "store_cloudkit_web_auth_token",
        lambda profile, token: stored.append((profile, token)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            401,
            json={
                "serverErrorCode": "AUTHENTICATION_REQUIRED",
                "redirectURL": "https://apple.example/sign-in",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(root, "_make_http_client", lambda: client)
    monkeypatch.setattr(
        root,
        "capture_loopback_callback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LoopbackLoginTimeoutError("Timed out waiting for CloudKit loopback callback")
        ),
    )

    result = runner.invoke(
        app,
        [
            "login",
            "--callback-strategy",
            "loopback",
            "--loopback-timeout",
            "0.01",
        ],
    )

    assert result.exit_code == 3
    assert stored == []
    assert "Timed out waiting for CloudKit loopback callback" in result.stderr
    assert "api-secret-token" not in result.stdout + result.stderr


def test_loopback_login_rejects_non_loopback_host_without_side_effects(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    opened: list[str] = []
    monkeypatch.setattr(root.webbrowser, "open", lambda url: opened.append(url) == [])
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        root,
        "store_cloudkit_web_auth_token",
        lambda profile, token: stored.append((profile, token)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            401,
            json={
                "serverErrorCode": "AUTHENTICATION_REQUIRED",
                "redirectURL": "https://apple.example/sign-in",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(
        app,
        [
            "login",
            "--callback-strategy",
            "loopback",
            "--loopback-host",
            "0.0.0.0",
            "--loopback-port",
            "0",
        ],
    )

    assert result.exit_code == 2
    assert opened == []
    assert stored == []
    combined = result.stdout + result.stderr
    assert "loopback IP address" in combined
    assert "api-secret-token" not in combined
    assert "ckWebAuthToken=" not in combined


def test_extract_callback_requires_https_for_manual_paste() -> None:
    with pytest.raises(MalformedCallbackURLError):
        extract_web_auth_token("http://example.com/callback?ckWebAuthToken=token")
