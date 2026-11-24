"""Fail closed if a delivered release contains the local profile or workspace path."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


# These exact published Windows wheel members contain the standard GitHub
# runner path from their upstream builds. Other files and all user/workspace
# paths must still pass the ordinary scan.
_UPSTREAM_RUNNER_FILES = {
    "_internal/hf_xet/hf_xet.pyd": "ea8f5ddb1ae42adf5e4d376ec27b7f1ea1fe843e15f5d7a9e45fe1298ca4fd4c",
    "_internal/numpy-2.2.6.dist-info/DELVEWHEEL": "513b04bcaa783e2cee07d76110f470d50fd5f12599006a93953588607f61c953",
    "_internal/tokenizers/tokenizers.pyd": "05848a2a85e1466d19242499ed6b8c016a886db79aca020792aa5598ded77ef9",
}


def _needle_variants(value: str) -> tuple[bytes, ...]:
    alternatives = {value, value.replace("\\", "\\\\")}
    return tuple(
        encoded
        for item in alternatives
        for encoded in (item.encode("utf-8"), item.encode("utf-16-le"))
    )


def _contains(path: Path, needles: tuple[bytes, ...]) -> bool:
    # A chunk overlap preserves matches that straddle a read boundary. bytes.find
    # is implemented in C, keeping scans of the onedir bundle practical.
    overlap = max(len(needle) for needle in needles) - 1
    tail = b""
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            payload = tail + block
            if any(payload.find(needle) >= 0 for needle in needles):
                return True
            tail = payload[-overlap:] if overlap else b""
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", type=Path, required=True)
    parser.add_argument("--needle", action="append", required=True)
    args = parser.parse_args()
    needles = tuple(needle for value in args.needle for needle in _needle_variants(value))
    other_needles = tuple(needle for value in args.needle if value.lower() != r"c:\users\runneradmin" for needle in _needle_variants(value))
    hits: list[str] = []
    for root in args.root:
        if not root.is_dir():
            raise SystemExit(f"privacy scan root is missing: {root}")
        for path in root.rglob("*"):
            if path.is_file() and _contains(path, needles):
                relative = path.relative_to(root).as_posix()
                expected = _UPSTREAM_RUNNER_FILES.get(relative)
                if expected and (not other_needles or not _contains(path, other_needles)):
                    with path.open("rb") as upstream:
                        if hashlib.file_digest(upstream, "sha256").hexdigest() == expected:
                            continue
                # Print only the relative file name, not any sensitive match.
                hits.append(f"{root.name}/{path.relative_to(root).as_posix()}")
    if hits:
        print("Release privacy scan found local path material in: " + "; ".join(hits))
        return 1
    print("Release privacy scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
