# Building the Windows release

Use a Windows x64 machine with Python 3.12, PowerShell, and the Inno Setup 6
compiler. The installer and portable application include their own runtime;
end users do not install Python.

From the repository root:

```powershell
pwsh -NoProfile -File .\packaging\Build-Release.ps1
```

The script uses `C:\VioletBuild` as a neutral staging directory so compiled
code does not carry a developer's personal workspace path. It creates a
dedicated environment, resolves the exact release dependency lock, records
wheel hashes, runs a dependency advisory scan, and freezes a PyInstaller onedir
application. It then checks the frozen runtime, scans the delivered files for
personal paths, and builds the portable ZIP and per-user Inno Setup installer.
Outputs are placed in `dist/`.

`-InstallInnoSetup` can install the official compiler through winget if it is
missing. `-ReuseBuildEnvironment` reuses the tool environment for an incremental
build. Read the script's parameters before using diagnostic skip flags; a build
that skips verification is not a reviewed release.

## Speech dependencies

The distributed app supplies NumPy microphone samples directly to
faster-whisper. SciPy performs band-limited resampling. The release excludes
PyAV and its bundled media codecs.

A narrow, checksum-guarded patch makes the file decoder import optional in
the pinned faster-whisper release. The patch and modified-source notice live
in [packaging](../packaging/MODIFIED_UPSTREAM_SOURCES.md). The app does not
offer audio-file decoding. The developer environment may use unmodified
faster-whisper/PyAV for the separate synthetic-file benchmark.

## Publication checks

Before distributing a build, check the source regressions, app-owned field and
control-panel fixtures, frozen runtime smoke, a real local NumPy transcription,
installer launch/shortcuts, dependency advisory report, codec exclusion, copied
licenses, and source/archive privacy scans. Test data must be synthetic. Do not
package personal caches, settings, recordings, or generated automation logs.

The GitHub workflow builds review artifacts with read-only repository access;
it does not automatically publish a release. Release binaries are unsigned
unless the release notes explicitly state otherwise. Publish SHA-256 checksums
with the final artifacts, along with dependency and license metadata.
