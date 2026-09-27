import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from anishelf_cli import config
from anishelf_cli.cli import config_commands, library_commands, root, tmdb_commands
from anishelf_cli.cli.root import app
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken
from anishelf_cli.cloudkit.executor import CloudKitExecutor
from anishelf_cli.config import KEYCHAIN_ACCOUNT
from anishelf_cli.models.output import RemovedCacheFilesResult
from anishelf_cli.models.tmdb import (
    TMDbTitleSearchMatch,
    TMDbTitleSearchQuery,
    TMDbTitleSearchResult,
)
from anishelf_cli.secrets import cloudkit_web_auth_token_secret
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError
from anishelf_cli.tmdb.tokens import TMDbAPIToken
from tests.support import MemorySecretStore, isolate_paths, runner


def _fake_store() -> object:
    return SimpleNamespace(
        scope=SimpleNamespace(
            container="iCloud.com.samuelhe.MyAnimeList",
            environment="production",
            database="private",
            zone="AniShelfLibrary",
            user_record_name="_test_user",
        ),
        list_entry_models=lambda *, include_tombstones=False: [],
        list_entry_models_filtered=lambda **kwargs: [],
        search_entry_models=lambda query, **kwargs: [],
        metadata_status_for_entries=lambda entries, **kwargs: SimpleNamespace(
            tracked_entries=0,
            hydrated_entries=0,
            missing_entries=0,
            ready=True,
        ),
        attach_metadata_summary_models=lambda entries, **kwargs: entries,
    )


def _tmdb_match(
    entry_type: str,
    tmdb_id: int,
    title: str,
    *,
    release_date: str,
    overview: str,
    poster_path: str,
) -> TMDbTitleSearchMatch:
    return TMDbTitleSearchMatch(
        entry_type=entry_type,
        tmdb_id=tmdb_id,
        title=title,
        original_title=title,
        release_date=release_date,
        original_language_code="en",
        overview=overview,
        poster_path=poster_path,
        details_url=(
            f"https://www.themoviedb.org/movie/{tmdb_id}"
            if entry_type == "movie"
            else f"https://www.themoviedb.org/tv/{tmdb_id}"
        ),
    )


def _install_tmdb_search_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_query: TMDbTitleSearchQuery,
    movies: tuple[TMDbTitleSearchMatch, ...] = (),
    series: tuple[TMDbTitleSearchMatch, ...] = (),
    error: Exception | None = None,
) -> None:
    class FakeTMDbClient:
        def search_titles(self, query: TMDbTitleSearchQuery) -> TMDbTitleSearchResult:
            assert query == expected_query
            if error is not None:
                raise error
            return TMDbTitleSearchResult(movies=movies, series=series)

    monkeypatch.setattr(tmdb_commands, "_tmdb_summary_client_or_exit", lambda: FakeTMDbClient())


def _assert_help(
    args: list[str],
    *,
    contains: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
) -> None:
    result = runner.invoke(app, [*args, "--help"])
    assert result.exit_code == 0
    for text in contains:
        assert text in result.stdout
    for text in absent:
        assert text not in result.stdout


def _store_with_web_auth_token(token: str = "web-secret-token") -> MemorySecretStore:
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, token)
    return store


def _install_root_auth_store(monkeypatch: pytest.MonkeyPatch, store: MemorySecretStore) -> None:
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    monkeypatch.setattr(root, "default_secret_store", lambda: store)


def _install_root_http_client(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> None:
    monkeypatch.setattr(
        root,
        "_make_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_root_help_mentions_global_options() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    normalized_help = " ".join(result.stdout.split())
    assert "First run:" in result.stdout
    assert "ani auth login" in normalized_help
    assert "ani config set-tmdb-api-key" in normalized_help
    assert "ani lib init" in normalized_help
    assert "--profile" not in result.stdout
    assert "--json" in result.stdout
    assert "-j" in result.stdout
    assert "--verbose" in result.stdout
    assert "-v" in result.stdout
    assert "--version" in result.stdout
    assert "--metadata-depth" not in result.stdout
    assert "--anishelf-source" not in result.stdout


def test_root_version_uses_installed_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root.metadata, "version", lambda package: "9.8.7")

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "ani 9.8.7\n"
    assert result.stderr == ""


def test_root_version_falls_back_to_source_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_package(package: str) -> str:
        raise root.metadata.PackageNotFoundError(package)

    monkeypatch.setattr(root.metadata, "version", missing_package)

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout == "ani 0.1.1\n"
    assert result.stderr == ""


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


def test_root_help_lists_lib_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "lib" in result.stdout


@pytest.mark.parametrize("command", ("zones", "records", "changes", "settings", "schema"))
def test_non_user_command_groups_are_removed(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


@pytest.mark.parametrize(
    ("args", "expected_exit", "stdout_entries", "stderr_fragment"),
    [
        (
            ["--json", "lib", "list", "--metadata"],
            2,
            None,
            "Option '--metadata' requires an argument.",
        ),
        (
            ["--json", "lib", "list", "-m"],
            2,
            None,
            "Option '-m' requires an argument.",
        ),
        (
            ["--json", "lib", "list", "--metadata=none"],
            2,
            None,
            "use --metadata <level>, not --metadata=<level>",
        ),
        (
            ["--json", "lib", "list", "-m=none"],
            2,
            None,
            "use -m <level>, not -m=<level>",
        ),
        (
            ["--json", "lib", "list", "--metadata", "full"],
            0,
            0,
            None,
        ),
        (["--json", "lib", "list", "--metadata", "none"], 0, 0, None),
    ],
)
def test_library_list_metadata_flag_handling(
    monkeypatch,
    args: list[str],
    expected_exit: int,
    stdout_entries: int | None,
    stderr_fragment: str | None,
) -> None:
    monkeypatch.setattr(library_commands, "_library_store_for_read", lambda: _fake_store())

    result = runner.invoke(app, args)

    assert result.exit_code == expected_exit
    if stdout_entries is not None:
        assert json.loads(result.stdout)["summary"]["entries"] == stdout_entries
    else:
        assert result.stdout == ""
    if stderr_fragment is not None:
        assert stderr_fragment in result.stderr


def test_library_get_accepts_matching_identity_after_separator(monkeypatch) -> None:
    monkeypatch.setattr(library_commands, "_library_store_for_read", lambda: _fake_store())

    result = runner.invoke(app, ["--json", "lib", "get", "--metadata", "summary", "--", "none"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"][0]["id"] == "none"
    assert payload["items"][0]["status"] == "error"
    assert payload["items"][0]["error"]["code"] == "invalid_id"


@pytest.mark.parametrize(
    ("args", "contains", "absent"),
    [
        (
            ["lib", "get"],
            (
                "--metadata",
                "-m",
                "--live-meta",
                "none",
                "summary",
                "details",
                "full",
                "[none|summary|details|full]",
                "--sync",
            ),
            (),
        ),
        (
            ["lib", "list"],
            (
                "--sync",
                "-f",
                "-s",
                "-w",
                "-l",
                "-j",
                "[none|summary|details|full]",
                "[table|list]",
                "[planToWatch|watching|watched|dropped]",
                "[saved|updated|title|score|started|finished|type|watch-status|air-date]",
                "[en|ja|zh]",
            ),
            ("--refresh-meta",),
        ),
        (["lib"], ("refresh-meta",), ("changes",)),
        (["lib"], ("AniShelf library commands.", "get", "refresh-meta"), ()),
        (["lib", "refresh-meta"], ("--json", "-j"), ("--metadata",)),
        (["lib", "search"], ("QUERY", "--sync", "--limit", "-l"), ("--title",)),
        (["lib", "export"], ("--sync",), ()),
        (
            ["tmdb", "search"],
            (
                "TITLE",
                "--title",
                "-t",
                "--limit",
                "-l",
                "--type",
                "--year",
                "-y",
                "--json",
                "-j",
                "[en|ja|zh]",
            ),
            (),
        ),
        (["lib", "init"], ("--json", "-j"), ()),
        (["lib", "sync"], ("--json", "-j"), ()),
        (["lib", "status"], ("--json", "-j"), ()),
        (["lib", "clear-cache"], ("--yes", "-y"), ()),
        (["auth", "logout"], ("clear local library cache files",), ()),
    ],
)
def test_help_text(args: list[str], contains: tuple[str, ...], absent: tuple[str, ...]) -> None:
    _assert_help(args, contains=contains, absent=absent)


def test_library_refresh_meta_rejects_ad_hoc_metadata_depth() -> None:
    result = runner.invoke(app, ["lib", "refresh-meta", "--metadata", "full"])

    assert result.exit_code == 2
    assert "No such option: --metadata" in result.stderr


def test_unknown_command_error_uses_plain_formatting() -> None:
    result = runner.invoke(app, ["auth", "loggg"])

    assert result.exit_code == 2
    assert "No such command 'loggg'. Did you mean 'login'?" in result.stderr
    for box_character in ("╭", "╮", "╰", "╯", "│", "─"):
        assert box_character not in result.stderr


def test_config_show_json_shows_effective_config_without_secrets(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)

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
    assert payload["tmdb"]["defaults"] == {
        "metadata_language": "en",
        "hydration_depth": "details",
    }
    assert payload["library"]["defaults"] == {
        "metadata": "summary",
        "display_fields": None,
        "output_style": "table",
        "show_hidden": False,
    }
    assert payload["secrets"] == {
        "backend": "system",
        "plaintext_file": None,
    }
    assert "config_dir" in payload["paths"]
    assert "config_file" in payload["paths"]
    assert "cache_dir" in payload["paths"]
    assert "data_dir" in payload["paths"]
    assert "profile" not in payload
    assert "anishelf_source" not in payload
    assert "cloudkit-api-token" not in result.stdout
    assert "api-secret-token" not in result.stdout
    assert "ckWebAuthToken" not in result.stdout


def test_config_show_accepts_command_level_json(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        ["config", "show", "--json"],
        env={"ANI_CLOUDKIT_API_TOKEN": "api-secret-token"},
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["cloudkit"]["app_auth_source"] == "env"
    assert "api-secret-token" not in result.stdout


def test_tmdb_search_verbose_logs_are_redacted(monkeypatch) -> None:
    monkeypatch.setattr(
        tmdb_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )
    monkeypatch.setattr(tmdb_commands, "default_secret_store", lambda: None)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"results": [{"id": 55, "title": "Alien", "genre_ids": [16]}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        tmdb_commands, "TMDbClient", lambda api_key: TMDbClient(api_key, client=client)
    )

    result = runner.invoke(
        app,
        ["--verbose", "--json", "tmdb", "search", "--title", "Alien", "--type", "movie"],
    )

    assert result.exit_code == 0, result.output
    assert requests != []
    assert '"total": 1' in result.stdout
    assert "[debug] TMDb request -> GET https://api.themoviedb.org/3/search/movie" in result.stderr
    assert "[debug] TMDb response <- HTTP 200 GET" in result.stderr
    assert "tmdb-secret-token" not in result.stderr
    assert "api_key=tmdb-secret-token" not in result.stderr
    assert "<redacted:sensitive-url>" in result.stderr or "<redacted:api_key>" in result.stderr


def test_verbose_flag_resets_across_multiple_invocations(monkeypatch) -> None:
    monkeypatch.setattr(
        tmdb_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )
    monkeypatch.setattr(tmdb_commands, "default_secret_store", lambda: None)

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"results": [{"id": 55, "title": "Alien", "genre_ids": [16]}]},
            )
        )
    )
    monkeypatch.setattr(
        tmdb_commands, "TMDbClient", lambda api_key: TMDbClient(api_key, client=client)
    )

    verbose_result = runner.invoke(
        app,
        ["--verbose", "--json", "tmdb", "search", "--title", "Alien", "--type", "movie"],
    )
    plain_result = runner.invoke(
        app,
        ["--json", "tmdb", "search", "--title", "Alien", "--type", "movie"],
    )

    assert verbose_result.exit_code == 0, verbose_result.output
    assert plain_result.exit_code == 0, plain_result.output
    assert "[debug] TMDb request -> GET https://api.themoviedb.org/3/search/movie" in (
        verbose_result.stderr
    )
    assert plain_result.stderr == ""


def test_config_show_human_output_uses_readable_sections(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "show"], env={"ANI_CLOUDKIT_API_TOKEN": "api"})

    assert result.exit_code == 0
    assert "CloudKit\n" in result.stdout
    assert "  Container" in result.stdout
    assert "iCloud.com.samuelhe.MyAnimeList" in result.stdout
    assert "  App auth" in result.stdout
    assert "env" in result.stdout
    assert "\nCallback\n" in result.stdout
    assert "  Strategy" in result.stdout
    assert "manual-paste" in result.stdout
    assert "\nTMDb\n" in result.stdout
    assert "  API key envs" in result.stdout
    assert "ANI_TMDB_API_KEY, TMDB_API_KEY" in result.stdout
    assert "  Metadata language" in result.stdout
    assert "en" in result.stdout
    assert "\nLibrary\n" in result.stdout
    assert "  Metadata" in result.stdout
    assert "  Display fields" in result.stdout
    assert "built-in" in result.stdout
    assert "  Show hidden" in result.stdout
    assert "no" in result.stdout
    assert "\nSecrets\n" in result.stdout
    assert "  Backend" in result.stdout
    assert "system" in result.stdout
    assert "  Plaintext file" in result.stdout
    assert "not used" in result.stdout
    assert "\nPaths\n" in result.stdout
    assert "  Config" in result.stdout
    assert "  Config file" in result.stdout
    assert "api" not in result.stdout


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


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["metadata", "--help"], "No such command 'metadata'."),
        (["config", "status"], "No such command"),
        (["profile", "status"], "No such command"),
        (["login"], "No such command"),
        (["logout"], "No such command"),
        (["whoami"], "No such command"),
        (["--profile", "prod", "config", "show"], "No such option"),
        (["config", "set-tmdb-token", "--help"], "No such command"),
        (["config", "set-cloudkit-token", "--help"], "No such command"),
        (["lib", "list", "--hidden"], "No such option"),
        (["lib", "list", "--on-display"], "No such option"),
        (["lib", "list", "--not-on-display"], "No such option"),
    ],
)
def test_removed_commands_and_options(args: list[str], message: str) -> None:
    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert message in result.stderr


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
    assert not (tmp_path / "config" / "config.toml").exists()


def test_config_set_defaults_stores_minimal_toml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "--json",
            "config",
            "set-defaults",
            "--metadata",
            "none",
            "--fields",
            "title,id,saved",
            "--style",
            "list",
            "--tmdb-language",
            "ja",
            "--show-hidden",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "stored"
    assert payload["defaults"]["library"] == {
        "metadata": "none",
        "display_fields": ["title", "id", "saved"],
        "output_style": "list",
        "show_hidden": True,
    }
    assert payload["defaults"]["tmdb"] == {
        "metadata_language": "ja",
        "hydration_depth": "details",
    }
    assert "TMDb metadata language changed" in result.stderr
    assert "ani lib clear-cache --yes" in result.stderr
    config_file = tmp_path / "config" / "config.toml"
    assert payload["path"] == str(config_file)
    assert config_file.read_text() == (
        '[library]\nmetadata = "none"\ndisplay_fields = ["title", "id", "saved"]\n'
        'output_style = "list"\nshow_hidden = true\n\n[tmdb]\nmetadata_language = "ja"\n'
    )


def test_config_set_secrets_backend_plaintext_requires_confirmation(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(app, ["config", "set-secrets-backend", "plaintext-file"], input="n\n")

    assert result.exit_code == 2
    assert "Security warning: plaintext-file stores CloudKit auth tokens" in result.stderr
    assert str(config.plaintext_keyring_file()) in result.stderr
    assert not config.user_config_file().exists()


def test_config_set_secrets_backend_plaintext_stores_warning_and_config(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        ["--json", "config", "set-secrets-backend", "plaintext-file", "--yes"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "stored",
        "backend": "plaintext-file",
        "plaintext_file": str(config.plaintext_keyring_file()),
        "path": str(config.user_config_file()),
    }
    assert "Security warning: plaintext-file stores CloudKit auth tokens" in result.stderr
    assert "reuse old plaintext" not in result.stderr
    assert config.user_config_file().read_text() == '[secrets]\nbackend = "plaintext-file"\n'


def test_config_set_secrets_backend_plaintext_preserves_parseable_unrelated_config_drift(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    existing_config = '[library]\nmetadata = "details"\n\n[tmdb]\nhydration_depth = "summary"\n'
    config.user_config_file().write_text(existing_config)

    result = runner.invoke(
        app,
        ["--json", "config", "set-secrets-backend", "plaintext-file", "--yes"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["backend"] == "plaintext-file"
    assert config.user_config_file().read_text() == (
        existing_config + '\n[secrets]\nbackend = "plaintext-file"\n'
    )


def test_config_set_secrets_backend_plaintext_warns_about_existing_plaintext_file(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.plaintext_keyring_file().parent.mkdir(parents=True, exist_ok=True)
    config.plaintext_keyring_file().write_text("old-plaintext-secret\n")

    result = runner.invoke(
        app,
        ["--json", "config", "set-secrets-backend", "plaintext-file", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "existing plaintext keyring file is present" in result.stderr
    assert "Delete that file first" in result.stderr
    assert str(config.plaintext_keyring_file()) in result.stderr
    assert "old-plaintext-secret" not in result.stdout + result.stderr


def test_config_set_secrets_backend_system_removes_minimal_plaintext_config(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('[secrets]\nbackend = "plaintext-file"\n')

    result = runner.invoke(app, ["--json", "config", "set-secrets-backend", "system"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "stored",
        "backend": "system",
        "plaintext_file": None,
        "path": str(config.user_config_file()),
    }
    assert not config.user_config_file().exists()


def test_config_set_secrets_backend_system_preserves_parseable_unrelated_config_drift(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text(
        '[library]\nfuture_key = true\n\n[secrets]\nbackend = "plaintext-file"\n'
    )

    result = runner.invoke(app, ["--json", "config", "set-secrets-backend", "system"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["backend"] == "system"
    assert config.user_config_file().read_text() == "[library]\nfuture_key = true\n"


def test_config_set_secrets_backend_system_rejects_misspelled_top_level_secrets_config(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config_text = '[secret]\nbackend = "plaintext-file"\n'
    config.user_config_file().write_text(config_text)

    result = runner.invoke(app, ["--json", "config", "set-secrets-backend", "system"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Unsupported top-level config key(s)" in result.stderr
    assert "'secret'" in result.stderr
    assert config.user_config_file().read_text() == config_text


@pytest.mark.parametrize(
    ("config_text", "args"),
    [
        (
            'secrets.backend = "system"\n',
            ["config", "set-secrets-backend", "plaintext-file", "--yes"],
        ),
        (
            'secrets = { backend = "plaintext-file" }\n',
            ["config", "set-secrets-backend", "system"],
        ),
    ],
)
def test_config_set_secrets_backend_rejects_unpatchable_existing_secret_shapes(
    tmp_path,
    monkeypatch,
    config_text: str,
    args: list[str],
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text(config_text)

    result = runner.invoke(app, args)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "must use a secrets TOML table" in " ".join(result.stderr.split())
    assert config.user_config_file().read_text() == config_text


def test_config_set_defaults_accepts_short_metadata_fields_style_and_json_options(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    result = runner.invoke(
        app,
        [
            "config",
            "set-defaults",
            "-m",
            "none",
            "-f",
            "title,id",
            "-s",
            "list",
            "-j",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["defaults"]["library"] == {
        "metadata": "none",
        "display_fields": ["title", "id"],
        "output_style": "list",
        "show_hidden": False,
    }


def test_config_set_defaults_can_reset_display_fields_to_builtin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\nmetadata = "none"\ndisplay_fields = ["title", "id"]\n'
    )

    result = runner.invoke(
        app,
        ["--json", "config", "set-defaults", "--fields", "default"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["defaults"]["library"] == {
        "metadata": "none",
        "display_fields": None,
        "output_style": "table",
        "show_hidden": False,
    }
    assert payload["defaults"]["tmdb"] == {
        "metadata_language": "en",
        "hydration_depth": "details",
    }
    assert (tmp_path / "config" / "config.toml").read_text() == ('[library]\nmetadata = "none"\n')


def test_config_set_defaults_can_reset_output_style_to_builtin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\nmetadata = "none"\noutput_style = "list"\n'
    )

    result = runner.invoke(
        app,
        ["--json", "config", "set-defaults", "--style", "table"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["defaults"]["library"] == {
        "metadata": "none",
        "display_fields": None,
        "output_style": "table",
        "show_hidden": False,
    }
    assert payload["defaults"]["tmdb"] == {
        "metadata_language": "en",
        "hydration_depth": "details",
    }
    assert (tmp_path / "config" / "config.toml").read_text() == ('[library]\nmetadata = "none"\n')


def test_config_show_reads_library_defaults_from_toml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\nmetadata = "none"\ndisplay_fields = ["title", "saved"]\n'
        'output_style = "list"\nshow_hidden = true\n\n[tmdb]\nmetadata_language = "zh"\n'
        '\n[secrets]\nbackend = "plaintext-file"\n'
    )

    result = runner.invoke(
        app,
        ["--json", "config", "show"],
        env={"ANI_CLOUDKIT_API_TOKEN": "api-secret-token"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["library"]["defaults"] == {
        "metadata": "none",
        "display_fields": ["title", "saved"],
        "output_style": "list",
        "show_hidden": True,
    }
    assert payload["tmdb"]["defaults"] == {
        "metadata_language": "zh",
        "hydration_depth": "details",
    }
    assert payload["secrets"] == {
        "backend": "plaintext-file",
        "plaintext_file": str(config.plaintext_keyring_file()),
    }


@pytest.mark.parametrize(
    ("args", "config_text", "message", "extra"),
    [
        (
            ["config", "set-defaults", "--metadata", "details"],
            None,
            "Library output defaults accept none or summary",
            None,
        ),
        (
            ["config", "set-defaults", "--fields", "title,bogus"],
            None,
            "Invalid display field 'bogus'",
            None,
        ),
        (
            ["config", "set-defaults", "--style", "grid"],
            None,
            "Invalid value",
            None,
        ),
        (
            ["config", "set-defaults", "--tmdb-language", "en-US"],
            None,
            "Invalid value",
            None,
        ),
        (
            ["config", "set-defaults", "--tmdb-language", "default"],
            None,
            "Invalid value",
            None,
        ),
        (
            ["config", "show"],
            '[tmdb]\nmetadata_language = "en-US"\n',
            "Invalid TMDb metadata language 'en-us'",
            None,
        ),
        (
            ["config", "show"],
            'unexpected = "value"\n',
            "Unsupported top-level config key(s)",
            "'unexpected'",
        ),
        (
            ["config", "show"],
            '[library]\nmetadata = "none"\nauto_sync = true\n',
            "Unsupported library defaults key(s)",
            "'auto_sync'",
        ),
        (
            ["config", "show"],
            '[library]\nshow_hidden = "true"\n',
            "library.show_hidden",
            "boolean.",
        ),
        (
            ["config", "show"],
            'library = "bad"\n',
            "must be a TOML table",
            None,
        ),
        (
            ["config", "show"],
            '[secrets]\nbackend = "encrypted-file"\n',
            "Invalid secrets backend 'encrypted-file'",
            None,
        ),
        (
            ["config", "show"],
            '[secrets]\npath = "custom"\n',
            "Unsupported secret defaults key(s)",
            "'path'",
        ),
        (
            ["config", "show"],
            'secrets = "bad"\n',
            "Secret defaults",
            "must be a TOML table",
        ),
    ],
)
def test_config_validation_errors(
    tmp_path,
    monkeypatch,
    args: list[str],
    config_text: str | None,
    message: str,
    extra: str | None,
) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    if config_text is not None:
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "config.toml").write_text(config_text)

    result = runner.invoke(app, args, env={"ANI_CLOUDKIT_API_TOKEN": "api"})

    assert result.exit_code == 2
    assert result.stdout == ""
    assert message in " ".join(result.stderr.split())
    if extra is not None:
        assert extra in result.stderr


def test_config_set_defaults_fails_on_broken_config_with_replacements(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    config_file = tmp_path / "config" / "config.toml"
    config_file.write_text('library = "bad"\n')

    result = runner.invoke(
        app,
        ["--json", "config", "set-defaults", "--metadata", "none"],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "must be a TOML table" in result.stderr
    assert config_file.read_text() == 'library = "bad"\n'


def test_config_set_defaults_still_fails_on_broken_config_without_replacements(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text('library = "bad"\n')

    result = runner.invoke(app, ["config", "set-defaults"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "must be a TOML table" in result.stderr


def test_save_user_defaults_does_not_overwrite_broken_existing_config(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('library = "bad"\n')

    with pytest.raises(config.UserConfigError, match="must be a TOML table"):
        config.save_user_defaults(config.UserDefaults())

    assert config.user_config_file().read_text() == 'library = "bad"\n'


def test_config_set_tmdb_api_key_stores_without_echoing_secret(monkeypatch) -> None:
    store = MemorySecretStore()
    monkeypatch.setattr(config_commands, "secret_store_for_backend", lambda backend: store)

    tmdb = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert tmdb.exit_code == 0
    assert "tmdb-secret-token" not in tmdb.stdout + tmdb.stderr
    assert ("anishelf-cli.tmdb-api-key", KEYCHAIN_ACCOUNT) in store.values


def test_config_set_tmdb_api_key_fails_with_config_guidance_when_system_fails(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)

    class FailingStore:
        def get_password(self, service: str, account: str) -> str | None:
            _ = service, account
            raise config_commands.SecretStorageUnavailableError("locked keyring")

        def set_password(self, service: str, account: str, password: str) -> None:
            _ = service, account, password
            raise config_commands.SecretStorageUnavailableError("locked keyring")

    original_store_for_backend = config_commands.secret_store_for_backend

    def store_for_backend(backend: config_commands.SecretBackend):
        if backend is config_commands.SecretBackend.SYSTEM:
            return FailingStore()
        return original_store_for_backend(backend)

    monkeypatch.setattr(config_commands, "secret_store_for_backend", store_for_backend)

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "tmdb-secret-token" not in result.stdout + result.stderr
    assert "locked keyring" in result.stderr
    assert 'secrets.backend = "system"' in result.stderr
    assert "ani config set-secrets-backend plaintext-file" in result.stderr
    assert not config.user_config_file().exists()


def test_config_set_tmdb_api_key_uses_explicit_plaintext_backend(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('[secrets]\nbackend = "plaintext-file"\n')

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {
        "secret_type": "tmdb-api-key",
        "status": "stored",
        "storage": "plaintext-file",
    }
    assert "tmdb-secret-token" not in result.stdout + result.stderr
    assert config.user_config_file().read_text() == '[secrets]\nbackend = "plaintext-file"\n'
    assert config.plaintext_keyring_file().exists()


def test_config_set_tmdb_api_key_uses_secrets_backend_with_unrelated_config_drift(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text(
        '[library]\nfuture_key = true\n\n[secrets]\nbackend = "plaintext-file"\n'
    )

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["storage"] == "plaintext-file"
    assert "tmdb-secret-token" not in result.stdout + result.stderr
    assert config.user_config_file().read_text() == (
        '[library]\nfuture_key = true\n\n[secrets]\nbackend = "plaintext-file"\n'
    )
    assert config.plaintext_keyring_file().exists()


def test_config_set_tmdb_api_key_plaintext_invalid_file_exits_cleanly(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('[secrets]\nbackend = "plaintext-file"\n')
    config.plaintext_keyring_file().parent.mkdir(parents=True, exist_ok=True)
    config.plaintext_keyring_file().write_text("tmdb-secret-token\n")

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="new-tmdb-secret\n",
    )

    combined_output = result.stdout + result.stderr
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Plaintext secret storage is unavailable" in result.stderr
    assert "Traceback" not in combined_output
    assert "tmdb-secret-token" not in combined_output
    assert "new-tmdb-secret" not in combined_output


def test_config_set_tmdb_api_key_plaintext_unusable_data_dir_exits_cleanly(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('[secrets]\nbackend = "plaintext-file"\n')
    (tmp_path / "data").write_text("not-a-directory\n")

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="new-tmdb-secret\n",
    )

    combined_output = result.stdout + result.stderr
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Plaintext secret storage is unavailable" in result.stderr
    assert "Traceback" not in combined_output
    assert "new-tmdb-secret" not in combined_output


def test_config_set_tmdb_api_key_malformed_secrets_config_exits_cleanly(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('secrets = "bad"\n')

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="new-tmdb-secret\n",
    )

    combined_output = result.stdout + result.stderr
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Secret defaults" in result.stderr
    assert "must be a TOML table" in result.stderr
    assert "Traceback" not in combined_output
    assert "new-tmdb-secret" not in combined_output


def test_config_set_tmdb_api_key_rejects_misspelled_top_level_secrets_config(
    tmp_path,
    monkeypatch,
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('[secret]\nbackend = "plaintext-file"\n')

    result = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="new-tmdb-secret\n",
    )

    combined_output = result.stdout + result.stderr
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Unsupported top-level config key(s)" in result.stderr
    assert "'secret'" in result.stderr
    assert "Traceback" not in combined_output
    assert "new-tmdb-secret" not in combined_output


def test_tmdb_search_json_output_is_stable(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
        ),
        series=(
            _tmdb_match(
                "series",
                95,
                "Alien Nation",
                release_date="1989-09-18",
                overview="A sci-fi police series.",
                poster_path="/series.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "query": {"language": "en", "mode": "search", "title": "Alien", "type": "all"},
        "results": {
            "movies": [
                {
                    "details_url": "https://www.themoviedb.org/movie/55",
                    "entry_type": "movie",
                    "original_language_code": "en",
                    "original_title": "Alien",
                    "overview": "A space horror film.",
                    "poster_path": "/poster.jpg",
                    "release_date": "1979-05-25",
                    "title": "Alien",
                    "tmdb_id": 55,
                }
            ],
            "series": [
                {
                    "details_url": "https://www.themoviedb.org/tv/95",
                    "entry_type": "series",
                    "original_language_code": "en",
                    "original_title": "Alien Nation",
                    "overview": "A sci-fi police series.",
                    "poster_path": "/series.jpg",
                    "release_date": "1989-09-18",
                    "title": "Alien Nation",
                    "tmdb_id": 95,
                }
            ],
        },
        "summary": {"movies": 1, "series": 1, "total": 2},
    }


def test_tmdb_search_marks_titles_already_in_the_library(tmp_path, monkeypatch) -> None:
    from tests.support import create_seeded_cache_store, live_record

    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("movie:55", "movie", 55),
        live_record("season:95:2:902", "season", 902),
        live_record("series:95", "series", 95),
    )
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie", 55, "Alien", release_date="1979-05-25", overview="", poster_path=""
            ),
            _tmdb_match(
                "movie", 56, "Aliens", release_date="1986-07-18", overview="", poster_path=""
            ),
        ),
        series=(
            _tmdb_match(
                "series", 95, "Alien Nation", release_date="1989-09-18", overview="", poster_path=""
            ),
        ),
    )

    machine = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])
    human = runner.invoke(app, ["tmdb", "search", "Alien"])

    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout)
    assert payload["summary"]["in_library"] == 2
    assert [match["library_ids"] for match in payload["results"]["movies"]] == [["movie:55"], []]
    assert payload["results"]["series"][0]["library_ids"] == ["series:95", "season:95:2:902"]
    assert human.exit_code == 0, human.output
    assert "In library  2" in human.stdout
    assert "yes, S2" in human.stdout


def _install_alien_search(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie", 95, "Alien", release_date="1979-05-25", overview="", poster_path=""
            ),
        ),
        series=(
            _tmdb_match(
                "series", 95, "Alien Nation", release_date="1989-09-18", overview="", poster_path=""
            ),
        ),
    )


def test_tmdb_search_library_markers_keep_movie_and_series_ids_apart(
    tmp_path,
    monkeypatch,
) -> None:
    from tests.support import create_seeded_cache_store, live_record

    create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("season:95:10:910", "season", 910),
        live_record("season:95:2:902", "season", 902, on_display=False),
    )
    _install_alien_search(monkeypatch)

    machine = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])
    human = runner.invoke(app, ["tmdb", "search", "Alien"])

    payload = json.loads(machine.stdout)
    # Movie 95 is a different title from series 95; only the series has saved seasons.
    assert payload["results"]["movies"][0]["library_ids"] == []
    assert payload["results"]["series"][0]["library_ids"] == [
        "season:95:2:902",
        "season:95:10:910",
    ]
    assert "S2, S10" in human.stdout


def test_tmdb_search_omits_markers_for_an_empty_cache(tmp_path, monkeypatch) -> None:
    from tests.support import create_seeded_cache_store

    create_seeded_cache_store(monkeypatch, tmp_path)
    _install_alien_search(monkeypatch)

    result = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])

    assert result.exit_code == 0, result.output
    assert "in_library" not in json.loads(result.stdout)["summary"]


def test_tmdb_search_never_rewrites_a_cache_with_another_schema_version(
    tmp_path,
    monkeypatch,
) -> None:
    import sqlite3

    from tests.support import create_seeded_cache_store, live_record

    store = create_seeded_cache_store(
        monkeypatch,
        tmp_path,
        live_record("movie:95", "movie", 95),
    )
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE cache_meta SET value = '2' WHERE key = 'schema_version'")
    _install_alien_search(monkeypatch)

    result = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])

    assert result.exit_code == 0, result.output
    assert "in_library" not in json.loads(result.stdout)["summary"]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM library_entries").fetchone()[0] == 1


def test_tmdb_search_survives_cache_filesystem_errors(monkeypatch) -> None:
    from anishelf_cli.cache.store import LibraryCacheStore

    def fail_scope() -> LibraryCacheStore:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(LibraryCacheStore, "find_default_scope", staticmethod(fail_scope))
    _install_alien_search(monkeypatch)

    result = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])

    assert result.exit_code == 0, result.output
    assert "in_library" not in json.loads(result.stdout)["summary"]


def test_tmdb_search_omits_library_markers_without_a_local_cache(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie", 55, "Alien", release_date="1979-05-25", overview="", poster_path=""
            ),
        ),
        series=(),
    )

    machine = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])
    human = runner.invoke(app, ["tmdb", "search", "Alien"])

    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout)
    assert "in_library" not in payload["summary"]
    assert "library_ids" not in payload["results"]["movies"][0]
    assert "Library" not in human.stdout


def test_tmdb_search_accepts_root_level_json_output(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["--json", "tmdb", "search", "--title", "Alien"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "query": {"language": "en", "mode": "search", "title": "Alien", "type": "all"},
        "results": {
            "movies": [
                {
                    "details_url": "https://www.themoviedb.org/movie/55",
                    "entry_type": "movie",
                    "original_language_code": "en",
                    "original_title": "Alien",
                    "overview": "A space horror film.",
                    "poster_path": "/poster.jpg",
                    "release_date": "1979-05-25",
                    "title": "Alien",
                    "tmdb_id": 55,
                }
            ],
            "series": [],
        },
        "summary": {"movies": 1, "series": 0, "total": 1},
    }


def test_tmdb_search_accepts_positional_title(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "Alien", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["query"] == {"language": "en", "mode": "search", "title": "Alien", "type": "all"}
    assert payload["summary"] == {"movies": 1, "series": 0, "total": 1}


def test_tmdb_search_rejects_positional_and_option_title_together(monkeypatch) -> None:
    monkeypatch.setattr(tmdb_commands, "_tmdb_summary_client_or_exit", lambda: None)

    result = runner.invoke(app, ["tmdb", "search", "Alien", "--title", "Cowboy"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Use either positional TITLE or --title, not both." in result.stderr


def test_tmdb_search_limit_caps_total_json_results(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
            _tmdb_match(
                "movie",
                56,
                "Aliens",
                release_date="1986-07-18",
                overview="A space action film.",
                poster_path="/aliens.jpg",
            ),
        ),
        series=(
            _tmdb_match(
                "series",
                95,
                "Alien Nation",
                release_date="1989-09-18",
                overview="A sci-fi police series.",
                poster_path="/series.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien", "--limit", "1", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["query"] == {
        "language": "en",
        "limit": 1,
        "mode": "search",
        "title": "Alien",
        "type": "all",
    }
    assert payload["summary"] == {"movies": 1, "series": 0, "total": 1}
    assert [match["tmdb_id"] for match in payload["results"]["movies"]] == [55]
    assert payload["results"]["series"] == []


def test_tmdb_search_human_output_is_concise(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien"])

    assert result.exit_code == 0
    assert "TMDb search\n" in result.stdout
    assert "  Mode      search\n" in result.stdout
    assert "  Query     Alien\n" in result.stdout
    assert "  Language  en\n" in result.stdout
    assert "\nMovies\n" in result.stdout
    assert "TMDb ID" in result.stdout
    assert "Alien" in result.stdout
    assert "79/05/25" in result.stdout


def test_tmdb_search_discovers_without_title_by_default(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title=None, year=None, entry_type="all"),
        series=(
            _tmdb_match(
                "series",
                1399,
                "Game of Thrones",
                release_date="2011-04-17",
                overview="Noble families fight for control.",
                poster_path="/got.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["query"] == {"language": "en", "mode": "discover", "type": "all"}
    assert payload["summary"] == {"movies": 0, "series": 1, "total": 1}
    assert payload["results"]["movies"] == []
    assert payload["results"]["series"][0]["tmdb_id"] == 1399


def test_tmdb_search_treats_whitespace_title_as_discover_query(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title=None, year=None, entry_type="all"),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "   ", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["query"] == {"language": "en", "mode": "discover", "type": "all"}
    assert payload["summary"] == {"movies": 0, "series": 0, "total": 0}


def test_tmdb_search_discovers_without_title_and_forwards_filters(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title=None, year=1979, entry_type="movie"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "--type", "movie", "--year", "1979", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["query"] == {
        "language": "en",
        "mode": "discover",
        "type": "movie",
        "year": 1979,
    }
    assert payload["summary"] == {"movies": 1, "series": 0, "total": 1}
    assert payload["results"]["movies"][0]["tmdb_id"] == 55
    assert payload["results"]["series"] == []


def test_tmdb_search_accepts_short_title_year_and_json_options(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=1979, entry_type="all"),
        movies=(
            _tmdb_match(
                "movie",
                55,
                "Alien",
                release_date="1979-05-25",
                overview="A space horror film.",
                poster_path="/poster.jpg",
            ),
        ),
    )

    result = runner.invoke(app, ["tmdb", "search", "-t", "Alien", "-y", "1979", "-j"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["query"] == {
        "language": "en",
        "mode": "search",
        "title": "Alien",
        "type": "all",
        "year": 1979,
    }
    assert payload["results"]["movies"][0]["tmdb_id"] == 55


def test_tmdb_search_human_output_reports_no_results(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien"])

    assert result.exit_code == 0
    assert "TMDb search\n" in result.stdout
    assert "  Mode      search\n" in result.stdout
    assert "  Query     Alien\n" in result.stdout
    assert "  Language  en\n" in result.stdout
    assert "  Movies    0\n" in result.stdout
    assert "  Series    0\n" in result.stdout
    assert "No TMDb titles matched the query." in result.stdout


def test_tmdb_search_requires_configured_tmdb_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANI_TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.setattr(tmdb_commands, "default_secret_store", lambda: MemorySecretStore())

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "TMDb API key is not configured." in result.stderr
    assert "ANI_TMDB_API_KEY" in result.stderr
    assert "TMDB_API_KEY" in result.stderr
    assert "config set-tmdb-api-key" in result.stderr


def test_tmdb_search_reports_request_errors(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
        error=TMDbRequestError("TMDb title search failed."),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "TMDb title search failed." in result.stderr


def test_auth_group_lists_auth_commands() -> None:
    result = runner.invoke(app, ["auth", "--help"])

    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "logout" in result.stdout
    assert "status" in result.stdout
    assert "refresh" in result.stdout


@pytest.mark.parametrize("command", (["auth", "logout"], ["auth", "status"], ["auth", "refresh"]))
def test_auth_commands_malformed_secrets_config_exit_cleanly(
    tmp_path,
    monkeypatch,
    command: list[str],
) -> None:
    isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-token")
    config.user_config_file().parent.mkdir(parents=True, exist_ok=True)
    config.user_config_file().write_text('secrets = "bad"\n')

    result = runner.invoke(app, command)

    combined_output = result.stdout + result.stderr
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Secret defaults" in result.stderr
    assert "must be a TOML table" in result.stderr
    assert "Traceback" not in combined_output
    assert "api-secret-token" not in combined_output


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


def test_logout_deletes_web_auth_token(monkeypatch) -> None:
    store = _store_with_web_auth_token()
    descriptor = cloudkit_web_auth_token_secret()
    monkeypatch.setattr(root, "default_secret_store", lambda: store)
    monkeypatch.setattr(
        root.LibraryCacheStore,
        "remove_all_local_caches",
        classmethod(lambda cls: RemovedCacheFilesResult(cache_files=2, lock_files=1)),
    )

    result = runner.invoke(app, ["--json", "auth", "logout"])

    assert result.exit_code == 0
    assert store.get_password(descriptor.service, descriptor.account) is None
    assert json.loads(result.stdout) == {
        "status": "logged-out",
        "cache": {
            "status": "cleared",
            "cache_files": 2,
            "lock_files": 1,
        },
    }


def test_logout_deletes_web_auth_token_before_releasing_lock(monkeypatch) -> None:
    events: list[str] = []
    store = _store_with_web_auth_token()
    monkeypatch.setattr(root, "default_secret_store", lambda: store)
    original_delete_password = store.delete_password

    def delete_password(service: str, account: str) -> None:
        events.append("delete-token")
        original_delete_password(service, account)

    @contextmanager
    def recording_lock(path: Path) -> Generator[None]:
        _ = path
        events.append("enter-lock")
        try:
            yield
        finally:
            events.append("exit-lock")

    store.delete_password = delete_password  # type: ignore[method-assign]
    monkeypatch.setattr(root, "whoami_lock_factory", lambda path: recording_lock(path))
    monkeypatch.setattr(
        root.LibraryCacheStore,
        "remove_all_local_caches",
        classmethod(lambda cls: RemovedCacheFilesResult(cache_files=0, lock_files=0)),
    )

    result = runner.invoke(app, ["--json", "auth", "logout"])

    assert result.exit_code == 0
    assert events == ["enter-lock", "delete-token", "exit-lock"]
    assert json.loads(result.stdout) == {
        "status": "logged-out",
        "cache": {
            "status": "cleared",
            "cache_files": 0,
            "lock_files": 0,
        },
    }


def test_whoami_success_json_uses_authenticated_current_user(monkeypatch) -> None:
    store = _store_with_web_auth_token()
    requests: list[httpx.Request] = []

    _install_root_auth_store(monkeypatch, store)

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

    _install_root_http_client(monkeypatch, handler)

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
    store = _store_with_web_auth_token()
    descriptor = cloudkit_web_auth_token_secret()
    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "userRecordName": "_abc123",
                "webAuthToken": "new-web-secret-token",
            },
        ),
    )

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
    store = _store_with_web_auth_token()
    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "userRecordName": "_abc123",
                "firstName": "Ani",
                "lastName": "Shelf",
            },
        ),
    )

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "Authenticated to CloudKit." in result.stdout
    assert "Name: Ani Shelf" in result.stdout
    assert "User record: _abc123" in result.stdout
    assert "web-secret-token" not in result.stdout + result.stderr


def test_whoami_missing_login_tells_user_to_login_without_network(monkeypatch) -> None:
    store = MemorySecretStore()
    requests: list[httpx.Request] = []

    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(500),
    )

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Run `ani auth login`" in result.stderr
    assert requests == []
    assert "api-secret-token" not in result.stdout + result.stderr


def test_whoami_saves_successor_token_before_releasing_lock(monkeypatch) -> None:
    store = _store_with_web_auth_token("old-web-secret-token")
    descriptor = cloudkit_web_auth_token_secret()
    events: list[str] = []

    _install_root_auth_store(monkeypatch, store)
    original_set_password = store.set_password

    def set_password(service: str, account: str, password: str) -> None:
        events.append(f"save:{password}")
        original_set_password(service, account, password)

    store.set_password = set_password  # type: ignore[method-assign]

    @contextmanager
    def recording_lock(path: Path) -> Generator[None]:
        _ = path
        events.append("enter-lock")
        try:
            yield
        finally:
            events.append("exit-lock")

    monkeypatch.setattr(root, "whoami_lock_factory", lambda path: recording_lock(path))

    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "userRecordName": "_abc123",
                "webAuthToken": "new-web-secret-token",
            },
        ),
    )

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 0, result.output
    assert events == ["enter-lock", "save:new-web-secret-token", "exit-lock"]
    assert store.get_password(descriptor.service, descriptor.account) == "new-web-secret-token"
    assert "new-web-secret-token" not in result.stdout + result.stderr


def test_whoami_auth_failure_clears_login_and_redacts_tokens(monkeypatch) -> None:
    store = _store_with_web_auth_token("bad-web-secret-token")
    descriptor = cloudkit_web_auth_token_secret()
    deleted: list[tuple[str, str]] = []

    _install_root_auth_store(monkeypatch, store)
    original_delete_password = store.delete_password

    def delete_password(service: str, account: str) -> None:
        deleted.append((service, account))
        original_delete_password(service, account)

    store.delete_password = delete_password  # type: ignore[method-assign]

    _install_root_http_client(
        monkeypatch,
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
        ),
    )

    result = runner.invoke(app, ["--json", "auth", "status"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "saved CloudKit login expired and was removed" in result.stderr
    assert "run `ani auth login`" in result.stderr
    assert deleted == [(descriptor.service, descriptor.account)]
    assert store.get_password(descriptor.service, descriptor.account) is None
    combined = result.stdout + result.stderr
    assert "api-secret-token" not in combined
    assert "bad-web-secret-token" not in combined
    assert "successor-secret-token" not in combined
    assert "callback-secret-token" not in combined
    assert "https://callback.example/done" not in combined


def test_whoami_non_json_403_preserves_login(monkeypatch) -> None:
    store = _store_with_web_auth_token()
    descriptor = cloudkit_web_auth_token_secret()
    deleted: list[tuple[str, str]] = []

    _install_root_auth_store(monkeypatch, store)
    original_delete_password = store.delete_password

    def delete_password(service: str, account: str) -> None:
        deleted.append((service, account))
        original_delete_password(service, account)

    store.delete_password = delete_password  # type: ignore[method-assign]

    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            403,
            content=b"<html><body>forbidden</body></html>",
            request=request,
        ),
    )

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 2
    assert "non-JSON response (HTTP 403)" in result.stderr
    assert "run `ani auth login`" not in result.stderr
    assert deleted == []
    assert store.get_password(descriptor.service, descriptor.account) == "web-secret-token"
    assert "api-secret-token" not in result.stdout + result.stderr
    assert "web-secret-token" not in result.stdout + result.stderr


def test_whoami_unclassified_json_403_preserves_login(monkeypatch) -> None:
    store = _store_with_web_auth_token()
    descriptor = cloudkit_web_auth_token_secret()
    deleted: list[tuple[str, str]] = []

    _install_root_auth_store(monkeypatch, store)
    original_delete_password = store.delete_password

    def delete_password(service: str, account: str) -> None:
        deleted.append((service, account))
        original_delete_password(service, account)

    store.delete_password = delete_password  # type: ignore[method-assign]

    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            403,
            json={"reason": "interstitial blocked request"},
            request=request,
        ),
    )

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 2
    assert "CloudKit whoami request failed (HTTP 403: interstitial blocked request)" in (
        result.stderr
    )
    assert "run `ani auth login`" not in result.stderr
    assert deleted == []
    assert store.get_password(descriptor.service, descriptor.account) == "web-secret-token"
    assert "api-secret-token" not in result.stdout + result.stderr
    assert "web-secret-token" not in result.stdout + result.stderr


def test_whoami_redacts_non_auth_cloudkit_error_details(monkeypatch) -> None:
    store = _store_with_web_auth_token()
    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
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
        ),
    )

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


def _executor_with_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> tuple[CloudKitExecutor, MemorySecretStore, list[float]]:
    monkeypatch.setenv("ANISHELF_CLI_DATA_DIR", str(tmp_path / "data"))
    sleeps: list[float] = []
    monkeypatch.setattr("anishelf_cli.cloudkit.executor.time.sleep", sleeps.append)
    store = MemorySecretStore()
    descriptor = cloudkit_web_auth_token_secret()
    store.set_password(descriptor.service, descriptor.account, "web-secret-token")
    executor = CloudKitExecutor(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        api_token_resolver=lambda: CloudKitAPIToken("api-secret-token", "test"),
        secret_store=store,
    )
    return executor, store, sleeps


def test_cloudkit_executor_retries_connection_failures(tmp_path, monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]", request=request)
        return httpx.Response(200, json={"userRecordName": "_user"})

    executor, _, sleeps = _executor_with_handler(tmp_path, monkeypatch, handler)

    assert executor.get_current_user().user_record_name == "_user"
    assert len(requests) == 2
    assert sleeps == [0.5]


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.WriteError,
        httpx.WriteTimeout,
        httpx.RemoteProtocolError,
        httpx.ProxyError,
        httpx.PoolTimeout,
    ],
)
def test_cloudkit_executor_does_not_retry_failures_after_the_request_was_sent(
    tmp_path,
    monkeypatch,
    error_type: type[httpx.TransportError],
) -> None:
    from anishelf_cli.cloudkit.executor import CloudKitRequestFailedError

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise error_type("transport failure", request=request)

    executor, store, sleeps = _executor_with_handler(tmp_path, monkeypatch, handler)

    with pytest.raises(CloudKitRequestFailedError) as exc_info:
        executor.get_current_user()

    assert len(requests) == 1
    assert sleeps == []
    assert str(exc_info.value) == (
        f"CloudKit whoami request failed ({error_type.__name__}). "
        "Check your network connection and try again."
    )
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "web-secret-token"


def test_cloudkit_executor_gives_up_after_bounded_connection_retries(
    tmp_path,
    monkeypatch,
) -> None:
    from anishelf_cli.cloudkit.executor import CloudKitRequestFailedError

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectTimeout(f"timed out connecting for {request.url}", request=request)

    executor, store, sleeps = _executor_with_handler(tmp_path, monkeypatch, handler)

    with pytest.raises(CloudKitRequestFailedError) as exc_info:
        executor.get_current_user()

    assert len(requests) == 4
    assert sleeps == [0.5, 1.0, 1.5]
    message = str(exc_info.value)
    assert "(ConnectTimeout)" in message
    assert "web-secret-token" not in message
    assert "api-secret-token" not in message
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "web-secret-token"


def test_cloudkit_executor_connection_retries_reuse_token_and_save_successor(
    tmp_path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(
            200,
            json={"userRecordName": "_user", "webAuthToken": "successor-secret-token"},
        )

    executor, store, _ = _executor_with_handler(tmp_path, monkeypatch, handler)

    assert executor.get_current_user().user_record_name == "_user"
    assert [request.url.params["ckWebAuthToken"] for request in requests] == [
        "web-secret-token",
        "web-secret-token",
        "web-secret-token",
    ]
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "successor-secret-token"


def test_cloudkit_executor_saves_successor_token_from_response_header(
    tmp_path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"X-Apple-CloudKit-Web-Auth-Token": f"header-successor-{len(requests)}"},
            json={"userRecordName": "_user"},
        )

    executor, store, _ = _executor_with_handler(tmp_path, monkeypatch, handler)

    executor.get_current_user()
    executor.get_current_user()

    # Each request sends the token the previous response rolled forward.
    assert [request.url.params["ckWebAuthToken"] for request in requests] == [
        "web-secret-token",
        "header-successor-1",
    ]
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "header-successor-2"


def test_cloudkit_executor_ignores_header_successor_on_error_response(
    tmp_path,
    monkeypatch,
) -> None:
    from anishelf_cli.cloudkit.executor import CloudKitRequestFailedError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            headers={"X-Apple-CloudKit-Web-Auth-Token": "header-successor-token"},
            json={"serverErrorCode": "INTERNAL_ERROR", "reason": "boom"},
        )

    executor, store, _ = _executor_with_handler(tmp_path, monkeypatch, handler)

    with pytest.raises(CloudKitRequestFailedError) as raised:
        executor.get_current_user()

    assert "header-successor-token" not in str(raised.value)
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "web-secret-token"


def test_cloudkit_executor_ignores_repeated_successor_header(tmp_path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers=[
                ("X-Apple-CloudKit-Web-Auth-Token", "first-successor-token"),
                ("X-Apple-CloudKit-Web-Auth-Token", "second-successor-token"),
            ],
            json={"userRecordName": "_user"},
        )

    executor, store, _ = _executor_with_handler(tmp_path, monkeypatch, handler)

    executor.get_current_user()

    # httpx would join the values into one invalid token; keep the stored login.
    descriptor = cloudkit_web_auth_token_secret()
    assert store.get_password(descriptor.service, descriptor.account) == "web-secret-token"


def test_whoami_auth_failure_with_successor_header_clears_login(monkeypatch) -> None:
    store = _store_with_web_auth_token("bad-web-secret-token")
    descriptor = cloudkit_web_auth_token_secret()
    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            401,
            headers={"X-Apple-CloudKit-Web-Auth-Token": "header-successor-token"},
            json={"serverErrorCode": "AUTHENTICATION_FAILED", "reason": "expired"},
        ),
    )

    result = runner.invoke(app, ["--verbose", "--json", "auth", "status"])

    assert result.exit_code == 2
    assert store.get_password(descriptor.service, descriptor.account) is None
    assert (
        "[debug] CloudKit web auth token -> cleared after serverErrorCode=AUTHENTICATION_FAILED"
        in result.stderr
    )
    assert "header-successor-token" not in result.stdout + result.stderr


def test_whoami_verbose_logs_ignored_successor_on_error_response(monkeypatch) -> None:
    store = _store_with_web_auth_token("old-web-secret-token")
    descriptor = cloudkit_web_auth_token_secret()
    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            503,
            headers={"X-Apple-CloudKit-Web-Auth-Token": "header-successor-token"},
            json={"serverErrorCode": "SERVICE_UNAVAILABLE", "reason": "try later"},
        ),
    )

    result = runner.invoke(app, ["--verbose", "--json", "auth", "status"])

    assert result.exit_code == 2
    assert "[debug] CloudKit web auth token -> successor ignored on HTTP 503" in result.stderr
    assert store.get_password(descriptor.service, descriptor.account) == "old-web-secret-token"
    assert "header-successor-token" not in result.stdout + result.stderr


def test_whoami_verbose_saves_header_successor_without_printing_it(monkeypatch) -> None:
    store = _store_with_web_auth_token("old-web-secret-token")
    descriptor = cloudkit_web_auth_token_secret()
    _install_root_auth_store(monkeypatch, store)
    _install_root_http_client(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={
                "X-Apple-CloudKit-Web-Auth-Token": "header-successor-token",
                "X-Apple-Request-UUID": "request-uuid-1",
            },
            json={"userRecordName": "_abc123"},
        ),
    )

    result = runner.invoke(app, ["--verbose", "--json", "auth", "status"])

    assert result.exit_code == 0, result.output
    assert "[debug] CloudKit web auth token lock -> acquired wait=" in result.stderr
    assert "attempt=1/4 elapsed=" in result.stderr
    assert "requestId=request-uuid-1" in result.stderr
    assert "[debug] CloudKit web auth token -> rolled forward, successor stored" in result.stderr
    assert store.get_password(descriptor.service, descriptor.account) == "header-successor-token"
    combined = result.stdout + result.stderr
    assert "header-successor-token" not in combined
    assert "old-web-secret-token" not in combined


def test_config_set_defaults_hydration_depth_accepts_only_cache_depths(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))

    raised = runner.invoke(app, ["config", "set-defaults", "--hydration-depth", "full"])
    invalid = runner.invoke(app, ["config", "set-defaults", "--hydration-depth", "summary"])
    shown = runner.invoke(app, ["config", "show"], env={"ANI_CLOUDKIT_API_TOKEN": "api"})
    help_text = runner.invoke(app, ["config", "set-defaults", "--help"])

    assert raised.exit_code == 0, raised.output
    assert "next `ani lib sync`" in " ".join(raised.stderr.split())
    assert invalid.exit_code == 2
    assert "  Hydration depth    full" in shown.stdout
    assert "[details|full]" in help_text.stdout
    assert "none, summary, details, or full" in " ".join(help_text.stdout.split())
