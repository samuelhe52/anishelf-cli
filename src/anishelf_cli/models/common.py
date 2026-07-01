from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AniShelfBaseModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=False,
        str_strip_whitespace=False,
    )
