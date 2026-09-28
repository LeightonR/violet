# Privacy

Violet transcribes on your Windows computer. There is no Violet account,
analytics service, cloud transcription endpoint, or transcript history.

## Audio and text

The microphone opens when you explicitly start dictation with the floating
button or hotkey. Audio is held in process memory and released after use.
Transcripts are held in memory until insertion or recovery. The **Copy** button
in recovery writes that transcript to the Windows clipboard only when you
choose it. Windows clipboard history or clipboard sync may then retain it,
depending on your Windows settings.

Violet does not intentionally save recordings or dictated text to disk. This
does not prevent Windows paging, crash dumps, or other software on your computer
from retaining process memory. Text inserted into another application is
subject to that application's privacy practices.

## Accessibility

While enabled, Violet checks the foreground window and focused Windows UI
Automation control to find an editable field. It examines control identity,
editable/password/read-only properties, selection geometry, and caret bounds.
It does not fetch the field's text contents, keep a keystroke log, or register
a general keyboard hook. It registers only its dictation hotkey.

Password and read-only fields are excluded when their accessibility provider
identifies them correctly. An application with an incorrect accessibility
implementation can limit these protections. Pause Violet in sensitive contexts
where that application's behavior is uncertain.

## Network and local files

Initial model setup downloads selected model files from Hugging Face and its
file delivery infrastructure. Those services receive ordinary network metadata
such as your IP address. Your audio, transcript, field contents, and detected
hardware profile are not uploaded. After setup, transcription runs offline.

Settings, relative widget placement, and model weights live under
`%LOCALAPPDATA%\Violet`. Installed application files live separately under
`%LOCALAPPDATA%\Programs\Violet`. A per-user startup shortcut is created when
startup is enabled. There is no administrator-level service or scheduled job.

Turning off **Show the microphone beside editable fields** pauses field polling,
recording, and pending insertion. **Quit Violet / release resources** exits the
process and releases the resident model. Model files remain available for the
next launch.
