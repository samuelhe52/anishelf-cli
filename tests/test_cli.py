import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from typer.testing import CliRunner

from anishelf_cli import config
from anishelf_cli.cli import groups, root
from anishelf_cli.cli.root import app
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken
from anishelf_cli.cloudkit.executor import CloudKitExecutor
from anishelf_cli.config import KEYCHAIN_ACCOUNT
from anishelf_cli.secrets import cloudkit_web_auth_token_secret

runner = CliRunner()


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.values[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


def test_root_help_mentions_global_options() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--profile" not in result.stdout
    assert "--json" in result.stdout
    assert "--metadata-depth" in result.stdout
    assert "--anishelf-source" not in result.stdout


def test_help_uses_plain_agent_friendly_formatting() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Usage: " in result.stdout
    assert "Commands:" in result.stdout
    for box_character in ("╭", "╮", "╰", "╯", "│", "─"):
        assert box_character not in result.stdout


def test_root_help_hides_non_user_command_groups() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("zones", "records", "changes", "settings", "schema"):
        assert command not in result.stdout


def test_non_user_command_groups_are_removed() -> None:
    for command in ("zones", "records", "changes", "settings", "schema"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 2
        assert "No such command" in result.stderr


def test_unknown_command_error_uses_plain_formatting() -> None:
    result = runner.invoke(app, ["auth", "loggg"])

    assert result.exit_code == 2
    assert "No such command 'loggg'. Did you mean 'login'?" in result.stderr
    for box_character in ("╭", "╮", "╰", "╯", "│", "─"):
        assert box_character not in result.stderr


def test_implemented_commands_have_help_text() -> None:
    missing: list[str] = []
    for group in app.registered_groups:
        for command in group.typer_instance.registered_commands:
            callback = command.callback
            if callback is None:
                continue
            if "emit_placeholder" in callback.__code__.co_names:
                continue
            if not command.help:
                missing.append(f"{group.name} {command.name}")

    assert missing == []


def test_config_show_json_shows_effective_config_without_secrets() -> None:
    result = runner.invoke(
        app,
        ["--json", "config", "show"],
        env={"ANI_CLOUDKIT_API_TOKEN": "api-secret-token"},
    )

    assert result.exit_code == 0
    assert "iCloud.com.samuelhe.MyAnimeList" in result.stdout
    payload = json.loads(result.stdout)
    assert payload["cloudkit"]["app_auth_source"] == "env"
    assert payload["cloudkit"]["app_auth_version"] is None
    assert payload["tmdb"]["api_key_envs"] == ["ANI_TMDB_API_KEY", "TMDB_API_KEY"]
    assert "config_dir" in payload["paths"]
    assert "cache_dir" in payload["paths"]
    assert "data_dir" in payload["paths"]
    assert "profile" not in payload
    assert "anishelf_source" not in payload
    assert "cloudkit-api-token" not in result.stdout
    assert "api-secret-token" not in result.stdout
    assert "ckWebAuthToken" not in result.stdout


def test_config_show_accepts_command_level_json() -> None:
    result = runner.invoke(
        app,
        ["config", "show", "--json"],
        env={"ANI_CLOUDKIT_API_TOKEN": "api-secret-token"},
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["cloudkit"]["app_auth_source"] == "env"
    assert "api-secret-token" not in result.stdout


def test_config_show_human_output_uses_readable_sections() -> None:
    result = runner.invoke(app, ["config", "show"], env={"ANI_CLOUDKIT_API_TOKEN": "api"})

    assert result.exit_code == 0
    assert "CloudKit\n" in result.stdout
    assert "  Container     iCloud.com.samuelhe.MyAnimeList\n" in result.stdout
    assert "  App auth      env\n" in result.stdout
    assert "\nCallback\n" in result.stdout
    assert "  Strategy      manual-paste\n" in result.stdout
    assert "\nTMDb\n" in result.stdout
    assert "  API key envs  ANI_TMDB_API_KEY, TMDB_API_KEY\n" in result.stdout
    assert "\nPaths\n" in result.stdout
    assert "  Config        " in result.stdout
    assert "api" not in result.stdout


def test_config_status_command_is_removed() -> None:
    result = runner.invoke(app, ["config", "status"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_default_posix_app_paths_use_dotdir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.Path, "home", lambda: tmp_path)

    assert config.config_dir() == tmp_path / ".anishelf-cli"
    assert config.data_dir() == tmp_path / ".anishelf-cli"
    assert config.cache_dir() == tmp_path / ".anishelf-cli" / "cache"


def test_windows_app_paths_use_local_app_data(monkeypatch, tmp_path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert config.config_dir() == local_app_data / "anishelf-cli"
    assert config.data_dir() == local_app_data / "anishelf-cli"
    assert config.cache_dir() == local_app_data / "anishelf-cli" / "cache"


def test_path_overrides_are_preserved(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config-override"))
    monkeypatch.setenv("ANISHELF_CLI_CACHE_DIR", str(tmp_path / "cache-override"))
    monkeypatch.setenv("ANISHELF_CLI_DATA_DIR", str(tmp_path / "data-override"))

    assert config.config_dir() == tmp_path / "config-override"
    assert config.cache_dir() == tmp_path / "cache-override"
    assert config.data_dir() == tmp_path / "data-override"


def test_profile_command_group_is_removed() -> None:
    result = runner.invoke(app, ["profile", "status"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_profile_option_is_removed() -> None:
    result = runner.invoke(app, ["--profile", "prod", "config", "show"])

    assert result.exit_code == 2
    assert "No such option" in result.stderr


def test_config_show_does_not_persist_profile_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "--json",
            "config",
            "show",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "profile" not in payload
    assert "env_file" not in payload
    assert "anishelf_source" not in payload
    assert not (tmp_path / "config" / "profiles").exists()


def test_config_set_tmdb_api_key_stores_without_echoing_secret(monkeypatch) -> None:
    store = MemorySecretStore()
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)

    tmdb = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert tmdb.exit_code == 0
    assert "tmdb-secret-token" not in tmdb.stdout + tmdb.stderr
    assert ("anishelf-cli.tmdb-api-key", KEYCHAIN_ACCOUNT) in store.values


def test_config_has_no_tmdb_token_command() -> None:
    result = runner.invoke(app, ["config", "set-tmdb-token", "--help"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_config_has_no_cloudkit_api_token_storage_command() -> None:
    result = runner.invoke(app, ["config", "set-cloudkit-token", "--help"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_auth_group_lists_auth_commands() -> None:
    result = runner.invoke(app, ["auth", "--help"])

    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "logout" in result.stdout
    assert "status" in result.stdout
    assert "refresh" in result.stdout


def test_auth_status_accepts_command_level_json(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "userRecordName": "_abc123",
                    "firstName": "Ani",
                },
            )
        )
    )
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["auth", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "authenticated"
    assert payload["user"]["user_record_name"] == "_abc123"
    assert "api-secret-token" not in result.stdout + result.stderr
    assert "web-secret-token" not in result.stdout + result.stderr


def test_auth_commands_are_not_top_level() -> None:
    for command in ("login", "logout", "whoami"):
        result = runner.invoke(app, [command])
        assert result.exit_code == 2
        assert "No such command" in result.stderr


def test_logout_deletes_web_auth_token(monkeypatch) -> None:
    deleted = False

    def delete_token() -> None:
        nonlocal deleted
        deleted = True

    monkeypatch.setattr(root, "delete_cloudkit_web_auth_token", delete_token)

    result = runner.invoke(app, ["--json", "auth", "logout"])

    assert result.exit_code == 0
    assert deleted is True
    assert json.loads(result.stdout) == {"status": "logged-out"}


def test_whoami_success_json_uses_authenticated_current_user(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "userRecordName": "_abc123",
                "firstName": "Ani",
                "lastName": "Shelf",
                "email": "ani@example.com",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "status": "authenticated",
        "user": {
            "user_record_name": "_abc123",
            "first_name": "Ani",
            "last_name": "Shelf",
            "email": "ani@example.com",
        },
    }
    assert requests[0].method == "GET"
    assert requests[0].url.path.endswith(
        "/database/1/iCloud.com.samuelhe.MyAnimeList/production/private/users/current"
    )
    assert requests[0].url.params["ckAPIToken"] == "api-secret-token"
    assert requests[0].url.params["ckWebAuthToken"] == "web-secret-token"
    assert "api-secret-token" not in result.stdout + result.stderr
    assert "web-secret-token" not in result.stdout + result.stderr


def test_auth_refresh_json_uses_authenticated_current_user(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "userRecordName": "_abc123",
                    "webAuthToken": "new-web-secret-token",
                },
            )
        )
    )
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "auth", "refresh"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "refreshed"
    assert payload["user"]["user_record_name"] == "_abc123"
    assert store.get_password(descriptor.service, descriptor.account) == "new-web-secret-token"
    assert "api-secret-token" not in result.stdout + result.stderr
    assert "web-secret-token" not in result.stdout + result.stderr
    assert "new-web-secret-token" not in result.stdout + result.stderr


def test_whoami_human_output(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "userRecordName": "_abc123",
                    "firstName": "Ani",
                    "lastName": "Shelf",
                },
            )
        )
    )
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "Authenticated to CloudKit." in result.stdout
    assert "Name: Ani Shelf" in result.stdout
    assert "User record: _abc123" in result.stdout
    assert "web-secret-token" not in result.stdout + result.stderr


def test_whoami_missing_login_tells_user_to_login_without_network(monkeypatch) -> None:
    store = MemorySecretStore()
    requests: list[httpx.Request] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)

    monkeypatch.setattr(
        root,
        "_make_http_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(500)
            )
        ),
    )

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Run `ani auth login`" in result.stderr
    assert requests == []
    assert "api-secret-token" not in result.stdout + result.stderr


def test_whoami_saves_successor_token_before_releasing_lock(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "old-web-secret-token")
    events: list[str] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)
    original_set_password = store.set_password

    def set_password(service: str, account: str, password: str) -> None:
        events.append(f"save:{password}")
        original_set_password(service, account, password)

    store.set_password = set_password  # type: ignore[method-assign]

    @contextmanager
    def recording_lock(path: Path) -> Iterator[None]:
        _ = path
        events.append("enter-lock")
        try:
            yield
        finally:
            events.append("exit-lock")

    monkeypatch.setattr(root, "whoami_lock_factory", lambda path: recording_lock(path))

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "userRecordName": "_abc123",
                    "webAuthToken": "new-web-secret-token",
                },
            )
        )
    )
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 0, result.output
    assert events == ["enter-lock", "save:new-web-secret-token", "exit-lock"]
    assert store.get_password(descriptor.service, descriptor.account) == "new-web-secret-token"
    assert "new-web-secret-token" not in result.stdout + result.stderr


def test_whoami_auth_failure_clears_login_and_redacts_tokens(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "bad-web-secret-token")
    deleted: list[tuple[str, str]] = []

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)
    original_delete_password = store.delete_password

    def delete_password(service: str, account: str) -> None:
        deleted.append((service, account))
        original_delete_password(service, account)

    store.delete_password = delete_password  # type: ignore[method-assign]

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                401,
                json={
                    "serverErrorCode": "AUTHENTICATION_FAILED",
                    "reason": (
                        "ckWebAuthToken=bad-web-secret-token "
                        "ckAPIToken=api-secret-token "
                        "https://callback.example/done?ckWebAuthToken=callback-secret-token"
                    ),
                    "webAuthToken": "successor-secret-token",
                },
            )
        )
    )
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "run `ani auth login`" in result.stderr
    assert deleted == [(descriptor.service, descriptor.account)]
    assert store.get_password(descriptor.service, descriptor.account) is None
    combined = result.stdout + result.stderr
    assert "api-secret-token" not in combined
    assert "bad-web-secret-token" not in combined
    assert "successor-secret-token" not in combined
    assert "callback-secret-token" not in combined
    assert "https://callback.example/done" not in combined


def test_whoami_redacts_non_auth_cloudkit_error_details(monkeypatch) -> None:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")

    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "serverErrorCode": "BAD_REQUEST",
                    "reason": (
                        "failed URL https://callback.example/done?"
                        "ckWebAuthToken=web-secret-token&ckAPIToken=api-secret-token"
                    ),
                    "webAuthToken": "successor-secret-token",
                },
            )
        )
    )
    monkeypatch.setattr(root, "_make_http_client", lambda: client)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 2
    combined = result.stdout + result.stderr
    assert "BAD_REQUEST" in combined
    assert "api-secret-token" not in combined
    assert "web-secret-token" not in combined
    assert "successor-secret-token" not in combined
    assert "https://callback.example/done" not in combined


def test_whoami_locking_serializes_token_consuming_requests(tmp_path, monkeypatch) -> None:
    import threading

    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")
    active_requests = 0
    max_active_requests = 0
    request_count = 0
    guard = threading.Lock()

    monkeypatch.setenv("ANISHELF_CLI_DATA_DIR", str(tmp_path / "data"))

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        nonlocal active_requests, max_active_requests, request_count
        with guard:
            active_requests += 1
            request_count += 1
            max_active_requests = max(max_active_requests, active_requests)
        try:
            threading.Event().wait(0.03)
            return httpx.Response(200, json={"userRecordName": f"_user{request_count}"})
        finally:
            with guard:
                active_requests -= 1

    client = httpx.Client(transport=httpx.MockTransport(handler))

    results: list[str] = []

    def request_current_user() -> None:
        user = CloudKitExecutor(
            client=client,
            api_token_resolver=lambda: CloudKitAPIToken("api-secret-token", "test"),
            secret_store=store,
        ).get_current_user()
        results.append(user.user_record_name)

    threads = [threading.Thread(target=request_current_user) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["_user1", "_user2"]
    assert request_count == 2
    assert max_active_requests == 1
