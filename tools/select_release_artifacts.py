"""Select the two verified distributions for upload, without rebuilding them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path


def select(verified: Path, output: Path, *, version: str) -> None:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ValueError("publishing requires a stable release version")
    report = json.loads((verified / "verification.json").read_text(encoding="utf-8"))
    expected = {f"asterstore-{version}-py3-none-any.whl", f"asterstore-{version}.tar.gz"}
    if report["version"] != version:
        raise ValueError("verified version differs from project version")
    artifacts = report["artifacts"]
    if len(artifacts) != 2 or {entry["file"] for entry in artifacts} != expected:
        raise ValueError("expected exactly the wheel and sdist for this release")
    required = {
        "protocol_schema_and_codecs",
        "wheel_core",
        "sdist_rebuild_core",
        "wheel_polars",
        "sdist_rebuild_polars",
    }
    if not required.issubset(report["checks"]):
        raise ValueError("distribution verification is incomplete")
    for entry in artifacts:
        data = (verified / entry["file"]).read_bytes()
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("verified artifact size or checksum differs")
    output.mkdir(parents=True, exist_ok=False)
    for name in sorted(expected):
        shutil.copyfile(verified / name, output / name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verified", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    select(args.verified, args.output, version=version)


if __name__ == "__main__":
    main()
