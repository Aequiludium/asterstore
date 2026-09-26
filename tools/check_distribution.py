"""Build sdist/wheels and exercise isolated installations outside the source tree."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

SMOKE = """
import hashlib
import importlib
import importlib.metadata
import importlib.resources
import json
import importlib.util
import pathlib
import sys
import asterstore

installed = pathlib.Path(asterstore.__file__).resolve()
assert installed.is_relative_to(pathlib.Path(sys.prefix).resolve()), installed
assert installed.with_name('py.typed').is_file()
schema_path = importlib.resources.files('asterstore.metadata').joinpath('schemas/v3.json')
schema = json.loads(schema_path.read_text())
assert len(schema['oneOf']) == 10
assert importlib.util.find_spec('jsonschema') is None
assert importlib.metadata.version('asterstore') == sys.argv[1]
distribution = importlib.metadata.distribution('asterstore')
metadata = distribution.metadata
assert metadata['License-Expression'] == 'Apache-2.0'
assert metadata.get_all('License-File') == ['LICENSE']
licenses = [p for p in distribution.files or [] if str(p).endswith('.dist-info/licenses/LICENSE')]
assert len(licenses) == 1
assert hashlib.sha256(distribution.locate_file(licenses[0]).read_bytes()).hexdigest() == sys.argv[2]
assert ('Repository, https://github.com/Aequiludium/asterstore'
        in metadata.get_all('Project-URL', []))
packages = (
    'metadata', 'reading', 'publishing', 'retention', 'inspection', 'storage', 'integrations'
)
for name in packages:
    importlib.import_module('asterstore.' + name)
importlib.import_module('asterstore.integrations.polars')
assert importlib.util.find_spec('polars') is None
requirements = importlib.metadata.requires('asterstore') or []
assert requirements and all('extra ==' in item for item in requirements), requirements
assert not any(name.split('.')[0] in {'polars', 'pyarrow', 'pandas', 'aster_protocol'}
               for name in sys.modules)
root = pathlib.Path.cwd() / 'data-not-created'
publication = asterstore.Publication(
    asterstore.Dataset('prices'), 'batch:table', [asterstore.ObjectRef('part.bin')]
)
binding = asterstore.Repository(root).bind(publication)
assert binding.files() == (root / 'part.bin',)
assert not root.exists()
repository = asterstore.Repository(pathlib.Path.cwd() / 'managed')
with repository.prepare(asterstore.Dataset('managed'), publication_id='batch:table') as candidate:
    candidate.write_bytes('part.bin', b'installed wheel')
    candidate_id = candidate.candidate_id
    candidate.commit()
assert repository.open('managed').files()[0].read_bytes() == b'installed wheel'
assert repository.candidate_status(candidate_id).state == 'current'
held = repository.retention.retain('../installed:run', 'managed')
assert held.binding.files()[0].read_bytes() == b'installed wheel'
repository.retention.release('../installed:run', expected_revision=held.revision)
with repository.prepare(asterstore.Dataset('managed'), publication_id='batch:second') as candidate:
    shared = candidate.reuse('batch:table')
    candidate.write_bytes('new.bin', b'incremental')
    second = candidate.commit()
assert second.objects[1:] == shared
assert repository.open('managed').files()[1] == held.binding.files()[0]
report = repository.retention.preview(asterstore.CollectionPolicy(('managed',)))
assert report.status == 'complete' and not report.reclaimable_objects
with repository.prepare(asterstore.Dataset('managed'), publication_id='p3') as candidate:
    candidate.write_bytes('latest.bin', b'latest')
    candidate.commit()
result = repository.retention.collect(asterstore.CollectionPolicy(('managed',)))
assert result.status == 'complete' and len(result.deleted_objects) == 2
assert repository.retention.resume_collection(result.operation_id) == result
assert repository.candidate_status(candidate_id).state == 'retired'
assert repository.open('managed').files()[0].read_bytes() == b'latest'
with repository.prepare(asterstore.Dataset('managed')) as candidate:
    candidate.write_bytes('partial.bin', b'failed writer')
    failed_candidate_id = candidate.candidate_id
assert repository.inspection.candidates().candidates[0].state == 'writing'
assert repository.abandon(failed_candidate_id).state == 'abandoned'
cleaned = repository.retention.cleanup_candidate(failed_candidate_id)
assert cleaned.status == 'complete' and len(cleaned.deleted_files) == 1
assert repository.retention.cleanup_candidate(failed_candidate_id) == cleaned
declaration = asterstore.Declaration(
    'simulation:temperature', 'run:17',
    asterstore.FileSet(
        [asterstore.Object('object:1', asterstore.Locator('external', 'part.bin'))],
        [asterstore.Member('../logical:member', 'object:1')],
    ), asterstore.Capabilities.registered(),
)
external_binding = asterstore.Repository(root).bind(
    declaration, resources={'external': root / 'external'},
)
assert external_binding.files(keys=['../logical:member']) == (root / 'external/part.bin',)
assert not root.exists()
try:
    declaration.capabilities.require_retention(asterstore.RetentionScope.OBJECTS)
except asterstore.UnsupportedCapabilityError:
    pass
else:
    raise AssertionError('external files gained object retention')
v4_schema = importlib.resources.files('asterstore.metadata').joinpath('schemas/v4.json')
assert len(json.loads(v4_schema.read_text())['oneOf']) == 11
registered = asterstore.Repository(pathlib.Path.cwd() / 'registered-control')
registered.initialize(store_id='store:installed', resource_ids=['external'])
record = registered.register(declaration, operation_id='registration:1', expected_generation=0)
reopened = asterstore.Repository(registered.root)
assert reopened.describe('simulation:temperature') == record
reopened_binding = reopened.open('simulation:temperature', resources={'external': root})
assert reopened_binding.files() == (root / 'part.bin',)
assert reopened.resume_registration('registration:1') == record
assert not root.exists()
managed_v4 = asterstore.Repository(pathlib.Path.cwd() / 'managed-v4')
managed_v4.initialize(store_id='installed', resource_ids=['owned'],
                      managed_resource_id='owned', lifecycle=True)
with managed_v4.prepare_managed('result', publication_id='p1', operation_id='op1',
                                expected_generation=0) as writer:
    writer.write_bytes('logical:member', b'v4 installed', relative_path='part.bin')
    writer.seal()
with managed_v4.resume_managed('op1') as recovery:
    recovery.commit()
assert managed_v4.open('result').files()[0].read_bytes() == b'v4 installed'
assert managed_v4.managed_status('op1').state == 'current'
held_v4 = managed_v4.governance.retain(
    'task', 'result', 'p1', scope=asterstore.RetentionScope.OBJECTS)
managed_v4.governance.retain('audit', 'result', 'p1', scope=asterstore.RetentionScope.METADATA)
with managed_v4.prepare_managed('result', publication_id='p2', operation_id='op2',
                                expected_generation=1) as writer:
    writer.write_bytes('new', b'next generation', relative_path='new.bin')
    writer.commit()
retained_v4 = managed_v4.governance.open('task', expected_revision=1)
assert retained_v4.files()[0].read_bytes() == b'v4 installed'
assert not managed_v4.governance.preview().reclaimable
managed_v4.governance.release('task', expected_revision=held_v4.revision)
collected_v4 = managed_v4.governance.collect('gc')
assert len(collected_v4.deleted_objects) == 1
assert managed_v4.governance.resume_collection('gc') == collected_v4
assert managed_v4.managed_status('op1').state == 'retired'
assert managed_v4.describe('result', publication_id='p1').declaration.publication_id == 'p1'
with managed_v4.prepare_managed('result', publication_id='failed', operation_id='failed',
                                expected_generation=2) as writer:
    partial = writer.write_bytes('partial', b'incomplete', relative_path='partial.bin')
managed_v4.governance.abandon('failed')
assert managed_v4.governance.preview_cleanup('failed').files == ('partial.bin',)
cleanup = managed_v4.governance.cleanup('failed')
assert cleanup.complete and cleanup.deleted_files == ('partial.bin',)
assert managed_v4.governance.resume_cleanup('failed') == cleanup
assert not partial.exists()
assert managed_v4.governance.collect('after-cleanup').complete
print('Isolated installation OK:', installed)
"""


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def only_file(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern} in {directory}, found {matches}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--polars", action="store_true", help="also install and verify the engine extra"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="asterstore-distribution-") as temporary:
        work = Path(temporary)
        output = work / "dist"
        run("uv", "build", "--no-sources", "--out-dir", str(output), str(root), cwd=work)
        sdist = only_file(output, "*.tar.gz")
        with tarfile.open(sdist) as archive:
            members = {name.partition("/")[2] for name in archive.getnames()}
            required = {
                "src/asterstore/py.typed",
                "CONTRIBUTING.md",
                "LICENSE",
                "SECURITY.md",
                "CHANGELOG.md",
                "tests/test_binding.py",
                "tests/test_declarations.py",
                "examples/declarations.py",
                "docs/declarations.md",
                "src/asterstore/metadata/membership/__init__.py",
                "src/asterstore/metadata/capabilities/__init__.py",
                "docs/architecture.md",
                "docs/protocol.md",
                "docs/protocol-v3.md",
                "docs/protocol-v4.md",
                "examples/registration.py",
                "src/asterstore/metadata/schemas/v4.json",
                "tests/fixtures/protocol/v4/publication.json",
                "tests/test_registration_processes.py",
                "tests/test_managed_processes.py",
                "examples/managed_declarations.py",
                "docs/managed-v4.md",
                "docs/governance-v4.md",
                "docs/cleanup-v4.md",
                "examples/cleanup_v4.py",
                "tests/test_managed_cleanup_processes.py",
                "tests/test_audit_regressions.py",
                "benchmarks/managed_membership.py",
                "docs/audit-fixes-v4.md",
                "benchmarks/governance_scale.py",
                "docs/release-scope.md",
                "tests/fixtures/protocol/v4/cleanup/plan.json",
                "tests/fixtures/protocol/v4/cleanup/progress.json",
                "src/asterstore/retention/governance/cleanup/__init__.py",
                "examples/governance_v4.py",
                "tests/test_governance_processes.py",
                "tests/fixtures/protocol/v4/governance/progress.json",
                "tests/fixtures/protocol/v4/managed/request.json",
                "src/asterstore/metadata/schemas/v3.json",
                "tests/fixtures/protocol/v3/opaque-candidate.json",
                "tests/fixtures/protocol/v3/invalid/mixed-collection.json",
                "tests/fixtures/protocol/v1/current.json",
                "examples/publication.py",
                "examples/retention.py",
                "examples/reuse.py",
                "examples/collection.py",
                "examples/candidate_cleanup.py",
                "examples/parquet.py",
                "src/asterstore/integrations/polars/__init__.py",
                "tests/test_polars.py",
                "benchmarks/parquet_read.py",
                "docs/integrations.md",
                "tests/fixtures/protocol/v2/abandoned-candidate.json",
                "tests/fixtures/protocol/v2/retired.json",
                "tests/fixtures/protocol/v2/reuse-candidate.json",
                "tests/fixtures/protocol/v2/reference.json",
                "examples/binding.py",
                "benchmarks/README.md",
                "tools/check_distribution.py",
            }
            license_names = [
                name for name in archive.getnames() if name.partition("/")[2] == "LICENSE"
            ]
            assert len(license_names) == 1
            license_stream = archive.extractfile(license_names[0])
            assert license_stream is not None
            with license_stream:
                assert license_stream.read() == (root / "LICENSE").read_bytes()
            if missing := required - members:
                raise RuntimeError(f"sdist is missing: {sorted(missing)}")
        rebuilt = work / "rebuilt"
        run("uv", "build", "--no-sources", "--wheel", str(sdist), "-o", str(rebuilt), cwd=work)
        for index, wheel in enumerate((only_file(output, "*.whl"), only_file(rebuilt, "*.whl"))):
            env = work / f"venv-{index}"
            run("uv", "venv", "--python", args.python, str(env), cwd=work)
            python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run("uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel), cwd=work)
            version = wheel.name.split("-")[1]
            smoke_directory = work / f"smoke-{index}"
            smoke_directory.mkdir()
            run(
                str(python),
                "-I",
                "-c",
                SMOKE,
                version,
                hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest(),
                cwd=smoke_directory,
            )
            if args.polars:
                run("uv", "pip", "install", "--python", str(python), f"{wheel}[polars]", cwd=work)
                run(
                    str(python),
                    "-I",
                    "-c",
                    (root / "examples/parquet.py").read_text(),
                    cwd=smoke_directory,
                )
    print("Wheel, sdist rebuild and isolated installations: PASS")


if __name__ == "__main__":
    main()
