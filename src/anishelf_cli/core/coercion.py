from __future__ import annotations


def nonempty_string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
