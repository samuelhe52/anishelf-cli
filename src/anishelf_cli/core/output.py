from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import typer
from rich.console import Console
from rich.text import Text

from anishelf_cli.core.redaction import SecretRedactor
from anishelf_cli.models import AppState


@dataclass(frozen=True, slots=True)
class HumanSection:
    title: str
    rows: Sequence[tuple[str, object]]


@dataclass(frozen=True, slots=True)
class HumanTableColumn:
    key: str
    label: str
    align: Literal["left", "right"] = "left"


@dataclass(frozen=True, slots=True)
class HumanTable:
    title: str | None
    columns: Sequence[HumanTableColumn]
    rows: Sequence[Mapping[str, object]]
    empty_message: str = "No results."


type HumanBlock = HumanSection | HumanTable

_VERBOSE_OUTPUT_ENABLED = False


def console(stderr: bool = False) -> Console:
    return Console(stderr=stderr)


def set_verbose_output(enabled: bool) -> None:
    global _VERBOSE_OUTPUT_ENABLED
    _VERBOSE_OUTPUT_ENABLED = enabled


def verbose_output_enabled() -> bool:
    return _VERBOSE_OUTPUT_ENABLED


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
    for label, value in section.rows:
        line = Text("  ")
        line.append(f"{label:<{label_width}}", style="cyan")
        line.append("  ")
        line.append(_human_value(value))
        out.print(line)


def _print_table(out: Console, table: HumanTable) -> None:
    if table.title:
        out.print(Text(table.title, style="bold cyan"))
    if not table.rows:
        out.print(f"  {_human_value(table.empty_message)}")
        return

    widths = {
        column.key: max(
            len(column.label),
            *(len(_human_value(row.get(column.key))) for row in table.rows),
        )
        for column in table.columns
    }
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


def _align(value: str, width: int, align: Literal["left", "right"]) -> str:
    return value.rjust(width) if align == "right" else value.ljust(width)


def _human_value(value: object) -> str:
    if value is None:
        return "not set"
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
    if not _VERBOSE_OUTPUT_ENABLED:
        return
    output = redactor.redact(message) if redactor else message
    typer.echo(f"[verbose] {output}", err=True)
