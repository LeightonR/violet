"""Portable recording window for macOS and Linux."""

import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .appearance import WINDOW_STYLE
from .audio import AudioRecorder
from .config import Settings, model_cache_dir
from .models import cached_model_path, download_verified_model, model_spec
from .transcribe import WhisperWorker


class PrepareModel(QThread):
    prepared = Signal(object)
    failed = Signal(str)

    def __init__(self, spec, confirmed):
        super().__init__()
        self.spec = spec
        self.confirmed = confirmed

    def run(self):
        try:
            cache = model_cache_dir()
            path = (download_verified_model(self.spec, cache, user_confirmed=True)
                    if self.confirmed else cached_model_path(self.spec, cache))
            self.prepared.emit(path)
        except Exception as error:
            self.failed.emit(str(error))


class DictationWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Violet")
        self.setStyleSheet(WINDOW_STYLE)
        self.resize(460, 340)
        self.spec = model_spec("small.en")
        self.worker = None
        self.recorder = AudioRecorder()
        layout = QVBoxLayout(self)
        title = QLabel("Violet")
        title.setObjectName("windowHeading")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.action = QPushButton()
        self.action.setObjectName("primary")
        self.action.clicked.connect(self.activate)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setAccessibleName("Transcript")
        copy = QPushButton("Copy transcript")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.result.toPlainText()))
        self.result.textChanged.connect(lambda: copy.setEnabled(bool(self.result.toPlainText())))
        copy.setEnabled(False)
        for control in (title, self.status, self.action, self.result, copy):
            layout.addWidget(control)
        self.prepare(False)

    def prepare(self, confirmed):
        self.status.setText("Preparing your local speech model…")
        self.action.setText("Preparing…")
        self.action.setEnabled(False)
        self.setup = PrepareModel(self.spec, confirmed)
        self.setup.prepared.connect(self.model_found)
        self.setup.failed.connect(self.failed)
        self.setup.start()

    def model_found(self, path):
        if path is None:
            self.status.setText("Download the English model once to dictate offline.")
            self.action.setText(f"Download model · {self.spec.download_mib:.0f} MiB")
            self.action.setEnabled(True)
            return
        self.worker = WhisperWorker(Settings(model_id=self.spec.model_id), path)
        self.worker.model_ready.connect(lambda _: self.ready())
        self.worker.model_failed.connect(self.model_failed)
        self.worker.transcript_ready.connect(self.completed)
        self.worker.transcription_failed.connect(lambda _, reason: self.failed(reason))
        self.worker.start()

    def model_failed(self, reason):
        self.worker.shutdown()
        self.worker = None
        self.failed(reason)

    def ready(self):
        self.action.setEnabled(True)
        self.action.setText("Start recording")
        self.status.setText("Ready to dictate")

    def failed(self, reason):
        self.action.setEnabled(True)
        self.action.setText("Try again")
        self.status.setText(reason)

    def completed(self, _, text):
        self.ready()
        if text:
            self.result.setPlainText(text)
            self.status.setText("Copy your transcript and paste it into your document.")
        else:
            self.status.setText("No speech detected. Try again.")

    def activate(self):
        if self.worker is None:
            self.prepare(True)
            return
        try:
            if self.recorder.is_recording:
                clip = self.recorder.stop()
                self.action.setEnabled(False)
                self.status.setText("Transcribing…")
                self.worker.submit(1, clip.samples)
            else:
                self.recorder.start()
                self.action.setText("Finish recording")
                self.status.setText("Listening…")
        except Exception as error:
            self.recorder.cancel()
            self.failed(str(error))

    def closeEvent(self, event):
        if self.setup.isRunning():
            self.status.setText("Finishing model setup. Close Violet when setup completes.")
            event.ignore()
            return
        self.recorder.cancel()
        if self.worker and not self.worker.shutdown():
            self.status.setText("Finishing transcription. Close Violet when it completes.")
            event.ignore()
            return
        event.accept()


def run():
    app = QApplication(sys.argv)
    window = DictationWindow()
    window.show()
    return app.exec()
