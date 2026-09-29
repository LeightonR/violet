"""Render the repository's SVG artwork in an isolated headless browser.

LCD subpixel text is disabled so the portable PNG has ordinary coverage
antialiasing, independent of the viewer's monitor subpixel arrangement.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    candidates = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    )
    browser = next((path for path in candidates if path.is_file()), None)
    if browser is None:
        raise RuntimeError("Rendering the artwork requires Chrome or Edge")
    artworks = (
        ("docs/images/violet-hero.svg", "artifacts/violet-hero-preview.png", "1200,540"),
        ("docs/images/violet-011-launch.svg", "docs/images/violet-011-launch.png", "1200,630"),
    )
    with tempfile.TemporaryDirectory(prefix="violet-artwork-") as profile:
        for source, target, size in artworks:
            output = ROOT / target
            result = subprocess.run(
                [
                    str(browser), "--headless=new", "--disable-gpu", "--disable-lcd-text",
                    "--force-color-profile=srgb", "--hide-scrollbars", "--no-first-run",
                    "--no-default-browser-check", f"--user-data-dir={profile}",
                    f"--window-size={size}", f"--screenshot={output}",
                    (ROOT / source).as_uri(),
                ],
                capture_output=True,
                timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Artwork renderer exited {result.returncode}")
            if not output.is_file():
                raise RuntimeError(f"Artwork renderer produced no PNG for {source}")
            print(output)


if __name__ == "__main__":
    main()
