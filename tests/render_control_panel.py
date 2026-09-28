"""Render Violet's compact control panel for app-owned visual QA."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from violet.app import ControlPanel


def main() -> int:
    app = QApplication([])
    panel = ControlPanel()
    # Public, app-owned preview data only: no local device names, paths, or
    # machine-specific identifiers are captured in the README artifact.
    panel.update_state(
        enabled=True,
        model_state="ready",
        startup_enabled=True,
        transform_enabled=False,
        model_id="large-v3-turbo",
        model_disclosure="Using local large-v3-turbo. Audio and transcripts stay on this PC.",
        hardware_summary="Local CPU profile selected a punctuation-ready English model.",
    )
    panel.show()
    QApplication.processEvents()
    source = panel.grab()
    # `grab()` is a physical-pixel pixmap on a scaled Windows display. Keep
    # the canvas at the same DPR so the intended 14 logical-pixel border does
    # not turn into excess right/bottom padding in the saved PNG.
    margin = 14
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
    output = ROOT / "artifacts" / "violet-control-panel.png"
    output.parent.mkdir(exist_ok=True)
    assert canvas.save(str(output)), f"Could not save {output}"
    panel.hide()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
