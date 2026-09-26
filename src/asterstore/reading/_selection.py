"""Select only members already present in a bound declaration."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from asterstore.errors import UnknownMemberError, UnknownObjectError


def select_files(
    paths: Mapping[str, Path], keys: Sequence[str], *, logical: bool = False
) -> tuple[Path, ...]:
    kind = "member" if logical else "object"
    if isinstance(keys, str):
        raise TypeError(f"keys must be a sequence of {kind} keys, not a single string")
    selected = []
    for key in keys:
        try:
            selected.append(paths[key])
        except KeyError as exc:
            error = UnknownMemberError if logical else UnknownObjectError
            raise error(f"{kind} is not in the bound publication: {key}") from exc
    return tuple(selected)
