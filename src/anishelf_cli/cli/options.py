from __future__ import annotations

from typing import Annotated

import typer

from anishelf_cli.models import MetadataDepth

MetadataOption = Annotated[
    MetadataDepth | None,
    typer.Option(
        "--metadata",
        help=(
            "Include TMDb metadata. Bare --metadata uses summary; explicit values may "
            "be passed as --metadata none or --metadata=none. Details and full are "
            "reserved until detail metadata caching exists. "
            "Use -- before a positional identity or title named none, summary, details, "
            "or full."
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
