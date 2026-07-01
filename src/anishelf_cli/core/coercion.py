from __future__ import annotations


def nonempty_string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def strict_int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
