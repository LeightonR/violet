<#
.SYNOPSIS
Installs Violet's optional per-user NVIDIA CUDA 12 runtime.

.DESCRIPTION
Downloads two NVIDIA-published PyPI wheels at fixed versions, verifies their
SHA-256 digests, and extracts only the DLLs and licenses Violet needs under
%LOCALAPPDATA%\Violet\runtime\cuda12. It neither installs a CUDA Toolkit nor
changes the system PATH, drivers, or other applications.

The optional -WheelDirectory parameter accepts a directory containing the
exact pinned wheels. It is useful for an air-gapped repeat installation and
for validating a release without downloading them again.
#>

[CmdletBinding()]
param(
    [string]$WheelDirectory,
    [switch]$KeepDownloads
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not [Environment]::Is64BitOperatingSystem) {
    throw 'Violet NVIDIA acceleration requires 64-bit Windows.'
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is unavailable; cannot choose Violet''s per-user runtime directory.'
}

# These are NVIDIA CUDA Installer Team uploads on PyPI, pinned by filename,
# version, direct HTTPS URL, and SHA-256. Do not replace these with an
# unverified "latest" download: Violet's CTranslate2 wheel expects CUDA 12.
$wheels = @(
    [pscustomobject]@{
        Package = 'nvidia-cublas-cu12'
        Version = '12.9.2.10'
        FileName = 'nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl'
        Url = 'https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl'
        Sha256 = '623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661'
        RuntimePrefix = 'nvidia/cublas/bin/'
        RequiredDll = 'nvidia/cublas/bin/cublas64_12.dll'
        LicenseEntry = 'nvidia_cublas_cu12-12.9.2.10.dist-info/licenses/License.txt'
        LicenseFile = 'NVIDIA-CUBLAS-LICENSE.txt'
    },
    [pscustomobject]@{
        Package = 'nvidia-cudnn-cu12'
        Version = '9.28.1.4'
        FileName = 'nvidia_cudnn_cu12-9.28.1.4-py3-none-win_amd64.whl'
        Url = 'https://files.pythonhosted.org/packages/c7/bd/1d6709d2223e99f04079f16005f8ae6e11a14ee16b5eab6833686cf72fe1/nvidia_cudnn_cu12-9.28.1.4-py3-none-win_amd64.whl'
        Sha256 = '3467aafa9b260e6adf5fbbfc0e21d180133305b42032aa1160be2d3142bb8b09'
        RuntimePrefix = 'nvidia/cudnn/bin/'
        RequiredDll = 'nvidia/cudnn/bin/cudnn64_9.dll'
        LicenseEntry = 'nvidia_cudnn_cu12-9.28.1.4.dist-info/licenses/LICENSE'
        LicenseFile = 'NVIDIA-CUDNN-LICENSE.txt'
    }
)

function Copy-ZipEntry {
    param(
        [Parameter(Mandatory)] [System.IO.Compression.ZipArchiveEntry]$Entry,
        [Parameter(Mandatory)] [string]$Destination
    )

    $destinationDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    $input = $Entry.Open()
    try {
        $output = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
        try {
            $input.CopyTo($output)
        }
        finally {
            $output.Dispose()
        }
    }
    finally {
        $input.Dispose()
    }
}

$violetRoot = Join-Path $env:LOCALAPPDATA 'Violet'
$runtimeParent = Join-Path $violetRoot 'runtime'
$target = Join-Path $runtimeParent 'cuda12'
$parentFull = [IO.Path]::GetFullPath($runtimeParent).TrimEnd('\')
$targetFull = [IO.Path]::GetFullPath($target)
if (-not $targetFull.StartsWith($parentFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing an unexpected CUDA runtime target.'
}

$stage = Join-Path $runtimeParent ('.cuda12-stage-' + [guid]::NewGuid().ToString('N'))
$downloadRoot = Join-Path ([IO.Path]::GetTempPath()) ('VioletCuda12-' + [guid]::NewGuid().ToString('N'))
$backup = $null

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    New-Item -ItemType Directory -Path $stage, $downloadRoot -Force | Out-Null

    foreach ($wheel in $wheels) {
        if ($WheelDirectory) {
            $wheelPath = Join-Path $WheelDirectory $wheel.FileName
            if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
                throw "Missing pinned wheel: $wheelPath"
            }
        }
        else {
            $wheelPath = Join-Path $downloadRoot $wheel.FileName
            Write-Host "Downloading $($wheel.Package) $($wheel.Version)..."
            Invoke-WebRequest -Uri $wheel.Url -OutFile $wheelPath -UseBasicParsing
        }

        $actualHash = (Get-FileHash -LiteralPath $wheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $wheel.Sha256) {
            throw "SHA-256 verification failed for $($wheel.FileName)."
        }

        $archive = [System.IO.Compression.ZipFile]::OpenRead($wheelPath)
        try {
            if ($null -eq $archive.GetEntry($wheel.RequiredDll)) {
                throw "Pinned wheel did not contain $($wheel.RequiredDll)."
            }
            $runtimeEntries = @($archive.Entries | Where-Object {
                $_.FullName.StartsWith($wheel.RuntimePrefix, [StringComparison]::Ordinal) -and
                -not $_.FullName.EndsWith('/')
            })
            if ($runtimeEntries.Count -eq 0) {
                throw "Pinned wheel did not contain runtime files for $($wheel.Package)."
            }
            foreach ($entry in $runtimeEntries) {
                $relative = $entry.FullName.Replace('/', '\')
                $destination = Join-Path $stage $relative
                Copy-ZipEntry -Entry $entry -Destination $destination
            }
            $license = $archive.GetEntry($wheel.LicenseEntry)
            if ($null -eq $license) {
                throw "Pinned wheel did not contain its license: $($wheel.LicenseEntry)."
            }
            Copy-ZipEntry -Entry $license -Destination (Join-Path $stage (Join-Path 'licenses' $wheel.LicenseFile))
        }
        finally {
            $archive.Dispose()
        }
    }

    foreach ($wheel in $wheels) {
        $required = Join-Path $stage $wheel.RequiredDll.Replace('/', '\')
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Runtime staging validation failed: $required"
        }
    }

    $manifest = [ordered]@{
        format = 1
        installed_utc = [DateTime]::UtcNow.ToString('o')
        source = 'NVIDIA CUDA Installer Team PyPI wheels; SHA-256 verified by Install-VioletCudaRuntime.ps1'
        packages = @($wheels | ForEach-Object {
            [ordered]@{
                package = $_.Package
                version = $_.Version
                filename = $_.FileName
                sha256 = $_.Sha256
                url = $_.Url
            }
        })
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage 'runtime-manifest.json') -Encoding utf8

    if (Test-Path -LiteralPath $targetFull) {
        $backup = Join-Path $runtimeParent ('.cuda12-previous-' + [guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $targetFull -Destination $backup
    }
    try {
        Move-Item -LiteralPath $stage -Destination $targetFull
        $stage = $null
    }
    catch {
        if ($backup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $targetFull)) {
            Move-Item -LiteralPath $backup -Destination $targetFull
        }
        throw
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        try {
            Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction Stop
        }
        catch {
            # The new runtime is already active. An older Violet process can
            # still have the replaced DLLs open, so preserving the backup is
            # safer than reporting a failed installation or killing an app.
            Write-Warning "The new runtime is installed. An old runtime remains at $backup until a running Violet process releases it."
        }
    }

    Write-Host "Installed the SHA-256-verified CUDA 12 runtime for Violet at $targetFull"
    Write-Host 'Restart Violet, then select Use NVIDIA GPU acceleration in the control panel.'
}
finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
    if (-not $KeepDownloads -and (Test-Path -LiteralPath $downloadRoot)) {
        Remove-Item -LiteralPath $downloadRoot -Recurse -Force
    }
}
