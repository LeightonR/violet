[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string[]]$PathToScan,
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = 'py.exe'
)

$ErrorActionPreference = 'Stop'

$resolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\\')
$needles = @(
    $env:USERPROFILE,
    $resolvedProject
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique

$scanner = Join-Path $PSScriptRoot 'privacy_scan.py'
$arguments = @($scanner)
foreach ($path in $PathToScan) { $arguments += @('--root', $path) }
foreach ($needle in $needles) { $arguments += @('--needle', $needle) }
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Release privacy scan failed.' }
