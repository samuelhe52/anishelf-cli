from __future__ import annotations

from typing import Annotated

import typer

from anishelf_cli.models import MetadataDepth

MetadataOption = Annotated[
    MetadataDepth | None,
    typer.Option(
        "--metadata",
        "-m",
        help=(
            "Include TMDb metadata. Bare --metadata uses summary; explicit values may "
            "be passed as --metadata none, summary, details, or full. "
            "Use -- before a positional id or title named none, summary, details, "
            "or full."
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
    str | None,
    typer.Option(
        "--style",
        "-s",
        help="Human output style: table or list. Use default to use the configured style.",
        show_default=False,
    ),
]
