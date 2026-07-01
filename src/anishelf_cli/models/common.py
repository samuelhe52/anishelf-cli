from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr

NonEmptyStr = Annotated[StrictStr, Field(min_length=1)]
"""A strict string that must contain at least one character."""


def _empty_tuple_if_none(value: object) -> object:
    return () if value is None else value


EmptyTupleForNone = BeforeValidator(_empty_tuple_if_none)
"""Coerce an explicit ``None`` to an empty tuple before sequence validation."""


class AniShelfBaseModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=False,
        str_strip_whitespace=False,
    )
