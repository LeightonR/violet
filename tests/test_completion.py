from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from violet.app import VioletController
from violet.wininput import InsertionResult


class CompletionTests(unittest.TestCase):
    """Completed text is either inserted or made recoverable."""

    def _controller(self, target: object | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            _requests={17: target if target is not None else object()},
            _enabled=True,
            _focus=object(),
            _overlay=SimpleNamespace(set_state=Mock()),
            _recovery=SimpleNamespace(present=Mock()),
            _tray=SimpleNamespace(showMessage=Mock()),
            _flash=Mock(),
            _return_to_idle=Mock(),
        )

    def test_successful_nonempty_result_inserts(self) -> None:
        controller = self._controller()
        target = controller._requests[17]

        with patch(
            "violet.app.insert_at_original_target",
            return_value=InsertionResult(True, "Inserted into the original text field."),
        ) as insert:
            VioletController._on_transcript(controller, 17, "fixture transcript.")

        insert.assert_called_once_with(controller._focus, target, "fixture transcript.")
        controller._flash.assert_called_once_with("Inserted")
        controller._return_to_idle.assert_called_once_with(delay_ms=900)
        controller._recovery.present.assert_not_called()

    def test_safe_insertion_decline_keeps_nonempty_result_recoverable(self) -> None:
        controller = self._controller()
        message = "The original text field changed. Transcript kept ready."

        with patch(
            "violet.app.insert_at_original_target",
            return_value=InsertionResult(False, message),
        ):
            VioletController._on_transcript(controller, 17, "fixture transcript.")

        controller._overlay.set_state.assert_called_once_with("error")
        controller._recovery.present.assert_called_once_with("fixture transcript.", message)
        controller._tray.showMessage.assert_called_once_with("Violet kept text ready", message)
        controller._return_to_idle.assert_called_once_with(delay_ms=1_000)

    def test_unexpected_insertion_error_keeps_nonempty_result_recoverable(self) -> None:
        controller = self._controller()

        with patch("violet.app.insert_at_original_target", side_effect=RuntimeError("fixture boundary failure")):
            VioletController._on_transcript(controller, 17, "fixture transcript.")

        expected = "Violet could not safely insert this result. Copy the text when you are ready."
        controller._overlay.set_state.assert_called_once_with("error")
        controller._recovery.present.assert_called_once_with("fixture transcript.", expected)
        controller._tray.showMessage.assert_called_once_with("Violet kept text ready", expected)
        controller._return_to_idle.assert_called_once_with(delay_ms=1_000)

    def test_empty_result_reports_no_speech_without_recovery(self) -> None:
        controller = self._controller()

        VioletController._on_transcript(controller, 17, "   ")

        controller._flash.assert_called_once_with("No speech heard")
        controller._return_to_idle.assert_called_once_with(delay_ms=1_700)
        controller._recovery.present.assert_not_called()


if __name__ == "__main__":
    unittest.main()
