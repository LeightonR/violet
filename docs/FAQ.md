# Frequently asked questions

### Does it need a GPU, subscription, or API key?

No. Violet uses your CPU and a downloaded speech model. No account or
subscription is needed. Model setup needs internet access; dictation afterward
is local.

### How do I use NVIDIA GPU acceleration?

Violet uses the CPU by default. On a Windows PC configured with Violet's
private CUDA 12 runtime, quit Violet and run the included installer as the same
Windows user:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\Violet\Install-VioletCudaRuntime.ps1"
```

For the portable ZIP, run its `Install-VioletCudaRuntime.ps1` from the extracted
Violet folder instead.

That command changes neither the system CUDA installation nor the persistent
PowerShell execution policy. It downloads about 1.17 GiB and installs about
1.86 GiB only at `%LOCALAPPDATA%\Violet\runtime\cuda12`. The script pins and
SHA-256-verifies NVIDIA CUDA Installer Team's
[`nvidia-cublas-cu12` 12.9.2.10](https://pypi.org/project/nvidia-cublas-cu12/12.9.2.10/)
and [`nvidia-cudnn-cu12` 9.28.1.4](https://pypi.org/project/nvidia-cudnn-cu12/9.28.1.4/)
Windows wheels before extracting their DLLs and licenses. Its source contains
the exact PyPI file URLs and hashes; no Python or CUDA Toolkit installation is
needed.

Then open the control panel, select **Use NVIDIA GPU acceleration**, then quit
and reopen Violet. The preference applies only to Violet. If CUDA cannot
complete Violet's startup check, it falls back to the local CPU engine
automatically.

GPU decoding is much faster on supported hardware, and can produce a slightly
different recognition result from CPU decoding. Uncheck **Use NVIDIA GPU
acceleration** and restart Violet to return to CPU decoding.

### Does it run on my version of Windows?

The release targets 64-bit Windows 10 (version 1809 or later) and Windows 11.
The Windows installer requires x64. Linux and macOS have a separate source preview with a recording window and a Copy transcript button. See the [installation instructions](../README.md#macos-and-linux-source-preview).

### Will it appear beside every input?

It works with editable controls that expose useful Windows accessibility data.
Some custom editors, remote desktops, games, and protected controls do not.
For an unsupported field, use **Ctrl+Alt+Space** to dictate into the recovery
window and copy the result yourself. Known password/read-only fields refuse
dictation. The app never guesses an insertion target.

### Where does the microphone sit?

By default, just left of the active text row. Open the control panel, enable
**Allow dragging**, drag the microphone into your preferred position, then
turn Allow dragging off. That relative alignment and gap follow other inputs.
**Reset position** restores the default. During text selection Violet holds
the last valid caret position rather than jumping to the center of the box.

### What does the waveform show?

The meter shows nine rounded square dots while Violet is listening quietly.
During detected speech, thicker warm-violet bars reflect the live level, with
enough visual sensitivity for ordinary speech to fill most of their height.
Stateful streaming voice detection smooths ordinary pauses between syllables;
silence and rejected noise return to the static dots. Its motion follows elapsed
time and the platform's display-update cadence where available, so it does not
promise one fixed frame rate. Voice
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

Finish with the **square Stop button on the right**, or **Ctrl+Alt+Space**.
The **X on the left cancels and discards** the recording or pending result.
The waveform area is only a meter; clicking its center does not finish dictation.

If focus or the target changes, or an unexpected insertion error occurs, Violet
keeps the completed transcript in its recovery window. Check the field, then use
**Copy** and paste where you intended. A normal user process also
cannot type into an administrator application. Violet never submits the text
with Enter.

### How do I update without downloading my model again?

Quit Violet, then run [the current Windows installer](https://github.com/LeightonR/violet/releases/download/v0.2.1/Violet-0.2.1-win64-setup.exe).
It replaces the application while preserving settings, saved placement, and the
model cache. For the portable edition, extract the new ZIP into its own folder;
do not mix files from different versions. Run only one copy at a time.

### Why is Windows showing an unknown-publisher warning?

The current release is unsigned. Check that you downloaded it from the project's
[GitHub Releases page](https://github.com/LeightonR/violet/releases/tag/v0.2.1)
and compare its published SHA-256. Do not disable Windows
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

### Where can I report a bug or share Violet?

Use [GitHub Issues](https://github.com/LeightonR/violet/issues) for ordinary bugs.
Include your version, application name, and steps using dummy text. Keep private
recordings, transcripts, and screenshots out of public reports. Security concerns
belong in the [private reporting channel](https://github.com/LeightonR/violet/security/advisories/new).

Share the [official release](https://github.com/LeightonR/violet/releases/tag/v0.2.1)
or use the [Violet launch card](images/violet-011-launch.png). Violet is an
independent project; it is not affiliated with OpenAI or Microsoft.
