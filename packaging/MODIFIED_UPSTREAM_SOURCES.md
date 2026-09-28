# Modified upstream source: faster-whisper 1.2.1

Violet modifies one file in the MIT-licensed `faster-whisper` 1.2.1 wheel at
release-build time. The unmodified `faster_whisper/audio.py` SHA-256 is:

```
60a1d8638f718cbf6d245aed3e5a5aa61c1f822a0b0fe9b48a7c928d47c23909
```

The patch removes its module-level PyAV import and imports PyAV only when
`decode_audio()` is asked to decode a file. Violet supplies in-memory NumPy
microphone samples to `WhisperModel.transcribe()` and does not offer file
decoding. If another caller requests file decoding in this codec-free build,
it receives a clear runtime error instead of downloading or loading codecs.
The helper used for file-frame grouping follows the same lazy path, so no
global PyAV symbol remains in the codec-free NumPy transcription path.

The patch also removes the `av>=11` requirement from the installed wheel
metadata, after verifying the unmodified metadata SHA-256:

```
b85622132a43897972924db98e024c0d40dcec19c577b1ee5640f1bc1b59aada
```

`apply_faster_whisper_codec_patch.py` fails closed on a version or checksum
mismatch. The original faster-whisper MIT license is copied into the release
licenses directory.
