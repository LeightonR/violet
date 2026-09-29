# Frequently asked questions

### Does it need a GPU, subscription, or API key?

No. Violet uses your CPU and a downloaded speech model. No account or
subscription is needed. Model setup needs internet access; dictation afterward
is local.

### Does it run on my version of Windows?

The release targets 64-bit Windows 10 (version 1809 or later) and Windows 11.
It is not a Windows ARM64, Windows 7/8, Linux, or macOS release. Hardware and
accessibility support still vary by computer and application.

### Will it appear beside every input?

It works with editable controls that expose useful Windows accessibility data.
Some custom editors, remote desktops, games, and protected controls do not.
For an unsupported field, use **Ctrl+Alt+Space** to dictate into the recovery
window and copy the result yourself. Known password/read-only fields refuse
dictation. The app never guesses an insertion target.

### Where does the microphone sit?

By default, just left of the active text row. Open the control panel, enable
**Transform position**, drag the microphone into your preferred position, then
uncheck Transform. That relative alignment and gap follow other inputs.
**Reset position** restores the default. During text selection Violet holds
the last valid caret position rather than jumping to the center of the box.

### What does the waveform show?

The meter stays blank while Violet is listening quietly. During detected speech,
nine spaced warm-violet bars reflect the live level. Stateful streaming voice
detection smooths ordinary pauses between syllables; silence and rejected noise
clear the bars. Its motion follows elapsed time and the platform's display-update
cadence where available, so it does not promise one fixed frame rate. Voice
detection is probabilistic, so music, another nearby voice, or unusual noise may
still be classified as speech. The meter is a display, not an audio compressor.

### Does it add punctuation or learn my style?

Whisper predicts capitalization and punctuation. Results still need review,
especially names, jargon, numbers, and noisy speech. This version does not
learn your vocabulary or punctuation preferences and does not keep training
history. The initial release is configured for English dictation.

### Why isn't transcription instantaneous?

Violet records until you click the square Stop control, then transcribes the
utterance. The X discards the current recording or pending result instead.
Smaller models finish faster on modest CPUs; larger models can improve accuracy
but need more memory and compute. CPU detection provides a practical starting
point, not a guarantee that one model is best for every speaker or workload.

### How do I stop it using resources?

Uncheck **Show the microphone beside editable fields** to pause the widget,
polling, and recording. The model stays loaded for a quick resume. Choose
**Quit Violet / release resources** to exit and release CPU/RAM resources.
Uncheck **Start Violet when I sign in** to disable automatic startup.

### Can I pin it to my taskbar?

Open Violet's desktop or Start-menu shortcut, then use Windows' taskbar pin
command. Opening it again shows the existing control panel rather than
starting another transcription process.

### Why wasn't my text inserted?

If focus or the target changes, Violet shows the transcript in its recovery
window. Use **Copy** and paste where you intended. A normal user process also
cannot type into an administrator application. Violet never submits the text
with Enter.

### Why is Windows showing an unknown-publisher warning?

The first release is unsigned. Check that you downloaded it from the project's
GitHub Releases page and compare its published SHA-256. Do not disable Windows
security globally. See [Security](../SECURITY.md).

### Is my microphone working?

Select your default input in Windows **Settings → System → Sound**, and allow
desktop microphone access in Windows privacy settings. Other apps may hold a
device exclusively. Violet records only after you start a dictation session.

### How do I remove it?

Quit Violet, then uninstall it through Windows Installed apps. Models and
preferences may be retained for a later installation. To remove those too,
delete `%LOCALAPPDATA%\Violet` after exiting the app. That folder contains
settings and model data, not an intentional recording/transcript history.
