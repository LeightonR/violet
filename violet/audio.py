"""Ephemeral microphone capture for Violet."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from math import gcd
from math import exp, log10

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal
from scipy.signal import resample_poly


TARGET_SAMPLE_RATE = 16_000
VAD_WINDOW_SAMPLES = 512
# The live meter processes one genuine 32 ms window at a time.  Its small
# streaming adapter retains Silero's recurrent h/c state and preceding audio,
# rather than resetting the model state every 128 ms as the batch wrapper does.
VAD_ANALYSIS_FRAMES = 1
VAD_ANALYSIS_SAMPLES = VAD_WINDOW_SAMPLES * VAD_ANALYSIS_FRAMES
# 160 ms bridges ordinary syllable gaps without leaving a visible waveform
# hanging around after a phrase has genuinely ended.
VAD_HANGOVER_FRAMES = 5

# Visual sensitivity is independent of captured audio. A wider quiet end and
# earlier full-scale reference let ordinary microphones use the meter height.
# A soft knee preserves headroom for loud syllables instead of pinning bars.
VOICE_FLOOR_DB = -54.0
VOICE_CEILING_DB = -20.0


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


class _StreamingSilero:
    """One recording's stateful view of faster-whisper's bundled Silero VAD.

    ``faster_whisper.vad.SileroVADModel.__call__`` intentionally initializes
    its h/c state for a complete offline clip.  Calling it repeatedly for a
    live meter therefore loses speech context every time.  Violet shares only
    the immutable ONNX session; each instance below owns new h/c/context so
    recordings and later transcription never share mutable state.

    The callable fallback is used only by deterministic test doubles.  The
    packaged model exposes ``.session`` and takes the stateful path.
    """

    _CONTEXT_SAMPLES = 64

    def __init__(self, model: object) -> None:
        self._model = model
        self._session = getattr(model, "session", None)
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(self._CONTEXT_SAMPLES, dtype=np.float32)

    def probability(self, window: np.ndarray) -> float:
        window = np.asarray(window, dtype=np.float32).reshape(-1)
        if len(window) != VAD_WINDOW_SAMPLES:
            raise ValueError("Silero live window must be exactly 512 samples")
        if self._session is None:
            # Test doubles keep the historical callable contract.  Production
            # uses the session branch, retaining state between 32 ms samples.
            values = np.asarray(self._model(window), dtype=np.float32).reshape(-1)  # type: ignore[operator]
            return float(values[-1])

        model_input = np.concatenate((self._context, window)).reshape(1, -1)
        outputs = self._session.run(
            None,
            {"input": model_input, "h": self._h, "c": self._c},
        )
        probability, self._h, self._c = outputs
        self._context = window[-self._CONTEXT_SAMPLES :].copy()
        return float(np.asarray(probability, dtype=np.float32).reshape(-1)[0])


def voice_level(window: np.ndarray) -> float:
    """Map a VAD-approved voice window to a stable, non-saturating 0..1 level."""

    values = np.asarray(window, dtype=np.float32).reshape(-1)
    if not len(values):
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64))))
    db = 20.0 * log10(max(rms, 1e-7))
    level = max(0.0, (db - VOICE_FLOOR_DB) / (VOICE_CEILING_DB - VOICE_FLOOR_DB))
    if level > 0.75:
        level = 0.75 + 0.25 * (1.0 - exp(-(level - 0.75) / 0.30))
    return float(min(1.0, level))


class SpeechGate:
    """Streaming Silero visual gate; it never changes audio sent to Whisper."""

    def __init__(
        self,
        threshold: float = 0.5,
        release_threshold: float = 0.35,
        hangover_frames: int = VAD_HANGOVER_FRAMES,
        analysis_frames: int = VAD_ANALYSIS_FRAMES,
    ) -> None:
        if analysis_frames != 1:
            raise ValueError("the live VAD must process one 32 ms frame at a time")
        if hangover_frames < 0:
            raise ValueError("hangover_frames cannot be negative")
        self._threshold = threshold
        self._release_threshold = min(threshold, max(0.0, release_threshold))
        self._hangover_frames = hangover_frames
        self._hangover = 0
        self._was_speech = False
        self._pending = np.empty(0, dtype=np.float32)
        self._model: object | None = None
        self._stream: _StreamingSilero | None = None

    def _probability(self, window: np.ndarray) -> float:
        if self._model is None:
            # faster-whisper's bundled Silero ONNX model has no network path
            # and is constructed only on the meter worker, never PortAudio's
            # callback.  The session itself may be shared, but this gate's h/c
            # and context remain private to one recording.
            self._model = _get_vad_model()
        if self._stream is None:
            self._stream = _StreamingSilero(self._model)
        return self._stream.probability(window)

    def process(self, samples: np.ndarray) -> list[tuple[bool, float]]:
        """Return one VAD-gated level per genuine complete 32 ms window."""

        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not len(samples):
            return []
        self._pending = np.concatenate((self._pending, samples))
        completed = len(self._pending) // VAD_WINDOW_SAMPLES * VAD_WINDOW_SAMPLES
        if not completed:
            return []
        batch, self._pending = self._pending[:completed], self._pending[completed:]
        states: list[tuple[bool, float]] = []
        for window in batch.reshape(-1, VAD_WINDOW_SAMPLES):
            probability = self._probability(window)
            if probability >= self._threshold:
                self._was_speech = True
                self._hangover = self._hangover_frames
                states.append((True, voice_level(window)))
            elif self._was_speech and probability >= self._release_threshold:
                self._hangover = self._hangover_frames
                states.append((True, voice_level(window)))
            elif self._was_speech and self._hangover:
                # A short visual hold prevents a single conservative VAD
                # result from blanking a running word.  Each real non-speech
                # frame contributes a zero target, so the renderer eases the
                # last measured bars away without inventing audio history.
                self._hangover -= 1
                states.append((True, 0.0))
            else:
                self._was_speech = False
                self._hangover = 0
                states.append((False, 0.0))
        return states


@dataclass(slots=True)
class _MeterRun:
    """Immutable ownership for one background visual-meter run."""

    generation: int
    samples: queue.Queue[np.ndarray | None]
    stopping: threading.Event
    sample_rate: int


class SpeechMeter(QObject):
    """Bounded asynchronous VAD meter used only for Violet's waveform."""

    speech_level_changed = Signal(float, bool)
    meter_failed = Signal(str)
    _run_level_changed = Signal(int, float, bool)
    _run_failed = Signal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._run_lock = threading.Lock()
        self._generation = 0
        self._run: _MeterRun | None = None
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=32)
        self._sample_rate = TARGET_SAMPLE_RATE
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        # Worker-originating Qt signals are queued back onto this object's GUI
        # thread.  These forwarding slots reject old generations *there*, so a
        # late old worker cannot update a new recording after a bounded join.
        self._run_level_changed.connect(self._forward_run_level)
        self._run_failed.connect(self._forward_run_failure)

    def start(self, sample_rate: int) -> None:
        self.stop()
        with self._run_lock:
            self._generation += 1
            run = _MeterRun(
                self._generation,
                queue.Queue(maxsize=32),
                threading.Event(),
                sample_rate,
            )
            self._run = run
            # Compatibility/debug aliases always point to the *current* run;
            # worker code below never dereferences these mutable attributes.
            self._queue = run.samples
            self._sample_rate = run.sample_rate
            self._stopping = run.stopping
            self._thread = threading.Thread(target=self._run_loop, args=(run,), name="VioletSpeechMeter", daemon=True)
            thread = self._thread
        thread.start()
        self.speech_level_changed.emit(0.0, False)

    def prewarm(self) -> None:
        """Warm the local Silero session once without touching microphone audio."""

        prewarm_speech_vad()

    def submit(self, samples: np.ndarray) -> None:
        # Never wait in the callback. Dropping stale meter blocks cannot affect
        # captured audio or transcription because this visual path is separate.
        with self._run_lock:
            run = self._run
        if run is None or run.stopping.is_set():
            return
        try:
            run.samples.put_nowait(samples)
        except queue.Full:
            try:
                run.samples.get_nowait()
            except queue.Empty:
                pass
            try:
                run.samples.put_nowait(samples)
            except queue.Full:
                pass

    def stop(self) -> None:
        with self._run_lock:
            run, thread = self._run, self._thread
            self._run = None
            self._thread = None
        if run is None:
            return
        run.stopping.set()
        try:
            run.samples.put_nowait(None)
        except queue.Full:
            pass
        if thread is not None:
            thread.join(timeout=1.5)
        self.speech_level_changed.emit(0.0, False)

    def _is_current_run(self, run: _MeterRun) -> bool:
        with self._run_lock:
            return self._run is run and not run.stopping.is_set()

    def _publish_level(self, run: _MeterRun, level: float, speech_active: bool) -> None:
        if self._is_current_run(run):
            self._run_level_changed.emit(run.generation, level, speech_active)

    def _publish_failure(self, run: _MeterRun, detail: str) -> None:
        if self._is_current_run(run):
            self._run_failed.emit(run.generation, detail)

    def _forward_run_level(self, generation: int, level: float, speech_active: bool) -> None:
        with self._run_lock:
            if self._run is None or self._run.generation != generation or self._run.stopping.is_set():
                return
        self.speech_level_changed.emit(level, speech_active)

    def _forward_run_failure(self, generation: int, detail: str) -> None:
        with self._run_lock:
            if self._run is None or self._run.generation != generation or self._run.stopping.is_set():
                return
        self.meter_failed.emit(detail)

    def _run_loop(self, run: _MeterRun) -> None:
        gate = SpeechGate()
        while not run.stopping.is_set():
            try:
                block = run.samples.get(timeout=0.15)
            except queue.Empty:
                continue
            if block is None:
                return
            try:
                meter_audio = resample_to_16khz(block, run.sample_rate)
                for speech_active, level in gate.process(meter_audio):
                    self._publish_level(run, level, speech_active)
            except Exception as error:
                # Meter faults must not compromise the microphone capture or
                # later full-audio VAD used by transcription.
                self._publish_level(run, 0.0, False)
                self._publish_failure(run, str(error))
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
