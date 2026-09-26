"""Explicit read/write boundary for control records and repositories."""

from asterstore.errors import InvalidDeclarationError

CURRENT_FORMAT_VERSION = 3
READABLE_FORMAT_VERSIONS = (1, 2, 3)


def validate_format_version(value: int) -> None:
    if type(value) is not int or value not in READABLE_FORMAT_VERSIONS:
        raise InvalidDeclarationError("unsupported format version")


def validate_governance_version(value: int) -> None:
    validate_format_version(value)
    if value == 1:
        raise InvalidDeclarationError("governance records require v2 or v3")
