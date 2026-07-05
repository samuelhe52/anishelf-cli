from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

import typer
from rich.cells import cell_len, set_cell_size
from rich.console import Console
from rich.text import Text

from anishelf_cli.core.logging import configure_logging, get_logger
from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models import AppState


@dataclass(frozen=True, slots=True)
class HumanSection:
    title: str
    rows: Sequence[tuple[str, object]]


@dataclass(frozen=True, slots=True)
class HumanParagraph:
    text: str


@dataclass(frozen=True, slots=True)
class HumanTableColumn:
    key: str
    label: str
    align: Literal["left", "right"] = "left"
    flexible: bool = False
    max_width: int | None = None
    min_width: int = 1


@dataclass(frozen=True, slots=True)
class HumanTable:
    title: str | None
    columns: Sequence[HumanTableColumn]
    rows: Sequence[Mapping[str, object]]
    empty_message: str = "No results."


type HumanBlock = HumanSection | HumanTable

_PARAGRAPH_CONTENT_INDENT = 4
_PARAGRAPH_MAX_WIDTH = 88
_APP_STATE: ContextVar[AppState | None] = ContextVar("anishelf_cli_app_state", default=None)


def console(stderr: bool = False) -> Console:
    return Console(stderr=stderr)


def set_current_app_state(state: AppState) -> None:
    _APP_STATE.set(state)
    configure_logging(verbose=state.verbose)


def verbose_output_enabled() -> bool:
    state = _APP_STATE.get()
    return bool(state and state.verbose)


def emit_json(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def emit_human_blocks(blocks: Sequence[HumanBlock]) -> None:
    out = console()
    for block_index, block in enumerate(blocks):
        if block_index:
            out.print()
        if isinstance(block, HumanSection):
            _print_section(out, block, _section_label_width(blocks))
        else:
            _print_table(out, block)


def emit_human_sections(sections: Sequence[HumanSection]) -> None:
    emit_human_blocks(sections)


def _section_label_width(blocks: Sequence[HumanBlock]) -> int:
    label_width = max(
        (
            len(label)
            for block in blocks
            if isinstance(block, HumanSection)
            for label, _ in block.rows
        ),
        default=0,
    )
    return label_width


def _print_section(out: Console, section: HumanSection, label_width: int) -> None:
    out.print(Text(section.title, style="bold cyan"))
    for row_index, (label, value) in enumerate(section.rows):
        if isinstance(value, HumanParagraph):
            _print_section_paragraph(out, label, value.text, label_width)
            if row_index < len(section.rows) - 1:
                out.print()
            continue
        line = Text("  ")
        line.append(f"{label:<{label_width}}", style="cyan")
        line.append("  ")
        line.append(_human_value(value))
        out.print(line)


def _print_section_paragraph(
    out: Console,
    label: str,
    value: str,
    label_width: int,
) -> None:
    label_line = Text("  ")
    label_line.append(label, style="cyan")
    out.print(label_line)

    indent = Text(" " * _PARAGRAPH_CONTENT_INDENT)
    content_width = min(max(out.width - indent.cell_len, 20), _PARAGRAPH_MAX_WIDTH)
    paragraphs = value.splitlines() or [""]

    for paragraph in paragraphs:
        wrapped_lines = Text(paragraph).wrap(out, content_width) if paragraph else [Text("")]
        for wrapped in wrapped_lines:
            line = indent.copy()
            line.append_text(wrapped)
            out.print(line)


def _print_table(out: Console, table: HumanTable) -> None:
    if table.title:
        out.print(Text(table.title, style="bold cyan"))
    if not table.rows:
        out.print(f"  {_human_value(table.empty_message)}")
        return

    widths = dict(_column_width(column, table.rows) for column in table.columns)
    widths = _fit_table_widths(widths, table.columns, max_width=out.width)
    header = Text("  ")
    for index, column in enumerate(table.columns):
        if index:
            header.append("  ")
        header.append(_align(column.label, widths[column.key], column.align), style="cyan")
    out.print(header)

    for row in table.rows:
        line = Text("  ")
        for index, column in enumerate(table.columns):
            if index:
                line.append("  ")
            line.append(_align(_human_value(row.get(column.key)), widths[column.key], column.align))
        out.print(line)


def _fit_table_widths(
    widths: dict[str, int],
    columns: Sequence[HumanTableColumn],
    *,
    max_width: int,
) -> dict[str, int]:
    fitted_widths = dict(widths)
    table_indent = 2
    column_spacing = 2 * max(len(columns) - 1, 0)
    available = max(max_width - table_indent - column_spacing, len(columns))

    def total_width() -> int:
        return sum(fitted_widths[column.key] for column in columns)

    def shrink_to(minimum_width: int, *, respect_column_min_width: bool) -> None:
        while total_width() > available:
            shrinkable = [
                column
                for column in columns
                if column.flexible
                if fitted_widths[column.key]
                > _minimum_column_width(
                    column,
                    minimum_width=minimum_width,
                    respect_column_min_width=respect_column_min_width,
                )
            ]
            if not shrinkable:
                return
            widest = max(
                shrinkable,
                key=lambda column: (fitted_widths[column.key], column.key == "title"),
            )
            fitted_widths[widest.key] -= 1

    shrink_to(minimum_width=3, respect_column_min_width=True)
    shrink_to(minimum_width=3, respect_column_min_width=False)
    shrink_to(minimum_width=1, respect_column_min_width=False)
    return fitted_widths


def _minimum_column_width(
    column: HumanTableColumn,
    *,
    minimum_width: int,
    respect_column_min_width: bool,
) -> int:
    if not respect_column_min_width:
        return minimum_width
    return max(minimum_width, column.min_width)


def _column_width(
    column: HumanTableColumn, rows: Sequence[Mapping[str, object]]
) -> tuple[str, int]:
    width = max(
        cell_len(column.label),
        *(cell_len(_human_value(row.get(column.key))) for row in rows),
    )
    if column.max_width is not None:
        width = min(width, column.max_width)
    return column.key, width


def _align(value: str, width: int, align: Literal["left", "right"]) -> str:
    fitted = _truncate_cell(value, width)
    padding = max(width - cell_len(fitted), 0)
    if align == "right":
        return f"{' ' * padding}{fitted}"
    return f"{fitted}{' ' * padding}"


def _truncate_cell(value: str, width: int) -> str:
    if cell_len(value) <= width:
        return value
    if width <= 3:
        return set_cell_size(value, width)
    return f"{set_cell_size(value, width - 3).rstrip()}..."


def _human_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_human_value(item) for item in value)
    return str(value)


def emit_placeholder(state: AppState, area: str) -> None:
    message = {
        "status": "not-implemented",
        "area": area,
    }
    if state.json_output:
        emit_json(message)
        raise typer.Exit(code=1)

    console(stderr=True).print(f"[yellow]{area} is scaffolded but not implemented yet.[/yellow]")
    raise typer.Exit(code=1)


def emit_error(message: str, *, redactor: SecretRedactor | None = None) -> None:
    output = redactor.redact(message) if redactor else message
    console(stderr=True).print(f"[red]{output}[/red]")


def emit_progress(message: str, *, redactor: SecretRedactor | None = None) -> None:
    output = redactor.redact(message) if redactor else message
    typer.echo(f"[progress] {output}", err=True)


def emit_verbose(message: str, *, redactor: SecretRedactor | None = None) -> None:
    if not verbose_output_enabled():
        return
    extra = {"redactor": redactor} if redactor is not None else None
    get_logger("core.output").debug(message, extra=extra)
