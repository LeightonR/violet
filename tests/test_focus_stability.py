from __future__ import annotations

import unittest

from violet.focus import EditableTarget, FieldRect, retain_last_caret_anchor


FIELD = FieldRect(100, 100, 700, 360)


def target(*, runtime: tuple[int, ...] = (1, 2, 3), anchor: FieldRect, has_caret: bool) -> EditableTarget:
    return EditableTarget(
        runtime_id=runtime,
        foreground_hwnd=99,
        process_id=77,
        control_type=50004,
        rect=FIELD,
        anchor_rect=anchor,
        anchor_is_caret=has_caret,
    )


class FocusStabilityTests(unittest.TestCase):
    def test_transient_noncollapsed_selection_keeps_last_valid_row(self) -> None:
        last_row = FieldRect(100, 142, 700, 164)
        prior = target(anchor=last_row, has_caret=True)
        transient = target(anchor=FIELD, has_caret=False)

        stable = retain_last_caret_anchor(prior, transient)

        self.assertEqual(stable.anchor_rect, last_row)
        self.assertTrue(stable.anchor_is_caret)
        self.assertEqual(stable.rect, FIELD)

    def test_real_caret_move_replaces_retained_row(self) -> None:
        prior = target(anchor=FieldRect(100, 142, 700, 164), has_caret=True)
        moved = target(anchor=FieldRect(100, 220, 700, 242), has_caret=True)

        self.assertEqual(retain_last_caret_anchor(prior, moved), moved)

    def test_fallback_from_a_different_control_is_not_reused(self) -> None:
        prior = target(anchor=FieldRect(100, 142, 700, 164), has_caret=True)
        different = target(runtime=(9, 9), anchor=FIELD, has_caret=False)

        self.assertEqual(retain_last_caret_anchor(prior, different), different)


if __name__ == "__main__":
    unittest.main()
