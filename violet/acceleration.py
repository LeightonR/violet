"""Optional, process-local CUDA acceleration for the Whisper worker.

The bundled CTranslate2 wheel supports CUDA, but it deliberately leaves NVIDIA
runtime libraries out of the application package.  Violet only activates this
path after an explicit user preference and when a pinned CUDA 12 runtime is
present under the current user's Violet data directory.  The process search
path is changed only for Violet and never changes the system CUDA install.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .config import Settings, app_data_dir


CUDA_RUNTIME_DIRECTORY = "cuda12"
_CUDA_DLL_NAMES = ("cublas64_12.dll", "cudnn64_9.dll")
_dll_directory_handles: list[object] = []
_activated_directories: set[Path] = set()


@dataclass(frozen=True, slots=True)
class InferencePlan:
    """The selected local inference backend and a user-facing summary."""

    device: str
    compute_type: str
    detail: str

    @property
    def uses_gpu(self) -> bool:
        return self.device == "cuda"


def cuda_runtime_root() -> Path:
    """Return Violet's private, side-by-side CUDA 12 runtime location."""

    return app_data_dir() / "runtime" / CUDA_RUNTIME_DIRECTORY


def select_inference_plan(
    gpu_acceleration: bool,
    *,
    runtime_root: Path | None = None,
) -> InferencePlan:
    """Choose GPU only after a local runtime and device both verify.

    This function never downloads drivers or model files.  If the optional
    path cannot be used, the established CPU configuration remains available.
    """

    cpu = InferencePlan("cpu", "int8", "CPU acceleration")
    if not gpu_acceleration:
        return cpu
    if sys.platform != "win32":
        return InferencePlan("cpu", "int8", "GPU acceleration is available on Windows only")

    root = Path(runtime_root) if runtime_root is not None else cuda_runtime_root()
    directories = _runtime_dll_directories(root)
    if directories is None:
        return InferencePlan(
            "cpu",
            "int8",
            "GPU acceleration is enabled, but Violet's CUDA 12 runtime is unavailable. Using CPU.",
        )
    try:
        _activate_dll_search_paths(directories)
        if _cuda_device_count() < 1:
            return InferencePlan("cpu", "int8", "No compatible NVIDIA GPU was found. Using CPU.")
    except Exception:
        return InferencePlan("cpu", "int8", "GPU acceleration could not start. Using CPU.")
    # Float16 avoids the recognition changes that can come from mixed INT8
    # GPU decoding. The selected Turbo model is small enough for modern GPUs.
    return InferencePlan("cuda", "float16", "NVIDIA GPU acceleration")


def load_local_whisper_model(
    settings: Settings,
    model_path: Path,
    *,
    model_factory: Callable[..., object] | None = None,
    cuda_probe: Callable[[object], None] | None = None,
) -> tuple[object, InferencePlan]:
    """Load the selected backend and fail safely back to the CPU when needed."""

    factory = model_factory or _whisper_model_factory
    plan = select_inference_plan(settings.gpu_acceleration)
    try:
        model = _construct_model(factory, model_path, settings, plan)
        if plan.uses_gpu:
            (cuda_probe or _probe_cuda_model)(model)
        return model, plan
    except Exception:
        if not plan.uses_gpu:
            raise
        fallback = InferencePlan(
            "cpu",
            "int8",
            "GPU acceleration could not complete its local check. Using CPU.",
        )
        return _construct_model(factory, model_path, settings, fallback), fallback


def _runtime_dll_directories(root: Path) -> tuple[Path, Path] | None:
    cublas = root / "nvidia" / "cublas" / "bin"
    cudnn = root / "nvidia" / "cudnn" / "bin"
    if not all(directory.is_dir() for directory in (cublas, cudnn)):
        return None
    if not (cublas / _CUDA_DLL_NAMES[0]).is_file() or not (cudnn / _CUDA_DLL_NAMES[1]).is_file():
        return None
    return cublas, cudnn


def _activate_dll_search_paths(directories: tuple[Path, Path]) -> None:
    """Make NVIDIA DLLs visible to CTranslate2 for this process only."""

    new_paths: list[str] = []
    for directory in directories:
        resolved = directory.resolve()
        if resolved in _activated_directories:
            continue
        handle = os.add_dll_directory(str(resolved))
        _dll_directory_handles.append(handle)
        _activated_directories.add(resolved)
        new_paths.append(str(resolved))
    if new_paths:
        # CTranslate2's own loader resolves cuBLAS through PATH. Keep this
        # process-local update paired with add_dll_directory for cuDNN's
        # dependent DLL chain.
        os.environ["PATH"] = os.pathsep.join([*new_paths, os.environ.get("PATH", "")])


def _cuda_device_count() -> int:
    import ctranslate2

    return int(ctranslate2.get_cuda_device_count())


def _whisper_model_factory(*args: object, **kwargs: object) -> object:
    from faster_whisper import WhisperModel

    return WhisperModel(*args, **kwargs)


def _construct_model(
    factory: Callable[..., object],
    model_path: Path,
    settings: Settings,
    plan: InferencePlan,
) -> object:
    return factory(
        str(model_path),
        device=plan.device,
        compute_type=plan.compute_type,
        cpu_threads=max(1, settings.cpu_threads),
        num_workers=1,
        local_files_only=True,
    )


def _probe_cuda_model(model: object) -> None:
    """Run one tiny local encoder call so missing DLLs fail before dictation."""

    extractor = getattr(model, "feature_extractor")
    encode = getattr(model, "encode")
    features = extractor(np.zeros(16_000, dtype=np.float32))
    encode(features)
