"""Minimal, coordinate-only pointer tracking for hidden browser editors.

Google Docs deliberately keeps the keyboard target in an off-screen iframe, so
Windows accessibility cannot always expose a caret rectangle.  The tracker
records only the position and monotonic time of a released left click; it never
reads window contents, injects input, or changes a mouse event.  Violet uses it
only after independently confirming that the current focused target is the
hidden editor whose visible canvas contains that point.
"""

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes


WH_MOUSE_LL = 14
WM_LBUTTONUP = 0x0202


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


@dataclass(frozen=True, slots=True)
class PointerClick:
    """One left-button release, containing no text or application identity."""

    sequence: int
    x: int
    y: int
    observed_at: float


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, ctypes.c_void_p, wintypes.DWORD)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


class PointerClickTracker:
    """Own a short-lived, passive Windows low-level mouse hook.

    A Windows low-level hook runs in the installing process's message loop.
    Returning ``CallNextHookEx`` unconditionally preserves normal mouse input.
    Installation can fail under a restrictive desktop policy; that is a
    non-fatal loss of the placement fallback rather than a dictation failure.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: PointerClick | None = None
        self._sequence = 0
        self._hook: int | None = None
        self._callback: object | None = None

    @property
    def installed(self) -> bool:
        return self._hook is not None

    def start(self) -> bool:
        """Install the passive hook once; return false if Windows rejects it."""

        if self._hook is not None:
            return True

        @_HOOKPROC
        def callback(code: int, message: int, lparam: int) -> int:
            if code >= 0 and message == WM_LBUTTONUP and lparam:
                try:
                    event = _MSLLHOOKSTRUCT.from_address(int(lparam))
                    self._record(int(event.pt.x), int(event.pt.y), time.monotonic())
                except Exception:
                    # A passive geometry aid must never disrupt the mouse if an
                    # event is malformed or the process is shutting down.
                    pass
            return int(user32.CallNextHookEx(None, code, message, lparam))

        ctypes.set_last_error(0)
        module = kernel32.GetModuleHandleW(None)
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, module, 0)
        if not hook:
            return False
        # Keep the CFUNCTYPE object alive for the whole native hook lifetime.
        self._callback = callback
        self._hook = int(hook)
        return True

    def stop(self) -> None:
        """Remove the hook without touching any user input state."""

        hook, self._hook = self._hook, None
        self._callback = None
        if hook:
            user32.UnhookWindowsHookEx(ctypes.c_void_p(hook))

    def latest(self) -> PointerClick | None:
        with self._lock:
            return self._latest

    def _record(self, x: int, y: int, observed_at: float) -> PointerClick:
        """Store an event atomically; exposed for deterministic unit coverage."""

        with self._lock:
            self._sequence += 1
            click = PointerClick(self._sequence, int(x), int(y), float(observed_at))
            self._latest = click
            return click
