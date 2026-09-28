"""Apply Violet's checked codec-free patch to one pinned faster-whisper wheel."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


EXPECTED_VERSION = "1.2.1"
UPSTREAM_AUDIO_SHA256 = "60a1d8638f718cbf6d245aed3e5a5aa61c1f822a0b0fe9b48a7c928d47c23909"
UPSTREAM_METADATA_SHA256 = "b85622132a43897972924db98e024c0d40dcec19c577b1ee5640f1bc1b59aada"
MARKER = "# VIOLET_CODEC_FREE_PATCH=1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest.lower()


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"Expected exactly one {label} in faster-whisper 1.2.1")
    return text.replace(old, new, 1)


def apply(site_packages: Path) -> None:
    package = site_packages / "faster_whisper"
    audio = package / "audio.py"
    metadata = site_packages / f"faster_whisper-{EXPECTED_VERSION}.dist-info" / "METADATA"
    if not audio.is_file() or not metadata.is_file():
        raise RuntimeError("Pinned faster-whisper 1.2.1 is not installed at the expected location")

    audio_text = audio.read_text(encoding="utf-8")
    metadata_text = metadata.read_text(encoding="utf-8")
    if MARKER in audio_text and "Violet-Codec-Free-Patch: 1" in metadata_text:
        return
    if MARKER in audio_text or "Violet-Codec-Free-Patch: 1" in metadata_text:
        raise RuntimeError("Partially patched faster-whisper installation; rebuild from a clean wheelhouse")
    if _sha256(audio) != UPSTREAM_AUDIO_SHA256:
        raise RuntimeError("faster-whisper audio.py checksum differs from reviewed 1.2.1 upstream")
    if _sha256(metadata) != UPSTREAM_METADATA_SHA256:
        raise RuntimeError("faster-whisper METADATA checksum differs from reviewed 1.2.1 upstream")

    audio_text = _replace_once(audio_text, "import av\nimport numpy as np", "import numpy as np", "module-level PyAV import")
    helper = '''# VIOLET_CODEC_FREE_PATCH=1
def _load_pyav_for_file_decoding():
    try:
        import av
    except ImportError as error:
        raise RuntimeError(
            "This Violet build accepts in-memory microphone samples only; file decoding is unavailable."
        ) from error
    return av


'''
    audio_text = _replace_once(audio_text, "def decode_audio(\n", helper + "def decode_audio(\n", "decode_audio definition")
    audio_text = _replace_once(
        audio_text,
        "    resampler = av.audio.resampler.AudioResampler(\n",
        "    av = _load_pyav_for_file_decoding()\n    resampler = av.audio.resampler.AudioResampler(\n",
        "audio resampler",
    )
    audio_text = _replace_once(
        audio_text,
        "        frames = _ignore_invalid_frames(frames)\n",
        "        frames = _ignore_invalid_frames(frames, av.error.InvalidDataError)\n",
        "invalid-frame call",
    )
    audio_text = _replace_once(
        audio_text,
        "        frames = _group_frames(frames, 500000)\n",
        "        frames = _group_frames(frames, 500000, av_module=av)\n",
        "frame grouping call",
    )
    audio_text = _replace_once(
        audio_text,
        "def _ignore_invalid_frames(frames):\n",
        "def _ignore_invalid_frames(frames, invalid_data_error):\n",
        "invalid-frame definition",
    )
    audio_text = _replace_once(
        audio_text,
        "        except av.error.InvalidDataError:\n",
        "        except invalid_data_error:\n",
        "invalid-frame exception",
    )
    audio_text = _replace_once(
        audio_text,
        "def _group_frames(frames, num_samples=None):\n    fifo = av.audio.fifo.AudioFifo()\n",
        "def _group_frames(frames, num_samples=None, av_module=None):\n"
        "    av = av_module or _load_pyav_for_file_decoding()\n"
        "    fifo = av.audio.fifo.AudioFifo()\n",
        "frame grouping definition",
    )
    metadata_text = _replace_once(metadata_text, "Requires-Dist: av>=11\n", "Violet-Codec-Free-Patch: 1\n", "PyAV requirement")
    audio.write_text(audio_text, encoding="utf-8", newline="\n")
    metadata.write_text(metadata_text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages", type=Path, required=True)
    args = parser.parse_args()
    apply(args.site_packages)
    print("Applied verified faster-whisper 1.2.1 codec-free patch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
