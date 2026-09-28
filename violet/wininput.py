"""Safe text injection through Windows Unicode input, without touching clipboard."""

from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass

from .focus import EditableTarget, FocusService


user32 = ctypes.WinDLL("user32", use_last_error=True)

WORD = ctypes.c_ushort
DWORD = ctypes.c_ulong
UINT = ctypes.c_uint
ULONG_PTR = ctypes.c_size_t
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    # INPUT's union must have the native MOUSEINPUT width even though Violet
    # only sends keyboard records.  Sending a too-small cbSize makes SendInput
    # fail on 64-bit Windows.
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = UINT


@dataclass(frozen=True, slots=True)
class InsertionResult:
    inserted: bool
    message: str


def normalize_for_typing(text: str) -> str:
    """Never inject navigation keys, especially Return/Enter, through dictation."""
    safe = []
    for character in text.strip():
        if character in "\r\n\t" or ord(character) < 32 or ord(character) == 127:
            safe.append(" ")
        else:
            safe.append(character)
    return " ".join("".join(safe).split())


def _unicode_units(text: str) -> tuple[int, ...]:
    raw = text.encode("utf-16-le", errors="surrogatepass")
    return struct.unpack("<" + "H" * (len(raw) // 2), raw)


def _send_unicode_units(units: tuple[int, ...]) -> bool:
    inputs = (INPUT * (len(units) * 2))()
    for index, unit in enumerate(units):
        down = index * 2
        inputs[down].type = INPUT_KEYBOARD
        inputs[down].ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0)
        inputs[down + 1].type = INPUT_KEYBOARD
        inputs[down + 1].ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
    sent = user32.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT))
    return sent == len(inputs)


def insert_at_original_target(
    focus: FocusService, expected: EditableTarget, transcript: str
) -> InsertionResult:
    """Insert only after re-identifying the exact original focused field.

    Windows has no atomic "verify target then type" primitive.  Re-checking before
    each small batch keeps that race extremely narrow and ensures a focus change
    produces a recoverable transcript instead of deliberate typing elsewhere.
    """
    text = normalize_for_typing(transcript)
    if not text:
        return InsertionResult(False, "Nothing to insert.")
    if not focus.is_current(expected):
        return InsertionResult(False, "The original text field changed. Transcript kept ready.")
    units = _unicode_units(text)
    # Frequent focus checks are cheap compared with inference and avoid feeding a
    # very long dictation into a target that changed halfway through.
    for start in range(0, len(units), 256):
        if not focus.is_current(expected):
            return InsertionResult(False, "Text field changed before insertion finished.")
        if not _send_unicode_units(units[start : start + 256]):
            error = ctypes.get_last_error()
            return InsertionResult(False, f"Windows did not accept the text (error {error}).")
    # The identity check protects the original field, but a user may still move
    # its caret while transcription is running. Do not promise a stale caret
    # position that Windows cannot verify atomically.
    return InsertionResult(True, "Inserted into the original text field.")
