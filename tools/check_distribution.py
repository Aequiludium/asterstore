"""Build sdist/wheels and exercise isolated installations outside the source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
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
schema_path = importlib.resources.files('asterstore.metadata').joinpath('schemas/store.json')
schema = json.loads(schema_path.read_text())
assert len(schema['oneOf']) == 11
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
    'metadata', 'reading', 'publishing', 'governance', 'registration', 'storage', 'integrations'
)
for name in packages:
    importlib.import_module('asterstore.' + name)
importlib.import_module('asterstore.integrations.polars')
assert importlib.util.find_spec('asterstore.retention') is None
assert importlib.util.find_spec('asterstore.inspection') is None
assert importlib.util.find_spec('asterstore.publishing.managed') is None
assert not any(hasattr(asterstore, name)
               for name in ('Dataset', 'Publication', 'ObjectRef', 'Retention'))
assert {p.name for p in schema_path.parent.iterdir()} == {'store.json'}
assert importlib.util.find_spec('polars') is None
requirements = importlib.metadata.requires('asterstore') or []
assert requirements and all('extra ==' in item for item in requirements), requirements
assert not any(name.split('.')[0] in {'polars', 'pyarrow', 'pandas', 'aster_protocol'}
               for name in sys.modules)
root = pathlib.Path.cwd() / 'data-not-created'
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
registered = asterstore.Repository(pathlib.Path.cwd() / 'registered-control')
registered.initialize(store_id='store:installed', resource_ids=['external'])
record = registered.register(declaration, operation_id='registration:1', expected_generation=0)
reopened = asterstore.Repository(registered.root)
assert reopened.describe('simulation:temperature') == record
reopened_binding = reopened.open('simulation:temperature', resources={'external': root})
assert reopened_binding.files() == (root / 'part.bin',)
assert reopened.resume_registration('registration:1') == record
assert not root.exists()
managed = asterstore.Repository(pathlib.Path.cwd() / 'managed')
managed.initialize(store_id='installed', resource_ids=['owned'],
                      managed_resource_id='owned', lifecycle=True)
with managed.prepare('result', publication_id='p1', operation_id='op1',
                                expected_generation=0) as writer:
    writer.write_bytes('logical:member', b'installed bytes', relative_path='part.bin')
    writer.seal()
with managed.resume('op1') as recovery:
    recovery.commit()
assert managed.open('result').files()[0].read_bytes() == b'installed bytes'
assert managed.candidate_status('op1').state == 'current'
held = managed.governance.retain(
    'task', 'result', 'p1', scope=asterstore.RetentionScope.OBJECTS)
managed.governance.retain('audit', 'result', 'p1', scope=asterstore.RetentionScope.METADATA)
with managed.prepare('result', publication_id='p2', operation_id='op2',
                                expected_generation=1) as writer:
    writer.write_bytes('new', b'next generation', relative_path='new.bin')
    writer.commit()
retained = managed.governance.open('task', expected_revision=1)
assert retained.files()[0].read_bytes() == b'installed bytes'
assert not managed.governance.preview().reclaimable
managed.governance.release('task', expected_revision=held.revision)
collected = managed.governance.collect('gc')
assert len(collected.deleted_objects) == 1
assert managed.governance.resume_collection('gc') == collected
assert managed.candidate_status('op1').state == 'retired'
assert managed.describe('result', publication_id='p1').declaration.publication_id == 'p1'
with managed.prepare('result', publication_id='failed', operation_id='failed',
                                expected_generation=2) as writer:
    partial = writer.write_bytes('partial', b'incomplete', relative_path='partial.bin')
managed.governance.abandon('failed')
assert managed.governance.preview_cleanup('failed').files == ('partial.bin',)
cleanup = managed.governance.cleanup('failed')
assert cleanup.complete and cleanup.deleted_files == ('partial.bin',)
assert managed.governance.resume_cleanup('failed') == cleanup
assert not partial.exists()
assert managed.governance.collect('after-cleanup').complete
snapshot = managed.governance.inspect()
assert snapshot.store_id == 'installed'
current_oid = managed.describe('result').declaration.files.objects[0].object_id
assert snapshot.explain_object(current_oid).reasons[0].kind == 'current'
assert managed.governance.check(level='existence').ok
assert managed.governance.check(
    level='checksum', checksums={current_oid: hashlib.sha256(b'next generation').hexdigest()},
).ok
print('Isolated installation OK:', installed)
"""


def run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def only_file(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern} in {directory}, found {matches}")
    return matches[0]


def source_digest(root: Path) -> str:
    """Hash source paths and bytes, excluding local build/environment state."""
    ignored = {
        ".git",
        ".venv",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
    digest = hashlib.sha256()
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = sorted(name for name in subdirs if name not in ignored)
        for name in sorted(files):
            path = Path(directory) / name
            data = path.read_bytes()
            digest.update(str(path.relative_to(root)).encode() + b"\0")
            digest.update(str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--polars", action="store_true", help="also install and verify the engine extra"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="preserve verified artifacts in a new directory"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output_dir is not None:
        args.output_dir = args.output_dir.resolve()
        if args.output_dir.exists():
            parser.error("output directory already exists; refusing to overwrite artifacts")
        if args.output_dir.is_relative_to(root) and not args.output_dir.is_relative_to(
            root / "dist"
        ):
            parser.error("use dist/ or an output directory outside the source tree")
    before = source_digest(root)
    run(args.python, str(root / "tools/check_protocol.py"), cwd=root)
    with tempfile.TemporaryDirectory(prefix="asterstore-distribution-") as temporary:
        work = Path(temporary)
        output = work / "dist"
        # Resolve the declared backend instead of using uv's bundled fast path.
        run(
            "uv",
            "build",
            "--force-pep517",
            "--no-sources",
            "--out-dir",
            str(output),
            str(root),
            cwd=work,
        )
        sdist = only_file(output, "*.tar.gz")
        with tarfile.open(sdist) as archive:
            members = {name.partition("/")[2] for name in archive.getnames()}
            required = {
                "docs/api.md",
                "tests/fixtures/protocol/publication.json",
                "docs/governance.md",
                "docs/protocol.md",
                "tests/fixtures/protocol/cleanup/plan.json",
                "tools/check_protocol.py",
                "examples/cleanup.py",
                "docs/releasing.md",
                "docs/publishing.md",
                "tools/check_distribution.py",
                "tests/test_audit_regressions.py",
                "benchmarks/managed_membership.py",
                "tests/test_polars.py",
                "tests/test_binding.py",
                "src/asterstore/metadata/schemas/store.json",
                "docs/compatibility.md",
                "SECURITY.md",
                "tests/test_governance_processes.py",
                "CONTRIBUTING.md",
                "examples/parquet.py",
                "benchmarks/governance_scale.py",
                "tests/test_supported_format.py",
                "examples/registration.py",
                "tests/test_managed_processes.py",
                "LICENSE",
                "tests/test_managed_cleanup_processes.py",
                "src/asterstore/py.typed",
                "tests/test_registration_processes.py",
                "docs/cleanup.md",
                "examples/governance.py",
                "CHANGELOG.md",
                "benchmarks/parquet_read.py",
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
        run(
            "uv",
            "build",
            "--force-pep517",
            "--no-sources",
            "--wheel",
            str(sdist),
            "-o",
            str(rebuilt),
            cwd=work,
        )
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
        if source_digest(root) != before:
            raise RuntimeError("source changed during verification; artifacts are not exportable")
        if args.output_dir is not None:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            artifacts = []
            for artifact in (only_file(output, "*.whl"), sdist):
                destination = args.output_dir / artifact.name
                shutil.copy2(artifact, destination)
                artifacts.append(
                    {
                        "file": destination.name,
                        "size": destination.stat().st_size,
                        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    }
                )
            report = {
                "version": version,
                "source_sha256": before,
                "python": sys.version,
                "checks": ["protocol_schema_and_codecs", "wheel_core", "sdist_rebuild_core"]
                + (["wheel_polars", "sdist_rebuild_polars"] if args.polars else []),
                "artifacts": artifacts,
                "rebuilt_wheel_sha256": hashlib.sha256(
                    only_file(rebuilt, "*.whl").read_bytes()
                ).hexdigest(),
            }
            (args.output_dir / "verification.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            print("Verified artifacts:", args.output_dir)
    print("Wheel, sdist rebuild and isolated installations: PASS")


if __name__ == "__main__":
    main()
