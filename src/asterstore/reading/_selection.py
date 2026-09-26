"""Select only members already present in a bound declaration."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from asterstore.errors import UnknownMemberError


def select_files(paths: Mapping[str, Path], keys: Sequence[str]) -> tuple[Path, ...]:
    if isinstance(keys, str):
        raise TypeError("keys must be a sequence of member keys, not a single string")
    selected = []
    for key in keys:
        try:
            selected.append(paths[key])
        except KeyError as exc:
            raise UnknownMemberError(f"member is not in the bound publication: {key}") from exc
    return tuple(selected)
