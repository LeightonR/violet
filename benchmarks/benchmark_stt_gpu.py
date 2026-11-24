#!/usr/bin/env python3
"""Measure Violet's optional local CUDA path on the checked synthetic fixture.

The script uses Violet's production CUDA runtime selection and the same
large-v3-turbo / VAD / beam-size settings as the desktop worker. It deliberately
does not download a model or CUDA dependencies: run the bundled CUDA installer
and download the approved Violet model first.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from violet.acceleration import load_local_whisper_model
from violet.config import Settings, model_cache_dir
from violet.models import cached_model_path, model_spec


ROOT = Path(__file__).resolve().parent
DEFAULT_AUDIO = ROOT / "synthetic_fixture_padded.wav"
DEFAULT_OUTPUT = ROOT / "stt_gpu_results.json"
MODEL_ID = "large-v3-turbo"


def duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def transcribe_once(model: object, samples: object) -> tuple[float, str]:
    started = time.perf_counter()
    segments, _info = model.transcribe(  # type: ignore[attr-defined]
        samples,
        language="en",
        beam_size=1,
        best_of=1,
        temperature=0,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )
    transcript = "".join(segment.text for segment in segments).strip()
    return time.perf_counter() - started, transcript


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeats < 1:
        print("--repeats must be positive.", file=sys.stderr)
        return 2
    audio = args.audio.resolve()
    if not audio.is_file():
        print(f"Fixture does not exist: {audio}", file=sys.stderr)
        return 2
    model_path = args.model_path
    if model_path is None:
        model_path = cached_model_path(model_spec(MODEL_ID), model_cache_dir())
    if model_path is None or not model_path.is_dir():
        print(
            "No verified local large-v3-turbo model was found. Download it in Violet first "
            "or pass --model-path.",
            file=sys.stderr,
        )
        return 2

    settings = Settings(model_id=MODEL_ID, language="en", cpu_threads=8, gpu_acceleration=True)
    load_started = time.perf_counter()
    model, plan = load_local_whisper_model(settings, model_path)
    load_seconds = time.perf_counter() - load_started
    if not plan.uses_gpu:
        print(f"GPU acceleration was not selected: {plan.detail}", file=sys.stderr)
        return 3

    # Violet's recorder passes 16 kHz NumPy samples directly to the worker.
    # Decode the checked WAV before timing so the report measures that same
    # inference path rather than optional file-codec overhead.
    from faster_whisper.audio import decode_audio

    samples = decode_audio(str(audio), sampling_rate=16_000)
    warmup_seconds, warmup_text = transcribe_once(model, samples)
    timings: list[float] = []
    transcripts: list[str] = []
    for _ in range(args.repeats):
        elapsed, transcript = transcribe_once(model, samples)
        timings.append(elapsed)
        transcripts.append(transcript)

    import ctranslate2
    import faster_whisper

    median_seconds = statistics.median(timings)
    report = {
        "purpose": "CUDA FP16 latency/output-consistency check only; synthetic SAPI fixture is not a general WER benchmark.",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "fixture": {"path": audio.name, "duration_seconds": round(duration_seconds(audio), 3)},
        "settings": {
            "model": MODEL_ID,
            "device": plan.device,
            "compute_type": plan.compute_type,
            "language": "en",
            "beam_size": 1,
            "best_of": 1,
            "temperature": 0,
            "condition_on_previous_text": False,
            "vad_filter": True,
            "vad_min_silence_duration_ms": 500,
            "input": "predecoded 16 kHz samples, matching Violet's recorder-to-worker path",
            "repeats_after_warmup": args.repeats,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "faster_whisper": getattr(faster_whisper, "__version__", "unknown"),
            "ctranslate2": getattr(ctranslate2, "__version__", "unknown"),
        },
        "result": {
            "model_load_and_probe_seconds": round(load_seconds, 3),
            "warmup_seconds": round(warmup_seconds, 3),
            "run_seconds": [round(value, 3) for value in timings],
            "median_seconds": round(median_seconds, 3),
            "median_realtime_factor": round(duration_seconds(audio) / median_seconds, 2),
            "warmup_transcript": warmup_text,
            "run_transcripts": transcripts,
            "consistent_output": len(set(transcripts)) == 1 and warmup_text == transcripts[0],
        },
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"CUDA FP16 median after warm-up: {median_seconds:.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
