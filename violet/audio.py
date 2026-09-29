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
# Silero's recurrent VAD needs a little neighboring audio to make stable speech
# decisions.  Four 32 ms frames keep the visual onset bounded to 128 ms while
# avoiding the context-reset flicker produced by calling the model one frame at
# a time.
VAD_ANALYSIS_FRAMES = 4
VAD_ANALYSIS_SAMPLES = VAD_WINDOW_SAMPLES * VAD_ANALYSIS_FRAMES
# 448 ms bridges normal syllable gaps; combined with a four-frame VAD batch it
# bounds a silence release at roughly 0.64 s in the live UI rather than making
# the meter linger for a full second after a phrase.
VAD_HANGOVER_FRAMES = 14


_vad_prewarm_lock = threading.Lock()
_vad_prewarm_started = False


def _get_vad_model():
    """Load faster-whisper's bundled, local Silero model on demand."""

    from faster_whisper.vad import get_vad_model

    return get_vad_model()


def prewarm_speech_vad() -> None:
    """Start one non-blocking local VAD warm-up after Whisper becomes ready.

    The meter must never make a first click wait for Python/ONNX imports.  This
    worker has no network path and is deliberately singleton/idempotent; a
    later recording still reports a clear visual-meter failure if its own VAD
    call cannot be made.
    """

    global _vad_prewarm_started
    with _vad_prewarm_lock:
        if _vad_prewarm_started:
            return
        _vad_prewarm_started = True

    def warm() -> None:
        try:
            _get_vad_model()
        except Exception:
            # SpeechMeter reports an actionable visual-meter problem if this
            # remains unavailable when recording actually starts.
            pass

    threading.Thread(target=warm, name="VioletVADWarmup", daemon=True).start()


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
        hangover_frames: int = VAD_HANGOVER_FRAMES,
        analysis_frames: int = VAD_ANALYSIS_FRAMES,
    ) -> None:
        if analysis_frames < 1:
            raise ValueError("analysis_frames must be at least one")
        if hangover_frames < 0:
            raise ValueError("hangover_frames cannot be negative")
        self._threshold = threshold
        self._release_threshold = min(threshold, max(0.0, release_threshold))
        self._hangover_frames = hangover_frames
        self._analysis_frames = analysis_frames
        self._analysis_samples = VAD_WINDOW_SAMPLES * analysis_frames
        self._hangover = 0
        self._last_speech_level = 0.0
        self._pending = np.empty(0, dtype=np.float32)
        self._model = None

    def process(self, samples: np.ndarray) -> list[tuple[bool, float]]:
        """Return `(speech_active, visual_level)` for complete 32ms windows.

        The VAD receives short contextual batches rather than isolated frames.
        Results still retain per-frame levels, so each waveform bar comes from
        actual microphone energy only after Silero has accepted it as speech.
        """
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not len(samples):
            return []
        self._pending = np.concatenate((self._pending, samples))
        completed = len(self._pending) // self._analysis_samples * self._analysis_samples
        if not completed:
            return []
        batch, self._pending = self._pending[:completed], self._pending[completed:]
        if self._model is None:
            # faster-whisper's bundled Silero ONNX model uses no network and is
            # constructed on the meter thread, never in PortAudio's callback.
            self._model = _get_vad_model()
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
    meter_failed = Signal(str)

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

    def prewarm(self) -> None:
        """Warm the local Silero session once without touching microphone audio."""

        prewarm_speech_vad()

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
            except Exception as error:
                # Meter faults must not compromise the microphone capture or
                # later full-audio VAD used by transcription.
                self.speech_level_changed.emit(0.0, False)
                self.meter_failed.emit(str(error))
                return


class AudioRecorder(QObject):
    level_changed = Signal(float, bool)
    meter_failed = Signal(str)

    def __init__(self, device: int | None = None) -> None:
        super().__init__()
        self._device = device
        self._stream: sd.InputStream | None = None
        self._samples: list[np.ndarray] = []
        self._sample_rate = TARGET_SAMPLE_RATE
        self._lock = threading.Lock()
        self._recording_generation = 0
        self._meter = SpeechMeter()
        self._meter.speech_level_changed.connect(self.level_changed.emit)
        self._meter.meter_failed.connect(self.meter_failed.emit)

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._samples = []
            self._recording_generation += 1
            generation = self._recording_generation
        self._sample_rate = TARGET_SAMPLE_RATE
        try:
            stream = self._open_stream(TARGET_SAMPLE_RATE, generation)
        except sd.PortAudioError:
            # A few USB interfaces reject a 16 kHz hardware stream.  Capture at
            # their native rate and resample in memory when the user stops.
            info = sd.query_devices(self._device, "input")
            native_rate = int(info["default_samplerate"])
            stream = self._open_stream(native_rate, generation)
            self._sample_rate = native_rate
        with self._lock:
            self._stream = stream
        self._meter.start(self._sample_rate)
        stream.start()

    def stop(self) -> AudioClip:
        with self._lock:
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
        """Release capture and discard its PCM without decoding or resampling.

        This path backs the visible X control.  It must not route through
        ``stop()``, because normal completion concatenates/resamples audio for
        Whisper.  Detaching the stream first also prevents a callback from
        being treated as an active recording while cleanup runs.
        """

        # Serialize detachment with PortAudio's callback. A callback that
        # arrives after this block sees no active stream and returns before it
        # can repopulate discarded PCM or the visual-meter queue.
        with self._lock:
            stream, self._stream = self._stream, None
            self._recording_generation += 1
        cleanup_error: Exception | None = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as error:
                cleanup_error = error
            try:
                stream.close()
            except Exception as error:
                cleanup_error = cleanup_error or error
        try:
            self._meter.stop()
        except Exception as error:
            cleanup_error = cleanup_error or error
        finally:
            # Clear even when a driver reports a stop/close problem. Audio is
            # never persisted, concatenated, resampled, or handed to Whisper.
            with self._lock:
                self._samples = []
        if cleanup_error is not None:
            raise cleanup_error

    def prewarm_meter(self) -> None:
        """Prepare the local speech meter without opening an input device."""

        self._meter.prewarm()

    def _open_stream(self, sample_rate: int, generation: int) -> sd.InputStream:
        return sd.InputStream(
            device=self._device,
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=0,
            callback=lambda indata, frames, time_info, status: self._callback(
                indata, frames, time_info, status, generation
            ),
        )

    def _callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time_info,
        _status,
        generation: int | None = None,
    ) -> None:
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        with self._lock:
            if self._stream is None or (generation is not None and generation != self._recording_generation):
                return
            self._samples.append(block)
            # This non-blocking queue operation stays inside the same small
            # lock so cancel cannot detach between retaining PCM and sending a
            # stale visual-meter block.
            self._meter.submit(block)
