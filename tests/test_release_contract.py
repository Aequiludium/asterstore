"""The release guard rejects drift instead of silently blessing a new baseline."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def check(contract: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/check_contract.py"), "--contract", str(contract)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_reviewed_contract_matches_current_package() -> None:
    result = check(ROOT / "docs/contracts/0.1.json")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("drift", ["parameter", "exception", "enum", "schema", "legacy"])
def test_contract_guard_rejects_drift(tmp_path: Path, drift: str) -> None:
    value = json.loads((ROOT / "docs/contracts/0.1.json").read_text())
    if drift == "parameter":
        value["api"]["Governance"]["methods"]["retain"]["signatures"] = ["(self, name)"]
    elif drift == "exception":
        value["api"]["CandidateAbandonedError"]["bases"] = ["ValueError"]
    elif drift == "enum":
        value["api"]["RetentionScope"]["enum"]["OBJECTS"] = "bytes"
    elif drift == "schema":
        value["wire"]["file_sha256"]["src/asterstore/metadata/schemas/v4.json"] = "0" * 64
    else:
        value["api"].pop("Dataset")
    contract = tmp_path / "changed.json"
    before = json.dumps(value).encode()
    contract.write_bytes(before)
    result = check(contract)
    assert result.returncode != 0
    assert "Contract changed" in result.stderr
    assert contract.read_bytes() == before
