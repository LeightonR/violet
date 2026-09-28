[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSCommandPath
$source = Join-Path $projectRoot 'VioletLauncher.cs'
$icon = Join-Path $projectRoot 'Violet.ico'
$output = Join-Path $projectRoot 'Violet.exe'
$compilerCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $compiler) {
    throw 'The Windows .NET C# compiler was not found. Violet.exe could not be built.'
}
if (-not (Test-Path -LiteralPath $source) -or -not (Test-Path -LiteralPath $icon)) {
    throw 'Violet launcher source or icon is missing.'
}

& $compiler /nologo /target:winexe /platform:anycpu /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$output" $source
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) {
    throw 'Violet.exe could not be built.'
}

Write-Host "Built Violet launcher: $output"
