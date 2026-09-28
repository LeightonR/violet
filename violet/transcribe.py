"""Resident faster-whisper worker.  Audio exists only in worker memory."""

from __future__ import annotations

import queue
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from .audio import contains_voice_energy
from .config import Settings


class WhisperWorker(QThread):
    model_ready = Signal(str)
    model_failed = Signal(str)
    transcript_ready = Signal(int, str)
    transcription_failed = Signal(int, str)

    def __init__(self, settings: Settings, model_path: Path) -> None:
        super().__init__()
        self._settings = settings
        self._model_path = Path(model_path)
        self._jobs: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue()

    def submit(self, request_id: int, samples: np.ndarray) -> None:
        # Copy guarantees the recorder can promptly release its buffer.
        self._jobs.put((request_id, samples.copy()))

    def shutdown(self, timeout_ms: int = 3_500) -> bool:
        """Request a clean stop without terminating native inference threads.

        A stuck model download or decode should not make an explicit desktop
        Quit hang forever.  The controller uses a process-level last resort if
        this bounded graceful wait expires, which lets Windows release all
        in-memory microphone and model resources safely.
        """
        self._jobs.put(None)
        if self.isRunning():
            self.wait(timeout_ms)
        return not self.isRunning()

    def run(self) -> None:
        try:
            from faster_whisper import WhisperModel

            # `model_path` has already been verified against Violet's pinned
            # static model manifest. Loading a directory with local_files_only
            # keeps normal dictation entirely offline and prevents a model ID
            # or network cache from changing the runtime after approval.
            model = WhisperModel(
                str(self._model_path),
                device="cpu",
                compute_type="int8",
                cpu_threads=max(1, self._settings.cpu_threads),
                num_workers=1,
                local_files_only=True,
            )
        except Exception as error:
            self.model_failed.emit(f"Could not prepare the local speech model: {error}")
            self._drain_failed_jobs("The speech model is unavailable.")
            return

        self.model_ready.emit(self._settings.model_id)
        while True:
            job = self._jobs.get()
            if job is None:
                return
            request_id, samples = job
            if not contains_voice_energy(samples):
                self.transcript_ready.emit(request_id, "")
                continue
            try:
                segments, _info = model.transcribe(
                    samples,
                    language=self._settings.language,
                    task="transcribe",
                    beam_size=1,
                    best_of=1,
                    temperature=0,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                    condition_on_previous_text=False,
                )
                text = "".join(segment.text for segment in segments).strip()
                self.transcript_ready.emit(request_id, text)
            except Exception as error:
                self.transcription_failed.emit(request_id, f"Transcription failed: {error}")

    def _drain_failed_jobs(self, message: str) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self.transcription_failed.emit(job[0], message)
