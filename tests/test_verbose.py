from __future__ import annotations

import json

import httpx

from anishelf_cli.cache.store import _log_value
from anishelf_cli.cli import tmdb_commands
from anishelf_cli.cli.root import app
from anishelf_cli.library.records import library_record_schema_summary
from anishelf_cli.tmdb.client import TMDbClient
from anishelf_cli.tmdb.tokens import TMDbAPIToken
from tests.support import (
    cloudkit_record,
    create_seeded_cache_store,
    isolate_paths,
    live_record,
    runner,
    tombstone_record,
)


def test_verbose_lib_list_logs_runtime_cache_queries_and_elapsed(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--verbose", "--json", "lib", "list", "--sort", "saved"])

    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["entries"]) == 1
    assert "[debug] Runtime -> ani=" in result.stderr
    assert "command=lib list" in result.stderr
    assert "[debug] Paths -> config=" in result.stderr
    assert "[debug] Library cache scope -> path=" in result.stderr
    assert "[debug] Library cache query -> list_entry_models_filtered" in result.stderr
    assert "sort=saved" in result.stderr
    assert "rows=1" in result.stderr
    assert "[debug] Command -> finished elapsed=" in result.stderr


def test_lib_list_without_verbose_keeps_stderr_empty(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["--json", "lib", "list"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_verbose_command_path_omits_argument_values(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, live_record("movie:55", "movie", 55))

    result = runner.invoke(app, ["-v", "--json", "lib", "get", "movie:55"])

    assert result.exit_code == 0, result.output
    runtime_line = next(
        line for line in result.stderr.splitlines() if line.startswith("[debug] Runtime")
    )
    assert runtime_line.endswith("command=lib get")
    assert "movie:55" not in runtime_line


def test_verbose_environment_lists_override_names_not_values(tmp_path, monkeypatch) -> None:
    isolate_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ANI_TMDB_API_KEY", "tmdb-secret-value")
    monkeypatch.setenv("ANI_CLOUDKIT_API_TOKEN", "api-secret-value")

    result = runner.invoke(app, ["--verbose", "config", "show"])

    assert result.exit_code == 0, result.output
    assert "[debug] Environment -> secretsBackend=system" in result.stderr
    assert "ANI_CLOUDKIT_API_TOKEN" in result.stderr
    assert "ANI_TMDB_API_KEY" in result.stderr
    assert "ANISHELF_CLI_CONFIG_DIR" in result.stderr
    combined = result.stdout + result.stderr
    assert "tmdb-secret-value" not in combined
    assert "api-secret-value" not in combined


def test_verbose_tmdb_retry_logs_delay_and_timing(monkeypatch) -> None:
    monkeypatch.setattr(
        tmdb_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )
    monkeypatch.setattr(tmdb_commands, "default_secret_store", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr("anishelf_cli.tmdb.client.time.sleep", sleeps.append)
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(200, json={"results": [{"id": 55, "title": "Alien"}]}),
        ]
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(responses)))
    monkeypatch.setattr(
        tmdb_commands, "TMDbClient", lambda api_key: TMDbClient(api_key, client=client)
    )

    result = runner.invoke(
        app,
        ["--verbose", "--json", "tmdb", "search", "--title", "Alien", "--type", "movie"],
    )

    assert result.exit_code == 0, result.output
    assert len(sleeps) == 1
    assert "[debug] TMDb request -> retrying in " in result.stderr
    assert "[debug] TMDb response <- HTTP 200 GET" in result.stderr
    assert "elapsed=" in result.stderr
    assert "tmdb-secret-token" not in result.stderr


def test_schema_summary_reports_nothing_unknown_for_supported_records() -> None:
    records = [
        cloudkit_record(live_record("movie:55", "movie", 55)),
        cloudkit_record(
            live_record("season:10:1:100", "season", 100, custom_poster_path="posters/a.jpg")
        ),
        cloudkit_record(tombstone_record("series:7", "series", 7)),
    ]

    assert library_record_schema_summary(records) == (
        "recordTypes=LibraryEntry:3 schemaVersions=2:3 deleted=0 unknownFields=none"
    )


def test_schema_summary_names_unknown_fields_without_values() -> None:
    drifted = live_record("movie:55", "movie", 55)
    drifted["fields"]["rewatchCount"] = {"value": 424242, "type": "INT64"}
    drifted["fields"]["schemaVersion"] = {"value": 3, "type": "INT64"}
    other = {"recordName": "settings", "recordType": "LibrarySettings", "fields": {}}
    deleted = {"recordName": "movie:56", "deleted": True}

    summary = library_record_schema_summary(
        [cloudkit_record(drifted), cloudkit_record(other), cloudkit_record(deleted)]
    )

    assert summary == (
        "recordTypes=LibraryEntry:1,LibrarySettings:1 schemaVersions=3:1 "
        "deleted=1 unknownFields=rewatchCount:1"
    )
    assert "424242" not in summary


def test_cache_query_log_values_never_echo_free_text_or_forge_lines() -> None:
    forged = "x\n[debug] forged line"

    assert _log_value(forged, redact_text=True) == f"<{len(forged)} chars>"
    assert "\n" not in _log_value(forged)
    assert _log_value("title") == "title"


def test_verbose_tmdb_search_logs_read_only_cache_open(tmp_path, monkeypatch) -> None:
    create_seeded_cache_store(monkeypatch, tmp_path, live_record("movie:55", "movie", 55))
    monkeypatch.setattr(
        tmdb_commands,
        "resolve_tmdb_api_token",
        lambda store: TMDbAPIToken("tmdb-secret-token", "env:ANI_TMDB_API_KEY"),
    )
    monkeypatch.setattr(tmdb_commands, "default_secret_store", lambda: None)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"results": [{"id": 55, "title": "Alien", "genre_ids": [16]}]}
            )
        )
    )
    monkeypatch.setattr(
        tmdb_commands, "TMDbClient", lambda api_key: TMDbClient(api_key, client=client)
    )

    result = runner.invoke(
        app,
        ["--verbose", "--json", "tmdb", "search", "--title", "Alien", "--type", "movie"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["results"]["movies"][0]["library_ids"] == ["movie:55"]
    assert "[debug] Library cache open -> mode=read-only path=" in result.stderr
    assert "[debug] Library cache query -> search_cached_entry_models" in result.stderr
