#!/usr/bin/env python3
"""Bounded, CPU-only comparison for Violet's two candidate English dictation models.

This intentionally measures latency and output consistency on one synthetic SAPI
fixture; it does not claim to measure general ASR accuracy.  It exercises the
same settings that Violet uses for a short utterance: CPU int8, English,
beam-size 1, and VAD with short silence removal.

Models are downloaded only into the supplied cache directory.  Run from the
repository root after creating ``benchmarks/synthetic_fixture.wav`` with
``benchmarks/create_synthetic_fixture.ps1``:

    .venv\\Scripts\\python.exe benchmarks\\benchmark_stt_cpu.py
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_AUDIO = ROOT / "synthetic_fixture_padded.wav"
DEFAULT_CACHE = ROOT / "model-cache"
MODELS = {
    "turbo": "large-v3-turbo",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
}


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def ensure_padded_fixture(source: Path, destination: Path) -> Path:
    """Add one second of silence at either end so the VAD path is exercised."""
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination

    with wave.open(str(source), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        frame_rate = reader.getframerate()
        frame_count = reader.getnframes()
        frames = reader.readframes(frame_count)
        params = reader.getparams()

    silent_frames = b"\x00" * (frame_rate * channels * sample_width)
    with wave.open(str(destination), "wb") as writer:
        writer.setparams(params)
        writer.writeframes(silent_frames + frames + silent_frames)
    return destination


def transcribe_once(model: Any, audio: Path) -> tuple[float, str]:
    started = time.perf_counter()
    segments, _ = model.transcribe(
        str(audio),
        language="en",
        task="transcribe",
        beam_size=1,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,
    )
    # faster-whisper begins work when this generator is consumed.
    transcript = "".join(segment.text for segment in segments).strip()
    return time.perf_counter() - started, transcript


def benchmark_case(
    model_label: str,
    model_id: str,
    audio: Path,
    cache: Path,
    threads: int,
    repeats: int,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    load_started = time.perf_counter()
    model = WhisperModel(
        model_id,
        device="cpu",
        compute_type="int8",
        cpu_threads=threads,
        num_workers=1,
        download_root=str(cache),
    )
    load_seconds = time.perf_counter() - load_started

    warmup_seconds, warmup_text = transcribe_once(model, audio)
    timings: list[float] = []
    texts: list[str] = []
    for _ in range(repeats):
        seconds, transcript = transcribe_once(model, audio)
        timings.append(seconds)
        texts.append(transcript)

    audio_seconds = wav_duration_seconds(audio)
    median_seconds = statistics.median(timings)
    result = {
        "model_label": model_label,
        "model_id": model_id,
        "threads": threads,
        "load_seconds": round(load_seconds, 3),
        "warmup_seconds": round(warmup_seconds, 3),
        "run_seconds": [round(value, 3) for value in timings],
        "median_seconds": round(median_seconds, 3),
        "audio_seconds": round(audio_seconds, 3),
        "median_realtime_factor": round(audio_seconds / median_seconds, 2),
        "warmup_transcript": warmup_text,
        "run_transcripts": texts,
        "consistent_output": len(set(texts)) == 1 and warmup_text == texts[0],
    }
    del model
    gc.collect()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audio",
        type=Path,
        default=DEFAULT_AUDIO,
        help="WAV fixture. The default padded fixture is made from the SAPI source fixture.",
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--threads", type=int, nargs="+", default=[8, 16, 32])
    parser.add_argument("--repeats", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_fixture = ROOT / "synthetic_fixture.wav"
    if args.audio == DEFAULT_AUDIO:
        if not source_fixture.exists():
            print(
                "Missing benchmarks/synthetic_fixture.wav. Run "
                "benchmarks/create_synthetic_fixture.ps1 first.",
                file=sys.stderr,
            )
            return 2
        audio = ensure_padded_fixture(source_fixture, DEFAULT_AUDIO)
    else:
        audio = args.audio.resolve()

    if not audio.exists():
        print(f"Fixture does not exist: {audio}", file=sys.stderr)
        return 2
    if args.repeats < 1 or any(value < 1 for value in args.threads):
        print("Threads and repeats must be positive.", file=sys.stderr)
        return 2

    args.cache.mkdir(parents=True, exist_ok=True)
    import ctranslate2
    import faster_whisper

    report: dict[str, Any] = {
        "purpose": "CPU latency/output-consistency comparison only; synthetic SAPI fixture is not a general WER benchmark.",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "fixture": {
            "path": audio.name,
            "duration_seconds": round(wav_duration_seconds(audio), 3),
            "source": "Windows SAPI synthetic English speech with one second of leading and trailing silence",
        },
        "settings": {
            "device": "cpu",
            "compute_type": "int8",
            "language": "en",
            "task": "transcribe",
            "beam_size": 1,
            "condition_on_previous_text": False,
            "vad_filter": True,
            "vad_min_silence_duration_ms": 500,
            "num_workers": 1,
            "repeats_after_warmup": args.repeats,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "os_cpu_count": os.cpu_count(),
            "faster_whisper": getattr(faster_whisper, "__version__", "unknown"),
            "ctranslate2": getattr(ctranslate2, "__version__", "unknown"),
        },
        "results": [],
    }

    for label, model_id in MODELS.items():
        for threads in args.threads:
            print(f"Benchmarking {label}, cpu_threads={threads}...", flush=True)
            result = benchmark_case(
                label,
                model_id,
                audio,
                args.cache,
                threads,
                args.repeats,
            )
            report["results"].append(result)
            print(
                f"  median={result['median_seconds']}s "
                f"({result['median_realtime_factor']}x real-time): "
                f"{result['run_transcripts'][0]!r}",
                flush=True,
            )

    report_path = ROOT / "stt_cpu_results.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
