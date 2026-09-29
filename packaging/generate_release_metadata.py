"""Generate deterministic release metadata without embedding local paths."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _requirement_names(lock: Path) -> list[str]:
    names: list[str] = []
    for raw in lock.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, marker, _version = line.partition("==")
        if not marker:
            raise ValueError(f"The runtime lock must pin every requirement: {line}")
        names.append(name)
    return names


def wheel_manifest(wheelhouse: Path, output: Path) -> None:
    wheels = []
    for wheel in sorted(wheelhouse.glob("*.whl"), key=lambda item: item.name.lower()):
        wheels.append({"file": wheel.name, "sha256": _sha256(wheel), "bytes": wheel.stat().st_size})
    if not wheels:
        raise RuntimeError(f"No wheels found in {wheelhouse}")
    _write_json(output, {"format": 1, "kind": "downloaded-wheel-sha256", "wheels": wheels})


def _spdx_identifier(name: str) -> str:
    return "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]+", "-", name)


def _source_date() -> str:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def spdx_sbom(lock: Path, output: Path, licenses_dir: Path) -> None:
    packages: list[dict[str, Any]] = []
    licenses_dir.mkdir(parents=True, exist_ok=True)
    for requirement in sorted(_requirement_names(lock), key=str.lower):
        distribution = metadata.distribution(requirement)
        package_name = distribution.metadata.get("Name") or requirement
        declared_license = _spdx_expression(distribution.metadata.get("License-Expression"))
        package = {
            "SPDXID": _spdx_identifier(package_name),
            "name": package_name,
            "versionInfo": distribution.version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": declared_license,
            "copyrightText": "NOASSERTION",
        }
        comments = list(distribution.metadata.get_all("Project-URL") or [])
        if declared_license == "NOASSERTION" and distribution.metadata.get("License"):
            comments.append("Upstream provided a non-SPDX License metadata field; see shipped license material where available.")
        if comments:
            package["comment"] = "; ".join(comments)
        packages.append(package)
        _copy_distribution_license_files(distribution, licenses_dir / _safe_directory_name(package_name))

    payload = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "Violet-runtime-0.1.1",
        "documentNamespace": "https://github.com/LeightonR/violet/releases/tag/v0.1.1/sbom",
        "creationInfo": {"creators": ["Tool: Violet packaging metadata"], "created": _source_date()},
        "documentDescribes": ["SPDXRef-Package-Violet-runtime"],
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-Violet-runtime",
                "name": "Violet",
                "versionInfo": "0.1.1",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
            },
            *packages,
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-Package-Violet-runtime",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package["SPDXID"],
            }
            for package in packages
        ],
    }
    _write_json(output, payload)


def _safe_directory_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _spdx_expression(value: str | None) -> str:
    """Return a short SPDX expression, never free-form license prose."""

    if not value:
        return "NOASSERTION"
    candidate = value.strip()
    if len(candidate) > 200 or "\n" in candidate:
        return "NOASSERTION"
    if re.fullmatch(r"[A-Za-z0-9.+\-() ]+(?: WITH [A-Za-z0-9.+\-() ]+)?", candidate):
        return candidate
    return "NOASSERTION"


def _copy_distribution_license_files(distribution: metadata.Distribution, destination: Path) -> None:
    files = distribution.files or []
    chosen = []
    for file in files:
        lower_parts = tuple(part.lower() for part in file.parts)
        leaf = file.name.lower()
        is_license = leaf.startswith(("license", "copying", "notice")) or "thirdpartynotices" in leaf
        nested_license = any(part in {"license", "licenses", "notices"} for part in lower_parts)
        if (is_license or nested_license) and "__pycache__" not in lower_parts and not leaf.endswith((".pyc", ".pyo")):
            chosen.append(file)
    for relative in chosen:
        source = distribution.locate_file(relative)
        if source.is_file():
            relative_target = Path(*relative.parts)
            target = destination / relative_target
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def artifact_manifest(directory: Path, output: Path) -> None:
    excluded = {output.resolve()}
    files = []
    for file in sorted(directory.rglob("*"), key=lambda item: item.as_posix().lower()):
        if file.is_file() and file.resolve() not in excluded:
            files.append(
                {
                    "path": file.relative_to(directory).as_posix(),
                    "sha256": _sha256(file),
                    "bytes": file.stat().st_size,
                }
            )
    _write_json(output, {"format": 1, "kind": "release-file-sha256", "files": files})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    wheel = commands.add_parser("wheel-manifest")
    wheel.add_argument("--wheelhouse", type=Path, required=True)
    wheel.add_argument("--output", type=Path, required=True)
    sbom = commands.add_parser("sbom")
    sbom.add_argument("--lock", type=Path, required=True)
    sbom.add_argument("--output", type=Path, required=True)
    sbom.add_argument("--licenses-dir", type=Path, required=True)
    artifact = commands.add_parser("artifact-manifest")
    artifact.add_argument("--directory", type=Path, required=True)
    artifact.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "wheel-manifest":
        wheel_manifest(args.wheelhouse, args.output)
    elif args.command == "sbom":
        spdx_sbom(args.lock, args.output, args.licenses_dir)
    else:
        artifact_manifest(args.directory, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
