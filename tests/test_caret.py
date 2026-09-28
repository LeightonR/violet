"""Geometry-only tests for Violet's active text-row provider."""

from __future__ import annotations

import unittest
from ctypes import c_double

from violet.caret import active_line_rect


class _GuardedRange:
    """A raw UIA-like range that fails if code tries to read text."""

    def __init__(self, direct: list[float], line: list[float]) -> None:
        self.direct = direct
        self.line = line
        self.expanded_to: int | None = None

    def GetBoundingRectangles(self) -> list[float]:
        return self.line if self.expanded_to is not None else self.direct

    def Clone(self) -> "_GuardedRange":
        return _GuardedRange(self.direct, self.line)

    def ExpandToEnclosingUnit(self, unit: int) -> int:
        self.expanded_to = unit
        return 0

    def GetText(self, *_: object) -> str:
        raise AssertionError("active_line_rect must not read text")


class _Pattern2:
    def __init__(self, active: bool, caret_range: _GuardedRange | None) -> None:
        self.active = active
        self.caret_range = caret_range

    def GetCaretRange(self) -> tuple[bool, _GuardedRange | None]:
        return self.active, self.caret_range


class _PatternWrapper:
    def __init__(self, raw: _Pattern2) -> None:
        self.pattern = raw


class _SelectionRange(_GuardedRange):
    def __init__(self, direct: list[float], line: list[float], collapsed: bool) -> None:
        super().__init__(direct, line)
        self.collapsed = collapsed

    def CompareEndpoints(self, *_: object) -> int:
        return 0 if self.collapsed else 1


class _SelectionWrapper:
    def __init__(self, raw: _SelectionRange) -> None:
        self.textRange = raw


class _TextPattern:
    def __init__(self, selection: _SelectionWrapper | None) -> None:
        self.selection = selection

    def GetSelection(self) -> list[_SelectionWrapper]:
        return [self.selection] if self.selection is not None else []


class _Control:
    def __init__(
        self,
        pattern2: _Pattern2 | None = None,
        text_pattern: _TextPattern | None = None,
    ) -> None:
        self.pattern2 = pattern2
        self.text_pattern = text_pattern
        self.NativeWindowHandle = 0

    def GetPattern(self, _: int) -> _PatternWrapper | None:
        return _PatternWrapper(self.pattern2) if self.pattern2 is not None else None

    def GetTextPattern(self) -> _TextPattern | None:
        return self.text_pattern

    @property
    def Value(self) -> str:
        raise AssertionError("active_line_rect must not read a control value")


class CaretTests(unittest.TestCase):
    field = (100, 100, 500, 300)

    def test_caret_line_geometry_tracks_the_same_field_at_two_rows(self) -> None:
        first = _Control(_Pattern2(True, _GuardedRange([], [168, 111, 91, 21])))
        second = _Control(_Pattern2(True, _GuardedRange([], [168, 166, 91, 21])))

        first_row = active_line_rect(first, self.field)
        second_row = active_line_rect(second, self.field)

        self.assertEqual(first_row, (100, 111, 500, 132))
        self.assertEqual(second_row, (100, 166, 500, 187))
        self.assertNotEqual(first_row[1:4], second_row[1:4])

    def test_direct_caret_geometry_keeps_its_actual_line_height(self) -> None:
        control = _Control(_Pattern2(True, _GuardedRange([420, 196, 1, 18], [0, 0, 0, 0])))

        self.assertEqual(active_line_rect(control, self.field), (100, 196, 500, 214))

    def test_ctypes_safearray_geometry_from_uia_is_parsed(self) -> None:
        control = _Control(
            _Pattern2(True, _GuardedRange([c_double(420), c_double(196), c_double(1), c_double(18)], []))
        )

        self.assertEqual(active_line_rect(control, self.field), (100, 196, 500, 214))

    def test_collapsed_textpattern_selection_is_a_geometry_only_fallback(self) -> None:
        selection = _SelectionWrapper(_SelectionRange([], [137, 151, 220, 22], collapsed=True))
        control = _Control(text_pattern=_TextPattern(selection))

        self.assertEqual(active_line_rect(control, self.field), (100, 151, 500, 173))

    def test_noncollapsed_selection_is_not_guessed_as_an_active_line(self) -> None:
        selection = _SelectionWrapper(_SelectionRange([], [137, 151, 220, 22], collapsed=False))
        control = _Control(text_pattern=_TextPattern(selection))

        self.assertIsNone(active_line_rect(control, self.field))

    def test_provider_without_visible_caret_returns_none_not_field_center(self) -> None:
        control = _Control(_Pattern2(False, None))

        self.assertIsNone(active_line_rect(control, self.field))
        self.assertIsNone(active_line_rect(control, (100, 100, 100, 300)))


if __name__ == "__main__":
    unittest.main()
