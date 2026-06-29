import json

from typer.testing import CliRunner

from anishelf_cli.cli import groups, root
from anishelf_cli.cli.root import app

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
    assert "--profile" in result.stdout
    assert "--json" in result.stdout
    assert "--verbose" in result.stdout
    assert "--metadata-depth" in result.stdout
    assert "--anishelf-source" in result.stdout


def test_profile_status_json_shows_effective_scope_without_secrets() -> None:
    result = runner.invoke(
        app,
        ["--json", "profile", "status"],
        env={"ANI_CLOUDKIT_API_TOKEN": "api-secret-token"},
    )

    assert result.exit_code == 0
    assert "iCloud.com.samuelhe.MyAnimeList" in result.stdout
    payload = json.loads(result.stdout)
    assert payload["cloudkit_api_token_source"] == "env"
    assert payload["cloudkit_api_token_version"] is None
    assert "cloudkit_token_source" not in payload
    assert "cloudkit_api_token_env" not in payload
    assert "cloudkit-api-token" not in result.stdout
    assert "api-secret-token" not in result.stdout
    assert "ckWebAuthToken" not in result.stdout


def test_profile_configure_persists_effective_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    env_file = tmp_path / "tokens.env"
    anishelf_source = tmp_path / "AniShelf"

    result = runner.invoke(
        app,
        [
            "--json",
            "--profile",
            "prod",
            "profile",
            "configure",
            "--env-file",
            str(env_file),
            "--anishelf-source",
            str(anishelf_source),
            "--tmdb-token-env",
            "ANI_TMDB_API_KEY",
            "--tmdb-token-env",
            "TMDB_API_KEY",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["profile"] == "prod"
    assert payload["env_file"] == str(env_file)
    assert payload["anishelf_source"] == str(anishelf_source)

    status = runner.invoke(app, ["--json", "--profile", "prod", "profile", "status"])

    assert status.exit_code == 0
    status_payload = json.loads(status.stdout)
    assert status_payload["cloudkit_api_token_source"] == "embedded-public"
    assert status_payload["tmdb_api_key_envs"] == ["ANI_TMDB_API_KEY", "TMDB_API_KEY"]


def test_profile_configure_has_no_cloudkit_token_source_option(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "profile",
            "configure",
            "--cloudkit-token-source",
            "env-file",
        ],
    )

    assert result.exit_code == 2
    assert "cloudkit-token-source" in result.stderr


def test_profile_configure_still_allows_tmdb_env_file_source(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "--json",
            "profile",
            "configure",
            "--tmdb-token-source",
            "env-file",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tmdb_token_source"] == "env-file"


def test_config_set_tmdb_token_stores_without_echoing_secret(monkeypatch) -> None:
    store = MemorySecretStore()
    monkeypatch.setattr(groups, "default_secret_store", lambda: store)

    tmdb = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-token", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert tmdb.exit_code == 0
    assert "tmdb-secret-token" not in tmdb.stdout + tmdb.stderr
    assert ("anishelf-cli.tmdb-api-key", "default") in store.values


def test_config_has_no_cloudkit_api_token_storage_command() -> None:
    result = runner.invoke(app, ["config", "set-cloudkit-token", "--help"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


def test_logout_deletes_selected_profile_web_auth_token(monkeypatch) -> None:
    deleted_profiles: list[str] = []
    monkeypatch.setattr(
        root,
        "delete_cloudkit_web_auth_token",
        lambda profile: deleted_profiles.append(profile),
    )

    result = runner.invoke(app, ["--json", "--profile", "prod", "logout"])

    assert result.exit_code == 0
    assert deleted_profiles == ["prod"]
    assert json.loads(result.stdout) == {"profile": "prod", "status": "logged-out"}
