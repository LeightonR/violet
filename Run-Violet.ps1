[CmdletBinding()]
param(
    [switch]$Startup,
    [switch]$Background,
    [switch]$ControlPanel
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSCommandPath
$nativeLauncher = Join-Path $projectRoot 'Violet.exe'

if (($Startup -or $Background) -and $ControlPanel) {
    throw 'Choose either the background mode or the control panel.'
}
$mode = if ($Startup -or $Background) { '--background' } else { '--control-panel' }

try {
    & (Join-Path $projectRoot 'Setup-Violet.ps1') -NoShortcut
}
catch {
    throw 'Violet setup did not complete. Run Setup-Violet.ps1 in a visible PowerShell window for details.'
}

if (-not (Test-Path -LiteralPath $nativeLauncher)) {
    throw "Violet's native launcher was not found: $nativeLauncher"
}

# Violet's local IPC and named mutex decide whether this is a fresh background
# process or a request to open the existing instance's control panel.
Start-Process -FilePath $nativeLauncher -ArgumentList $mode -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
