"""Run the live, app-owned Violet fixture without touching user documents."""

from __future__ import annotations

import subprocess
import sys
import time
import ctypes
from collections.abc import Callable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uiautomation as auto
import win32con
import win32api
import win32gui
import win32process

from violet.focus import FocusService


FIXTURE_TITLE = "Violet test field - Violet"
OVERLAY_TITLE = "Violet"
DEMO_TEXT = "dictated text."
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20


def eventually(check: Callable[[], object], description: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except Exception as error:  # UIA can race control construction.
            last_error = error
        time.sleep(0.08)
    raise AssertionError(f"Timed out waiting for {description}") from last_error


def process_windows(pid: int) -> list[int]:
    windows: list[int] = []

    def visit(hwnd: int, _unused: object) -> bool:
        if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
            windows.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    return windows


def visible_window(pid: int | None, title: str) -> int | None:
    windows = process_windows(pid) if pid is not None else []
    if pid is None:
        def visit(hwnd: int, _unused: object) -> bool:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
                windows.append(hwnd)
            return True
        win32gui.EnumWindows(visit, None)
    for hwnd in windows:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            return hwnd
    return None


def recovery_window(pid: int) -> int | None:
    for hwnd in process_windows(pid):
        title = win32gui.GetWindowText(hwnd).lower()
        if win32gui.IsWindowVisible(hwnd) and title.startswith("violet") and "text ready" in title:
            return hwnd
    return None


def visible_control_panel(pid: int) -> auto.Control | None:
    """Return Violet's actual panel, never the similarly titled overlay."""
    for hwnd in process_windows(pid):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindowText(hwnd) != "Violet":
            continue
        panel = auto.ControlFromHandle(hwnd)
        if panel.CheckBoxControl(Name="Transform position (drag the violet mic)").Exists(0, 0):
            return panel
    return None


def descendants(control: auto.Control) -> Iterator[auto.Control]:
    for child in control.GetChildren():
        yield child
        yield from descendants(child)


def fixture_controls() -> dict[str, auto.Control]:
    root = auto.WindowControl(Name=FIXTURE_TITLE)
    discovered = {control.AutomationId.rsplit(".", 1)[-1]: control for control in descendants(root) if control.AutomationId}
    required = {"violet_test_field", "violet_password_field", "violet_read_only_field", "violet_document_field"}
    missing = required - discovered.keys()
    if missing:
        raise AssertionError(f"fixture controls unavailable: {missing}")
    return discovered


def click_overlay(hwnd: int) -> None:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    x, y = (right - left) // 2, (bottom - top) // 2
    lparam = (y << 16) | x
    # The fixture's overlay is a normal Qt HWND. Posting its two mouse messages
    # exercises the actual no-activate mouse handler without moving the user's
    # real mouse or typing anywhere outside this app-owned fixture.
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)


def real_click_overlay(hwnd: int) -> None:
    """Exercise WM_MOUSEACTIVATE, then restore the user's pointer position."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    original = win32api.GetCursorPos()
    try:
        win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    finally:
        win32api.SetCursorPos(original)


def drag_overlay(hwnd: int, destination: tuple[int, int]) -> None:
    """Drag only Violet's app-owned overlay, then restore the user's cursor."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    original = win32api.GetCursorPos()
    try:
        win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.04)
        win32api.SetCursorPos(destination)
        time.sleep(0.08)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    finally:
        win32api.SetCursorPos(original)


def main() -> int:
    process = subprocess.Popen(
        [sys.executable, "-m", "violet", "--fixture", "--demo-text", DEMO_TEXT],
        cwd=str(ROOT),
    )
    app_pid: int | None = None
    fixture_hwnd: int | None = None
    try:
        # The venv's Python launcher starts the actual interpreter as a child on
        # this Windows installation, so derive its PID from the uniquely titled
        # app-owned fixture instead of assuming Popen's PID owns the HWND.
        fixture_hwnd = eventually(lambda: visible_window(None, FIXTURE_TITLE), "fixture window")
        assert fixture_hwnd
        app_pid = win32process.GetWindowThreadProcessId(fixture_hwnd)[1]
        controls = eventually(fixture_controls, "fixture controls")
        test_field = controls["violet_test_field"]
        password = controls["violet_password_field"]
        readonly = controls["violet_read_only_field"]
        document = controls["violet_document_field"]
        fallback_button = next(
            control
            for control in descendants(auto.WindowControl(Name=FIXTURE_TITLE))
            if control.ControlTypeName == "ButtonControl" and "Move focus away" in control.Name
        )

        # A second registration of the same combination must fail while Violet
        # owns it, proving the normal global-hotkey registration succeeded.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        assert not user32.RegisterHotKey(None, 0x564A, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE)

        assert test_field.SetFocus()
        focus = FocusService()
        target = eventually(lambda: focus.snapshot(), "editable field UIA snapshot")
        assert target
        def initial_left_geometry():
            overlay = visible_window(app_pid, OVERLAY_TITLE)
            if not overlay:
                return None
            rect = win32gui.GetWindowRect(overlay)
            gap = target.rect.left - rect[2]
            return (overlay, rect) if rect[2] <= target.rect.left and 4 <= gap <= 14 else None

        overlay_hwnd, overlay_rect = eventually(initial_left_geometry, "visible violet overlay at field edge")
        # This assertion proves the production physical-to-Qt DPI conversion:
        # UIA reports physical bounds and the native overlay lands at the field edge.
        assert overlay_rect[2] <= target.rect.left, (overlay_rect, target.rect)
        assert 4 <= target.rect.left - overlay_rect[2] <= 14, (overlay_rect, target.rect)

        # Transform has its own deliberate edit mode. Opening the panel takes
        # keyboard focus, so this also proves the controller retained only the
        # prior field identity/geometry for a visual anchor (never its text).
        before_transform = test_field.GetValuePattern().Value
        panel_request = subprocess.run(
            [sys.executable, "-m", "violet", "--control-panel"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert panel_request.returncode == 0, panel_request.stderr
        panel = eventually(lambda: visible_control_panel(app_pid), "visible Transform panel")
        assert panel
        transform = panel.CheckBoxControl(Name="Transform position (drag the violet mic)")
        transform.Click()
        eventually(lambda: visible_control_panel(app_pid) is None, "panel hidden for unobstructed drag")
        eventually(lambda: win32gui.IsWindowVisible(overlay_hwnd), "drag overlay")
        field_rect = test_field.BoundingRectangle
        drag_overlay(overlay_hwnd, (field_rect.right + 42, (field_rect.top + field_rect.bottom) // 2))
        assert test_field.GetValuePattern().Value == before_transform, "Transform drag started dictation"
        panel_request = subprocess.run(
            [sys.executable, "-m", "violet", "--control-panel"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert panel_request.returncode == 0, panel_request.stderr
        panel = eventually(lambda: visible_control_panel(app_pid), "panel to lock Transform")
        transform = panel.CheckBoxControl(Name="Transform position (drag the violet mic)")
        transform.Click()  # lock and persist the field-relative placement
        panel.ButtonControl(Name="Close panel").Click()
        eventually(lambda: visible_control_panel(app_pid) is None, "panel closed after locking")

        # A field of a different size/position receives the same right-hand
        # relationship. Then reset it so this developer fixture leaves the
        # production preference at Violet's required default left edge.
        assert document.SetFocus()
        second_target = eventually(lambda: focus.snapshot(), "second editable field")
        def right_side_geometry():
            moved_overlay = visible_window(app_pid, OVERLAY_TITLE)
            if not moved_overlay:
                return None
            moved_rect = win32gui.GetWindowRect(moved_overlay)
            return moved_rect if moved_rect[0] >= second_target.rect.right else None

        moved_rect = eventually(right_side_geometry, "field-relative placement on second field")
        panel_request = subprocess.run(
            [sys.executable, "-m", "violet", "--control-panel"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert panel_request.returncode == 0, panel_request.stderr
        panel = eventually(lambda: visible_control_panel(app_pid), "panel for reset")
        reset = panel.ButtonControl(Name="Reset position")
        assert reset.Exists(2, 0.1)
        reset.Click()
        panel.ButtonControl(Name="Close panel").Click()
        eventually(lambda: visible_control_panel(app_pid) is None, "panel closed after reset")
        assert test_field.SetFocus()
        target = eventually(lambda: focus.snapshot(), "field after reset")
        overlay_hwnd = eventually(lambda: visible_window(app_pid, OVERLAY_TITLE), "default overlay after reset")
        def default_left_geometry():
            reset_rect = win32gui.GetWindowRect(overlay_hwnd)
            gap = target.rect.left - reset_rect[2]
            return reset_rect if reset_rect[2] <= target.rect.left and 4 <= gap <= 14 else None

        reset_rect = eventually(default_left_geometry, "default left placement after reset")

        before = test_field.GetValuePattern().Value
        real_click_overlay(overlay_hwnd)
        eventually(lambda: win32gui.GetWindowRect(overlay_hwnd)[2] - win32gui.GetWindowRect(overlay_hwnd)[0] > 100, "recording pill")
        assert focus.is_current(target), "overlay click stole the original caret"
        real_click_overlay(overlay_hwnd)
        expected = "before dictated text. after"
        eventually(lambda: test_field.GetValuePattern().Value == expected, "punctuated transcript inserted at caret")

        # Violet must never advertise itself in password or read-only fields.
        assert password.SetFocus()
        eventually(lambda: not win32gui.IsWindowVisible(overlay_hwnd), "overlay hidden for password")
        assert focus.hotkey_fallback_rect() is None
        assert readonly.SetFocus()
        eventually(lambda: not win32gui.IsWindowVisible(overlay_hwnd), "overlay hidden for read-only field")
        assert focus.hotkey_fallback_rect() is None

        # Unknown/custom focus can use the hotkey-only recovery flow, while it
        # never receives an automatic icon or insertion target.
        assert fallback_button.SetFocus()
        eventually(lambda: focus.snapshot() is None, "unsupported focus")
        assert focus.hotkey_fallback_rect() is not None

        # Recording for one field then moving focus produces recovery text rather
        # than unicode input in the new document target.
        assert test_field.SetFocus()
        changed_target = eventually(lambda: focus.snapshot(), "source field restored")
        overlay_hwnd = eventually(lambda: visible_window(app_pid, OVERLAY_TITLE), "overlay restored")
        unchanged_source = test_field.GetValuePattern().Value
        real_click_overlay(overlay_hwnd)
        eventually(lambda: win32gui.GetWindowRect(overlay_hwnd)[2] - win32gui.GetWindowRect(overlay_hwnd)[0] > 100, "second recording pill")
        assert document.SetFocus()
        eventually(lambda: not focus.is_current(changed_target), "target focus changed")
        real_click_overlay(overlay_hwnd)
        eventually(lambda: recovery_window(app_pid), "recoverable transcript window")
        assert test_field.GetValuePattern().Value == unchanged_source
        value = document.GetValuePattern().Value
        assert DEMO_TEXT not in value, "transcript was typed into a changed target"

        # A real text-selection drag in Violet's own field must not re-anchor
        # the topmost control while the button is held, steal focus, or turn a
        # foreign mouse-up over Violet into a recording toggle. This is the
        # exact interaction boundary that protects ordinary editors such as
        # browser message boxes; it never touches user documents.
        ready_window = recovery_window(app_pid)
        if ready_window:
            win32gui.PostMessage(ready_window, win32con.WM_CLOSE, 0, 0)
        assert test_field.SetFocus()
        selection_target = eventually(lambda: focus.snapshot(), "selection fixture focus")
        overlay_hwnd = eventually(lambda: visible_window(app_pid, OVERLAY_TITLE), "overlay before selection drag")
        eventually(
            lambda: overlay_hwnd
            if win32gui.GetWindowRect(overlay_hwnd)[2] - win32gui.GetWindowRect(overlay_hwnd)[0] <= 80
            else None,
            "idle overlay before selection drag",
        )
        time.sleep(0.28)
        before_drag = win32gui.GetWindowRect(overlay_hwnd)
        field_rect = test_field.BoundingRectangle
        cursor = win32api.GetCursorPos()
        try:
            win32api.SetCursorPos((field_rect.left + 50, (field_rect.top + field_rect.bottom) // 2))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            # Cover several 220ms focus polls while the external field owns
            # the selection gesture.
            time.sleep(0.56)
            assert win32gui.GetWindowRect(overlay_hwnd) == before_drag, "overlay moved during text selection"
            left, top, right, bottom = before_drag
            win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
            time.sleep(0.08)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        finally:
            win32api.SetCursorPos(cursor)
        eventually(lambda: focus.is_current(selection_target), "selection drag retained original field focus")
        time.sleep(0.25)
        width = win32gui.GetWindowRect(overlay_hwnd)[2] - win32gui.GetWindowRect(overlay_hwnd)[0]
        assert width <= 80, "foreign selection release started Violet recording"

        print(
            "fixture integration passed: Transform drag/lock/reset, caret preserved, safe insertion, "
            "password/read-only exclusion, target-change recovery, selection-drag isolation"
        )
        return 0
    finally:
        if fixture_hwnd:
            win32gui.PostMessage(fixture_hwnd, win32con.WM_CLOSE, 0, 0)
        # On this installation the virtual-environment launcher starts the Qt
        # interpreter as a child. Terminate the verified fixture PID too, so an
        # interrupted test cannot orphan an overlay or global hotkey.
        if app_pid:
            try:
                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, app_pid)
                win32process.TerminateProcess(handle, 0)
                win32api.CloseHandle(handle)
            except win32api.error:
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
