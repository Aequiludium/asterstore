"""Prevent uploading unverified, substituted or wrongly versioned artifacts."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest

select = runpy.run_path(str(Path(__file__).parents[1] / "tools/select_release_artifacts.py"))[
    "select"
]


@pytest.fixture
def verified(tmp_path: Path) -> Path:
    source = tmp_path / "verified"
    source.mkdir()
    records = []
    for name in ("asterstore-0.1.0-py3-none-any.whl", "asterstore-0.1.0.tar.gz"):
        data = name.encode()
        (source / name).write_bytes(data)
        records.append(
            {"file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    (source / "verification.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "artifacts": records,
                "checks": [
                    "protocol_schema_and_codecs",
                    "wheel_core",
                    "sdist_rebuild_core",
                    "wheel_polars",
                    "sdist_rebuild_polars",
                ],
            }
        )
    )
    return source


def test_select_only_original_distribution_bytes(verified: Path, tmp_path: Path) -> None:
    output = tmp_path / "upload"
    select(verified, output, version="0.1.0")
    assert {p.name for p in output.iterdir()} == {
        "asterstore-0.1.0-py3-none-any.whl",
        "asterstore-0.1.0.tar.gz",
    }
    for artifact in output.iterdir():
        assert artifact.read_bytes() == (verified / artifact.name).read_bytes()
    with pytest.raises(FileExistsError):
        select(verified, output, version="0.1.0")


@pytest.mark.parametrize("change", ["version", "filename", "checks", "bytes", "prerelease"])
def test_reject_unverified_uploads(verified: Path, tmp_path: Path, change: str) -> None:
    report_path = verified / "verification.json"
    report = json.loads(report_path.read_text())
    version = "0.1.0"
    if change == "version":
        report["version"] = "0.2.0"
    elif change == "filename":
        report["artifacts"][0]["file"] = "../outside.whl"
    elif change == "checks":
        report["checks"].remove("sdist_rebuild_polars")
    elif change == "bytes":
        (verified / report["artifacts"][0]["file"]).write_bytes(b"substituted bytes")
    elif change == "prerelease":
        version = "0.1.0.dev1"
    report_path.write_text(json.dumps(report))
    output = tmp_path / "upload"
    with pytest.raises(ValueError):
        select(verified, output, version=version)
    assert not output.exists()
