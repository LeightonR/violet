"""Privacy-preserving geometry for the focused text caret's visual row.

This module deliberately reads only UI Automation geometry and native caret
metadata.  It never obtains text, a value, a selection's contents, or changes
focus.  Callers keep the field's identity and full rectangle separately; this
returns just the visible row that contains the insertion point.
"""

from __future__ import annotations

import ctypes
import math
from collections.abc import Iterable, Sequence
from ctypes import wintypes
from typing import TypeAlias

import uiautomation as auto


ScreenRect: TypeAlias = tuple[int, int, int, int]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(_GUITHREADINFO))
_user32.GetGUIThreadInfo.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(_POINT))
_user32.ClientToScreen.restype = wintypes.BOOL


def active_line_rect(control: object, field_bounds_tuple: Sequence[int]) -> ScreenRect | None:
    """Return the active caret row in physical screen pixels, or ``None``.

    The returned rectangle spans the editable field horizontally and uses the
    actual visible line/caret geometry for its vertical bounds.  A provider
    that cannot expose a caret or a visible line returns ``None`` rather than
    guessing from the field centre.  No text or value is retrieved.

    UIA TextPattern2's caret range is preferred.  A collapsed TextPattern
    selection is a compatible UIA fallback.  Finally, the Win32 GUI-thread
    caret is mapped from its client coordinates to screen pixels for native
    editors that do not expose either UIA caret interface.
    """

    field_bounds = _valid_rect(field_bounds_tuple)
    if field_bounds is None:
        return None

    for resolver in (_text_pattern2_row, _text_pattern_selection_row):
        try:
            row = resolver(control, field_bounds)
        except Exception:
            # UIA elements can disappear or reject a pattern between calls.
            row = None
        if row is not None:
            return row

    try:
        return _native_caret_row(control, field_bounds)
    except Exception:
        # A missing HWND or a protected/cross-process native caret is ordinary
        # unsupported-provider behavior, not a reason to infer a line.
        return None


def _text_pattern2_row(control: object, field_bounds: ScreenRect) -> ScreenRect | None:
    getter = getattr(control, "GetPattern", None)
    if not callable(getter):
        return None
    wrapper = getter(auto.PatternId.TextPattern2)
    raw_pattern = getattr(wrapper, "pattern", None)
    if raw_pattern is None:
        return None
    active, raw_range = raw_pattern.GetCaretRange()
    if not active or raw_range is None:
        return None
    return _row_from_text_range(raw_range, field_bounds)


def _text_pattern_selection_row(control: object, field_bounds: ScreenRect) -> ScreenRect | None:
    getter = getattr(control, "GetTextPattern", None)
    if not callable(getter):
        return None
    text_pattern = getter()
    if text_pattern is None:
        return None
    selections = text_pattern.GetSelection()
    if not selections:
        return None

    raw_range = getattr(selections[0], "textRange", None)
    if raw_range is None:
        return None
    start = auto.TextPatternRangeEndpoint.Start
    end = auto.TextPatternRangeEndpoint.End
    # A non-empty selection does not identify one active row reliably.  Do not
    # inspect its contents or choose an arbitrary endpoint.
    if raw_range.CompareEndpoints(start, raw_range, end) != 0:
        return None
    return _row_from_text_range(raw_range, field_bounds)


def _row_from_text_range(raw_range: object, field_bounds: ScreenRect) -> ScreenRect | None:
    """Use a raw UIA range's geometry, expanding a clone to its visual line."""

    direct = _row_from_rectangles(_bounding_rectangles(raw_range), field_bounds)
    if direct is not None:
        return direct

    # A degenerate caret range commonly has no bounding rectangles.  Expand a
    # clone to Line to cover blank/newline rows without ever fetching its text.
    clone = raw_range.Clone()
    clone.ExpandToEnclosingUnit(auto.TextUnit.Line)
    return _row_from_rectangles(_bounding_rectangles(clone), field_bounds)


def _bounding_rectangles(raw_range: object) -> list[tuple[float, float, float, float]]:
    """Read only raw ``GetBoundingRectangles`` geometry from a UIA range."""

    values = raw_range.GetBoundingRectangles()
    return _as_xywh_rectangles(values)


def _as_xywh_rectangles(values: object) -> list[tuple[float, float, float, float]]:
    """Normalize UIA's flat ``x, y, width, height`` collection for testing."""

    if values is None:
        return []
    try:
        items = list(values)  # UIA SAFEARRAY is iterable in uiautomation.
    except TypeError:
        return []
    if not items:
        return []

    # The live API returns a flat double SAFEARRAY.  Supporting Rect-like
    # objects keeps this pure geometry parser straightforward to unit-test.
    # comtypes can expose a SAFEARRAY as ctypes scalar values.  Convert those
    # numerically instead of requiring Python ``float`` instances.
    try:
        numeric_items = [_numeric_value(item) for item in items]
    except (TypeError, ValueError):
        numeric_items = []
    if len(numeric_items) == len(items):
        return [
            (
                numeric_items[index],
                numeric_items[index + 1],
                numeric_items[index + 2],
                numeric_items[index + 3],
            )
            for index in range(0, len(numeric_items) - 3, 4)
        ]

    rects: list[tuple[float, float, float, float]] = []
    for item in items:
        try:
            left = float(item.left)
            top = float(item.top)
            right = float(item.right)
            bottom = float(item.bottom)
        except (AttributeError, TypeError, ValueError):
            continue
        rects.append((left, top, right - left, bottom - top))
    return rects


def _row_from_rectangles(
    rectangles: Iterable[tuple[float, float, float, float]], field_bounds: ScreenRect
) -> ScreenRect | None:
    """Turn one visible caret/line rectangle into a field-width row."""

    field_left, field_top, field_right, field_bottom = field_bounds
    visible_rows: list[tuple[float, float]] = []
    for x, y, width, height in rectangles:
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            continue
        if width < 0 or height <= 0:
            continue
        top = max(float(field_top), y)
        bottom = min(float(field_bottom), y + height)
        if bottom > top:
            visible_rows.append((top, bottom))
    if not visible_rows:
        return None

    # A Line range should normally map to one row.  Providers may split wrapped
    # glyph runs into adjacent rectangles, so retain their full visible vertical
    # extent instead of selecting an arbitrary fragment.
    top = _round_screen(min(row[0] for row in visible_rows))
    bottom = _round_screen(max(row[1] for row in visible_rows))
    if bottom <= top:
        return None
    return (field_left, top, field_right, bottom)


def _native_caret_row(control: object, field_bounds: ScreenRect) -> ScreenRect | None:
    hwnd = _window_handle(control)
    if not hwnd:
        return None
    thread_id = _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
    if not thread_id:
        return None

    info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
    if not _user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)) or not info.hwndCaret:
        return None

    top_left = _POINT(info.rcCaret.left, info.rcCaret.top)
    bottom_right = _POINT(info.rcCaret.right, info.rcCaret.bottom)
    if not (
        _user32.ClientToScreen(info.hwndCaret, ctypes.byref(top_left))
        and _user32.ClientToScreen(info.hwndCaret, ctypes.byref(bottom_right))
    ):
        return None
    return _row_from_rectangles(
        [(float(top_left.x), float(top_left.y), float(bottom_right.x - top_left.x), float(bottom_right.y - top_left.y))],
        field_bounds,
    )


def _window_handle(control: object) -> int:
    try:
        value = getattr(control, "NativeWindowHandle", 0)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _valid_rect(values: Sequence[int]) -> ScreenRect | None:
    try:
        if len(values) != 4:
            return None
        left, top, right, bottom = (int(value) for value in values)
    except (TypeError, ValueError, OverflowError):
        return None
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _round_screen(value: float) -> int:
    return int(math.floor(value + 0.5))


def _numeric_value(value: object) -> float:
    """Convert Python and ctypes SAFEARRAY scalar values without text access."""

    try:
        return float(value)
    except (TypeError, ValueError):
        return float(getattr(value, "value"))
