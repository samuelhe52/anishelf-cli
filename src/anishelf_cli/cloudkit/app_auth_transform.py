from __future__ import annotations

_HEX_ALPHABET = "0123456789abcdef"


def restore_transformed_hex(value: str, *, key: str) -> str:
    """Reverse the build-time hex transform used for embedded app auth.

    This is extraction friction only, not cryptographic protection.
    """
    if not value or not key:
        return ""

    offsets = _hex_offsets(key)
    decoded_reversed = "".join(
        _rotate_hex(char, -offsets[index % len(offsets)]) for index, char in enumerate(value)
    )
    return decoded_reversed[::-1]


def transform_hex(value: str, *, key: str) -> str:
    """Apply the reversible transform used to prepare embedded app auth values."""
    if not value or not key:
        return ""

    offsets = _hex_offsets(key)
    reversed_value = value[::-1]
    return "".join(
        _rotate_hex(char, offsets[index % len(offsets)])
        for index, char in enumerate(reversed_value)
    )


def _hex_offsets(key: str) -> tuple[int, ...]:
    return tuple(ord(char) % len(_HEX_ALPHABET) for char in key)


def _rotate_hex(char: str, offset: int) -> str:
    index = _HEX_ALPHABET.index(char)
    return _HEX_ALPHABET[(index + offset) % len(_HEX_ALPHABET)]
