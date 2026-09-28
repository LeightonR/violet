"""Paths that work from both source and the frozen Windows executable."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Whether Violet is running from a PyInstaller-style executable."""

    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    """Return the launchable executable for shortcuts and Startup entries."""

    return Path(sys.executable).resolve()


def installation_root() -> Path:
    """Return Violet's installed directory without assuming a source checkout."""

    if is_frozen():
        return executable_path().parent
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    """Resolve an immutable packaged resource below the application root."""

    return installation_root().joinpath(*parts)
