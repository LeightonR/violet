"""Ephemeral microphone capture for Violet."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from math import gcd

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal
from scipy.signal import resample_poly


TARGET_SAMPLE_RATE = 16_000
VAD_WINDOW_SAMPLES = 512


@dataclass(frozen=True, slots=True)
class AudioClip:
    samples: np.ndarray
    sample_rate: int


def list_input_devices() -> list[dict[str, object]]:
    """Return microphone metadata only; no test recording is made."""
    devices: list[dict[str, object]] = []
    for index, item in enumerate(sd.query_devices()):
        if item["max_input_channels"] > 0:
            devices.append(
                {
                    "index": index,
                    "name": item["name"],
                    "channels": int(item["max_input_channels"]),
                    "default_sample_rate": int(item["default_samplerate"]),
                }
            )
    return devices


def resample_to_16khz(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Resample microphone PCM with a band-limited polyphase filter.

    Violet receives NumPy microphone frames, never media files.  Keeping this
    path in SciPy avoids packaging PyAV/FFmpeg codecs solely for a 44.1/48 kHz
    input-device fallback.  ``resample_poly`` includes the low-pass filter
    needed before downsampling, unlike linear interpolation.
    """

    if sample_rate <= 0:
        raise ValueError("microphone sample rate must be positive")
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate == TARGET_SAMPLE_RATE:
        return samples
    if len(samples) < 2:
        return samples
    divisor = gcd(sample_rate, TARGET_SAMPLE_RATE)
    up = TARGET_SAMPLE_RATE // divisor
    down = sample_rate // divisor
    converted = resample_poly(samples, up, down)
    return np.asarray(converted, dtype=np.float32)


def contains_voice_energy(samples: np.ndarray) -> bool:
    """A cheap silence guard before model inference; VAD remains authoritative."""
    if len(samples) < TARGET_SAMPLE_RATE // 5:
        return False
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return rms >= 0.0008


class SpeechGate:
    """Silero-VAD visual gate; it never changes the audio sent to Whisper."""

    def __init__(
        self,
        threshold: float = 0.5,
        release_threshold: float = 0.35,
        hangover_frames: int = 12,
    ) -> None:
        self._threshold = threshold
        self._release_threshold = min(threshold, max(0.0, release_threshold))
        self._hangover_frames = hangover_frames
        self._hangover = 0
        self._last_speech_level = 0.0
        self._pending = np.empty(0, dtype=np.float32)
        self._model = None

    def process(self, samples: np.ndarray) -> list[tuple[bool, float]]:
        """Return `(speech_active, visual_level)` for complete 32ms windows."""
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not len(samples):
            return []
        self._pending = np.concatenate((self._pending, samples))
        completed = len(self._pending) // VAD_WINDOW_SAMPLES * VAD_WINDOW_SAMPLES
        if not completed:
            return []
        batch, self._pending = self._pending[:completed], self._pending[completed:]
        if self._model is None:
            # faster-whisper's bundled Silero ONNX model uses no network and is
            # constructed on the meter thread, never in PortAudio's callback.
            from faster_whisper.vad import get_vad_model

            self._model = get_vad_model()
        probabilities = np.asarray(self._model(batch), dtype=np.float32).reshape(-1)
        windows = batch.reshape(-1, VAD_WINDOW_SAMPLES)
        states: list[tuple[bool, float]] = []
        for window, probability in zip(windows, probabilities):
            if probability >= self._threshold:
                self._hangover = self._hangover_frames
                self._last_speech_level = min(
                    1.0,
                    float(np.sqrt(np.mean(np.square(window, dtype=np.float64))) * 12.0),
                )
                states.append((True, self._last_speech_level))
            elif self._hangover and probability >= self._release_threshold:
                # A lower release threshold avoids visibly dropping the meter
                # between syllables while preserving the higher threshold for
                # initially showing it above non-speech noise.
                self._hangover = self._hangover_frames
                self._last_speech_level = min(
                    1.0,
                    float(np.sqrt(np.mean(np.square(window, dtype=np.float64))) * 12.0),
                )
                states.append((True, self._last_speech_level))
            elif self._hangover:
                self._hangover -= 1
                # Decay the prior speech envelope; noise is never made into a
                # visual bar during the short speech hangover.
                states.append(
                    (True, self._last_speech_level * self._hangover / max(1, self._hangover_frames))
                )
            else:
                self._last_speech_level = 0.0
                states.append((False, 0.0))
        return states


class SpeechMeter(QObject):
    """Bounded asynchronous VAD meter used only for Violet's waveform."""

    speech_level_changed = Signal(float, bool)

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=32)
        self._sample_rate = TARGET_SAMPLE_RATE
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def start(self, sample_rate: int) -> None:
        self.stop()
        self._sample_rate = sample_rate
        self._queue = queue.Queue(maxsize=32)
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="VioletSpeechMeter", daemon=True)
        self._thread.start()
        self.speech_level_changed.emit(0.0, False)

    def submit(self, samples: np.ndarray) -> None:
        # Never wait in the callback. Dropping stale meter blocks cannot affect
        # captured audio or transcription because this visual path is separate.
        try:
            self._queue.put_nowait(samples)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(samples)
            except queue.Full:
                pass

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stopping.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread.join(timeout=1.5)
        self.speech_level_changed.emit(0.0, False)

    def _run(self) -> None:
        gate = SpeechGate()
        while not self._stopping.is_set():
            try:
                block = self._queue.get(timeout=0.15)
            except queue.Empty:
                continue
            if block is None:
                return
            try:
                meter_audio = resample_to_16khz(block, self._sample_rate)
                for speech_active, level in gate.process(meter_audio):
                    self.speech_level_changed.emit(level, speech_active)
            except Exception:
                # Meter faults must not compromise the microphone capture or
                # later full-audio VAD used by transcription.
                self.speech_level_changed.emit(0.0, False)


class AudioRecorder(QObject):
    level_changed = Signal(float, bool)

    def __init__(self, device: int | None = None) -> None:
        super().__init__()
        self._device = device
        self._stream: sd.InputStream | None = None
        self._samples: list[np.ndarray] = []
        self._sample_rate = TARGET_SAMPLE_RATE
        self._lock = threading.Lock()
        self._meter = SpeechMeter()
        self._meter.speech_level_changed.connect(self.level_changed.emit)

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._samples = []
        self._sample_rate = TARGET_SAMPLE_RATE
        try:
            self._stream = self._open_stream(TARGET_SAMPLE_RATE)
        except sd.PortAudioError:
            # A few USB interfaces reject a 16 kHz hardware stream.  Capture at
            # their native rate and resample in memory when the user stops.
            info = sd.query_devices(self._device, "input")
            native_rate = int(info["default_samplerate"])
            self._stream = self._open_stream(native_rate)
            self._sample_rate = native_rate
        self._meter.start(self._sample_rate)
        self._stream.start()

    def stop(self) -> AudioClip:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        self._meter.stop()
        with self._lock:
            blocks, self._samples = self._samples, []
        samples = np.concatenate(blocks) if blocks else np.empty(0, dtype=np.float32)
        return AudioClip(resample_to_16khz(samples, self._sample_rate), TARGET_SAMPLE_RATE)

    def cancel(self) -> None:
        if self._stream is not None:
            self.stop()

    def _open_stream(self, sample_rate: int) -> sd.InputStream:
        return sd.InputStream(
            device=self._device,
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=0,
            callback=self._callback,
        )

    def _callback(self, indata: np.ndarray, _frames: int, _time_info, _status) -> None:
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        with self._lock:
            self._samples.append(block)
        self._meter.submit(block)
