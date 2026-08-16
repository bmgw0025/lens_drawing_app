[CmdletBinding()]
param(
    [string]$InstallRoot,
    [string]$OutputJson,
    [int]$TimeoutSeconds = 900,
    [Parameter(Mandatory = $true, Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$AgentArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-FullPath([string]$PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Add-Candidate([System.Collections.Generic.List[string]]$Candidates, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    try {
        $full = Get-FullPath $Value
    } catch {
        return
    }
    if (-not $Candidates.Contains($full)) {
        $Candidates.Add($full)
    }
}

function Find-LensDrawingInstall([string]$ExplicitRoot) {
    $candidates = [System.Collections.Generic.List[string]]::new()
    Add-Candidate $candidates $ExplicitRoot
    Add-Candidate $candidates $env:LENS_DRAWING_INSTALL_DIR

    $relativeRoot = Join-Path $PSScriptRoot "..\..\.."
    Add-Candidate $candidates $relativeRoot

    $installationFiles = @(
        (Join-Path $relativeRoot "installation.json"),
        (Join-Path $env:LOCALAPPDATA "LensDrawing\installation.json"),
        (Join-Path $env:LOCALAPPDATA "Lens Drawing\installation.json"),
        (Join-Path $env:PROGRAMDATA "LensDrawing\installation.json")
    )
    foreach ($file in $installationFiles) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            continue
        }
        try {
            $installation = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
            Add-Candidate $candidates ([string]$installation.install_root)
            if (-not [string]::IsNullOrWhiteSpace([string]$installation.executable)) {
                Add-Candidate $candidates (Split-Path -Parent ([string]$installation.executable))
            }
        } catch {
            continue
        }
    }

    foreach ($registryPath in @(
        "HKCU:\Software\LensDrawing",
        "HKLM:\Software\LensDrawing",
        "HKLM:\Software\WOW6432Node\LensDrawing"
    )) {
        try {
            $item = Get-ItemProperty -LiteralPath $registryPath -ErrorAction Stop
            Add-Candidate $candidates ([string]$item.InstallPath)
            Add-Candidate $candidates ([string]$item.InstallRoot)
        } catch {
            continue
        }
    }

    Add-Candidate $candidates (Join-Path $env:ProgramFiles "LensDrawing")
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        Add-Candidate $candidates (Join-Path ${env:ProgramFiles(x86)} "LensDrawing")
    }
    Add-Candidate $candidates (Join-Path $env:LOCALAPPDATA "Programs\LensDrawing")

    foreach ($candidate in $candidates) {
        $executable = Join-Path $candidate "LensDrawing.exe"
        if (Test-Path -LiteralPath $executable -PathType Leaf) {
            return [PSCustomObject]@{
                InstallRoot = $candidate
                Executable = $executable
            }
        }
    }
    throw "LensDrawing.exe was not found. Set LENS_DRAWING_INSTALL_DIR or pass -InstallRoot. Checked: $($candidates -join '; ')"
}

function Invoke-AgentProcess(
    [string]$Executable,
    [string[]]$Arguments,
    [string]$ResultPath,
    [int]$WaitSeconds
) {
    if (Test-Path -LiteralPath $ResultPath) {
        throw "Output JSON already exists and will not be overwritten: $ResultPath"
    }
    $parent = Split-Path -Parent $ResultPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $global:LASTEXITCODE = 0
    & $Executable --agent --output-json $ResultPath @Arguments
    $nativeExitCode = $global:LASTEXITCODE
    $deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    while (-not (Test-Path -LiteralPath $ResultPath -PathType Leaf)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "Timed out waiting for Lens Drawing Agent output: $ResultPath"
        }
        Start-Sleep -Milliseconds 200
    }
    try {
        $payload = Get-Content -LiteralPath $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw "Lens Drawing Agent returned invalid JSON: $ResultPath"
    }
    return [PSCustomObject]@{
        NativeExitCode = $nativeExitCode
        Payload = $payload
    }
}

if ($AgentArguments.Count -lt 1) {
    throw "An Agent command is required."
}

$installation = Find-LensDrawingInstall $InstallRoot
$skillSpec = Get-FullPath (Join-Path $PSScriptRoot "..\references\lens_drawing_agent_spec.json")
if (-not (Test-Path -LiteralPath $skillSpec -PathType Leaf)) {
    throw "Bundled Skill spec is missing: $skillSpec"
}

$probePath = Join-Path $env:TEMP ("lens_drawing_spec_" + [Guid]::NewGuid().ToString("N") + ".json")
try {
    $probe = Invoke-AgentProcess $installation.Executable @("spec") $probePath $TimeoutSeconds
    if (-not [bool]$probe.Payload.ok) {
        throw "Installed Lens Drawing spec command failed: $($probe.Payload.error.message)"
    }
    $skillSpecHash = (Get-FileHash -LiteralPath $skillSpec -Algorithm SHA256).Hash.ToLowerInvariant()
    $installedSpecHash = [string]$probe.Payload.result.runtime_identity.agent_spec_file_sha256
    if ($skillSpecHash -ne $installedSpecHash.ToLowerInvariant()) {
        throw "Skill/EXE spec mismatch. Skill=$skillSpecHash Installed=$installedSpecHash. Install the Skill bundled with this Lens Drawing version."
    }
} finally {
    Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
}

if ([string]::IsNullOrWhiteSpace($OutputJson)) {
    $OutputJson = Join-Path (Get-Location) (
        "lens_drawing_agent_" + (Get-Date -Format "yyyyMMdd_HHmmss") + "_" +
        [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".json"
    )
}
$resultPath = Get-FullPath $OutputJson
$result = Invoke-AgentProcess $installation.Executable $AgentArguments $resultPath $TimeoutSeconds

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$result.Payload | ConvertTo-Json -Depth 100
if ($null -ne $result.Payload.exit_code) {
    exit [int]$result.Payload.exit_code
}
exit [int]$result.NativeExitCode
