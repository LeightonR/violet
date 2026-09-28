from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from violet.config import DEFAULT_MODEL, MAX_CPU_THREADS, Settings, load_settings, save_settings, settings_path
from violet.hardware import HardwareProfile
from violet.models import ModelRecommendation, model_spec


def compatible_profile() -> HardwareProfile:
    return HardwareProfile(
        architecture="AMD64",
        windows_major=10,
        windows_minor=0,
        windows_build=26_200,
        logical_cpus=16,
        physical_cores=8,
        total_ram_bytes=32 * 1024**3,
        available_ram_bytes=24 * 1024**3,
        sse41=True,
        avx2=True,
        avx512f=False,
        ctranslate2_compute_types=frozenset({"int8"}),
    )


class ConfigSafetyTests(unittest.TestCase):
    def test_load_settings_allowlists_model_and_bounds_local_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            path = settings_path()
            path.write_text(
                json.dumps(
                    {
                        "model_id": "an-untrusted/hub-repository",
                        "cpu_threads": 10_000,
                        "language": "fr",
                        "input_device": -3,
                    }
                ),
                encoding="utf-8",
            )

            settings = load_settings()

        self.assertEqual(settings.model_id, DEFAULT_MODEL)
        self.assertEqual(settings.cpu_threads, MAX_CPU_THREADS)
        self.assertEqual(settings.language, "en")
        self.assertIsNone(settings.input_device)

    def test_existing_settings_are_not_replaced_by_first_run_heuristic(self) -> None:
        from violet.app import initial_settings_for_hardware

        profile = compatible_profile()
        recommended = ModelRecommendation(model_spec("large-v3-turbo"), 8, "test recommendation", True)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}):
            with patch("violet.app.detect_hardware", return_value=profile), patch(
                "violet.app.recommend_model", return_value=recommended
            ):
                first, _, _ = initial_settings_for_hardware(persist_first_launch=True)
            self.assertEqual(first.model_id, "large-v3-turbo")
            self.assertEqual(first.cpu_threads, 8)

            save_settings(Settings(model_id="base.en", cpu_threads=3, input_device=2))
            with patch("violet.app.detect_hardware", return_value=profile), patch(
                "violet.app.recommend_model", return_value=recommended
            ):
                existing, _, _ = initial_settings_for_hardware(persist_first_launch=True)

        self.assertEqual(existing.model_id, "base.en")
        self.assertEqual(existing.cpu_threads, 3)
        self.assertEqual(existing.input_device, 2)


if __name__ == "__main__":
    unittest.main()
