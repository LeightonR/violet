"""Regression coverage for browser editors with hidden keyboard targets."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import uiautomation as auto

from violet.focus import (
    EditableTarget,
    FieldRect,
    FocusService,
    anchor_hidden_target_at_point,
    anchor_hidden_target_at_rect,
)


class _Bounds:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Control:
    def __init__(
        self,
        *,
        control_type: int = auto.ControlType.DocumentControl,
        bounds: tuple[int, int, int, int] = (120, 160, 920, 760),
        is_offscreen: bool = False,
        text_pattern: bool = True,
        text_pattern2: bool = False,
        parent: "_Control | None" = None,
        read_only: bool = False,
        value_is_read_only: bool | None = None,
        runtime_id: tuple[int, ...] = (42, 7),
    ) -> None:
        self.ControlType = control_type
        self.BoundingRectangle = _Bounds(*bounds)
        self.IsOffscreen = is_offscreen
        self.IsEnabled = True
        self.IsPassword = False
        self.HasKeyboardFocus = True
        self.IsKeyboardFocusable = not is_offscreen
        self.ProcessId = 1200
        self.NativeWindowHandle = 0
        self._text_pattern = object() if text_pattern else None
        self._text_pattern2 = object() if text_pattern2 else None
        self._parent = parent
        self._runtime_id = runtime_id
        self._read_only = read_only
        self._value_is_read_only = value_is_read_only

    def GetValuePattern(self) -> object | None:
        if self._value_is_read_only is None:
            return None
        return SimpleNamespace(IsReadOnly=self._value_is_read_only)

    def GetTextPattern(self) -> object | None:
        return self._text_pattern

    def GetPattern(self, pattern_id: int) -> object | None:
        if pattern_id == auto.PatternId.TextPattern2:
            return self._text_pattern2
        return None

    def GetLegacyIAccessiblePattern(self) -> object | None:
        if self._read_only:
            return SimpleNamespace(State=auto.AccessibleState.ReadOnly)
        return None

    def GetParentControl(self) -> "_Control | None":
        return self._parent

    def GetRuntimeId(self) -> list[int]:
        return list(self._runtime_id)


class GoogleDocsFocusTests(unittest.TestCase):
    def _snapshot(self, control: _Control):
        with (
            patch("violet.focus.auto.GetFocusedControl", return_value=control),
            patch("violet.focus.foreground_window", return_value=501),
        ):
            return FocusService().snapshot()

    def test_hidden_browser_editor_uses_visible_ancestor_for_placement(self) -> None:
        visible_editor = _Control(
            control_type=auto.ControlType.PaneControl,
            bounds=(100, 140, 1_100, 900),
            text_pattern=False,
        )
        hidden_typing_target = _Control(
            bounds=(-10_000, -10_000, -9_999, -9_999),
            is_offscreen=True,
            parent=visible_editor,
        )

        target = self._snapshot(hidden_typing_target)

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.runtime_id, (42, 7))
        self.assertEqual(target.rect, FieldRect(100, 140, 1_100, 900))
        self.assertEqual(target.anchor_rect, target.rect)
        self.assertFalse(target.anchor_is_caret)
        self.assertTrue(target.uses_visible_ancestor)

    def test_hidden_editor_does_not_need_focusable_metadata_when_it_has_focus(self) -> None:
        visible_editor = _Control(
            control_type=auto.ControlType.PaneControl,
            bounds=(100, 140, 1_100, 900),
            text_pattern=False,
        )
        hidden_typing_target = _Control(is_offscreen=True, parent=visible_editor)
        self.assertFalse(hidden_typing_target.IsKeyboardFocusable)

        self.assertIsNotNone(self._snapshot(hidden_typing_target))

    def test_textpattern2_only_editor_is_accepted_without_reading_text(self) -> None:
        editor = _Control(text_pattern=False, text_pattern2=True)

        target = self._snapshot(editor)

        self.assertIsNotNone(target)

    def test_hidden_editor_without_a_visible_container_is_not_an_insertion_target(self) -> None:
        hidden_typing_target = _Control(is_offscreen=True)

        self.assertIsNone(self._snapshot(hidden_typing_target))

    def test_hidden_read_only_editor_remains_excluded(self) -> None:
        visible_editor = _Control(
            control_type=auto.ControlType.PaneControl,
            bounds=(100, 140, 1_100, 900),
            text_pattern=False,
        )
        hidden_typing_target = _Control(is_offscreen=True, parent=visible_editor, read_only=True)

        self.assertIsNone(self._snapshot(hidden_typing_target))

    def test_writable_search_combobox_is_an_insertion_target(self) -> None:
        search_box = _Control(
            control_type=auto.ControlType.ComboBoxControl,
            text_pattern=False,
            value_is_read_only=False,
        )

        self.assertIsNotNone(self._snapshot(search_box))

    def test_read_only_dropdown_remains_excluded(self) -> None:
        dropdown = _Control(
            control_type=auto.ControlType.ComboBoxControl,
            text_pattern=False,
            value_is_read_only=True,
        )

        self.assertIsNone(self._snapshot(dropdown))

    def test_combobox_without_a_writable_value_pattern_remains_excluded(self) -> None:
        dropdown = _Control(control_type=auto.ControlType.ComboBoxControl, text_pattern=True)

        self.assertIsNone(self._snapshot(dropdown))

    def test_hidden_editor_click_uses_a_compact_caret_like_anchor(self) -> None:
        target = EditableTarget(
            runtime_id=(42, 7),
            foreground_hwnd=501,
            process_id=1200,
            control_type=auto.ControlType.EditControl,
            rect=FieldRect(100, 140, 1_100, 900),
            anchor_rect=FieldRect(100, 140, 1_100, 900),
            anchor_is_caret=False,
            uses_visible_ancestor=True,
        )

        anchored = anchor_hidden_target_at_point(target, 460, 360)

        self.assertTrue(anchored.anchor_is_caret)
        self.assertEqual(anchored.anchor_rect, FieldRect(460, 349, 462, 371))

    def test_outside_click_and_visible_target_do_not_change_anchor(self) -> None:
        hidden = EditableTarget(
            runtime_id=(42, 7),
            foreground_hwnd=501,
            process_id=1200,
            control_type=auto.ControlType.EditControl,
            rect=FieldRect(100, 140, 1_100, 900),
            anchor_rect=FieldRect(100, 140, 1_100, 900),
            anchor_is_caret=False,
            uses_visible_ancestor=True,
        )
        visible = EditableTarget(
            runtime_id=(42, 8),
            foreground_hwnd=501,
            process_id=1200,
            control_type=auto.ControlType.EditControl,
            rect=FieldRect(100, 140, 1_100, 900),
            anchor_rect=FieldRect(100, 140, 1_100, 900),
            anchor_is_caret=False,
        )

        self.assertIs(anchor_hidden_target_at_point(hidden, 90, 360), hidden)
        self.assertIs(anchor_hidden_target_at_point(visible, 460, 360), visible)

    def test_exact_browser_rect_is_accepted_only_inside_hidden_canvas(self) -> None:
        target = EditableTarget(
            runtime_id=(42, 7),
            foreground_hwnd=501,
            process_id=1200,
            control_type=auto.ControlType.EditControl,
            rect=FieldRect(100, 140, 1_100, 900),
            anchor_rect=FieldRect(100, 140, 1_100, 900),
            anchor_is_caret=False,
            uses_visible_ancestor=True,
        )
        caret = FieldRect(460, 349, 462, 372)

        anchored = anchor_hidden_target_at_rect(target, caret)

        self.assertEqual(anchored.anchor_rect, caret)
        self.assertTrue(anchored.anchor_is_caret)
        self.assertIs(anchor_hidden_target_at_rect(target, FieldRect(99, 349, 101, 372)), target)


if __name__ == "__main__":
    unittest.main()
