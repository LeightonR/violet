[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSCommandPath
$launcher = Join-Path $projectRoot 'Violet.exe'
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Violet's native launcher was not found: $launcher"
}

$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders('Desktop')
$startup = $shell.SpecialFolders('Startup')
if (-not (Test-Path -LiteralPath $startup)) {
    New-Item -ItemType Directory -Path $startup -Force | Out-Null
}

function Set-VioletShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Mode,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $launcher
    $shortcut.Arguments = $Mode
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = $Description
    $shortcut.IconLocation = "$launcher,0"
    $shortcut.Save()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut)
    $shortcut = $null

    # Store the same app ID used by the native launcher and Violet's Qt app so
    # a user-pinned shortcut retains Violet's taskbar identity rather than
    # grouping as PowerShell or Python.
    & $launcher '--set-shortcut-app-id' $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Violet's taskbar metadata could not be written: $Path"
    }
}

$desktopShortcut = Join-Path $desktop 'Violet.lnk'
$startupShortcut = Join-Path $startup 'Violet.lnk'
Set-VioletShortcut -Path $desktopShortcut -Mode '--control-panel' -Description 'Violet control panel'
Set-VioletShortcut -Path $startupShortcut -Mode '--background' -Description 'Start Violet in the background at sign-in'

Write-Host "Created Violet Desktop shortcut: $desktopShortcut"
Write-Host "Created Violet sign-in shortcut: $startupShortcut"
