import json

from typer.testing import CliRunner

from anishelf_cli.cli import groups, root
from anishelf_cli.cli.root import app
from anishelf_cli.config import KEYCHAIN_ACCOUNT

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
    assert "--verbose" in result.stdout
    assert "--metadata-depth" in result.stdout
    assert "--anishelf-source" not in result.stdout


def test_config_status_json_shows_effective_scope_without_secrets() -> None:
    result = runner.invoke(
        app,
        ["--json", "config", "status"],
        env={"ANI_CLOUDKIT_API_TOKEN": "api-secret-token"},
    )

    assert result.exit_code == 0
    assert "iCloud.com.samuelhe.MyAnimeList" in result.stdout
    payload = json.loads(result.stdout)
    assert payload["cloudkit_api_token_source"] == "env"
    assert payload["cloudkit_api_token_version"] is None
    assert payload["tmdb_api_key_envs"] == ["ANI_TMDB_API_KEY", "TMDB_API_KEY"]
    assert "profile" not in payload
    assert "anishelf_source" not in payload
    assert "cloudkit-api-token" not in result.stdout
    assert "api-secret-token" not in result.stdout
    assert "ckWebAuthToken" not in result.stdout


def test_profile_command_group_is_removed() -> None:
    result = runner.invoke(app, ["profile", "status"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_profile_option_is_removed() -> None:
    result = runner.invoke(app, ["--profile", "prod", "config", "status"])

    assert result.exit_code == 2
    assert "No such option" in result.stderr


def test_config_status_does_not_persist_profile_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "--json",
            "config",
            "status",
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


def test_logout_deletes_web_auth_token(monkeypatch) -> None:
    deleted = False

    def delete_token() -> None:
        nonlocal deleted
        deleted = True

    monkeypatch.setattr(root, "delete_cloudkit_web_auth_token", delete_token)

    result = runner.invoke(app, ["--json", "logout"])

    assert result.exit_code == 0
    assert deleted is True
    assert json.loads(result.stdout) == {"status": "logged-out"}
