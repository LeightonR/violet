"""Render Violet's owned overlay states to PNGs for visual QA."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from violet.overlay import VioletOverlay


def save_preview(overlay: VioletOverlay, path: Path) -> None:
    QApplication.processEvents()
    source = overlay.grab()
    # Match `grab()`'s DPR so this app-owned preview is cropped in logical
    # coordinates on every Windows scaling setting.
    margin = 12
    dpr = source.devicePixelRatio()
    canvas = QPixmap(
        round(source.width() + 2 * margin * dpr),
        round(source.height() + 2 * margin * dpr),
    )
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QColor("#dce9ee"))
    painter = QPainter(canvas)
    painter.drawPixmap(margin, margin, source)
    painter.end()
    assert canvas.save(str(path)), f"Could not save {path}"


def main() -> int:
    app = QApplication([])
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    overlay = VioletOverlay()
    overlay.move(80, 80)
    overlay.show()
    overlay.set_state("idle")
    overlay._place(animated=False)
    save_preview(overlay, out / "violet-idle.png")
    overlay.set_state("recording")
    overlay._geometry_animation.stop()
    overlay._place(animated=False)
    overlay.set_speech_level(0.0, False)
    overlay._tick()
    save_preview(overlay, out / "violet-recording-silent.png")
    # Populate the recent envelope with a loud, varied speaking pattern, then
    # render the maximum level edge case inside its fixed waveform clip.
    for level in (0.10, 0.42, 0.78, 0.26, 0.92, 0.55, 1.0, 0.68, 0.34, 0.86, 1.0):
        overlay.set_speech_level(level, True)
        overlay._tick()  # visual test only; recorded audio is never touched
    overlay.set_speech_level(1.0, True)
    for _ in range(5):
        overlay._tick()
    save_preview(overlay, out / "violet-recording-max.png")
    overlay.set_state("transcribing")
    overlay._geometry_animation.stop()
    overlay._place(animated=False)
    save_preview(overlay, out / "violet-transcribing.png")
    overlay.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
