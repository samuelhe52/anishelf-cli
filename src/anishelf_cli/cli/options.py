from __future__ import annotations

from typing import Annotated

import typer

from anishelf_cli.models import MetadataDepth

MetadataOption = Annotated[
    MetadataDepth | None,
    typer.Option(
        "--metadata",
        help=(
            "Include TMDb metadata. Bare --metadata uses the default summary level; "
            "explicit values are none, summary, details, or full. Use none to "
            "disable TMDb requests."
        ),
        show_default=False,
    ),
]
FieldListOption = Annotated[
    str | None,
    typer.Option(
        "--fields",
        help=(
            "Comma-separated human table fields. Use default to use the built-in "
            "fields for this invocation."
        ),
        show_default=False,
    ),
]
