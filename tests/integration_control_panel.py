"""Own-process integration check for Violet's panel, pause switch, and IPC."""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path

import uiautomation as auto
import win32gui


ROOT = Path(__file__).resolve().parents[1]


def visible_panel() -> auto.Control | None:
    handles: list[int] = []

    def collect(hwnd: int, _unused: object) -> bool:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == "Violet":
            handles.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    for hwnd in handles:
        panel = auto.ControlFromHandle(hwnd)
        if panel.CheckBoxControl(Name="Show the microphone beside editable fields").Exists(0, 0):
            return panel
    return None


class ControlPanelIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # `venv\Scripts\python.exe` starts the real Python interpreter as a
        # child on Windows. taskkill /T below is deliberately scoped to this
        # Popen tree, so it cannot touch a separately launched user Violet.
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "violet", "--background", "--demo-text", "Panel IPC test"],
            cwd=ROOT,
        )
        time.sleep(0.55)
        result = subprocess.run(
            [sys.executable, "-m", "violet", "--control-panel"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode:
            raise AssertionError(f"control-panel invocation failed: {result.stderr}")
        deadline = time.monotonic() + 4
        cls.panel = None
        while time.monotonic() < deadline and cls.panel is None:
            cls.panel = visible_panel()
            if cls.panel is None:
                time.sleep(0.1)
        if cls.panel is None:
            raise AssertionError("existing Violet process did not show its control panel")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(
            ["taskkill", "/PID", str(cls.process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            cls.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def test_pause_switch_suppresses_widget_and_resumes(self) -> None:
        toggle = self.panel.CheckBoxControl(Name="Show the microphone beside editable fields")
        self.assertTrue(toggle.Exists(2, 0.1))
        self.assertEqual(toggle.GetTogglePattern().ToggleState, 1)
        toggle.Click()
        time.sleep(0.25)
        self.assertEqual(toggle.GetTogglePattern().ToggleState, 0)
        names = [child.Name for child in self.panel.GetChildren()]
        self.assertTrue(any(name.startswith("Paused") for name in names), names)
        toggle.Click()
        time.sleep(0.2)
        self.assertEqual(toggle.GetTogglePattern().ToggleState, 1)


if __name__ == "__main__":
    unittest.main()
