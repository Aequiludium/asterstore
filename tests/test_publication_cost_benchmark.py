"""Check measurement boundaries against real small repositories, not timing thresholds."""

import runpy
from pathlib import Path

import pytest

BENCH = runpy.run_path(str(Path(__file__).parents[1] / "benchmarks/publication_cost.py"))


@pytest.mark.parametrize("durable", [False, True])
def test_publication_probe_observes_verification_and_sync(tmp_path, durable):
    report = BENCH["measure"](tmp_path, "new", 2, 2, durable, "probe")
    counts = report["counts"]
    assert counts["candidate_verify_calls"] == 2
    assert bool(counts.get("fsync_calls", 0)) is durable
    assert counts["gc_lock_acquisitions"] >= 1
    assert counts["control_write_attempt_bytes"] > 0
    assert counts.get("payload_read_calls", 0) == 0
    assert not list(tmp_path.iterdir())


def test_gc_probe_distinguishes_first_delete_and_completed_retry(tmp_path):
    first = BENCH["measure"](tmp_path, "gc_first", 2, 3, False, "probe")["counts"]
    retry = BENCH["measure"](tmp_path, "gc_retry", 2, 3, False, "probe")["counts"]
    assert first["delete_owned_file_calls"] == 4
    assert retry.get("delete_owned_file_calls", 0) == 0
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("case", ["sealed_resume", "installed_resume", "weak_to_strong"])
def test_recovery_probe_excludes_setup_verification(tmp_path, case):
    counts = BENCH["measure"](tmp_path, case, 2, 2, True, "probe")["counts"]
    assert counts["candidate_verify_calls"] == 1
    assert counts["fsync_calls"] > 0
    assert not list(tmp_path.iterdir())
