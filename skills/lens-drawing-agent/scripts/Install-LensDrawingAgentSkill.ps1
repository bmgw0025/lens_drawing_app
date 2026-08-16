[CmdletBinding()]
param(
    [string]$DestinationRoot,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$source = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
foreach ($required in @(
    "SKILL.md",
    "scripts\Invoke-LensDrawingAgent.ps1",
    "references\lens_drawing_agent_spec.json"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $required) -PathType Leaf)) {
        throw "Bundled Skill is incomplete: $required"
    }
}

if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $DestinationRoot = Join-Path $env:CODEX_HOME "skills"
    } else {
        $DestinationRoot = Join-Path $HOME ".codex\skills"
    }
}
$root = [System.IO.Path]::GetFullPath($DestinationRoot)
$destination = [System.IO.Path]::GetFullPath((Join-Path $root "lens-drawing-agent"))
$rootPrefix = $root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $destination.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside the requested skill root: $destination"
}

if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
}
if (Test-Path -LiteralPath $destination) {
    if (-not $Force) {
        throw "Skill already exists: $destination. Pass -Force to replace it."
    }
    Remove-Item -LiteralPath $destination -Recurse -Force
}
Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force

[PSCustomObject]@{
    installed = $true
    skill = "lens-drawing-agent"
    source = $source
    destination = $destination
} | ConvertTo-Json -Depth 4
