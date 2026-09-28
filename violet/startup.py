"""Per-user startup shortcut management for Violet's control panel."""

from __future__ import annotations

import os
from pathlib import Path

from .runtime import executable_path, installation_root, is_frozen


APP_USER_MODEL_ID = "Violet.DesktopDictation"


def project_root() -> Path:
    """Compatibility name for the immutable installed/source root."""

    return installation_root()


def startup_shortcut_path() -> Path:
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Violet.lnk"


def is_startup_enabled() -> bool:
    return startup_shortcut_path().is_file()


def set_startup_enabled(enabled: bool) -> None:
    """Create or remove Violet's *own* per-user background startup shortcut."""
    shortcut_path = startup_shortcut_path()
    if not enabled:
        if shortcut_path.exists():
            shortcut_path.unlink()
        return

    root = project_root()
    native_launcher = executable_path() if is_frozen() else root / "Violet.exe"
    if native_launcher.is_file():
        target = native_launcher
        arguments = "--background"
    else:
        # Setup normally supplies Violet.exe. This fallback keeps the panel
        # usable during source development and preserves the background mode.
        powershell = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        launcher = root / "Run-Violet.ps1"
        target = powershell
        arguments = f'-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{launcher}" --background'

    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(str(shortcut_path))
        shortcut.TargetPath = str(target)
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = str(root)
        shortcut.Description = "Violet - local desktop dictation"
        icon = root / "Violet.ico"
        if icon.is_file():
            shortcut.IconLocation = f"{icon},0"
        shortcut.Save()
    except Exception as error:
        raise RuntimeError(f"Could not update Violet startup: {error}") from error
