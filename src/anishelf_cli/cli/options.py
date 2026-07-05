from __future__ import annotations

from typing import Annotated

import typer

from anishelf_cli.models import HumanOutputStyle, MetadataDepth

MetadataOption = Annotated[
    MetadataDepth | None,
    typer.Option(
        "--metadata",
        "-m",
        help=(
            "Include TMDb metadata: none, summary, details, or full. "
            "Use separated form, for example --metadata summary."
        ),
        show_default=False,
    ),
]
FieldListOption = Annotated[
    str | None,
    typer.Option(
        "--fields",
        "-f",
        help=(
            "Comma-separated human output fields. Use default to use the built-in "
            "fields for this invocation."
        ),
        show_default=False,
    ),
]
OutputStyleOption = Annotated[
    HumanOutputStyle | None,
    typer.Option(
        "--style",
        "-s",
        help="Human output style.",
        show_default=False,
    ),
]
