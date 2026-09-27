import json

from anishelf_cli.core.output import (
    HumanParagraph,
    HumanSection,
    HumanTable,
    HumanTableColumn,
    emit_human_blocks,
    emit_json,
)


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


def test_emit_json_preserves_non_ascii_text(capsys) -> None:
    emit_json({"title": "エイリアン", "overview": "中文简介"})

    output = capsys.readouterr().out
    assert '"title": "エイリアン"' in output
    assert '"overview": "中文简介"' in output
    assert "\\u30a8" not in output
    assert json.loads(output) == {"overview": "中文简介", "title": "エイリアン"}


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


def test_library_table_keeps_ids_whole_and_truncates_titles_on_narrow_terminals(
    capsys, monkeypatch
) -> None:
    from rich.console import Console

    from anishelf_cli.cli.presentation import DISPLAY_FIELD_COLUMNS
    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(
        output_module,
        "console",
        lambda stderr=False: Console(width=60, stderr=stderr),
    )

    emit_human_blocks(
        [
            HumanTable(
                "Library entries",
                tuple(
                    DISPLAY_FIELD_COLUMNS[key] for key in ("title", "id", "type", "status", "score")
                ),
                (
                    {
                        "title": "葬送のフリーレン A Very Long Localized Season Title",
                        "id": "season:209867:1:307972",
                        "type": "season",
                        "status": "watched",
                        "score": 5,
                    },
                ),
            )
        ]
    )

    output = capsys.readouterr().out
    assert "season:209867:1:307972" in output
    assert "..." in output
    assert all(len(line) <= 60 for line in output.splitlines())


def test_console_uses_unbounded_width_when_output_is_piped(monkeypatch) -> None:
    import io
    import sys

    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    for name in ("COLUMNS", "FORCE_COLOR", "TTY_COMPATIBLE"):
        monkeypatch.delenv(name, raising=False)
    piped = output_module.console()
    monkeypatch.setenv("COLUMNS", "not-a-number")
    invalid_columns = output_module.console()
    monkeypatch.setenv("COLUMNS", "72")
    sized = output_module.console()

    assert piped.width >= 1000
    assert invalid_columns.width >= 1000
    assert sized.width == 72


def test_emit_error_prints_bracketed_text_literally(capsys, monkeypatch) -> None:
    from anishelf_cli.core.output import emit_error

    emit_error("Unexpected value [type=int_parsing] in [/field].")

    assert "Unexpected value [type=int_parsing] in [/field]." in capsys.readouterr().err


def _render_default_library_table(monkeypatch, capsys, width: int, row: dict[str, object]) -> str:
    from rich.console import Console

    from anishelf_cli.cli.presentation import DISPLAY_FIELD_COLUMNS, LIBRARY_LIST_DEFAULT_FIELDS
    from anishelf_cli.core import output as output_module

    monkeypatch.setattr(
        output_module,
        "console",
        lambda stderr=False: Console(width=width, stderr=stderr),
    )
    emit_human_blocks(
        [
            HumanTable(
                "Library entries",
                tuple(DISPLAY_FIELD_COLUMNS[key] for key in LIBRARY_LIST_DEFAULT_FIELDS),
                (row,),
            )
        ]
    )
    return capsys.readouterr().out


_SEASON_ROW: dict[str, object] = {
    "title": "葬送のフリーレン A Very Long Localized Season Title",
    "id": "season:209867:1:307972",
    "type": "season",
    "status": "watched",
    "score": 5,
    "favorite": "yes",
    "updated": "26/05/11",
}


def test_default_library_table_keeps_ids_whole_at_80_columns(capsys, monkeypatch) -> None:
    from rich.cells import cell_len

    output = _render_default_library_table(monkeypatch, capsys, 80, _SEASON_ROW)

    assert "season:209867:1:307972" in output
    assert "..." in output
    assert all(cell_len(line) <= 80 for line in output.splitlines())


def test_default_library_table_truncates_ids_only_as_last_resort(capsys, monkeypatch) -> None:
    from rich.cells import cell_len

    output = _render_default_library_table(monkeypatch, capsys, 60, _SEASON_ROW)

    lines = output.splitlines()
    # The title keeps a readable floor and rows stay on one line each.
    assert len(lines) == 3
    assert all(cell_len(line) <= 60 for line in lines)
    assert "season:209867:1:307972" not in output
    assert "season..." in output
