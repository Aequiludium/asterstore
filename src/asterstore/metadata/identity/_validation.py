"""Logical identifiers never participate in path construction."""

import unicodedata

from asterstore.errors import InvalidDeclarationError

MAX_IDENTIFIER_BYTES = 4096


def validate_identifier(value: str, field: str, *, max_bytes: int | None = None) -> None:
    """Require nonempty UTF-8 text without Unicode Cc control characters.

    Preserve case, punctuation, whitespace and Unicode normalization exactly.
    Separators such as ':' and '/' have no filesystem meaning in an identity.
    """
    if not isinstance(value, str) or not value:
        raise InvalidDeclarationError(f"{field} must be a nonempty identifier")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidDeclarationError(f"{field} must be valid UTF-8") from exc
    if max_bytes is not None and len(encoded) > max_bytes:
        raise InvalidDeclarationError(f"{field} exceeds {max_bytes} UTF-8 bytes")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise InvalidDeclarationError(f"{field} must not contain control characters")


def validate_object_key(value: str) -> None:
    """Validate a canonical relative physical locator."""
    field = "object key"
    if (
        not isinstance(value, str)
        or not value
        or not value.isprintable()
        or ":" in value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise InvalidDeclarationError(f"{field} must be a nonempty canonical relative name")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidDeclarationError(f"{field} must be valid UTF-8") from exc
