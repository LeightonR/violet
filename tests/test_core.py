from __future__ import annotations

import ctypes
import struct
import unittest

import numpy as np

from violet.audio import TARGET_SAMPLE_RATE, contains_voice_energy, resample_to_16khz
from violet.wininput import INPUT, normalize_for_typing


class CoreTests(unittest.TestCase):
    def test_unicode_input_uses_native_input_size(self) -> None:
        # SendInput rejects an INPUT struct smaller than the full mouse/keyboard
        # union. This caught an x64-specific failure before fixture testing.
        expected = 40 if struct.calcsize("P") == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)

    def test_dictation_normalization_never_emits_enter_or_tab(self) -> None:
        self.assertEqual(
            normalize_for_typing("  Hello\nworld\t— next\x00  "),
            "Hello world — next",
        )

    def test_audio_resampling_and_silence_guard(self) -> None:
        source = np.linspace(-0.1, 0.1, 8_000, dtype=np.float32)
        converted = resample_to_16khz(source, 8_000)
        self.assertEqual(len(converted), TARGET_SAMPLE_RATE)
        microphone_rate = np.sin(np.linspace(0, 70, 44_100, dtype=np.float32))
        microphone_converted = resample_to_16khz(microphone_rate, 44_100)
        self.assertEqual(len(microphone_converted), TARGET_SAMPLE_RATE)
        self.assertTrue(np.isfinite(microphone_converted).all())
        self.assertFalse(contains_voice_energy(np.zeros(TARGET_SAMPLE_RATE, dtype=np.float32)))
        self.assertTrue(contains_voice_energy(np.full(TARGET_SAMPLE_RATE, 0.01, dtype=np.float32)))

    def test_polyphase_resampler_suppresses_out_of_band_input(self) -> None:
        # A 12 kHz tone is above Whisper's 8 kHz Nyquist limit. The microphone
        # fallback must filter it rather than fold it into a false low-frequency
        # signal as interpolation would.
        source_rate = 48_000
        time = np.arange(source_rate, dtype=np.float32) / source_rate
        high = np.sin(2 * np.pi * 12_000 * time).astype(np.float32)
        low = np.sin(2 * np.pi * 1_000 * time).astype(np.float32)
        high_converted = resample_to_16khz(high, source_rate)
        low_converted = resample_to_16khz(low, source_rate)
        middle = slice(500, -500)  # Ignore finite-filter start/end transients.
        high_rms = float(np.sqrt(np.mean(np.square(high_converted[middle]))))
        low_rms = float(np.sqrt(np.mean(np.square(low_converted[middle]))))
        self.assertEqual(high_converted.dtype, np.float32)
        self.assertTrue(np.isfinite(high_converted).all())
        self.assertLess(high_rms, 0.02)
        self.assertGreater(low_rms, 0.5)


if __name__ == "__main__":
    unittest.main()
