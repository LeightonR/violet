#!/usr/bin/env python3
"""Verify that Violet's selected CPU configuration does not emit text for silence."""

from __future__ import annotations

import json
import time
import wave
from pathlib import Path

from faster_whisper import WhisperModel


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "model-cache"
WAV = ROOT / "silence_3s_16khz_mono.wav"
RESULT = ROOT / "silence_check.json"


def write_silence() -> None:
    sample_rate = 16_000
    seconds = 3
    with wave.open(str(WAV), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * sample_rate * seconds)


def main() -> int:
    write_silence()
    model = WhisperModel(
        "large-v3-turbo",
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
        num_workers=1,
        download_root=str(CACHE),
    )
    started = time.perf_counter()
    segments, _ = model.transcribe(
        str(WAV),
        language="en",
        task="transcribe",
        beam_size=1,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,
    )
    segments = list(segments)
    transcript = "".join(segment.text for segment in segments).strip()
    result = {
        "model": "large-v3-turbo",
        "settings": {
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 8,
            "language": "en",
            "beam_size": 1,
            "vad_filter": True,
            "vad_min_silence_duration_ms": 500,
        },
        "fixture": "3 seconds of zero-valued 16 kHz mono PCM",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "segment_count": len(segments),
        "transcript": transcript,
        "passed": not transcript,
    }
    RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
