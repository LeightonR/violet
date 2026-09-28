# Security

Violet is an early desktop utility, not a security boundary. No software can
honestly promise to be fully secure. This repository documents the safeguards
and known limitations so they can be reviewed.

## Supported release

The current Windows 10/11 x64 release is the only supported platform. Run Violet
as your normal user. It does not need administrator access. Windows integrity
rules prevent a normal process from injecting text into elevated applications.

## Safeguards

- Recording starts only after an explicit microphone click or registered hotkey.
- The app examines accessibility metadata and geometry, not existing field text.
- Password and read-only controls are refused when identified by their provider.
- Insertion checks the original target identity and refuses a changed target.
- Text injection uses Unicode input without pressing Enter or taking over the
  clipboard. If the target cannot be verified, a recovery window offers Copy.
- The overlay does not activate or repeatedly raise itself while you edit.
  Pointer presses that began outside the overlay cannot start dictation.
- No server, telemetry collector, embedded credential, or auto-update executor
  is part of the app. Models are data, not remote Python plugins.

Target checks reduce accidental insertion but cannot make Windows input
injection atomic: another program can change focus between a check and the OS
receiving input. Accessibility providers and the local machine are trusted.
Check important dictated text before submitting it.

## Downloads and verification

Download installers from this repository's Releases page. Compare the file's
SHA-256 with `SHA256SUMS.txt` from the same release. Hashes detect a corrupted or
different file; they are not an independent publisher signature. Releases are
unsigned unless the release notes explicitly identify a signing certificate.
Windows may warn about an unknown publisher.

Runtime and model provenance are included with the build. Dependency scanning
and regression tests help find known problems; they do not constitute an
independent penetration test or guarantee the absence of vulnerabilities.

## Reporting a problem

For a suspected vulnerability, use **Security → Report a vulnerability** if
private reporting is enabled on the GitHub repository. If that option is not
available, open a minimal issue requesting a private contact channel, without
exploit details, credentials, recordings, transcripts, or personal information.

Ordinary bugs can be filed as GitHub issues. Include the Violet version,
Windows version, application name, and steps using dummy text. Do not attach
private conversations or model caches.
