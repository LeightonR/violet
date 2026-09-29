from __future__ import annotations

import os
import time
import unittest
import wave
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from violet.audio import SpeechMeter, _get_vad_model, resample_to_16khz
from violet.focus import FieldRect
from violet.overlay import VioletOverlay


ROOT = Path(__file__).resolve().parents[1]
SPEECH_FIXTURE = ROOT / "benchmarks" / "synthetic_fixture.wav"
OPTIONAL_FIXTURE_REASON = (
    "optional real-audio integration fixture is absent; "
    "run benchmarks/create_synthetic_fixture.ps1 to exercise it"
)


class WaveformAndSpringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _overlay(self) -> VioletOverlay:
        overlay = VioletOverlay()
        # The target is a one-line caret row.  A recording pill must remain
        # attached to its vertical centre throughout the spring.
        overlay.set_target_rect(FieldRect(600, 300, 1300, 322))
        overlay.show()
        self.app.processEvents()
        QTest.qWait(15)
        return overlay

    def _close(self, overlay: VioletOverlay) -> None:
        overlay.hide()
        overlay.deleteLater()
        self.app.processEvents()

    def test_waveform_history_advances_only_with_fresh_speech_samples(self) -> None:
        overlay = self._overlay()
        try:
            overlay.set_state("recording")
            overlay._geometry_animation.stop()
            overlay._place(animated=False)
            overlay.set_speech_level(0.12, True)
            after_quiet_voice = tuple(overlay._envelope)
            quiet_bar = after_quiet_voice[-1]

            # The paint/spinner timer may run many times, but it is forbidden
            # from inventing meter history between actual audio samples.
            for _ in range(12):
                overlay._tick()
            self.assertEqual(tuple(overlay._envelope), after_quiet_voice)

            overlay.set_speech_level(0.90, True)
            self.assertGreater(overlay._envelope[-1], quiet_bar)
            self.assertNotEqual(tuple(overlay._envelope), after_quiet_voice)

            # A VAD-off sample clears every visible bar, including any stale
            # history from a previous phrase.
            overlay.set_speech_level(0.0, False)
            self.assertFalse(overlay._speech_active)
            self.assertTrue(all(level == 0.0 for level in overlay._envelope))
        finally:
            self._close(overlay)

    def test_silent_recording_shows_a_static_hairline_not_fake_speech(self) -> None:
        overlay = self._overlay()
        try:
            overlay.set_state("recording")
            overlay._geometry_animation.stop()
            overlay._place(animated=False)
            overlay.set_speech_level(0.0, False)
            QTest.qWait(12)
            before = overlay.grab().toImage()
            # The thin quiet line is deliberately darker than the nearby body,
            # making the meter lane legible without a false animated waveform.
            self.assertLess(before.pixelColor(75, 21).lightness(), before.pixelColor(75, 24).lightness() - 15)
            for _ in range(10):
                overlay._tick()
            QTest.qWait(12)
            after = overlay.grab().toImage()
            self.assertEqual(before.pixelColor(75, 21), after.pixelColor(75, 21))
            self.assertTrue(all(level == 0.0 for level in overlay._envelope))
        finally:
            self._close(overlay)

    def test_spring_is_recording_lifecycle_only_and_keeps_caret_row_center(self) -> None:
        overlay = self._overlay()
        try:
            original_center = overlay.geometry().top() + overlay.geometry().height() / 2
            state_changes: list[tuple[object, object]] = []
            overlay._geometry_animation.stateChanged.connect(
                lambda new, old: state_changes.append((new, old))
            )

            overlay.set_state("recording")
            QTest.qWait(35)
            self.assertEqual(overlay._geometry_animation.state(), QAbstractAnimation.State.Running)
            before_meter_updates = len(state_changes)
            # This mirrors the 220 ms focus poll.  A same-row update must not
            # stop/restart the spring underneath an active recording.
            same_row = FieldRect(600, 300, 1300, 322)
            for _ in range(3):
                overlay.set_target_rect(same_row)
                overlay.show_for_target(same_row)
            for level in (0.11, 0.54, 0.26, 0.88):
                overlay.set_speech_level(level, True)
                self.app.processEvents()
            # Meter updates alter bars only; they cannot reopen/restart the
            # pill spring while a recording is already in progress.
            self.assertEqual(len(state_changes), before_meter_updates)

            widths: list[int] = []
            centres: list[float] = []
            for _ in range(15):
                QTest.qWait(18)
                geometry = overlay.geometry()
                widths.append(geometry.width())
                centres.append(geometry.top() + geometry.height() / 2)
            self.assertEqual(overlay.width(), overlay._widths["recording"])
            self.assertTrue(any(width > overlay._widths["recording"] for width in widths))
            self.assertTrue(all(abs(center - original_center) <= 0.5 for center in centres))

            # Stopping records contracts to the smaller working pill through
            # the same bounded spring, without changing the active-line row.
            overlay.set_state("transcribing")
            contraction_centres: list[float] = []
            for _ in range(15):
                QTest.qWait(18)
                geometry = overlay.geometry()
                contraction_centres.append(geometry.top() + geometry.height() / 2)
            self.assertEqual(overlay.width(), overlay._widths["transcribing"])
            self.assertTrue(all(abs(center - original_center) <= 0.5 for center in contraction_centres))
        finally:
            self._close(overlay)

    def test_recording_controls_never_occlude_mic_during_spring_widths(self) -> None:
        overlay = self._overlay()
        try:
            overlay.set_state("recording")
            # Every intermediate width that the property animation may render
            # keeps the 42px mic lane free. The clipped waveform must also end
            # before the stop button, rather than relying on paint order.
            mic_lane = QRectF(0, 0, 42, 42)
            for width in range(42, overlay._widths["recording"] + 1, 7):
                overlay.resize(width, 42)
                waveform, stop = overlay._recording_layout()
                if stop is not None:
                    self.assertGreaterEqual(stop.left(), 48.0)
                    self.assertFalse(stop.intersects(mic_lane))
                if waveform is not None:
                    self.assertGreaterEqual(waveform.left(), 48.0)
                    self.assertFalse(waveform.intersects(mic_lane))
                    if stop is not None:
                        self.assertLessEqual(waveform.right(), stop.left() - 6.0)
        finally:
            self._close(overlay)

    @unittest.skipUnless(SPEECH_FIXTURE.is_file(), OPTIONAL_FIXTURE_REASON)
    def test_threaded_meter_updates_overlay_then_clears_after_silence(self) -> None:
        """Exercise the production meter signal path without a microphone."""
        with wave.open(str(SPEECH_FIXTURE), "rb") as source:
            speech = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            speech = resample_to_16khz(speech, source.getframerate())
        overlay = self._overlay()
        meter = SpeechMeter()
        readings: list[tuple[bool, float, float]] = []
        meter.speech_level_changed.connect(overlay.set_speech_level)
        meter.speech_level_changed.connect(lambda level, active: readings.append((active, level, time.monotonic())))
        try:
            overlay.set_state("recording")
            overlay._geometry_animation.stop()
            overlay._place(animated=False)
            # This mirrors the normal app's background model-ready warm-up.
            # It keeps the fixture about real meter timing rather than first
            # import time of optional native packages in a source checkout.
            _get_vad_model()
            meter.start(16_000)

            # Feed real fixture frames at microphone cadence. This verifies
            # actual Silero -> worker thread -> Qt signal -> overlay timing,
            # not just a helper's return values.
            began = time.monotonic()
            for offset in range(0, min(len(speech), 16_000), 512):
                meter.submit(speech[offset : offset + 512])
                self.app.processEvents()
                QTest.qWait(32)
                if any(active for active, _level, _when in readings):
                    break
            self.assertTrue(any(active for active, _level, _when in readings))
            first_voice_time = next(when for active, _level, when in readings if active)
            self.assertLessEqual(first_voice_time - began, 0.45)

            # Continue the known speech briefly so actual fixture energy has
            # several distinct levels rather than a one-frame proxy.
            for offset in range(offset + 512, min(len(speech), offset + 8_192), 512):
                meter.submit(speech[offset : offset + 512])
                self.app.processEvents()
                QTest.qWait(32)
            self.assertTrue(any(level > 0.01 for active, level, _when in readings if active))
            active_levels = [level for active, level, _when in readings if active]
            self.assertGreater(max(active_levels) - min(active_levels), 0.04)

            # Once the worker has drained, paint ticks alone cannot generate a
            # repeating voice pattern; only another meter signal may alter it.
            quiet_deadline = time.monotonic() + 3.0
            last_count = len(readings)
            quiet_since = time.monotonic()
            settled = False
            while time.monotonic() < quiet_deadline:
                self.app.processEvents()
                if len(readings) != last_count:
                    last_count = len(readings)
                    quiet_since = time.monotonic()
                if meter._queue.empty() and time.monotonic() - quiet_since >= 0.35:
                    settled = True
                    break
                QTest.qWait(15)
            self.assertTrue(settled, "meter did not drain its fixture frames")
            history = tuple(overlay._envelope)
            QTest.qWait(180)
            self.assertEqual(tuple(overlay._envelope), history)

            before_silence = len(readings)
            silence_began = time.monotonic()
            for _ in range(30):
                meter.submit(np.zeros(512, dtype=np.float32))
                self.app.processEvents()
                QTest.qWait(32)
                if any(not active for active, _level, _when in readings[before_silence:]):
                    break
            self.assertTrue(any(not active for active, _level, _when in readings[before_silence:]))
            first_silence_time = next(when for active, _level, when in readings[before_silence:] if not active)
            self.assertLessEqual(first_silence_time - silence_began, 0.85)
            self.assertFalse(overlay._speech_active)
            self.assertTrue(all(level == 0.0 for level in overlay._envelope))
        finally:
            meter.stop()
            self._close(overlay)


if __name__ == "__main__":
    unittest.main()
