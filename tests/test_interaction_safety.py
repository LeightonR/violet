from __future__ import annotations

import os
import unittest


class OverlayClickSafetyTests(unittest.TestCase):
    def test_only_a_stationary_overlay_press_can_toggle(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        from violet.overlay import VioletOverlay

        app = QApplication.instance() or QApplication([])
        overlay = VioletOverlay()
        overlay.setGeometry(0, 0, 42, 42)
        toggles: list[bool] = []
        overlay.clicked.connect(lambda: toggles.append(True))

        def event(kind, position, button, buttons):
            point = QPointF(*position)
            return QMouseEvent(kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)

        # A foreign mouse-up is ignored even if it arrives at Violet.
        overlay.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, (8, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertEqual(toggles, [])

        # A normal press/release is a valid toggle.
        overlay.mousePressEvent(event(QEvent.Type.MouseButtonPress, (8, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        overlay.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, (8, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertEqual(toggles, [True])

        # A drag that starts on Violet never becomes a dictation toggle.
        overlay.mousePressEvent(event(QEvent.Type.MouseButtonPress, (8, 8), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        overlay.mouseMoveEvent(event(QEvent.Type.MouseMove, (20, 20), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
        overlay.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, (20, 20), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertEqual(toggles, [True])
        overlay.deleteLater()
        app.processEvents()

    def test_state_changes_keep_the_same_caret_row_center(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        from violet.focus import FieldRect
        from violet.overlay import VioletOverlay

        app = QApplication.instance() or QApplication([])
        overlay = VioletOverlay()
        # This is an active-line row, deliberately much shorter than a normal
        # multiline editor. Expanding to recording/transcribing must only grow
        # horizontally, never dip to a field-centre fallback.
        row = FieldRect(600, 300, 1_300, 322)
        overlay.set_target_rect(row)
        overlay.show()
        app.processEvents()
        QTest.qWait(220)
        centres = []
        for state in ("idle", "recording", "transcribing", "idle"):
            overlay.set_state(state)
            # Changing state widens/narrows the visible Tool window through a
            # geometry animation. Waiting for that actual animation catches a
            # vertical dip that a hidden-widget geometry assertion would miss.
            QTest.qWait(220)
            app.processEvents()
            geometry = overlay.geometry()
            centres.append(geometry.top() + geometry.height() / 2)
        self.assertTrue(all(center == centres[0] for center in centres))
        overlay.deleteLater()
        app.processEvents()

    def test_recording_hit_zones_keep_stop_and_discard_separate(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication

        from violet.overlay import VioletOverlay

        app = QApplication.instance() or QApplication([])
        overlay = VioletOverlay()
        actions: list[str] = []
        overlay.clicked.connect(lambda: actions.append("start"))
        overlay.cancel_requested.connect(lambda: actions.append("cancel"))
        overlay.stop_requested.connect(lambda: actions.append("stop"))

        def event(kind, position, button, buttons):
            point = QPointF(*position)
            return QMouseEvent(kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier)

        def click(position: tuple[float, float]) -> None:
            overlay.mousePressEvent(event(QEvent.Type.MouseButtonPress, position, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
            overlay.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, position, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))

        overlay.set_state("recording")
        overlay.setGeometry(0, 0, overlay._widths["recording"], 42)
        waveform, stop = overlay._recording_layout()
        self.assertIsNotNone(waveform)
        self.assertIsNotNone(stop)

        # The live meter itself is deliberately inert: only the left X can
        # discard and only the right square can finish-and-insert.
        click((70, 21))
        self.assertEqual(actions, [])
        click((22, 21))
        self.assertEqual(actions, ["cancel"])
        click((stop.center().x(), stop.center().y()))  # type: ignore[union-attr]
        self.assertEqual(actions, ["cancel", "stop"])

        # Dragging away from X remains a no-op, preserving selection safety.
        overlay.mousePressEvent(event(QEvent.Type.MouseButtonPress, (22, 21), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        overlay.mouseMoveEvent(event(QEvent.Type.MouseMove, (40, 21), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
        overlay.mouseReleaseEvent(event(QEvent.Type.MouseButtonRelease, (40, 21), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertEqual(actions, ["cancel", "stop"])

        # While Whisper is still working, X remains a cancel-only control;
        # neither a spinner nor an arbitrary click can start another request.
        overlay.set_state("transcribing")
        overlay.setGeometry(0, 0, overlay._widths["transcribing"], 42)
        click((22, 21))
        click((60, 21))
        self.assertEqual(actions, ["cancel", "stop", "cancel"])
        overlay.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
