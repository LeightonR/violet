[CmdletBinding()]
param(
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSCommandPath
$venvDir = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$requirementsPath = Join-Path $projectRoot 'requirements.txt'
$requirementsStamp = Join-Path $venvDir '.violet-requirements.sha256'

if (-not (Test-Path -LiteralPath $requirementsPath)) {
    throw "Violet's requirements file was not found: $requirementsPath"
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating Violet virtual environment...'
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -m venv $venvDir
    }
    elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv $venvDir
    }
    else {
        throw 'Python 3 was not found. Install Python 3, then run Setup-Violet.ps1 again.'
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the Violet virtual environment.'
    }
}

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$needsInstall = $true
if (Test-Path -LiteralPath $requirementsStamp) {
    $savedHash = (Get-Content -LiteralPath $requirementsStamp -Raw).Trim()
    if ($savedHash -eq $requirementsHash) {
        $needsInstall = $false
        try {
            & $venvPython -c 'import PySide6, faster_whisper, sounddevice, uiautomation, win32api'
            if ($LASTEXITCODE -ne 0) {
                $needsInstall = $true
            }
        }
        catch {
            $needsInstall = $true
        }
    }
}

if ($needsInstall) {
    Write-Host 'Installing Violet pinned local dependencies...'
    & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Violet dependency installation did not complete.'
    }
    Set-Content -LiteralPath $requirementsStamp -Value $requirementsHash -Encoding ascii -NoNewline
}

$nativeLauncher = Join-Path $projectRoot 'Violet.exe'
$launcherSource = Join-Path $projectRoot 'VioletLauncher.cs'
$launcherIcon = Join-Path $projectRoot 'Violet.ico'
$launcherBuildScript = Join-Path $projectRoot 'Build-Violet-Launcher.ps1'
if (-not (Test-Path -LiteralPath $launcherSource) -or -not (Test-Path -LiteralPath $launcherIcon)) {
    throw 'Violet launcher source or icon is missing.'
}
$needsLauncherBuild = -not (Test-Path -LiteralPath $nativeLauncher)
if (-not $needsLauncherBuild) {
    $launcherTime = (Get-Item -LiteralPath $nativeLauncher).LastWriteTimeUtc
    $needsLauncherBuild = $null -ne (@($launcherSource, $launcherIcon, $launcherBuildScript) |
        Where-Object { (Get-Item -LiteralPath $_).LastWriteTimeUtc -gt $launcherTime } |
        Select-Object -First 1)
}
if ($needsLauncherBuild) {
    Write-Host 'Building Violet native launcher...'
    & $launcherBuildScript
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $nativeLauncher)) {
        throw 'Violet native launcher could not be built.'
    }
}

# Reuse the completed benchmark cache if it is present. Copying preserves the
# benchmark cache and avoids a second model download on this workstation.
$sourceCache = Join-Path $projectRoot 'benchmarks\model-cache'
$modelCache = Join-Path $env:LOCALAPPDATA 'Violet\models'
$turboCacheName = 'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo'
$targetTurboCache = Join-Path $modelCache $turboCacheName
if ((Test-Path -LiteralPath $sourceCache) -and -not (Test-Path -LiteralPath $targetTurboCache)) {
    Write-Host 'Copying the local speech-model cache for Violet...'
    New-Item -ItemType Directory -Path $modelCache -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceCache -Force | ForEach-Object {
        $target = Join-Path $modelCache $_.Name
        if (-not (Test-Path -LiteralPath $target)) {
            Copy-Item -LiteralPath $_.FullName -Destination $modelCache -Recurse -Force
        }
    }
}

if (-not $NoShortcut) {
    try {
        & (Join-Path $projectRoot 'Create-Violet-Shortcut.ps1')
    }
    catch {
        throw 'Violet was set up, but its Desktop shortcut could not be created.'
    }
}

Write-Host 'Violet is ready. Use the Desktop Violet shortcut to open its control panel; it starts in the background when you sign in.'
