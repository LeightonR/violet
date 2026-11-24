"""Run the portable window against a synthetic clip and a local model."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from scipy.io.wavfile import read
from violet.audio import AudioClip, resample_to_16khz
from violet.portable import DictationWindow

def main():
    model, fixture = map(Path, sys.argv[1:])
    rate, samples = read(fixture)
    clip = AudioClip(resample_to_16khz(samples.astype("float32") / 32768, rate), 16000)
    app = QApplication([])
    with patch("violet.portable.cached_model_path", return_value=model):
        window = DictationWindow()
        def advance():
            if window.result.toPlainText():
                app.exit(0)
            elif window.status.text() == "Ready to dictate":
                with patch.object(type(window.recorder), "is_recording", True), patch.object(window.recorder, "stop", return_value=clip):
                    window.activate()
        timer = QTimer()
        timer.timeout.connect(advance)
        timer.start(50)
        QTimer.singleShot(120000, lambda: app.exit(1))
        status = app.exec()
        print(window.result.toPlainText() or window.status.text())
        window.close()
        return status

if __name__ == "__main__":
    raise SystemExit(main())
