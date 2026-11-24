# Violet CPU speech-to-text benchmark

This folder records the bounded model comparison used to choose Violet's first
local dictation engine. It measures short-utterance latency and repeatable output
on this computer; it is not a general transcription-accuracy evaluation.

## Selected configuration

Use one resident `faster-whisper` worker configured as follows:

```python
WhisperModel(
    "large-v3-turbo",
    device="cpu",
    compute_type="int8",
    cpu_threads=8,
    num_workers=1,
    download_root=model_cache,
)
```

For each utterance, use English transcription, `beam_size=1`,
`condition_on_previous_text=False`, and the VAD filter with
`min_silence_duration_ms=500`. Consume the returned segments generator before
considering transcription complete.

The app should construct this model in a background worker at launch and retain
it. Constructing it from an existing cache took about 2.5 seconds, so lazy
loading on the first dictation click would be noticeable.

## Results

Environment: Windows 11, AMD Threadripper 7970X (32 cores / 64 logical CPUs),
Python 3.12.10, faster-whisper 1.2.1, and CTranslate2 4.8.2. Each value is the
median of three post-warmup runs using CPU int8, English, beam 1, and VAD.

| Model | CPU threads | Median latency | Real-time factor |
| --- | ---: | ---: | ---: |
| `large-v3-turbo` | 8 | 3.286 s | 5.92x |
| `large-v3-turbo` | 16 | 3.581 s | 5.43x |
| `large-v3-turbo` | 32 | 3.716 s | 5.23x |
| `distil-large-v3.5-ct2` | 8 | 4.199 s | 4.63x |
| `distil-large-v3.5-ct2` | 16 | 3.579 s | 5.44x |
| `distil-large-v3.5-ct2` | 32 | 3.334 s | 5.83x |

Turbo at eight threads and Distil v3.5 at 32 threads had similar latency. Turbo
was selected because it reached that result with fewer inference threads, leaving
more headroom for a responsive desktop, not because this single test proves a
universal model-speed ranking.

## Optional NVIDIA acceleration

Violet's public default remains CPU INT8. A locally configured Windows NVIDIA
path uses the same `large-v3-turbo` model with CUDA FP16 only after the user
selects **Use NVIDIA GPU acceleration** and restarts Violet. Violet validates
the locally installed CUDA 12 libraries and runs a short encoder probe during
startup. A failed probe falls back to CPU before a recording can be lost.

On this workstation's RTX 5090, the padded 19.451-second fixture completed in
0.252 seconds median after warm-up, compared with 3.638 seconds for the CPU
configuration rerun during the 0.2.0 upgrade. The output was deterministic in
each mode, but GPU decoding changed one ambiguous phrase in the synthetic
fixture. This is why acceleration is explicitly opt-in rather than a global
quality claim. The current workstation's NVIDIA libraries are stored privately
at `%LOCALAPPDATA%\Violet\runtime\cuda12`; Violet changes only its own process
DLL search path and never edits the system CUDA installation.

The user-run `Install-VioletCudaRuntime.ps1` script pins and SHA-256 verifies
the official NVIDIA CUDA Installer Team
[`nvidia-cublas-cu12` 12.9.2.10](https://pypi.org/project/nvidia-cublas-cu12/12.9.2.10/)
and [`nvidia-cudnn-cu12` 9.28.1.4](https://pypi.org/project/nvidia-cudnn-cu12/9.28.1.4/)
Windows wheels. The exact PyPI files and digests live in the script, rather than
depending on an unpinned CUDA Toolkit or changing the system installation.

## Fixture and limits

`synthetic_fixture.wav` is a 17.451-second English Windows SAPI voice sample;
`synthetic_fixture_padded.wav` adds one second of silence at both ends, resulting
in the 19.451-second fixture used above. This exercises VAD silence removal.

The result strings are stored in `stt_cpu_results.json`. They were stable across
repeats, but synthetic speech cannot establish real-world WER or punctuation
quality. This benchmark does not cover live microphones, accents, background
noise, proper nouns, long dictation, or actual user phrasing.

`silence_check.py` adds a separate all-zero three-second WAV check with the
selected configuration. Its output is recorded in `silence_check.json`; a pass
means VAD returned an empty transcript for that fixture.

Latest run: **pass** — zero segments and an empty transcript in 0.119 seconds.

## Reproduce

From the repository root, after installing `requirements.txt` into `.venv`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmarks\create_synthetic_fixture.ps1
.\.venv\Scripts\python.exe .\benchmarks\benchmark_stt_cpu.py
.\.venv\Scripts\python.exe .\benchmarks\silence_check.py
```

The benchmark downloads only its two candidate CTranslate2 models under
`benchmarks\model-cache`. The completed Turbo cache can be copied intact to the
app's normal model-cache location to avoid a duplicate download.

After running `Install-VioletCudaRuntime.ps1` and downloading Violet's approved
large-v3-turbo model, measure the production CUDA configuration with:

```powershell
.\.venv\Scripts\python.exe .\benchmarks\benchmark_stt_gpu.py
```

It writes `stt_gpu_results.json`, including the backend, post-warmup timings,
and decoded strings. It exits nonzero rather than silently benchmarking CPU if
the optional CUDA path is unavailable.

## Primary sources

- [faster-whisper CPU int8 and VAD documentation](https://github.com/SYSTRAN/faster-whisper)
- [OpenAI Whisper large-v3-turbo model card](https://huggingface.co/openai/whisper-large-v3-turbo)
- [Distil-Whisper large-v3.5 model card and faster-whisper integration](https://huggingface.co/distil-whisper/distil-large-v3.5)
- [CTranslate2 CPU performance guidance](https://github.com/OpenNMT/CTranslate2/blob/master/docs/performance.md)
- [Current Windows NeMo TDT / Parakeet sherpa-onnx issue](https://github.com/k2-fsa/sherpa-onnx/issues/3767)
