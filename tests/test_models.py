from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from violet.hardware import HardwareProfile
from violet.models import (
    MODEL_SPECS,
    ModelDownloadConsentRequired,
    ModelIntegrityError,
    adopt_verified_legacy_cache,
    cached_model_path,
    download_verified_model,
    model_spec,
    recommend_model,
    verify_model_directory,
)
from violet.models import _snapshot_download


def profile(**overrides: object) -> HardwareProfile:
    values: dict[str, object] = {
        "architecture": "AMD64",
        "windows_major": 10,
        "windows_minor": 0,
        "windows_build": 26_200,
        "logical_cpus": 8,
        "physical_cores": 8,
        "total_ram_bytes": 16 * 1024**3,
        "available_ram_bytes": 10 * 1024**3,
        "sse41": True,
        "avx2": False,
        "avx512f": False,
        "ctranslate2_compute_types": frozenset({"int8", "float32"}),
    }
    values.update(overrides)
    return HardwareProfile(**values)  # type: ignore[arg-type]


class ModelPolicyTests(unittest.TestCase):
    def test_no_avx2_gate_and_turbo_uses_a_bounded_thread_budget(self) -> None:
        selected = recommend_model(profile())

        self.assertTrue(selected.supported)
        self.assertEqual(selected.spec, model_spec("large-v3-turbo"))
        self.assertEqual(selected.cpu_threads, 8)

    def test_policy_steps_down_for_smaller_hardware(self) -> None:
        small = recommend_model(profile(logical_cpus=4, physical_cores=2, total_ram_bytes=6 * 1024**3, available_ram_bytes=3 * 1024**3))
        base = recommend_model(profile(logical_cpus=2, physical_cores=2, total_ram_bytes=4 * 1024**3, available_ram_bytes=2 * 1024**3))
        tiny = recommend_model(profile(logical_cpus=1, physical_cores=1, total_ram_bytes=2 * 1024**3, available_ram_bytes=1 * 1024**3))

        self.assertEqual((small.spec.model_id, small.cpu_threads), ("small.en", 2))
        self.assertEqual((base.spec.model_id, base.cpu_threads), ("base.en", 2))
        self.assertEqual((tiny.spec.model_id, tiny.cpu_threads), ("tiny.en", 1))

    def test_unsupported_os_or_sse41_returns_no_model(self) -> None:
        old_os = recommend_model(profile(windows_build=17_000))
        old_cpu = recommend_model(profile(sse41=False))

        self.assertFalse(old_os.supported)
        self.assertFalse(old_cpu.supported)
        self.assertIsNone(old_os.spec)
        self.assertIsNone(old_cpu.spec)

    def test_unknown_model_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            model_spec("arbitrary-hub-model")

    def test_consent_is_required_before_any_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ModelDownloadConsentRequired):
                download_verified_model(model_spec("tiny.en"), Path(directory))

    def test_verified_download_is_atomic_and_records_provenance(self) -> None:
        payloads = {name: f"{name}-contents".encode() for name in ("config.json", "model.bin")}
        spec = MODEL_SPECS[0]
        test_spec = type(spec)(
            model_id="test.en",
            repo_id="test/repo",
            revision="a" * 40,
            artifacts={name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()},
            download_bytes=sum(map(len, payloads.values())),
            minimum_total_ram_gib=1,
            minimum_available_ram_gib=1,
            minimum_logical_cpus=1,
            thread_cap=1,
        )

        def fake_download(**kwargs: object) -> str:
            stage = Path(str(kwargs["local_dir"]))
            stage.mkdir(parents=True, exist_ok=True)
            for name, value in payloads.items():
                (stage / name).write_bytes(value)
            return str(stage)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = download_verified_model(test_spec, root, user_confirmed=True, snapshot_download=fake_download)

            self.assertTrue((path / "provenance.json").is_file())
            self.assertEqual(cached_model_path(test_spec, root), path)
            self.assertFalse(any(root.rglob("*.partial-*")))

    def test_bad_checksum_fails_closed(self) -> None:
        spec = model_spec("tiny.en")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in spec.artifacts:
                (root / name).write_bytes(b"not-the-model")
            with self.assertRaises(ModelIntegrityError):
                verify_model_directory(spec, root)

    def test_corrupt_approved_directory_is_never_deleted_by_download(self) -> None:
        spec = model_spec("tiny.en")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "approved" / spec.cache_key / spec.revision
            corrupt.mkdir(parents=True)
            marker = corrupt / "keep-me.txt"
            marker.write_text("do not delete", encoding="utf-8")
            with self.assertRaises(ModelIntegrityError):
                download_verified_model(spec, root, user_confirmed=True)
            self.assertTrue(marker.is_file())

    def test_hub_download_disables_tqdm_for_windowed_processes(self) -> None:
        with patch("huggingface_hub.snapshot_download", return_value="downloaded") as download:
            self.assertEqual(_snapshot_download(repo_id="test/repo"), "downloaded")

        tqdm_class = download.call_args.kwargs["tqdm_class"]
        progress = tqdm_class(total=1)
        try:
            self.assertTrue(progress.disable)
        finally:
            progress.close()

    def test_verified_legacy_snapshot_is_adopted_without_network(self) -> None:
        payloads = {name: f"{name}-contents".encode() for name in ("config.json", "model.bin")}
        original = MODEL_SPECS[0]
        spec = type(original)(
            model_id="adopt.en",
            repo_id="test/legacy-model",
            revision="b" * 40,
            artifacts={name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()},
            download_bytes=sum(map(len, payloads.values())),
            minimum_total_ram_gib=1,
            minimum_available_ram_gib=1,
            minimum_logical_cpus=1,
            thread_cap=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "models--test--legacy-model" / "snapshots" / spec.revision
            legacy.mkdir(parents=True)
            for name, value in payloads.items():
                (legacy / name).write_bytes(value)

            adopted = adopt_verified_legacy_cache(spec, root)

            self.assertIsNotNone(adopted)
            assert adopted is not None
            self.assertEqual(cached_model_path(spec, root), adopted)
            self.assertEqual((legacy / "model.bin").read_bytes(), payloads["model.bin"])
            self.assertIn("no network request", (adopted / "provenance.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
