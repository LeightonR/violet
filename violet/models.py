"""Pinned, local-only Whisper model policy and verified model acquisition.

The selector is a conservative startup heuristic, not a hardware benchmark or
a claim that one model is universally best.  It has no side effects.  Download
is an explicit second step because even the Turbo tier is about 1.5 GiB.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from filelock import FileLock

from .hardware import HardwareProfile


# These must be established before the first huggingface_hub import.  Violet
# uses the Hub only for a user-approved, pinned model download and never needs
# an authenticated account or usage telemetry to do that.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")


GIB = 1024**3


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One audited, English-only CTranslate2 model snapshot."""

    model_id: str
    repo_id: str
    revision: str
    artifacts: Mapping[str, str]
    download_bytes: int
    minimum_total_ram_gib: int
    minimum_available_ram_gib: int
    minimum_logical_cpus: int
    thread_cap: int

    @property
    def download_mib(self) -> float:
        return round(self.download_bytes / (1024**2), 1)

    @property
    def cache_key(self) -> str:
        return self.model_id.replace(".", "_").replace("-", "_")


# These are immutable commit hashes and SHA-256s for every runtime artifact
# fetched by faster-whisper. They were captured from the Hub at package review.
# Only these static files are allowlisted; no Python/model-side code is fetched.
MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        model_id="tiny.en",
        repo_id="Systran/faster-whisper-tiny.en",
        revision="0d3d19a32d3338f10357c0889762bd8d64bbdeba",
        artifacts={
            "config.json": "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5",
            "model.bin": "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a",
            "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
        },
        download_bytes=78_090_594,
        minimum_total_ram_gib=2,
        minimum_available_ram_gib=1,
        minimum_logical_cpus=1,
        thread_cap=2,
    ),
    ModelSpec(
        model_id="base.en",
        repo_id="Systran/faster-whisper-base.en",
        revision="3d3d5dee26484f91867d81cb899cfcf72b96be6c",
        artifacts={
            "config.json": "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb",
            "model.bin": "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
            "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
        },
        download_bytes=147_769_510,
        minimum_total_ram_gib=4,
        minimum_available_ram_gib=2,
        minimum_logical_cpus=2,
        thread_cap=4,
    ),
    ModelSpec(
        model_id="small.en",
        repo_id="Systran/faster-whisper-small.en",
        revision="d1d751a5f8271d482d14ca55d9e2deeebbae577f",
        artifacts={
            "config.json": "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae",
            "model.bin": "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
            "tokenizer.json": "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df",
            "vocabulary.txt": "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf",
        },
        download_bytes=486_098_798,
        minimum_total_ram_gib=6,
        minimum_available_ram_gib=3,
        minimum_logical_cpus=4,
        thread_cap=6,
    ),
    ModelSpec(
        model_id="large-v3-turbo",
        repo_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        artifacts={
            "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
            "model.bin": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
            "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
            "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
        },
        download_bytes=1_621_665_983,
        minimum_total_ram_gib=12,
        minimum_available_ram_gib=7,
        minimum_logical_cpus=8,
        thread_cap=8,
    ),
)

_SPECS_BY_ID = {spec.model_id: spec for spec in MODEL_SPECS}


@dataclass(frozen=True, slots=True)
class ModelRecommendation:
    spec: ModelSpec | None
    cpu_threads: int
    reason: str
    supported: bool

    @property
    def download_disclosure(self) -> str:
        if self.spec is None:
            return "No compatible local model was selected."
        return f"{self.spec.model_id} is not included. Download size: {self.spec.download_mib:.1f} MiB."


class ModelDownloadConsentRequired(RuntimeError):
    """Raised before any network request when no explicit model consent exists."""


class ModelIntegrityError(RuntimeError):
    """Raised if a pinned model snapshot is incomplete or has a bad checksum."""


def model_spec(model_id: str) -> ModelSpec:
    """Return an allowlisted model; arbitrary Hub IDs are deliberately rejected."""

    try:
        return _SPECS_BY_ID[model_id]
    except KeyError as error:
        raise ValueError(f"Unsupported Violet model: {model_id}") from error


def recommend_model(profile: HardwareProfile) -> ModelRecommendation:
    """Choose the largest conservative English tier that fits local facts."""

    if not profile.supports_windows_10_1809_or_later:
        return ModelRecommendation(None, 0, "Violet requires Windows 10 version 1809 or later.", False)
    if not profile.is_x64:
        return ModelRecommendation(None, 0, "The CTranslate2 Windows build requires an x64 processor.", False)
    if profile.sse41 is not True:
        return ModelRecommendation(None, 0, "The local CTranslate2 CPU runtime requires SSE4.1 support.", False)
    if profile.ctranslate2_compute_types and "int8" not in profile.ctranslate2_compute_types:
        return ModelRecommendation(None, 0, "The installed CTranslate2 runtime does not expose CPU int8 inference.", False)

    for spec in reversed(MODEL_SPECS):
        if _fits(spec, profile):
            threads = _thread_budget(spec, profile)
            return ModelRecommendation(
                spec,
                threads,
                (
                    "Conservative CPU/RAM heuristic; CTranslate2 chooses the best available ISA at runtime. "
                    "Whisper supplies casing and punctuation directly."
                ),
                True,
            )
    return ModelRecommendation(
        None,
        0,
        "Available memory or CPU capacity is below Violet's conservative tiny.en threshold.",
        False,
    )


def cached_model_path(spec: ModelSpec, cache_root: Path) -> Path | None:
    """Return a verified immutable model snapshot without downloading anything."""

    path = _approved_path(cache_root, spec)
    try:
        verify_model_directory(spec, path)
    except ModelIntegrityError:
        return None
    return path


def adopt_verified_legacy_cache(spec: ModelSpec, cache_root: Path) -> Path | None:
    """Adopt a matching older Hugging Face snapshot without network access.

    Earlier Violet builds used Hugging Face's normal cache layout.  A complete
    snapshot at the exact allowlisted revision is verified byte-for-byte, then
    copied (or hard-linked when supported) into the immutable approved layout.
    The source cache is never modified or deleted.
    """

    cache_root = Path(cache_root)
    target = _approved_path(cache_root, spec)
    lock_path = cache_root / ".locks" / f"{spec.cache_key}-{spec.revision}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path)):
        cached = cached_model_path(spec, cache_root)
        if cached is not None:
            return cached

        # Do not overwrite an unexpected existing approved directory while
        # attempting a non-destructive migration.
        if target.exists():
            return None

        legacy_snapshot = _legacy_snapshot_path(cache_root, spec)
        try:
            verify_model_directory(spec, legacy_snapshot)
        except ModelIntegrityError:
            return None

        target.parent.mkdir(parents=True, exist_ok=True)
        stage = target.parent / f".{spec.revision}.adopting-{uuid.uuid4().hex}"
        try:
            for relative_path in spec.artifacts:
                _copy_or_hardlink(legacy_snapshot / relative_path, stage / relative_path)
            verify_model_directory(spec, stage)
            _write_provenance(spec, stage, source="Verified local legacy Hugging Face cache; no network request.")
            os.replace(stage, target)
            return target
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def download_verified_model(
    spec: ModelSpec,
    cache_root: Path,
    *,
    user_confirmed: bool = False,
    snapshot_download: Callable[..., str] | None = None,
) -> Path:
    """Explicitly fetch one fixed snapshot, verify every file, and atomically publish it.

    No model code is executed: the Hub call allows only the static artifact
    names in ``spec.artifacts`` and sends ``token=False`` so a personal Hub
    login is neither required nor used.
    """

    if not user_confirmed:
        raise ModelDownloadConsentRequired(
            f"Explicit consent is required before downloading {spec.model_id} ({spec.download_mib:.1f} MiB)."
        )

    cache_root = Path(cache_root)
    target = _approved_path(cache_root, spec)
    lock_path = cache_root / ".locks" / f"{spec.cache_key}-{spec.revision}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path)):
        cached = cached_model_path(spec, cache_root)
        if cached is not None:
            return cached
        # Never recursively delete an unexpected approved directory. A user or
        # another process could have replaced it with a junction after a prior
        # failed verification; fail closed and leave repair/removal explicit.
        if target.exists() or target.is_symlink():
            raise ModelIntegrityError(
                f"Existing approved model directory failed verification: {target}. "
                "Remove or repair it before retrying the download."
            )

        stage_parent = target.parent
        stage_parent.mkdir(parents=True, exist_ok=True)
        stage = stage_parent / f".{spec.revision}.partial-{uuid.uuid4().hex}"
        try:
            downloader = snapshot_download or _snapshot_download
            downloader(
                repo_id=spec.repo_id,
                repo_type="model",
                revision=spec.revision,
                local_dir=str(stage),
                local_dir_use_symlinks=False,
                allow_patterns=sorted(spec.artifacts),
                token=False,
            )
            verify_model_directory(spec, stage)
            _write_provenance(spec, stage)
            os.replace(stage, target)
            return target
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise


def verify_model_directory(spec: ModelSpec, directory: Path) -> None:
    """Fail closed unless every expected static artifact matches its SHA-256."""

    directory = Path(directory)
    for relative_path, expected_hash in spec.artifacts.items():
        artifact = directory / relative_path
        if not artifact.is_file():
            raise ModelIntegrityError(f"Missing required model artifact: {relative_path}")
        actual_hash = _sha256_file(artifact)
        if actual_hash != expected_hash:
            raise ModelIntegrityError(f"Checksum mismatch for model artifact: {relative_path}")


def _fits(spec: ModelSpec, profile: HardwareProfile) -> bool:
    total = profile.total_ram_gib
    available = profile.available_ram_gib
    if total is None or total < spec.minimum_total_ram_gib:
        return False
    if available is not None and available < spec.minimum_available_ram_gib:
        return False
    return profile.logical_cpus >= spec.minimum_logical_cpus


def _thread_budget(spec: ModelSpec, profile: HardwareProfile) -> int:
    physical = profile.physical_cores or profile.logical_cpus
    # A resident dictation worker should leave capacity for the desktop.  This
    # is intentionally capped rather than using every logical processor.
    return max(1, min(spec.thread_cap, profile.logical_cpus, physical))


def _approved_path(cache_root: Path, spec: ModelSpec) -> Path:
    return Path(cache_root) / "approved" / spec.cache_key / spec.revision


def _legacy_snapshot_path(cache_root: Path, spec: ModelSpec) -> Path:
    cache_name = "models--" + spec.repo_id.replace("/", "--")
    return Path(cache_root) / cache_name / "snapshots" / spec.revision


def _snapshot_download(**kwargs: object) -> str:
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    class _VioletSilentTqdm(tqdm):
        """Keep an explicit model download safe under pythonw/no console."""

        def __init__(self, *args: object, **progress_kwargs: object) -> None:
            progress_kwargs["disable"] = True
            super().__init__(*args, **progress_kwargs)

    return str(snapshot_download(tqdm_class=_VioletSilentTqdm, **kwargs))


def _write_provenance(
    spec: ModelSpec,
    directory: Path,
    *,
    source: str = "Pinned Hugging Face model snapshot; static allowlisted files only; no authentication token.",
) -> None:
    payload = {
        "format": 1,
        "model": {
            **asdict(spec),
            "artifacts": dict(spec.artifacts),
        },
        "source": source,
    }
    temporary = directory / ".provenance.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(directory / "provenance.json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_or_hardlink(source: Path, destination: Path) -> None:
    """Create one artifact in staging without changing the legacy cache."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved_source = source.resolve(strict=True)
    try:
        os.link(resolved_source, destination)
    except OSError:
        shutil.copy2(resolved_source, destination)
