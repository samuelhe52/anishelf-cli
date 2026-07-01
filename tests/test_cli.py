import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from anishelf_cli import config
from anishelf_cli.cli import config_commands, groups, library_commands, root, tmdb_commands
from anishelf_cli.cli.root import _normalize_metadata_args, app
from anishelf_cli.cloudkit.api_token import CloudKitAPIToken
from anishelf_cli.cloudkit.executor import CloudKitExecutor
from anishelf_cli.config import KEYCHAIN_ACCOUNT
from anishelf_cli.models.tmdb import (
    TMDbTitleSearchMatch,
    TMDbTitleSearchQuery,
    TMDbTitleSearchResult,
)
from anishelf_cli.secrets import cloudkit_web_auth_token_secret
from anishelf_cli.tmdb.client import TMDbClient, TMDbRequestError
from anishelf_cli.tmdb.tokens import TMDbAPIToken
from tests.support import MemorySecretStore, runner


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
        search_entry_models_by_title=lambda title: [],
        attach_metadata_summary_models=lambda entries: entries,
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
    assert "--profile" not in result.stdout
    assert "--json" in result.stdout
    assert "--verbose" in result.stdout
    assert "--metadata-depth" not in result.stdout
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


def test_root_help_lists_lib_alias() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "library" in result.stdout
    assert "lib" in result.stdout


def test_command_tree_registers_public_groups() -> None:
    group_names = {group.name for group in app.registered_groups}

    assert {"auth", "config", "library", "lib", "tmdb"} <= group_names
    assert groups.config_app is config_commands.config_app
    assert groups.library_app is library_commands.library_app
    assert groups.tmdb_app is tmdb_commands.tmdb_app


@pytest.mark.parametrize("command", ("zones", "records", "changes", "settings", "schema"))
def test_non_user_command_groups_are_removed(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 2
    assert "No such command" in result.stderr


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            ["library", "get", "--metadata", "movie:55"],
            ["library", "get", "--metadata=summary", "movie:55"],
        ),
        (
            ["library", "list", "--metadata", "details", "--json"],
            ["library", "list", "--metadata=details", "--json"],
        ),
        (
            ["library", "export", "--metadata", "none"],
            ["library", "export", "--metadata=none"],
        ),
        (
            ["library", "get", "--metadata", "--", "none"],
            ["library", "get", "--metadata=summary", "--", "none"],
        ),
    ],
)
def test_normalize_metadata_args(args: list[str], expected: list[str]) -> None:
    assert _normalize_metadata_args(args) == expected


@pytest.mark.parametrize(
    ("args", "expected_exit", "stdout_entries", "stderr_fragment"),
    [
        (["--json", "library", "list", "--metadata"], 0, 0, None),
        (
            ["--json", "library", "list", "--metadata", "full"],
            2,
            None,
            "reserved until TMDb detail metadata caching exists",
        ),
        (["--json", "library", "list", "--metadata", "none"], 0, 0, None),
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

    result = runner.invoke(app, ["--json", "library", "get", "--metadata", "--", "none"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"requested": 1, "found": 0, "errors": 1}
    assert payload["items"][0]["identity"] == "none"
    assert payload["items"][0]["status"] == "error"
    assert payload["items"][0]["error"]["code"] == "invalid_identity"


@pytest.mark.parametrize(
    ("args", "contains", "absent"),
    [
        (
            ["library", "get"],
            ("--metadata", "--live-meta", "none", "summary", "details", "full", "--sync"),
            (),
        ),
        (["library", "list"], ("--sync",), ("--refresh-meta",)),
        (["library"], ("refresh-meta",), ("changes",)),
        (["lib"], ("AniShelf library commands.", "get", "refresh-meta"), ()),
        (["library", "refresh-meta"], ("--json",), ()),
        (["library", "search"], ("--sync",), ()),
        (["library", "export"], ("--sync",), ()),
        (["tmdb", "search"], ("--title", "--type", "--year", "--json"), ()),
        (["library", "init"], ("--json",), ()),
        (["library", "sync"], ("--json",), ()),
        (["library", "status"], ("--json",), ()),
        (["library", "clear-cache"], ("--yes",), ()),
        (["auth", "logout"], ("clear local library cache files",), ()),
    ],
)
def test_help_text(args: list[str], contains: tuple[str, ...], absent: tuple[str, ...]) -> None:
    _assert_help(args, contains=contains, absent=absent)


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
    assert payload["library"]["defaults"] == {
        "metadata": "summary",
        "display_fields": None,
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
        return httpx.Response(200, json={"results": [{"id": 55, "title": "Alien"}]})

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
    assert (
        "[verbose] TMDb request -> GET https://api.themoviedb.org/3/search/movie" in result.stderr
    )
    assert "[verbose] TMDb response <- HTTP 200 GET" in result.stderr
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
            lambda request: httpx.Response(200, json={"results": [{"id": 55, "title": "Alien"}]})
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
    assert "[verbose] TMDb request -> GET https://api.themoviedb.org/3/search/movie" in (
        verbose_result.stderr
    )
    assert plain_result.stderr == ""


def test_config_show_human_output_uses_readable_sections() -> None:
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
    assert "\nLibrary\n" in result.stdout
    assert "  Metadata" in result.stdout
    assert "  Display fields" in result.stdout
    assert "built-in" in result.stdout
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
            "title,identity,saved",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "stored"
    assert payload["defaults"]["library"] == {
        "metadata": "none",
        "display_fields": ["title", "identity", "saved"],
    }
    config_file = tmp_path / "config" / "config.toml"
    assert payload["path"] == str(config_file)
    assert config_file.read_text() == (
        '[library]\nmetadata = "none"\ndisplay_fields = ["title", "identity", "saved"]\n'
    )


def test_config_set_defaults_can_reset_display_fields_to_builtin(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\nmetadata = "none"\ndisplay_fields = ["title", "identity"]\n'
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
    }
    assert (tmp_path / "config" / "config.toml").read_text() == ('[library]\nmetadata = "none"\n')


def test_config_show_reads_library_defaults_from_toml(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANISHELF_CLI_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "config.toml").write_text(
        '[library]\nmetadata = "none"\ndisplay_fields = ["title", "saved"]\n'
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
    }


@pytest.mark.parametrize(
    ("args", "config_text", "message", "extra"),
    [
        (
            ["config", "set-defaults", "--metadata", "details"],
            None,
            "reserved until TMDb detail metadata caching exists",
            None,
        ),
        (
            ["config", "set-defaults", "--fields", "title,bogus"],
            None,
            "Invalid display field 'bogus'",
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
            'library = "bad"\n',
            "must be a TOML table",
            None,
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


def test_config_set_defaults_can_recover_from_malformed_config_with_replacements(
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

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["defaults"]["library"] == {
        "metadata": "none",
        "display_fields": None,
    }
    assert config_file.read_text() == ('[library]\nmetadata = "none"\n')


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


def test_config_set_tmdb_api_key_stores_without_echoing_secret(monkeypatch) -> None:
    store = MemorySecretStore()
    monkeypatch.setattr(config_commands, "default_secret_store", lambda: store)

    tmdb = runner.invoke(
        app,
        ["--json", "config", "set-tmdb-api-key", "--stdin"],
        input="tmdb-secret-token\n",
    )

    assert tmdb.exit_code == 0
    assert "tmdb-secret-token" not in tmdb.stdout + tmdb.stderr
    assert ("anishelf-cli.tmdb-api-key", KEYCHAIN_ACCOUNT) in store.values


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
        "query": {"mode": "search", "title": "Alien", "type": "all"},
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
        "query": {"mode": "search", "title": "Alien", "type": "all"},
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
    assert "  Mode    search\n" in result.stdout
    assert "  Query   Alien\n" in result.stdout
    assert "\nMovies\n" in result.stdout
    assert "TMDb ID" in result.stdout
    assert "Alien" in result.stdout
    assert "1979-05-25" in result.stdout


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
    assert payload["query"] == {"mode": "discover", "type": "all"}
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
    assert payload["query"] == {"mode": "discover", "type": "all"}
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
    assert payload["query"] == {"mode": "discover", "type": "movie", "year": 1979}
    assert payload["summary"] == {"movies": 1, "series": 0, "total": 1}
    assert payload["results"]["movies"][0]["tmdb_id"] == 55
    assert payload["results"]["series"] == []


def test_tmdb_search_human_output_reports_no_results(monkeypatch) -> None:
    _install_tmdb_search_client(
        monkeypatch,
        expected_query=TMDbTitleSearchQuery(title="Alien", year=None, entry_type="all"),
    )

    result = runner.invoke(app, ["tmdb", "search", "--title", "Alien"])

    assert result.exit_code == 0
    assert "TMDb search\n" in result.stdout
    assert "  Mode    search\n" in result.stdout
    assert "  Query   Alien\n" in result.stdout
    assert "  Movies  0\n" in result.stdout
    assert "  Series  0\n" in result.stdout
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
        classmethod(lambda cls: {"cache_files": 2, "lock_files": 1}),
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
        classmethod(lambda cls: {"cache_files": 0, "lock_files": 0}),
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
