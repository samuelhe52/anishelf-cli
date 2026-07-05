from anishelf_cli.core.output import (
    HumanParagraph,
    HumanSection,
    HumanTable,
    HumanTableColumn,
    emit_human_blocks,
    emit_verbose,
    set_current_app_state,
    verbose_output_enabled,
)
from anishelf_cli.models import AppState


def test_emit_human_blocks_formats_sections_and_tables(capsys) -> None:
    emit_human_blocks(
        [
            HumanSection(
                "Entry",
                (
                    ("ID", "movie:550"),
                    ("Favorite", True),
                ),
            ),
            HumanTable(
                "Library",
                (
                    HumanTableColumn("id", "ID"),
                    HumanTableColumn("type", "Type"),
                    HumanTableColumn("score", "Score", align="right"),
                ),
                (
                    {"id": "movie:550", "type": "movie", "score": 9},
                    {"id": "series:1399", "type": "series", "score": None},
                ),
            ),
        ]
    )

    assert capsys.readouterr().out == (
        "Entry\n"
        "  ID        movie:550\n"
        "  Favorite  yes\n"
        "\n"
        "Library\n"
        "  ID           Type    Score\n"
        "  movie:550    movie       9\n"
        "  series:1399  series   none\n"
    )


def test_emit_human_blocks_formats_empty_table(capsys) -> None:
    emit_human_blocks(
        [
            HumanTable(
                "Library",
                (HumanTableColumn("id", "ID"),),
                (),
                empty_message="No library entries.",
            )
        ]
    )

    assert capsys.readouterr().out == "Library\n  No library entries.\n"


def test_emit_human_blocks_truncates_wide_tables_to_console_width(capsys, monkeypatch) -> None:
    from rich.console import Console

    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(
        output_module,
        "console",
        lambda stderr=False: Console(width=42, stderr=stderr),
    )

    emit_human_blocks(
        [
            HumanTable(
                "Library",
                (
                    HumanTableColumn("title", "Title", flexible=True),
                    HumanTableColumn("id", "ID", flexible=True),
                    HumanTableColumn("status", "Status"),
                ),
                (
                    {
                        "title": "A Very Long Localized Movie Title That Would Wrap",
                        "id": "movie:12345678901234567890",
                        "status": "watching",
                    },
                ),
            )
        ]
    )

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert all(len(line) <= 42 for line in lines)
    assert "..." in output
    assert "A Very Long" in output


def test_emit_human_blocks_respects_column_max_width(capsys, monkeypatch) -> None:
    from rich.console import Console

    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(
        output_module,
        "console",
        lambda stderr=False: Console(width=96, stderr=stderr),
    )

    emit_human_blocks(
        [
            HumanTable(
                "Library",
                (
                    HumanTableColumn("title", "Title", flexible=True, max_width=16),
                    HumanTableColumn("id", "ID"),
                    HumanTableColumn("status", "Status"),
                ),
                (
                    {
                        "title": "A Very Long Localized Movie Title That Would Otherwise Dominate",
                        "id": "movie:1234567890",
                        "status": "watching",
                    },
                ),
            )
        ]
    )

    output = capsys.readouterr().out
    assert "movie:1234567890" in output
    assert "watching" in output
    assert "Localized" not in output
    assert "..." in output


def test_emit_human_blocks_shrinks_long_titles_before_other_columns(capsys, monkeypatch) -> None:
    from rich.console import Console

    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(
        output_module,
        "console",
        lambda stderr=False: Console(width=50, stderr=stderr),
    )

    emit_human_blocks(
        [
            HumanTable(
                "Library",
                (
                    HumanTableColumn("title", "Title", flexible=True, max_width=40),
                    HumanTableColumn("id", "ID"),
                    HumanTableColumn("status", "Status"),
                ),
                (
                    {
                        "title": "A Very Long Localized Movie Title",
                        "id": "movie:1234567890",
                        "status": "watching",
                    },
                ),
            )
        ]
    )

    output = capsys.readouterr().out
    assert "movie:1234567890" in output
    assert "watching" in output
    assert "..." in output
    assert all(len(line) <= 50 for line in output.splitlines())


def test_emit_human_blocks_formats_paragraph_values_with_indentation(capsys, monkeypatch) -> None:
    from rich.console import Console

    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(
        output_module,
        "console",
        lambda stderr=False: Console(width=28, stderr=stderr),
    )

    emit_human_blocks(
        [
            HumanSection(
                "Entry",
                (
                    ("ID", "movie:550"),
                    ("Overview", HumanParagraph("Alpha beta gamma\ndelta epsilon")),
                ),
            )
        ]
    )

    assert capsys.readouterr().out == (
        "Entry\n  ID        movie:550\n  Overview\n    Alpha beta gamma\n    delta epsilon\n"
    )


def test_emit_verbose_is_disabled_without_app_state(capsys) -> None:
    set_current_app_state(AppState(verbose=False))

    emit_verbose("hidden")

    assert capsys.readouterr().err == ""
    assert not verbose_output_enabled()


def test_emit_verbose_uses_request_scoped_app_state(capsys) -> None:
    set_current_app_state(AppState(verbose=True))

    emit_verbose("visible")

    assert capsys.readouterr().err == "[debug] visible\n"
    assert verbose_output_enabled()
