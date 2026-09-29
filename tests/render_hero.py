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
    output = ROOT / "artifacts/violet-hero-preview.png"
    with tempfile.TemporaryDirectory(prefix="violet-artwork-") as profile:
        result = subprocess.run(
            [
                str(browser), "--headless=new", "--disable-gpu", "--disable-lcd-text",
                "--force-color-profile=srgb", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", f"--user-data-dir={profile}",
                "--window-size=1200,540", f"--screenshot={output}",
                (ROOT / "docs/images/violet-hero.svg").as_uri(),
            ],
            capture_output=True,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Artwork renderer exited {result.returncode}")
    if not output.is_file():
        raise RuntimeError("Artwork renderer produced no PNG")
    print(output)


if __name__ == "__main__":
    main()
