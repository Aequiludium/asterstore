"""Report peak and post-cleanup bytes, with fixed membership and repeated updates."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from asterstore import Repository


def size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*.json"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publications", type=int, default=30)
    parser.add_argument("--members", type=int, default=128)
    args = parser.parse_args()
    if args.publications < 2 or args.members < 1:
        parser.error("need at least two publications and one member")
    with TemporaryDirectory() as temporary:
        repo = Repository(temporary)
        repo.initialize()
        peak = 0
        for index in range(args.publications):
            with repo.prepare("data", durable=False) as draft:
                if index:
                    draft.reuse_current()
                for member in range(args.members) if not index else [index % args.members]:
                    draft.allocate(str(member)).write_bytes(b"x")
                draft.seal()
                draft.commit()
            peak = max(peak, size(repo.root))
        repo.clean("data", quiescent=True)
        manifests = list(repo.root.rglob("versions/*.json"))
        assert len(manifests) == 2
        print(
            json.dumps(
                {
                    "publications": args.publications,
                    "members": args.members,
                    "peak_metadata_bytes": peak,
                    "retained_metadata_bytes": size(repo.root),
                    "retained_manifests": len(manifests),
                }
            )
        )


if __name__ == "__main__":
    main()
