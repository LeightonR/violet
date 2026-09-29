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
    voice_level,
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

    def test_streaming_frames_bound_onset_and_silence_release(self) -> None:
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

        # A retained-state Silero call emits one genuine frame at a time, so
        # onset is one 32 ms window rather than the old 128 ms batch burst.
        # Five release frames bridge a tiny syllable gap, then the overlay is
        # explicitly returned to its quiet-listening dots.
        self.assertEqual(emissions[0][0] * 32, 32)
        flattened = [state for _frame, states in emissions for state in states]
        first_false = next(index for index, (active, _level) in enumerate(flattened) if not active)
        self.assertEqual((first_false + 1) * 32, (VAD_HANGOVER_FRAMES + 2) * 32)
        self.assertTrue(all(active for active, _level in flattened[:first_false]))
        self.assertEqual(flattened[first_false], (False, 0.0))
        self.assertEqual(VAD_ANALYSIS_FRAMES, 1)
        self.assertTrue(all(size == VAD_WINDOW_SAMPLES for size in model.calls))

    def test_meter_level_tracks_real_audio_amplitude_not_a_timer_pattern(self) -> None:
        # Silero accepts every frame as speech; only input RMS may now change
        # the visual level.  This exercises the complete gate output that the
        # meter signal forwards to the overlay.
        amplitudes = (0.01, 0.03, 0.06, 0.09)
        model = Probabilities([0.92] * len(amplitudes))
        gate = SpeechGate()
        gate._model = model  # type: ignore[attr-defined]
        samples = np.concatenate(
            [np.full(VAD_WINDOW_SAMPLES, amplitude, dtype=np.float32) for amplitude in amplitudes]
        )
        states = gate.process(samples)
        self.assertEqual(len(states), len(amplitudes))
        self.assertTrue(all(active for active, _level in states))
        levels = [level for _active, level in states]
        self.assertEqual(levels, sorted(levels))
        self.assertGreater(levels[-1] - levels[0], 0.35)
        self.assertLess(levels[-1], 0.95, "the fixed voice map must not saturate ordinary speech")

    def test_voice_level_keeps_quiet_normal_and_loud_speech_distinct(self) -> None:
        windows = [np.full(VAD_WINDOW_SAMPLES, amplitude, dtype=np.float32) for amplitude in (0.01, 0.04, 0.12)]
        levels = [voice_level(window) for window in windows]
        self.assertEqual(levels, sorted(levels))
        self.assertGreater(levels[1] - levels[0], 0.15)
        self.assertGreater(levels[2] - levels[1], 0.10)
        self.assertGreater(levels[0], 0.35, "quiet speech should remain clearly visible")
        self.assertGreater(levels[1], 0.70, "normal speech should use most of the meter height")
        self.assertGreater(levels[2], 0.88, "loud syllables should approach the top of the meter")
        self.assertLess(levels[-1], 0.95, "ordinary loud speech must not pin every bar at full height")

    def test_streaming_session_retains_context_but_new_gate_resets_it(self) -> None:
        class Session:
            def __init__(self) -> None:
                self.calls: list[dict[str, np.ndarray]] = []

            def run(self, _unused, feeds: dict[str, np.ndarray]):
                self.calls.append({key: value.copy() for key, value in feeds.items()})
                return [np.asarray([0.92], dtype=np.float32), feeds["h"] + 1, feeds["c"] + 2]

        class Model:
            def __init__(self, session: Session) -> None:
                self.session = session

        session = Session()
        model = Model(session)
        first = np.full(VAD_WINDOW_SAMPLES, 0.02, dtype=np.float32)
        second = np.full(VAD_WINDOW_SAMPLES, 0.08, dtype=np.float32)
        gate = SpeechGate()
        gate._model = model  # type: ignore[attr-defined]
        gate.process(first)
        gate.process(second)

        self.assertEqual(len(session.calls), 2)
        np.testing.assert_array_equal(session.calls[0]["input"][0, :64], np.zeros(64, dtype=np.float32))
        np.testing.assert_array_equal(session.calls[1]["input"][0, :64], first[-64:])
        np.testing.assert_array_equal(session.calls[1]["h"], np.ones((1, 1, 128), dtype=np.float32))

        fresh = SpeechGate()
        fresh._model = model  # type: ignore[attr-defined]
        fresh.process(second)
        np.testing.assert_array_equal(session.calls[-1]["input"][0, :64], np.zeros(64, dtype=np.float32))
        np.testing.assert_array_equal(session.calls[-1]["h"], np.zeros((1, 1, 128), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
