from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from violet.placement import (
    DEFAULT_PLACEMENT,
    PlacementRect,
    PlacementStore,
    RelativePlacement,
    geometry_for_placement,
    placement_from_geometry,
)


class PlacementTests(unittest.TestCase):
    def test_default_placement_stays_left_with_an_eight_pixel_gap(self) -> None:
        field = PlacementRect(100, 200, 500, 240)
        overlay = geometry_for_placement(field, 42, 42, DEFAULT_PLACEMENT)

        self.assertEqual(overlay.left, 50)
        self.assertEqual(overlay.right, 92)
        self.assertEqual(overlay.top, 199)
        self.assertEqual(placement_from_geometry(field, overlay), DEFAULT_PLACEMENT)

    def test_dragged_relative_placement_follows_different_fields(self) -> None:
        first_field = PlacementRect(100, 100, 300, 140)
        dragged = PlacementRect(315, 90, 357, 132)
        placement = placement_from_geometry(first_field, dragged)

        self.assertEqual(placement.side, "right")
        self.assertAlmostEqual(placement.edge_alignment, 0.275)
        self.assertEqual(placement.gap_offset, 15)

        second_field = PlacementRect(600, 300, 1_000, 500)
        moved = geometry_for_placement(second_field, 42, 42, placement)
        self.assertEqual(moved.left, 1_015)
        self.assertEqual(moved.top, 334)
        self.assertEqual(moved.right, 1_057)
        self.assertEqual(moved.bottom, 376)

    def test_top_anchor_uses_normalized_horizontal_alignment(self) -> None:
        placement = RelativePlacement(side="top", edge_alignment=0.25, gap_offset=12)
        first = geometry_for_placement(PlacementRect(100, 100, 300, 160), 42, 42, placement)
        second = geometry_for_placement(PlacementRect(700, 400, 1_100, 520), 42, 42, placement)

        self.assertEqual((first.left, first.top), (129, 46))
        self.assertEqual((second.left, second.top), (779, 346))

    def test_lock_save_load_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "placement.json"
            store = PlacementStore(path)
            saved = store.save(RelativePlacement(side="bottom", edge_alignment=0.8, gap_offset=27))

            self.assertTrue(path.is_file())
            self.assertEqual(store.load(), saved)
            store.reset()
            self.assertFalse(path.exists())
            self.assertEqual(store.load(), DEFAULT_PLACEMENT)

    def test_out_of_bounds_drag_is_normalized_and_screen_clamped(self) -> None:
        field = PlacementRect(10, 10, 80, 40)
        placement = RelativePlacement(side="left", edge_alignment=-3, gap_offset=9_999).normalized()
        overlay = geometry_for_placement(field, 42, 42, placement, PlacementRect(0, 0, 200, 120))

        self.assertEqual(placement.edge_alignment, 0)
        self.assertEqual(placement.gap_offset, 512)
        self.assertEqual((overlay.left, overlay.top), (0, 0))

    def test_transform_mode_suppresses_clicks_and_locks_when_disabled(self) -> None:
        # Keep the Qt-only interaction test isolated from the user's real
        # placement preference. This checks the overlay's normal click contract
        # as well as Transform's drag-only behavior.
        previous_local_app_data = os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory() as directory:
            os.environ["LOCALAPPDATA"] = directory
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            try:
                from PySide6.QtCore import QEvent, QPointF, Qt
                from PySide6.QtGui import QMouseEvent
                from PySide6.QtWidgets import QApplication

                from violet.focus import FieldRect
                from violet.overlay import VioletOverlay

                app = QApplication.instance() or QApplication([])
                overlay = VioletOverlay()
                overlay._target_rect = FieldRect(100, 200, 500, 240)
                clicked: list[bool] = []
                overlay.clicked.connect(lambda: clicked.append(True))
                release = lambda: QMouseEvent(
                    QEvent.Type.MouseButtonRelease,
                    QPointF(5, 5),
                    QPointF(5, 5),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )

                press = lambda: QMouseEvent(
                    QEvent.Type.MouseButtonPress,
                    QPointF(5, 5),
                    QPointF(5, 5),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )

                # A release delivered without an overlay-originated press must
                # never toggle dictation (for example after a selection drag
                # in another application crosses the floating control).
                overlay.mouseReleaseEvent(release())
                self.assertEqual(len(clicked), 0)
                overlay.mousePressEvent(press())
                overlay.mouseReleaseEvent(release())
                self.assertEqual(len(clicked), 1)
                overlay.set_transform_enabled(True)
                overlay.mouseReleaseEvent(release())
                self.assertEqual(len(clicked), 1)
                overlay.set_transform_enabled(False)
                self.assertTrue(Path(directory, "Violet", "placement.json").is_file())
                overlay.deleteLater()
                app.processEvents()
            finally:
                if previous_local_app_data is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = previous_local_app_data


if __name__ == "__main__":
    unittest.main()
