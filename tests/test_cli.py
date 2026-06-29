from typer.testing import CliRunner

from anishelf_cli.cli.root import app

runner = CliRunner()


def test_root_help_mentions_global_options() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "--profile" in result.stdout
    assert "--json" in result.stdout
    assert "--verbose" in result.stdout
    assert "--metadata-depth" in result.stdout
    assert "--anishelf-source" in result.stdout


def test_profile_status_json_shows_effective_scope_without_secrets() -> None:
    result = runner.invoke(app, ["--json", "profile", "status"])

    assert result.exit_code == 0
    assert "iCloud.com.samuelhe.MyAnimeList" in result.stdout
    assert "cloudkit-api-token" not in result.stdout
    assert "ckWebAuthToken" not in result.stdout
