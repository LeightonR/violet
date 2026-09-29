"""Render Violet's owned overlay states to PNGs for visual QA."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from violet.overlay import VioletOverlay


def save_preview(overlay: VioletOverlay, path: Path) -> None:
    QApplication.processEvents()
    # Let the offscreen backing store consume the current paint request before
    # grabbing; otherwise a just-entered silent recording state can capture a
    # stale blank body instead of its mic and stop controls.
    QTest.qWait(8)
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
    # Neutral warm gray makes the violet-only rim legible without suggesting
    # a blue/cyan accent that is not present in the transparent widget.
    canvas.fill(QColor("#f1edf4"))
    painter = QPainter(canvas)
    painter.drawPixmap(margin, margin, source)
    painter.end()
    assert canvas.save(str(path)), f"Could not save {path}"


def save_spring_sheet(frames: list[QPixmap], path: Path) -> None:
    """Save a small non-personal animation contact sheet for visual review."""
    assert frames
    dpr = frames[0].devicePixelRatio()
    margin = 12
    logical_width = sum(frame.width() / dpr for frame in frames) + margin * (len(frames) + 1)
    logical_height = max(frame.height() / dpr for frame in frames) + 2 * margin
    canvas = QPixmap(round(logical_width * dpr), round(logical_height * dpr))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QColor("#f1edf4"))
    painter = QPainter(canvas)
    x = margin
    for frame in frames:
        painter.drawPixmap(x, margin, frame)
        x += frame.width() / dpr + margin
    painter.end()
    assert canvas.save(str(path)), f"Could not save {path}"


def artifact_path(out: Path, stem: str) -> Path:
    """Allow the multi-DPI renderer to keep every scale's raw PNG separate."""

    suffix = os.environ.get("VIOLET_RENDER_SUFFIX", "")
    return out / f"{stem}{suffix}.png"


def main() -> int:
    app = QApplication([])
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    overlay = VioletOverlay()
    overlay.move(80, 80)
    overlay.show()
    overlay.set_state("idle")
    overlay._place(animated=False)
    save_preview(overlay, artifact_path(out, "violet-idle"))

    overlay.set_state("recording")
    overlay._stop_spring()
    overlay._place(animated=False)
    overlay.set_speech_level(0.0, False)
    save_preview(overlay, artifact_path(out, "violet-recording-silent"))

    # These are explicitly supplied, VAD-approved synthetic speech envelopes;
    # no timer generates or repeats bars between them.
    for level in (0.10, 0.42, 0.78, 0.26, 0.92, 0.55, 1.0, 0.68, 0.34, 0.86, 1.0):
        overlay.set_speech_level(level, True)
        # Match the live stream's 32 ms VAD target cadence.  Frame pacing may
        # ease between these targets, but this script never manufactures bars.
        QTest.qWait(32)
        app.processEvents()
    QTest.qWait(16)
    save_preview(overlay, artifact_path(out, "violet-recording-max"))

    # Capture the restrained recording-lifecycle spring as a contact sheet:
    # opening into a live recording and contracting when it becomes Working.
    overlay.set_speech_level(0.0, False)
    overlay.set_state("idle")
    overlay._place(animated=False)
    overlay.set_state("recording")
    frames: list[QPixmap] = []
    for delay in (0, 55, 75, 110):
        if delay:
            QTest.qWait(delay)
        app.processEvents()
        frames.append(overlay.grab())
    overlay.set_state("transcribing")
    for delay in (0, 55, 75, 110):
        if delay:
            QTest.qWait(delay)
        app.processEvents()
        frames.append(overlay.grab())
    save_spring_sheet(frames, artifact_path(out, "violet-recording-spring"))

    overlay._stop_spring()
    overlay._place(animated=False)
    save_preview(overlay, artifact_path(out, "violet-transcribing"))
    overlay.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
