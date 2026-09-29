"""Render Violet's owned widget at common Windows DPI scales for visual QA.

This uses isolated Qt processes because Qt reads ``QT_SCALE_FACTOR`` while it
starts.  It never opens a microphone, reads desktop content, or controls an
external application.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SCALES = (("100", "1"), ("125", "1.25"), ("150", "1.5"), ("200", "2"))
STEMS = ("violet-idle", "violet-recording-silent", "violet-recording-max", "violet-transcribing")


def render_scale(label: str, factor: str) -> None:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = factor
    environment.pop("QT_SCREEN_SCALE_FACTORS", None)
    environment["VIOLET_RENDER_SUFFIX"] = f"-dpi{label}"
    subprocess.run(
        [sys.executable, str(ROOT / "tests" / "render_overlay.py")],
        cwd=ROOT,
        env=environment,
        check=True,
    )


def make_contact_sheet() -> Path:
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    images: dict[tuple[str, str], QImage] = {}
    for label, _factor in SCALES:
        for stem in STEMS:
            image = QImage(str(ARTIFACTS / f"{stem}-dpi{label}.png"))
            if image.isNull():
                raise RuntimeError(f"Missing DPI render: {stem}-dpi{label}.png")
            images[(label, stem)] = image

    margin = 16
    column_widths = {
        label: max(images[(label, stem)].width() for stem in STEMS) + 2 * margin for label, _factor in SCALES
    }
    row_heights = {stem: max(images[(label, stem)].height() for label, _factor in SCALES) + 2 * margin for stem in STEMS}
    canvas = QImage(
        sum(column_widths.values()),
        sum(row_heights.values()),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    canvas.fill(QColor("#f1edf4"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    y = 0
    for stem in STEMS:
        x = 0
        for label, _factor in SCALES:
            image = images[(label, stem)]
            painter.drawImage(x + (column_widths[label] - image.width()) // 2, y + (row_heights[stem] - image.height()) // 2, image)
            x += column_widths[label]
        y += row_heights[stem]
    painter.end()
    output = ARTIFACTS / "violet-dpi-contact-sheet.png"
    if not canvas.save(str(output)):
        raise RuntimeError(f"Could not write {output}")
    (ARTIFACTS / "violet-dpi-contact-sheet.txt").write_text(
        "Columns: 100%, 125%, 150%, 200% Windows Qt scale.\n"
        "Rows: idle, recording silence, recording speech, transcribing.\n"
        "Each source image is a raw app-owned raster; the contact sheet does not resample them.\n",
        encoding="utf-8",
    )
    app.quit()
    return output


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    for label, factor in SCALES:
        render_scale(label, factor)
    print(make_contact_sheet())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
