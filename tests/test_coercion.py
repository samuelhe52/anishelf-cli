from __future__ import annotations

from anishelf_cli.core.coercion import nonempty_string_or_none


def test_nonempty_string_or_none_rejects_empty_and_non_strings() -> None:
    assert nonempty_string_or_none("Alien") == "Alien"
    assert nonempty_string_or_none("") is None
    assert nonempty_string_or_none(55) is None
