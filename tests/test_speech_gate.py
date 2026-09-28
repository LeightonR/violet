from __future__ import annotations

import wave
from pathlib import Path
import unittest

import numpy as np

from violet.audio import SpeechGate, TARGET_SAMPLE_RATE, resample_to_16khz


ROOT = Path(__file__).resolve().parents[1]


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


class SpeechGateTests(unittest.TestCase):
    def test_silence_and_broadband_noise_draw_no_waveform(self) -> None:
        silence = np.zeros(TARGET_SAMPLE_RATE * 2, dtype=np.float32)
        noise = np.random.default_rng(7).normal(0.0, 0.12, TARGET_SAMPLE_RATE * 2).astype(np.float32)
        for label, signal in (("silence", silence), ("noise", noise)):
            states = gate_states(signal)
            self.assertTrue(states, label)
            self.assertFalse(any(active for active, _level in states), label)
            self.assertTrue(all(level == 0.0 for _active, level in states), label)

    def test_known_speech_enables_visual_meter(self) -> None:
        speech = pcm16_wave(ROOT / "benchmarks" / "synthetic_fixture.wav")
        states = gate_states(speech)
        self.assertTrue(any(active for active, _level in states))
        self.assertGreater(max(level for _active, level in states), 0.01)

    def test_recognized_voice_has_hysteretic_syllable_gaps_then_clears(self) -> None:
        class Probabilities:
            def __init__(self, values: list[float]) -> None:
                self.values = iter(values)

            def __call__(self, samples: np.ndarray) -> np.ndarray:
                count = len(samples) // 512
                return np.asarray([next(self.values) for _ in range(count)], dtype=np.float32)

        # Once actual speech is recognized, weak/quiet VAD windows should not
        # make the visible waveform blink at ordinary syllable gaps. A finite
        # hangover still returns to fully blank after sustained non-speech.
        gate = SpeechGate(hangover_frames=12)
        gate._model = Probabilities([0.92, *([0.05] * 15)])  # type: ignore[attr-defined]
        window = np.full(512, 0.08, dtype=np.float32)
        states = [gate.process(window)[0] for _ in range(16)]
        self.assertTrue(all(active for active, _level in states[:13]))
        self.assertFalse(states[-1][0])
        self.assertEqual(states[-1][1], 0.0)


if __name__ == "__main__":
    unittest.main()
