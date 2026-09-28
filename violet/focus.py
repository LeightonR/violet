"""Focused editable-control discovery without inspecting user text."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
from typing import Optional

import uiautomation as auto

from .caret import active_line_rect


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = ctypes.c_void_p


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.GetCursorPos.argtypes = (ctypes.POINTER(_POINT),)
user32.GetCursorPos.restype = ctypes.c_int


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


@dataclass(frozen=True, slots=True)
class FieldRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class EditableTarget:
    """A privacy-preserving identity for one currently focused text target."""

    runtime_id: tuple[int, ...]
    foreground_hwnd: int
    process_id: int
    control_type: int
    # The full field remains the safety/identity boundary. This separate row
    # rectangle is only for positioning the no-activation Violet control.
    rect: FieldRect
    anchor_rect: FieldRect
    # True only when UIA/native caret geometry was available.  A whole-field
    # anchor is an explicit visual fallback, never evidence that a selection
    # has a caret row.
    anchor_is_caret: bool


class FocusService:
    """Reads UIA structure only. It deliberately never reads a field's value."""

    _supported_types = {auto.ControlType.EditControl, auto.ControlType.DocumentControl}

    def snapshot(self) -> Optional[EditableTarget]:
        try:
            control = auto.GetFocusedControl()
            if control is None:
                return None
            foreground = foreground_window()
            if not foreground or not control.HasKeyboardFocus:
                return None
            if control.ControlType not in self._supported_types:
                return None
            if not control.IsEnabled or control.IsOffscreen or control.IsPassword:
                return None
            if not control.IsKeyboardFocusable:
                return None
            if self._is_read_only(control):
                return None
            if not self._is_text_capable(control):
                return None
            bounds = control.BoundingRectangle
            rect = FieldRect(bounds.left, bounds.top, bounds.right, bounds.bottom)
            if rect.width < 2 or rect.height < 2:
                return None
            anchor_rect, anchor_is_caret = self._anchor_rect(control, rect)
            runtime_id = tuple(int(part) for part in control.GetRuntimeId())
            if not runtime_id:
                return None
            return EditableTarget(
                runtime_id=runtime_id,
                foreground_hwnd=foreground,
                process_id=int(control.ProcessId),
                control_type=int(control.ControlType),
                rect=rect,
                anchor_rect=anchor_rect,
                anchor_is_caret=anchor_is_caret,
            )
        except Exception:
            # UIA can throw when an app destroys an element during the query.
            return None

    def is_current(self, expected: EditableTarget) -> bool:
        """True only while focus and target identity are still exactly unchanged."""
        current = self.snapshot()
        return bool(
            current
            and current.foreground_hwnd == expected.foreground_hwnd
            and current.runtime_id == expected.runtime_id
            and current.process_id == expected.process_id
            and current.control_type == expected.control_type
        )

    @staticmethod
    def _anchor_rect(control: auto.Control, field_rect: FieldRect) -> tuple[FieldRect, bool]:
        """Use caret-row geometry when a provider exposes it, else field bounds.

        The helper reads only caret/line rectangles. It never reads a control's
        value or text, and returning the field geometry is an explicit visual
        fallback for providers that cannot expose a visible caret row.
        """
        try:
            row = active_line_rect(
                control,
                (field_rect.left, field_rect.top, field_rect.right, field_rect.bottom),
            )
            if row is not None:
                candidate = FieldRect(*row)
                if candidate.width >= 2 and candidate.height >= 1:
                    return candidate, True
        except Exception:
            pass
        return field_rect, False

    def hotkey_fallback_rect(self) -> Optional[FieldRect]:
        """Return a cursor anchor only for an unknown, non-protected focus.

        The global shortcut can still collect a transcript for a custom editor
        that UIA cannot identify. It is deliberately recovery-only: callers get
        no insertion target and must offer Copy instead. Password and known
        read-only controls stay excluded.
        """
        try:
            control = auto.GetFocusedControl()
            if control is not None:
                if control.IsPassword:
                    return None
                try:
                    known_text_surface = control.ControlType in self._supported_types or control.GetValuePattern() is not None
                except Exception:
                    known_text_surface = control.ControlType in self._supported_types
                if known_text_surface and self._is_read_only(control):
                    return None
                if not control.IsEnabled:
                    return None
        except Exception:
            # If UIA cannot inspect a custom surface at all, recovery-only is
            # still safe because it never types into that surface.
            pass
        point = _POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        return FieldRect(point.x, point.y, point.x + 1, point.y + 1)

    @staticmethod
    def _is_text_capable(control: auto.Control) -> bool:
        # Edit controls normally expose ValuePattern. Document controls often use
        # TextPattern (for example browser content-editable surfaces). Retrieving
        # a pattern does not retrieve the user's text.
        try:
            if control.GetValuePattern() is not None:
                return True
            getter = getattr(control, "GetTextPattern", None)
            return bool(getter and getter() is not None)
        except Exception:
            return False

    @staticmethod
    def _is_read_only(control: auto.Control) -> bool:
        try:
            value_pattern = control.GetValuePattern()
            if value_pattern is not None and value_pattern.IsReadOnly:
                return True
        except Exception:
            # An inaccessible ValuePattern is not proof of writability.
            return True
        try:
            legacy = control.GetLegacyIAccessiblePattern()
            if legacy is not None and legacy.State & auto.AccessibleState.ReadOnly:
                return True
        except Exception:
            pass
        return False


def retain_last_caret_anchor(
    previous: EditableTarget | None, current: EditableTarget
) -> EditableTarget:
    """Keep a valid visible row through a transient UIA selection gap.

    Browser accessibility providers commonly report no collapsed caret while a
    user is dragging a selection or immediately after a mouse-up. Replacing a
    known row with the full field during that short gap visibly moves Violet and
    can put the control under the pointer. Retain only the prior geometry for
    the same, unmoved editable control; this never changes insertion identity
    and never reads selection text.
    """

    if current.anchor_is_caret or previous is None or not previous.anchor_is_caret:
        return current
    same_control = (
        previous.runtime_id == current.runtime_id
        and previous.foreground_hwnd == current.foreground_hwnd
        and previous.process_id == current.process_id
        and previous.control_type == current.control_type
        and previous.rect == current.rect
    )
    if not same_control:
        return current
    return replace(current, anchor_rect=previous.anchor_rect, anchor_is_caret=True)
