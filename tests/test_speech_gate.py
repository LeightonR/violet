from __future__ import annotations

import wave
from pathlib import Path
import unittest

import numpy as np

from violet.audio import (
    SpeechGate,
    TARGET_SAMPLE_RATE,
    VAD_ANALYSIS_FRAMES,
    VAD_HANGOVER_FRAMES,
    VAD_WINDOW_SAMPLES,
    resample_to_16khz,
)


ROOT = Path(__file__).resolve().parents[1]
SPEECH_FIXTURE = ROOT / "benchmarks" / "synthetic_fixture.wav"
OPTIONAL_FIXTURE_REASON = (
    "optional real-audio integration fixture is absent; "
    "run benchmarks/create_synthetic_fixture.ps1 to exercise it"
)


def pcm16_wave(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        assert source.getnchannels() == 1
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        return resample_to_16khz(samples, source.getframerate())


def gate_states(samples: np.ndarray) -> list[tuple[bool, float]]:
    gate = SpeechGate()
    output: list[tuple[bool, float]] = []
    for start in range(0, len(samples), 1024):
        output.extend(gate.process(samples[start : start + 1024]))
    return output


class Probabilities:
    """Deterministic stand-in for bundled Silero used by timing tests."""

    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)
        self.calls: list[int] = []

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        count = len(samples) // VAD_WINDOW_SAMPLES
        self.calls.append(len(samples))
        return np.asarray([next(self.values) for _ in range(count)], dtype=np.float32)


class SpeechGateTests(unittest.TestCase):
    def test_silence_and_broadband_noise_draw_no_waveform(self) -> None:
        silence = np.zeros(TARGET_SAMPLE_RATE * 2, dtype=np.float32)
        noise = np.random.default_rng(7).normal(0.0, 0.12, TARGET_SAMPLE_RATE * 2).astype(np.float32)
        for label, signal in (("silence", silence), ("noise", noise)):
            states = gate_states(signal)
            self.assertTrue(states, label)
            self.assertFalse(any(active for active, _level in states), label)
            self.assertTrue(all(level == 0.0 for _active, level in states), label)

    @unittest.skipUnless(SPEECH_FIXTURE.is_file(), OPTIONAL_FIXTURE_REASON)
    def test_known_speech_enables_visual_meter(self) -> None:
        speech = pcm16_wave(SPEECH_FIXTURE)
        states = gate_states(speech)
        self.assertTrue(any(active for active, _level in states))
        self.assertGreater(max(level for _active, level in states), 0.01)

    def test_contextual_batches_bound_onset_and_silence_release(self) -> None:
        """The meter is prompt but does not flicker between speech syllables."""
        # Frame zero is speech.  Every later frame is explicit non-speech.  The
        # test feeds the same one-frame cadence used by a normal meter queue.
        model = Probabilities([0.92, *([0.05] * 31)])
        gate = SpeechGate(
            hangover_frames=VAD_HANGOVER_FRAMES,
            analysis_frames=VAD_ANALYSIS_FRAMES,
        )
        gate._model = model  # type: ignore[attr-defined]
        window = np.full(VAD_WINDOW_SAMPLES, 0.08, dtype=np.float32)

        emissions: list[tuple[int, list[tuple[bool, float]]]] = []
        for frame_number in range(1, 33):
            states = gate.process(window)
            if states:
                emissions.append((frame_number, states))

        # Four contextual VAD windows cap visual speech onset at 128 ms.  The
        # first non-speech result is emitted at 512 ms of sample cadence (about
        # 480 ms after the final voiced 32 ms window), bridging a syllable gap
        # without leaving a conspicuous lingering meter.
        self.assertEqual(emissions[0][0] * 32, 128)
        flattened = [state for _frame, states in emissions for state in states]
        first_false = next(index for index, (active, _level) in enumerate(flattened) if not active)
        self.assertEqual((first_false + 1) * 32, 512)
        self.assertTrue(all(active for active, _level in flattened[:first_false]))
        self.assertEqual(flattened[first_false], (False, 0.0))
        self.assertTrue(all(size % (VAD_WINDOW_SAMPLES * VAD_ANALYSIS_FRAMES) == 0 for size in model.calls))

    def test_meter_level_tracks_real_audio_amplitude_not_a_timer_pattern(self) -> None:
        # Silero accepts every frame as speech; only input RMS may now change
        # the visual level.  This exercises the complete gate output that the
        # meter signal forwards to the overlay.
        amplitudes = (0.01, 0.03, 0.06, 0.09)
        model = Probabilities([0.92] * len(amplitudes))
        gate = SpeechGate(analysis_frames=4)
        gate._model = model  # type: ignore[attr-defined]
        samples = np.concatenate(
            [np.full(VAD_WINDOW_SAMPLES, amplitude, dtype=np.float32) for amplitude in amplitudes]
        )
        states = gate.process(samples)
        self.assertEqual(len(states), len(amplitudes))
        self.assertTrue(all(active for active, _level in states))
        levels = [level for _active, level in states]
        self.assertEqual(levels, sorted(levels))
        self.assertGreater(levels[-1], levels[0] * 5)


if __name__ == "__main__":
    unittest.main()
