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
import importlib.metadata
import importlib.util
import pathlib
import sys
import asterstore
installed = pathlib.Path(asterstore.__file__).resolve()
assert installed.is_relative_to(pathlib.Path(sys.prefix).resolve()), installed
assert installed.with_name('py.typed').is_file()
assert importlib.metadata.version('asterstore') == sys.argv[1]
distribution = importlib.metadata.distribution('asterstore')
assert distribution.metadata['License-Expression'] == 'Apache-2.0'
licenses = [p for p in distribution.files or [] if str(p).endswith('.dist-info/licenses/LICENSE')]
assert len(licenses) == 1
assert hashlib.sha256(distribution.locate_file(licenses[0]).read_bytes()).hexdigest() == sys.argv[2]
assert importlib.util.find_spec('jsonschema') is None
assert not hasattr(asterstore.Repository, 'governance')
repo = asterstore.Repository(pathlib.Path.cwd() / 'store')
repo.initialize()
for index in range(3):
    with repo.prepare('data') as draft:
        draft.allocate('value').write_bytes(str(index).encode())
        draft.seal()
        draft.commit()
assert repo.open('data').files()[0].read_bytes() == b'2'
assert repo.open('data', version='rollback').files()[0].read_bytes() == b'1'
assert repo.clean('data', quiescent=True).deleted_files == 2
repo.rollback('data', expected_revision=3)
assert repo.open('data').files()[0].read_bytes() == b'1'
assert repo.describe('data').rollback is None
print('Isolated format-2 installation OK:', installed)
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
                "README.md",
                "docs/api.md",
                "docs/protocol.md",
                "docs/releasing.md",
                "tools/check_protocol.py",
                "tools/check_distribution.py",
                "examples/parquet.py",
                "examples/two_versions.py",
                "tests/test_two_version.py",
                "tests/test_polars.py",
                "src/asterstore/py.typed",
                "src/asterstore/schemas/store.json",
                "benchmarks/retention_scale.py",
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
