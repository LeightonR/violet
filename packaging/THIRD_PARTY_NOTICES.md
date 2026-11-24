# Third-party notices for Violet 0.2.1

The portable ZIP and installer contain the listed Python/Qt runtime components.
The release build also emits `SBOM.spdx.json` and `dependency-manifest.json` with
the exact package versions and wheel SHA-256 values used for that build.

| Component | License declared by upstream | Source |
| --- | --- | --- |
| Qt for Python / PySide6 and Qt runtime | LGPL-3.0-only or GPL-3.0-only | https://www.qt.io/licensing/ |
| faster-whisper | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | MIT | https://github.com/OpenNMT/CTranslate2 |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| SciPy | BSD-3-Clause | https://scipy.org/ |
| python-sounddevice | MIT | https://github.com/spatialaudio/python-sounddevice |
| uiautomation | Apache-2.0 | https://github.com/yinkaisheng/Python-UIAutomation-for-Windows |
| pywin32 | Python Software Foundation License | https://github.com/mhammond/pywin32 |
| Hugging Face Hub | Apache-2.0 | https://github.com/huggingface/huggingface_hub |
| OpenAI Whisper model weights | MIT | https://huggingface.co/openai/whisper-large-v3-turbo |

Violet keeps Qt dynamically linked in its onedir release. Qt's LGPL terms,
including the ability to replace the dynamically linked Qt libraries, apply to
the Qt components. Qt source is available from
https://code.qt.io/cgit/qt/qtbase.git/ and the PySide binding source is
available from https://code.qt.io/cgit/pyside/pyside-setup.git/; the exact
runtime versions are recorded in `SBOM.spdx.json`. See the upstream licensing
material above for complete license texts and obligations. The build copies
license files supplied by installed distributions into the `licenses`
directory when present. The `licenses` directory also includes the LGPL-3.0,
GPL-3.0, and Python-2.0 license texts used by the dynamically linked Qt and
embedded CPython runtime.

`faster-whisper` 1.2.1 is included with the narrow codec-free patch described
in `MODIFIED_UPSTREAM_SOURCES.md`. The patch defers its optional PyAV import to
file decoding, which Violet does not expose; the distribution contains no
PyAV, FFmpeg, x264, or x265 binaries.

The optional `Install-VioletCudaRuntime.ps1` is not part of Violet's default
runtime. Only after a person runs it explicitly does it download the
NVIDIA CUDA Installer Team's SHA-256-pinned `nvidia-cublas-cu12` 12.9.2.10 and
`nvidia-cudnn-cu12` 9.28.1.4 Windows wheels from PyPI. It copies their supplied
licenses into that person's private `%LOCALAPPDATA%\Violet\runtime\cuda12`
directory. The installer source records the exact upstream file URLs and hashes.

Model weights are not bundled with Violet.  When a person explicitly approves
a model download, Violet only accepts the fixed files and SHA-256 values in its
allowlist; the chosen model's upstream license still applies.
