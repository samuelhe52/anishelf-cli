from anishelf_cli.core.output import (
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
                    ("Identity", "movie:550"),
                    ("Favorite", True),
                ),
            ),
            HumanTable(
                "Library",
                (
                    HumanTableColumn("identity", "Identity"),
                    HumanTableColumn("type", "Type"),
                    HumanTableColumn("score", "Score", align="right"),
                ),
                (
                    {"identity": "movie:550", "type": "movie", "score": 9},
                    {"identity": "series:1399", "type": "series", "score": None},
                ),
            ),
        ]
    )

    assert capsys.readouterr().out == (
        "Entry\n"
        "  Identity  movie:550\n"
        "  Favorite  yes\n"
        "\n"
        "Library\n"
        "  Identity     Type      Score\n"
        "  movie:550    movie         9\n"
        "  series:1399  series  not set\n"
    )


def test_emit_human_blocks_formats_empty_table(capsys) -> None:
    emit_human_blocks(
        [
            HumanTable(
                "Library",
                (HumanTableColumn("identity", "Identity"),),
                (),
                empty_message="No library entries.",
            )
        ]
    )

    assert capsys.readouterr().out == "Library\n  No library entries.\n"


def test_emit_verbose_is_disabled_without_app_state(capsys) -> None:
    set_current_app_state(AppState(verbose=False))

    emit_verbose("hidden")

    assert capsys.readouterr().err == ""
    assert not verbose_output_enabled()


def test_emit_verbose_uses_request_scoped_app_state(capsys) -> None:
    set_current_app_state(AppState(verbose=True))

    emit_verbose("visible")

    assert capsys.readouterr().err == "[verbose] visible\n"
    assert verbose_output_enabled()
