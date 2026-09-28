"""PyInstaller entry point and a non-interactive frozen-runtime smoke check."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import wave
from pathlib import Path


def _write_smoke_result(payload: dict[str, object]) -> None:
    destination = os.environ.get("VIOLET_SMOKE_RESULT")
    if destination:
        Path(destination).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _read_wave_as_16khz_float32(path: Path, numpy, resample_poly):
    """Decode the private release fixture with stdlib wave and SciPy only."""

    with wave.open(str(path), "rb") as source:
        if source.getcomptype() != "NONE" or source.getsampwidth() != 2:
            raise RuntimeError("The packaging fixture must be uncompressed 16-bit PCM WAV")
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        samples = numpy.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(numpy.float32)
    if channels < 1 or samples.size % channels:
        raise RuntimeError("The packaging fixture has invalid channel data")
    if channels > 1:
        samples = samples.reshape((-1, channels)).mean(axis=1)
    samples /= numpy.float32(32768.0)
    divisor = math.gcd(sample_rate, 16_000)
    if sample_rate != 16_000:
        samples = resample_poly(samples, up=16_000 // divisor, down=sample_rate // divisor)
    return numpy.asarray(samples, dtype=numpy.float32)


def _run_optional_local_inference(numpy, resample_poly) -> bool:
    """Run one offline NumPy inference only when the build explicitly opts in."""

    raw_model_path = os.environ.get("VIOLET_SMOKE_MODEL_PATH")
    raw_audio_path = os.environ.get("VIOLET_SMOKE_AUDIO_PATH")
    if not raw_model_path and not raw_audio_path:
        return False
    if not raw_model_path or not raw_audio_path:
        raise RuntimeError("The packaging inference smoke needs both a local model and WAV fixture")

    from faster_whisper import WhisperModel
    from violet.models import model_spec, verify_model_directory

    model_path = Path(raw_model_path)
    verify_model_directory(model_spec("large-v3-turbo"), model_path)
    samples = _read_wave_as_16khz_float32(Path(raw_audio_path), numpy, resample_poly)
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
        num_workers=1,
        local_files_only=True,
    )
    segments, _info = model.transcribe(
        samples,
        language="en",
        task="transcribe",
        beam_size=1,
        best_of=1,
        temperature=0,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )
    # Consume the lazy iterator: loading the model alone is not sufficient.
    list(segments)
    return True


def _probe_owned_qt_edit(window_handle: int) -> tuple[int, int, int, int]:
    """Read only our temporary Qt fixture through UIA on a COM worker thread."""

    import pythoncom
    import uiautomation as auto

    pythoncom.CoInitialize()
    try:
        root = auto.ControlFromHandle(window_handle)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            for child in root.GetChildren():
                if (
                    child.ControlTypeName == "EditControl"
                    and child.AutomationId.endswith(".violet_frozen_packaging_field")
                ):
                    rectangle = child.BoundingRectangle
                    if rectangle.right > rectangle.left and rectangle.bottom > rectangle.top:
                        return (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)
            time.sleep(0.05)
        raise RuntimeError("UI Automation could not see the packaged Qt fixture")
    finally:
        pythoncom.CoUninitialize()


def _owned_qt_uia_bounds(application, window) -> tuple[int, int, int, int]:
    """Pump the Qt GUI thread while a separate COM thread probes the fixture."""

    result: dict[str, object] = {}

    def probe() -> None:
        try:
            result["bounds"] = _probe_owned_qt_edit(int(window.winId()))
        except Exception as error:  # Return the thread error to the smoke gate.
            result["error"] = error

    worker = threading.Thread(target=probe, name="violet-uia-smoke", daemon=True)
    worker.start()
    deadline = time.monotonic() + 4.0
    while worker.is_alive() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    worker.join(timeout=0.1)
    if worker.is_alive():
        raise RuntimeError("UI Automation fixture probe timed out")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result["bounds"]  # type: ignore[return-value]


def _frozen_smoke() -> int:
    """Exercise packaged native imports without recording or reading user text."""

    try:
        import ctranslate2
        import faster_whisper
        import numpy
        import onnxruntime
        import sounddevice
        import scipy
        from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget
        from scipy.signal import resample_poly

        application = QApplication.instance() or QApplication([])
        fixture = QWidget()
        fixture.setWindowTitle("Violet frozen packaging UIA fixture")
        fixture.setObjectName("violet_frozen_packaging_window")
        edit = QLineEdit()
        edit.setObjectName("violet_frozen_packaging_field")
        edit.setAccessibleName("Violet frozen packaging editable field")
        layout = QVBoxLayout(fixture)
        layout.addWidget(edit)
        fixture.resize(300, 70)
        fixture.show()
        application.processEvents()
        bounds = _owned_qt_uia_bounds(application, fixture)
        # Querying devices loads PortAudio but does not start an input stream.
        device_count = len(sounddevice.query_devices())
        resampled = resample_poly(numpy.array([0.0, 1.0, 0.0], dtype=numpy.float32), up=2, down=1)
        if len(resampled) != 6:
            raise RuntimeError("SciPy resample_poly is unavailable in the frozen bundle")
        assets = Path(faster_whisper.__file__).resolve().parent / "assets"
        if not assets.is_dir():
            raise RuntimeError("faster-whisper VAD assets are missing from the frozen bundle")
        fixture.close()
        inference_completed = _run_optional_local_inference(numpy, resample_poly)
        _write_smoke_result(
            {
                "ctranslate2_compute_types": sorted(ctranslate2.get_supported_compute_types("cpu")),
                "device_count": device_count,
                "faster_whisper": getattr(faster_whisper, "__version__", "unknown"),
                "onnxruntime": onnxruntime.__version__,
                "scipy": scipy.__version__,
                "local_numpy_inference_completed": inference_completed,
                "uia_fixture_bounds": list(bounds),
            }
        )
        return 0
    except Exception as error:
        _write_smoke_result({"error": f"{type(error).__name__}: {error}"})
        return 1


def main() -> int:
    if "--packaging-smoke" in sys.argv[1:]:
        return _frozen_smoke()
    from violet.app import run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
