from __future__ import annotations

import os
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from violet.audio import SpeechMeter, _get_vad_model, resample_to_16khz
from violet.focus import FieldRect
from violet.overlay import QUIET_DOT_SIZE, VioletOverlay


ROOT = Path(__file__).resolve().parents[1]
SPEECH_FIXTURE = ROOT / "benchmarks" / "synthetic_fixture.wav"
OPTIONAL_FIXTURE_REASON = (
    "optional real-audio integration fixture is absent; "
    "run benchmarks/create_synthetic_fixture.ps1 to exercise it"
)


class PaintTrackingOverlay(VioletOverlay):
    """App-owned paint timestamps for frame-driver validation."""

    def __init__(self) -> None:
        self.paint_times: list[int] = []
        super().__init__()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        self.paint_times.append(time.perf_counter_ns())
        super().paintEvent(event)


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

    def test_frame_driver_smooths_measured_targets_then_goes_idle(self) -> None:
        overlay = PaintTrackingOverlay()
        overlay.set_target_rect(FieldRect(600, 300, 1300, 322))
        overlay.show()
        try:
            overlay.set_state("recording")
            overlay._stop_spring()
            overlay._place(animated=False)
            QTest.qWait(10)
            overlay.paint_times.clear()
            overlay.set_speech_level(0.18, True)
            QTest.qWait(34)
            overlay.set_speech_level(0.82, True)
            QTest.qWait(85)
            # These are actual QWidget paint timestamps, not nominal timer
            # intervals.  The headless platform takes the bounded fallback;
            # native exposed windows use QWindow UpdateRequest instead.
            self.assertGreaterEqual(len(overlay.paint_times), 3)
            deltas = [(later - earlier) / 1_000_000_000 for earlier, later in zip(overlay.paint_times, overlay.paint_times[1:])]
            self.assertTrue(all(delta > 0.0 for delta in deltas))
            self.assertLess(max(deltas), 0.10)
            self.assertGreater(overlay._display_envelope[-1], 0.60)
            measured_history = tuple(overlay._envelope)
            self.assertEqual(measured_history[-1], 0.82)

            # Rendering may interpolate the display target, but cannot append
            # synthetic bars.  Once it settles, it schedules no idle loop.
            QTest.qWait(420)
            self.assertEqual(tuple(overlay._envelope), measured_history)
            settled_count = len(overlay.paint_times)
            QTest.qWait(70)
            self.assertEqual(len(overlay.paint_times), settled_count)
            self.assertFalse(overlay._frame_fallback.isActive())
            self.assertFalse(overlay._frame_update_pending)

            overlay.hide()
            QTest.qWait(12)
            hidden_count = len(overlay.paint_times)
            QTest.qWait(70)
            self.assertEqual(len(overlay.paint_times), hidden_count)
            self.assertFalse(overlay._frame_fallback.isActive())
            self.assertFalse(overlay._frame_update_pending)
        finally:
            self._close(overlay)

    def test_high_refresh_reports_keep_their_actual_requested_period(self) -> None:
        """High-rate displays must not silently take a 60/250 Hz path."""

        for refresh in (360.0, 480.0, 500.0, 540.0):
            with self.subTest(refresh=refresh):
                self.assertAlmostEqual(
                    VioletOverlay._refresh_period_ms(refresh),
                    1000.0 / refresh,
                    places=9,
                )
        # The non-exposed fallback has only QTimer's whole-millisecond
        # precision floor; 540 Hz therefore requests 2 ms, not an arbitrary
        # 5/16 ms low-refresh fallback.
        self.assertEqual(VioletOverlay._fallback_interval_for_refresh(540.0), 2)
        self.assertEqual(VioletOverlay._fallback_interval_for_refresh(float("nan")), 5)

    def test_meter_restart_isolates_a_blocked_previous_worker(self) -> None:
        """A timed-out old VAD worker cannot consume or signal a new run."""

        entered = threading.Event()
        release = threading.Event()

        class Gate:
            count = 0

            def __init__(self) -> None:
                type(self).count += 1
                self.number = type(self).count

            def process(self, _samples):
                if self.number == 1:
                    entered.set()
                    release.wait(1.0)
                    return [(True, 0.91)]
                return [(True, 0.25)]

        meter = SpeechMeter()
        levels: list[float] = []
        meter.speech_level_changed.connect(lambda level, active: levels.append(level) if active else None)
        try:
            with patch("violet.audio.SpeechGate", Gate), patch("violet.audio.threading.Thread.join", return_value=None):
                meter.start(16_000)
                meter.submit(np.ones(512, dtype=np.float32))
                self.assertTrue(entered.wait(0.5), "first worker did not enter its blocked VAD call")
                # `start` performs the normal bounded stop then immediately
                # creates a separate queue/event/generation for the new run.
                meter.start(16_000)
                meter.submit(np.ones(512, dtype=np.float32))
                deadline = time.monotonic() + 0.5
                while 0.25 not in levels and time.monotonic() < deadline:
                    self.app.processEvents()
                    QTest.qWait(5)
                self.assertIn(0.25, levels)
                release.set()
                QTest.qWait(45)
                self.app.processEvents()
                self.assertNotIn(0.91, levels, "late old-worker output reached the new recording")
        finally:
            release.set()
            meter.stop()

    def test_waveform_history_advances_only_with_fresh_speech_samples(self) -> None:
        overlay = self._overlay()
        try:
            overlay.set_state("recording")
            overlay._stop_spring()
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

            # A VAD-off sample clears speech history; the painter keeps only
            # the static quiet-listening dots.
            overlay.set_speech_level(0.0, False)
            self.assertFalse(overlay._speech_active)
            self.assertTrue(all(level == 0.0 for level in overlay._envelope))
        finally:
            self._close(overlay)

    def test_silent_recording_draws_nine_static_rounded_dots(self) -> None:
        overlay = self._overlay()
        try:
            overlay.set_state("recording")
            overlay._stop_spring()
            overlay._place(animated=False)
            overlay.set_speech_level(0.0, False)
            QTest.qWait(12)
            waveform, stop = overlay._recording_layout()
            self.assertIsNotNone(waveform)
            silent = overlay.grab().toImage()
            # Compare against the same body without the meter. Quiet listening
            # has nine small, separated dots rather than a moving waveform.
            with patch.object(overlay, "_recording_layout", return_value=(None, stop)):
                overlay.update()
                QTest.qWait(12)
                without_meter = overlay.grab().toImage()
            dpr = silent.devicePixelRatio()
            occupied_columns: list[int] = []
            occupied_rows: set[int] = set()
            for x in range(int(waveform.left() * dpr), int(waveform.right() * dpr)):  # type: ignore[union-attr]
                changed = False
                for y in range(int(waveform.top() * dpr), int(waveform.bottom() * dpr)):  # type: ignore[union-attr]
                    if silent.pixelColor(x, y) != without_meter.pixelColor(x, y):
                        changed = True
                        occupied_rows.add(y)
                if changed:
                    occupied_columns.append(x)
            groups: list[list[int]] = []
            for x in occupied_columns:
                if not groups or x != groups[-1][-1] + 1:
                    groups.append([])
                groups[-1].append(x)
            self.assertEqual(len(groups), 9)
            # Quiet dots are deliberately larger than the earlier 2.2px mark
            # but remain nine individually separated rounded squares.
            self.assertEqual(QUIET_DOT_SIZE, 3.2)
            self.assertTrue(all(len(group) <= 5 * dpr for group in groups))
            self.assertGreaterEqual(max(len(group) for group in groups), max(3, int(3 * dpr)))
            self.assertGreaterEqual(len(occupied_rows), max(3, int(3 * dpr)))
            self.assertLessEqual(len(occupied_rows), 5 * dpr)
            for _ in range(10):
                overlay._tick()
            QTest.qWait(12)
            self.assertTrue(all(level == 0.0 for level in overlay._envelope))
            self.assertTrue(all(level == 0.0 for level in overlay._display_envelope))
            self.assertEqual(overlay.grab().toImage(), silent)
            self.assertFalse(overlay._frame_fallback.isActive())
            self.assertFalse(overlay._frame_update_pending)
        finally:
            self._close(overlay)

    def test_spring_is_recording_lifecycle_only_and_keeps_caret_row_center(self) -> None:
        overlay = self._overlay()
        try:
            original_center = overlay.geometry().top() + overlay.geometry().height() / 2
            overlay.set_state("recording")
            QTest.qWait(35)
            self.assertTrue(overlay.spring_active)
            spring_started = overlay._spring_started_ms
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
            self.assertTrue(overlay.spring_active)
            self.assertEqual(overlay._spring_started_ms, spring_started)

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
            overlay._stop_spring()
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
