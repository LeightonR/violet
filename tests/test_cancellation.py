from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from violet.audio import AudioRecorder
from violet.app import VioletController


class CancellationTests(unittest.TestCase):
    def _controller(self, state: str, requests: dict[int, object]) -> SimpleNamespace:
        return SimpleNamespace(
            _enabled=True,
            _state=state,
            _requests=requests,
            _recording_target=object(),
            _recording_recovery_only=False,
            _status_until=99.0,
            _demo_text=None,
            _recorder=SimpleNamespace(cancel=Mock()),
            _overlay=SimpleNamespace(set_speech_level=Mock(), set_state=Mock()),
            _poll_focus=Mock(),
            _recovery=SimpleNamespace(present=Mock()),
            _tray=SimpleNamespace(showMessage=Mock()),
        )

    def test_cancel_during_capture_discards_audio_and_returns_idle(self) -> None:
        controller = self._controller("recording", {})

        VioletController.cancel_dictation(controller)

        controller._recorder.cancel.assert_called_once_with()
        self.assertEqual(controller._requests, {})
        self.assertIsNone(controller._recording_target)
        self.assertFalse(controller._recording_recovery_only)
        self.assertEqual(controller._state, "idle")
        controller._overlay.set_speech_level.assert_called_once_with(0.0, False)
        controller._overlay.set_state.assert_called_once_with("idle")
        controller._poll_focus.assert_called_once_with()

    def test_cancelled_pending_result_never_inserts_or_opens_recovery(self) -> None:
        controller = self._controller("transcribing", {41: object()})

        VioletController.cancel_dictation(controller)
        self.assertEqual(controller._requests, {})
        controller._recorder.cancel.assert_not_called()

        # A Whisper result/failure that was already queued is intentionally
        # ignored by request identity. It may neither type text nor surface a
        # recovery/Copy flow after the user pressed the X.
        with patch("violet.app.insert_at_original_target", side_effect=AssertionError("must not insert")):
            VioletController._on_transcript(controller, 41, "discard this dictation")
        VioletController._on_transcription_failed(controller, 41, "late worker failure")
        controller._recovery.present.assert_not_called()
        controller._tray.showMessage.assert_not_called()

    def test_cancel_discard_path_clears_pcm_even_when_stream_stop_fails(self) -> None:
        class BrokenStream:
            def __init__(self) -> None:
                self.stop_calls = 0
                self.close_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1
                raise RuntimeError("driver stop failed")

            def close(self) -> None:
                self.close_calls += 1

        class Meter:
            def __init__(self) -> None:
                self.stop_calls = 0
                self.submit_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1

            def submit(self, _samples) -> None:
                self.submit_calls += 1

        recorder = AudioRecorder()
        stream = BrokenStream()
        meter = Meter()
        recorder._stream = stream  # type: ignore[assignment]
        recorder._meter = meter  # type: ignore[assignment]
        recorder._samples = [np.ones(512, dtype=np.float32)]

        # Cancellation is forbidden from executing the normal completion path.
        with patch("violet.audio.np.concatenate", side_effect=AssertionError("must not concatenate")), patch(
            "violet.audio.resample_to_16khz", side_effect=AssertionError("must not resample")
        ):
            with self.assertRaisesRegex(RuntimeError, "driver stop failed"):
                recorder.cancel()

        self.assertIsNone(recorder._stream)
        self.assertEqual(stream.stop_calls, 1)
        self.assertEqual(stream.close_calls, 1)
        self.assertEqual(meter.stop_calls, 1)
        self.assertEqual(recorder._samples, [])
        # A late PortAudio callback after cancellation cannot recreate PCM or
        # send stale visual-meter work into the now-stopped queue.
        recorder._callback(np.ones((4, 1), dtype=np.float32), 4, None, None)
        self.assertEqual(recorder._samples, [])
        self.assertEqual(meter.submit_calls, 0)
        # If the user starts again immediately, an old stream generation is
        # still rejected rather than contaminating the new capture.
        recorder._stream = object()  # type: ignore[assignment]
        recorder._recording_generation = 8
        recorder._callback(np.ones((4, 1), dtype=np.float32), 4, None, None, generation=7)
        self.assertEqual(recorder._samples, [])
        self.assertEqual(meter.submit_calls, 0)

    def test_cancel_reports_unconfirmed_microphone_cleanup(self) -> None:
        controller = self._controller("recording", {})
        controller._recorder.cancel.side_effect = RuntimeError("driver stop failed")

        VioletController.cancel_dictation(controller)

        self.assertEqual(controller._state, "idle")
        controller._tray.showMessage.assert_called_once()
        self.assertIn("could not confirm microphone cleanup", controller._tray.showMessage.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
