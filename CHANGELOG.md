# Changelog

## 0.1.1 — Voice-driven motion

- Adds an X that discards an active recording or pending result; the separate
  Stop control still finishes dictation.
- Shows nine small rounded dots in silence, growing into thicker, taller
  warm-violet bars for detected speech. Increased visual sensitivity keeps quiet
  speech visible while preserving headroom for louder syllables.
- Adds frame-paced, elapsed-time motion and a brief spring transition as the
  microphone bubble expands and contracts.

## 0.1.0 — First public beta

- CPU-only local English dictation with punctuation and capitalization.
- Automatic hardware-based model recommendation and explicit model setup.
- Verified model snapshots with pinned revisions and SHA-256 hashes.
- Warm purple microphone beside the active text row, plus a bounded speech waveform.
- Transform mode to save relative widget placement across supported inputs.
- Tray controls, desktop shortcut, taskbar identity, and startup at sign-in.
- Per-user Windows installer and portable build.
- Protected-field checks and recovery Copy when an insertion target changes.

Interaction fixes included before the first release:

- Stop repeatedly raising the overlay during field polling.
- Keep the widget fixed during mouse selection and transient caret loss.
- Require a click to begin on the widget before it can start recording.
- Preserve placement through Stop/transcribing transitions.
- Smooth syllable gaps with voice-activity hysteresis while clearing silence.

Known limits: Windows 10/11 x64 only, English by default, variable support for
custom accessibility controls, heuristic model selection, and unsigned release
binaries. This version does not learn personal vocabulary or punctuation style.
