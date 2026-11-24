from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from violet.acceleration import InferencePlan, load_local_whisper_model, select_inference_plan
from violet.config import Settings


def cuda_runtime(root: Path) -> Path:
    cublas = root / "nvidia" / "cublas" / "bin"
    cudnn = root / "nvidia" / "cudnn" / "bin"
    cublas.mkdir(parents=True)
    cudnn.mkdir(parents=True)
    (cublas / "cublas64_12.dll").write_bytes(b"test")
    (cudnn / "cudnn64_9.dll").write_bytes(b"test")
    return root


class AccelerationTests(unittest.TestCase):
    def test_cpu_is_the_default(self) -> None:
        plan = select_inference_plan(False)
        self.assertEqual((plan.device, plan.compute_type), ("cpu", "int8"))

    def test_missing_side_by_side_runtime_keeps_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = select_inference_plan(True, runtime_root=Path(directory))
        self.assertEqual(plan.device, "cpu")
        self.assertIn("runtime", plan.detail)

    def test_verified_runtime_and_nvidia_device_select_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = cuda_runtime(Path(directory))
            with patch("violet.acceleration.sys.platform", "win32"), patch(
                "violet.acceleration._activate_dll_search_paths"
            ) as activate, patch("violet.acceleration._cuda_device_count", return_value=1):
                plan = select_inference_plan(True, runtime_root=root)
        self.assertEqual((plan.device, plan.compute_type), ("cuda", "float16"))
        activate.assert_called_once()

    def test_gpu_load_failure_retries_once_on_cpu(self) -> None:
        calls: list[dict[str, object]] = []

        def factory(_path: str, **kwargs: object) -> object:
            calls.append(kwargs)
            if kwargs["device"] == "cuda":
                raise RuntimeError("CUDA DLL missing")
            return object()

        with patch(
            "violet.acceleration.select_inference_plan",
            return_value=InferencePlan("cuda", "float16", "NVIDIA GPU acceleration"),
        ):
            model, plan = load_local_whisper_model(Settings(gpu_acceleration=True), Path("model"), model_factory=factory)

        self.assertIsNotNone(model)
        self.assertEqual(plan.device, "cpu")
        self.assertEqual([call["device"] for call in calls], ["cuda", "cpu"])
        self.assertIn("local check", plan.detail)

    def test_gpu_probe_failure_retries_once_on_cpu(self) -> None:
        calls: list[dict[str, object]] = []

        def factory(_path: str, **kwargs: object) -> object:
            calls.append(kwargs)
            return object()

        def probe(_model: object) -> None:
            raise RuntimeError("probe failed")

        with patch(
            "violet.acceleration.select_inference_plan",
            return_value=InferencePlan("cuda", "float16", "NVIDIA GPU acceleration"),
        ):
            _model, plan = load_local_whisper_model(
                Settings(gpu_acceleration=True),
                Path("model"),
                model_factory=factory,
                cuda_probe=probe,
            )

        self.assertEqual(plan.device, "cpu")
        self.assertEqual([call["device"] for call in calls], ["cuda", "cpu"])


if __name__ == "__main__":
    unittest.main()
