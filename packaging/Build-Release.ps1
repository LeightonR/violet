[CmdletBinding()]
param(
    [string]$BuildRoot = 'C:\VioletBuild',
    [switch]$ReuseBuildEnvironment,
    [switch]$PrepareOnly,
    [switch]$InstallInnoSetup,
    [switch]$SkipInstaller,
    [switch]$SkipFrozenSmoke,
    [switch]$RequireLocalModelSmoke
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT = '1'
$env:PYTHONNOUSERSITE = '1'
# ZIP-based console-script generation rejects pre-1980 timestamps.  This is
# still a stable build timestamp while remaining compatible with pip/Python.
$env:SOURCE_DATE_EPOCH = '315532800'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:HF_HUB_DISABLE_IMPLICIT_TOKEN = '1'
$env:DO_NOT_TRACK = '1'

if ($RequireLocalModelSmoke -and $SkipFrozenSmoke) {
    throw 'RequireLocalModelSmoke cannot be combined with SkipFrozenSmoke.'
}

$projectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$buildRootFull = [IO.Path]::GetFullPath($BuildRoot).TrimEnd('\\')
if ($buildRootFull -match '(?i)\\users\\' -or $buildRootFull -notmatch '^[A-Za-z]:\\') {
    throw 'BuildRoot must be an absolute non-profile Windows path (for example C:\VioletBuild).'
}
if ($buildRootFull -eq ([IO.Path]::GetPathRoot($buildRootFull).TrimEnd('\\'))) {
    throw 'BuildRoot cannot be a drive root.'
}

function Invoke-Checked {
    param([Parameter(Mandatory)][scriptblock]$Action, [Parameter(Mandatory)][string]$Description)
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Find-Iscc {
    $candidates = @(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    return $candidates | Select-Object -First 1
}

function Copy-ReleaseSource {
    param([Parameter(Mandatory)][string]$Destination)
    # Destination is always the marked build root's disposable source snapshot.
    # Clearing it avoids carrying stale deleted source files into a later freeze.
    if (Test-Path -LiteralPath $Destination) {
        Get-ChildItem -LiteralPath $Destination -Force | Remove-Item -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($directory in @('violet', 'packaging')) {
        $origin = Join-Path $projectRoot $directory
        $destinationDirectory = Join-Path $Destination $directory
        Get-ChildItem -LiteralPath $origin -Recurse -File | Where-Object {
            $_.Extension -notin @('.pyc', '.pyo') -and $_.FullName -notmatch '(?i)\\__pycache__\\'
        } | ForEach-Object {
            $relative = $_.FullName.Substring($origin.Length).TrimStart('\\')
            $target = Join-Path $destinationDirectory $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
    foreach ($file in @('Violet.ico', 'LICENSE', 'pyproject.toml')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $Destination -Force
    }
}

function Assert-CodecFreeBundle {
    param([Parameter(Mandatory)][string]$Bundle)
    $forbidden = @('ffmpeg', 'x264', 'x265', 'avcodec', 'avformat', 'avfilter', 'avdevice', 'avutil', 'swresample', 'swscale')
    $bad = Get-ChildItem -LiteralPath $Bundle -Recurse -File | Where-Object {
        $relative = $_.FullName.Substring($Bundle.Length).TrimStart('\\').Replace('\\', '/').ToLowerInvariant()
        $segments = $relative.Split('/')
        ($segments -contains 'av') -or ($segments -contains 'av.libs') -or
        (@($forbidden | Where-Object { $relative.Contains($_) }).Count -gt 0)
    }
    if ($bad) {
        throw ('Codec-free release contains forbidden PyAV/FFmpeg files: ' + (($bad | Select-Object -ExpandProperty FullName) -join '; '))
    }
}

function Remove-IncompatibleBundledIcu {
    param([Parameter(Mandatory)][string]$Bundle)

    # PyInstaller can collect ICU 78 from an unrelated native dependency at
    # _internal\icu*.dll. Qt 6.11 imports the Windows ICU ABI on our supported
    # Windows 10 1809+ floor; the bundled ICU uses incompatible versioned
    # exports and makes Qt fail before Python can report the error.
    $internal = Join-Path $Bundle '_internal'
    foreach ($name in @('icuuc.dll', 'icudt78.dll')) {
        $candidate = Join-Path $internal $name
        if (Test-Path -LiteralPath $candidate) {
            Remove-Item -LiteralPath $candidate -Force
        }
        if (Test-Path -LiteralPath $candidate) {
            throw "Incompatible bundled ICU runtime remains: $candidate"
        }
    }
}

if ($projectRoot.StartsWith("$buildRootFull\\", [System.StringComparison]::OrdinalIgnoreCase) -or
    $buildRootFull.StartsWith("$projectRoot\\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'BuildRoot must not contain the source checkout or be inside it.'
}
New-Item -ItemType Directory -Path $buildRootFull -Force | Out-Null
$buildMarker = Join-Path $buildRootFull '.violet-build-root'
if (-not (Test-Path -LiteralPath $buildMarker)) {
    $existing = @(Get-ChildItem -LiteralPath $buildRootFull -Force)
    if ($existing.Count -gt 0) {
        throw "Refusing to clean an unmarked BuildRoot: $buildRootFull"
    }
    Set-Content -LiteralPath $buildMarker -Value 'Violet build root; safe for Build-Release.ps1 to clean.' -Encoding ascii
}
if (-not $ReuseBuildEnvironment) {
    Get-ChildItem -LiteralPath $buildRootFull -Force | Where-Object { $_.FullName -ne $buildMarker } |
        Remove-Item -Recurse -Force
}

$sourceRoot = Join-Path $buildRootFull 'source'
$venvRoot = Join-Path $buildRootFull '.build-venv'
$wheelhouse = Join-Path $buildRootFull 'wheelhouse'
$pyInstallerRoot = Join-Path $buildRootFull 'pyinstaller'
$auditRoot = Join-Path $buildRootFull 'audit'
$runtimeLock = Join-Path $projectRoot 'packaging\requirements-runtime.lock'
$buildRequirements = Join-Path $projectRoot 'packaging\requirements-build.in'
$metadataScript = Join-Path $projectRoot 'packaging\generate_release_metadata.py'
$privacyScript = Join-Path $projectRoot 'packaging\Test-ReleasePrivacy.ps1'
$releaseRoot = Join-Path $projectRoot 'dist'
$bundleRoot = Join-Path $releaseRoot 'Violet'

$pythonLauncher = (Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
if (-not $pythonLauncher) {
    throw 'Python Launcher (py.exe) with Python 3.12 is required for a release build.'
}
$buildPython = Join-Path $venvRoot 'Scripts\python.exe'
if (-not $ReuseBuildEnvironment -or -not (Test-Path -LiteralPath $buildPython)) {
    Invoke-Checked -Description 'Creating dedicated Python 3.12 build environment' -Action {
        & $pythonLauncher -3.12 -m venv $venvRoot
    }
}
Invoke-Checked -Description 'Pinning build pip' -Action {
    & $buildPython -m pip install --disable-pip-version-check --upgrade 'pip==26.2.1'
}

New-Item -ItemType Directory -Path $wheelhouse, $auditRoot -Force | Out-Null
if (-not $ReuseBuildEnvironment -or -not (Get-ChildItem -LiteralPath $wheelhouse -Filter '*.whl' -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    Invoke-Checked -Description 'Downloading locked release wheels' -Action {
        & $buildPython -m pip download --disable-pip-version-check --only-binary=:all: --no-deps --dest $wheelhouse -r $runtimeLock
        if ($LASTEXITCODE -ne 0) { throw 'Locked runtime wheel download failed.' }
        & $buildPython -m pip download --disable-pip-version-check --only-binary=:all: --dest $wheelhouse -r $buildRequirements
    }
}
Invoke-Checked -Description 'Writing dependency SHA-256 manifest' -Action {
    & $buildPython $metadataScript wheel-manifest --wheelhouse $wheelhouse --output (Join-Path $auditRoot 'dependency-manifest.json')
}
Invoke-Checked -Description 'Installing from the local wheelhouse' -Action {
    & $buildPython -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse --no-deps -r $runtimeLock
    if ($LASTEXITCODE -ne 0) { throw 'Locked runtime wheel installation failed.' }
    & $buildPython -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse -r $buildRequirements
}
Invoke-Checked -Description 'Applying checked codec-free faster-whisper patch' -Action {
    & $buildPython (Join-Path $projectRoot 'packaging\apply_faster_whisper_codec_patch.py') --site-packages (Join-Path $venvRoot 'Lib\site-packages')
}
Invoke-Checked -Description 'Checking locked release dependencies' -Action {
    & $buildPython -m pip check
}

$auditReport = Join-Path $auditRoot 'pip-audit.json'
& $buildPython -m pip_audit -r $runtimeLock --format json --output $auditReport
if ($LASTEXITCODE -ne 0) {
    throw "pip-audit found a vulnerability or could not complete. See $auditReport."
}

if ($PrepareOnly) {
    Write-Host "Release environment prepared and audited: $buildRootFull"
    return
}

Copy-ReleaseSource -Destination $sourceRoot

$pyInstallerDist = Join-Path $pyInstallerRoot 'dist'
$pyInstallerWork = Join-Path $pyInstallerRoot 'work'
$pyInstallerSpec = Join-Path $pyInstallerRoot 'spec'
New-Item -ItemType Directory -Path $pyInstallerDist, $pyInstallerWork, $pyInstallerSpec -Force | Out-Null
$entry = Join-Path $sourceRoot 'packaging\violet_entry.py'
$icon = Join-Path $sourceRoot 'Violet.ico'
$versionInfo = Join-Path $sourceRoot 'packaging\windows-version.txt'
$pyInstallerArguments = @(
    '--noconfirm', '--clean', '--onedir', '--windowed', '--name', 'Violet',
    '--paths', $sourceRoot,
    '--icon', $icon,
    '--version-file', $versionInfo,
    '--distpath', $pyInstallerDist,
    '--workpath', $pyInstallerWork,
    '--specpath', $pyInstallerSpec,
    '--collect-all', 'faster_whisper',
    '--collect-all', 'ctranslate2',
    '--collect-all', 'onnxruntime',
    '--collect-all', 'tokenizers',
    '--collect-all', 'huggingface_hub',
    '--collect-all', 'uiautomation',
    '--collect-all', 'comtypes',
    '--exclude-module', 'av',
    '--hidden-import', 'pythoncom',
    '--hidden-import', 'pywintypes',
    '--hidden-import', 'win32timezone',
    $entry
)
Invoke-Checked -Description 'Building the PyInstaller onedir executable' -Action {
    # Build from the neutral snapshot, not the developer checkout.  PyInstaller
    # puts its working directory on sys.path, so this also keeps source paths
    # out of the frozen import graph.
    Push-Location -LiteralPath $sourceRoot
    try {
        & $buildPython -m PyInstaller @pyInstallerArguments
    }
    finally {
        Pop-Location
    }
}

$builtBundle = Join-Path $pyInstallerDist 'Violet'
if (-not (Test-Path -LiteralPath (Join-Path $builtBundle 'Violet.exe'))) {
    throw 'PyInstaller did not produce Violet.exe.'
}
Remove-IncompatibleBundledIcu -Bundle $builtBundle
if (Test-Path -LiteralPath $releaseRoot) {
    Remove-Item -LiteralPath $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
Copy-Item -LiteralPath $builtBundle -Destination $bundleRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'packaging\THIRD_PARTY_NOTICES.md') -Destination (Join-Path $bundleRoot 'THIRD_PARTY_NOTICES.md') -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'packaging\MODIFIED_UPSTREAM_SOURCES.md') -Destination (Join-Path $bundleRoot 'MODIFIED_UPSTREAM_SOURCES.md') -Force
Copy-Item -LiteralPath (Join-Path $auditRoot 'dependency-manifest.json') -Destination (Join-Path $bundleRoot 'dependency-manifest.json') -Force
Copy-Item -LiteralPath $auditReport -Destination (Join-Path $bundleRoot 'pip-audit.json') -Force
Copy-Item -LiteralPath (Join-Path $sourceRoot 'LICENSE') -Destination (Join-Path $bundleRoot 'LICENSE') -Force
Invoke-Checked -Description 'Writing runtime SBOM and copied license files' -Action {
    & $buildPython $metadataScript sbom --lock $runtimeLock --output (Join-Path $bundleRoot 'SBOM.spdx.json') --licenses-dir (Join-Path $bundleRoot 'licenses')
}
Copy-Item -Path (Join-Path $sourceRoot 'packaging\licenses\*') -Destination (Join-Path $bundleRoot 'licenses') -Recurse -Force
Assert-CodecFreeBundle -Bundle $bundleRoot

if (-not $SkipFrozenSmoke) {
    $smokeResult = Join-Path $auditRoot 'frozen-smoke.json'
    $localModelRoot = Join-Path $env:LOCALAPPDATA 'Violet\models'
    $turboRevision = '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf'
    $approvedTurbo = Join-Path $localModelRoot ("approved\large_v3_turbo\" + $turboRevision)
    $legacyTurbo = Join-Path $localModelRoot ("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo\snapshots\" + $turboRevision)
    $fixtureWav = Join-Path $projectRoot 'benchmarks\synthetic_fixture.wav'
    $localModelForSmoke = if (Test-Path -LiteralPath $approvedTurbo) { $approvedTurbo } elseif (Test-Path -LiteralPath $legacyTurbo) { $legacyTurbo } else { $null }
    Remove-Item -LiteralPath $smokeResult -Force -ErrorAction SilentlyContinue
    try {
        if ($localModelForSmoke -and (Test-Path -LiteralPath $fixtureWav)) {
            $env:VIOLET_SMOKE_MODEL_PATH = $localModelForSmoke
            $env:VIOLET_SMOKE_AUDIO_PATH = $fixtureWav
        }
        elseif ($RequireLocalModelSmoke) {
            throw 'A verified local large-v3-turbo cache and synthetic fixture are required for this release smoke gate.'
        }
        $env:VIOLET_SMOKE_RESULT = $smokeResult
        # A windowed executable returns control to PowerShell immediately.  Use
        # its Process handle and a bounded wait so a broken native import cannot
        # leave a release build waiting for an invisible GUI indefinitely.
        $smokeProcess = Start-Process -FilePath (Join-Path $bundleRoot 'Violet.exe') -ArgumentList @('--packaging-smoke') -WindowStyle Hidden -PassThru
        if (-not $smokeProcess.WaitForExit(120000)) {
            Stop-Process -Id $smokeProcess.Id -Force -ErrorAction SilentlyContinue
            throw 'The frozen runtime smoke check timed out after 120 seconds.'
        }
        $smokeExit = $smokeProcess.ExitCode
    }
    finally {
        Remove-Item Env:VIOLET_SMOKE_RESULT -ErrorAction SilentlyContinue
        Remove-Item Env:VIOLET_SMOKE_MODEL_PATH -ErrorAction SilentlyContinue
        Remove-Item Env:VIOLET_SMOKE_AUDIO_PATH -ErrorAction SilentlyContinue
    }
    if ($smokeExit -ne 0 -or -not (Test-Path -LiteralPath $smokeResult)) {
        throw 'The frozen runtime smoke check did not complete.'
    }
    $smoke = Get-Content -LiteralPath $smokeResult -Raw | ConvertFrom-Json
    if ($smoke.error) {
        throw "The frozen runtime smoke check failed: $($smoke.error)"
    }
    if ($RequireLocalModelSmoke -and -not $smoke.local_numpy_inference_completed) {
        throw 'The required frozen local NumPy inference smoke did not complete.'
    }
}

& $privacyScript -PathToScan @($bundleRoot) -ProjectRoot $projectRoot -Python $buildPython
if ($LASTEXITCODE -ne 0) {
    throw 'Release privacy scan failed before archive creation.'
}

$portableZip = Join-Path $releaseRoot 'Violet-0.1.1-win64-portable.zip'
Compress-Archive -Path (Join-Path $bundleRoot '*') -DestinationPath $portableZip -CompressionLevel Optimal -Force

if (-not $SkipInstaller) {
    $iscc = Find-Iscc
    if (-not $iscc -and $InstallInnoSetup) {
        Invoke-Checked -Description 'Installing Inno Setup from winget' -Action {
            & winget.exe install --id JRSoftware.InnoSetup --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
        }
        $iscc = Find-Iscc
    }
    if (-not $iscc) {
        throw 'Inno Setup 6 was not found. Re-run with -InstallInnoSetup or use -SkipInstaller for a portable-only build.'
    }
    $iss = Join-Path $sourceRoot 'packaging\Violet.iss'
    Invoke-Checked -Description 'Compiling the per-user Inno Setup installer' -Action {
        & $iscc "/DBundleDir=$bundleRoot" "/DOutputDir=$releaseRoot" "/DSourceRoot=$sourceRoot" $iss
    }
}

Invoke-Checked -Description 'Writing release file SHA-256 manifest' -Action {
    & $buildPython $metadataScript artifact-manifest --directory $releaseRoot --output (Join-Path $releaseRoot 'artifact-manifest.json')
}
$sumTargets = @($portableZip, (Join-Path $bundleRoot 'Violet.exe')) | Where-Object { Test-Path -LiteralPath $_ }
$installer = Join-Path $releaseRoot 'Violet-0.1.1-win64-setup.exe'
if (Test-Path -LiteralPath $installer) { $sumTargets += $installer }
$sums = foreach ($target in $sumTargets) {
    $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    $relative = $target.Substring($releaseRoot.Length).TrimStart('\\').Replace('\\', '/')
    "$hash *$relative"
}
Set-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt') -Value $sums -Encoding ascii

$portableExtracted = Join-Path $buildRootFull 'portable-extracted'
Expand-Archive -LiteralPath $portableZip -DestinationPath $portableExtracted -Force
& $privacyScript -PathToScan @($bundleRoot, $portableExtracted) -ProjectRoot $projectRoot -Python $buildPython
if ($LASTEXITCODE -ne 0) {
    throw 'Release privacy scan failed after archive creation.'
}

Write-Host "Release build complete: $releaseRoot"
Get-ChildItem -LiteralPath $releaseRoot -Force | Select-Object Name, Length
