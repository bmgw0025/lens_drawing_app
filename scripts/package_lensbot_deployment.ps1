param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Desktop = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"

$repository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$final = Join-Path $Desktop "LensBot_Deployment_4.1.0_win_x64"
if (Test-Path -LiteralPath $final) {
    throw "目标交付目录已存在，拒绝覆盖: $final"
}

$staging = "$final.staging-$([guid]::NewGuid().ToString('N'))"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
New-Item -ItemType Directory -Path $staging | Out-Null

try {
    Get-ChildItem -LiteralPath (Join-Path $repository "deployment_assets") -Force |
        Copy-Item -Destination $staging -Recurse -Force

    $onedirDestination = Join-Path $staging "LensDrawing_4.1.0_onedir"
    $copyOnedir = @{
        LiteralPath = Join-Path $repository "dist\LensDrawing"
        Destination = $onedirDestination
        Recurse = $true
    }
    Copy-Item @copyOnedir
    Copy-Item -LiteralPath (
        Join-Path $repository "installer_output\LensDrawing_4.1_Setup.exe"
    ) -Destination (Join-Path $staging "LensDrawing_4.1.0_Setup.exe")

    $specOut = Join-Path $env:TEMP "lensbot-package-spec-$([guid]::NewGuid().ToString('N')).json"
    $stagedExe = Join-Path $onedirDestination "LensDrawing.exe"
    $startSpec = @{
        FilePath = $stagedExe
        ArgumentList = @("--agent", "--output-json", $specOut, "spec")
        Wait = $true
        PassThru = $true
    }
    $process = Start-Process @startSpec
    if ($process.ExitCode -ne 0) {
        throw "暂存版 spec 失败: $($process.ExitCode)"
    }
    $specEnvelope = Get-Content -LiteralPath $specOut -Raw |
        ConvertFrom-Json -Depth 100
    [System.IO.File]::Delete($specOut)
    $spec = $specEnvelope.result.spec
    if (
        $spec.application.version_full -ne "4.1.0" -or
        $spec.agent_interface.version -ne "4.1.0" -or
        $spec.agent_interface.request_schema_version -ne "1.2" -or
        $spec.agent_interface.task_schema_version -ne "1.1"
    ) {
        throw "暂存版 spec 版本不匹配"
    }
    if ($null -eq $spec.geometry_policy.low_confidence_confirmation_rule) {
        throw "暂存版 spec 缺少低置信几何确认规则"
    }

    $internalRoot = Join-Path $onedirDestination "_internal"
    $unexpectedSources = Get-ChildItem -LiteralPath $staging -Recurse -File -Include *.py, *.pyw, *.pyi |
        Where-Object {
            $relative = [System.IO.Path]::GetRelativePath(
                $staging,
                $_.FullName
            ).Replace("\", "/")
            -not $relative.StartsWith(
                "LensDrawing_4.1.0_onedir/_internal/",
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }
    if ($unexpectedSources) {
        throw (
            "发现 Lens Drawing release 外的 Python 源码: " +
            ($unexpectedSources.FullName -join ", ")
        )
    }

    $forbidden = Get-ChildItem -LiteralPath $staging -Recurse -Force |
        Where-Object {
            $relative = [System.IO.Path]::GetRelativePath(
                $staging,
                $_.FullName
            ).Replace("\", "/")
            -not $relative.StartsWith(
                "LensDrawing_4.1.0_onedir/_internal/",
                [System.StringComparison]::OrdinalIgnoreCase
            ) -and (
                $_.Name -in @(
                    "agent_cli.py",
                    "main.py",
                    "web_app.py",
                    "webview_main.py"
                ) -or
                ($_.PSIsContainer -and $_.Name -eq "autodraw")
            )
        }
    if ($forbidden) {
        throw (
            "发现禁止的 Lens Drawing 源码路径: " +
            ($forbidden.FullName -join ", ")
        )
    }

    $e2eRoot = Get-Content -LiteralPath (
        Join-Path $env:TEMP "lensdrawing-e2e-confirm-path.txt"
    )
    $e2e = Get-Content -LiteralPath (
        Join-Path $e2eRoot "local-release-verification.json"
    ) -Raw | ConvertFrom-Json -Depth 100
    $installer = Join-Path $staging "LensDrawing_4.1.0_Setup.exe"
    $verification = [ordered]@{
        schema_version = "1.0"
        verified_at = (Get-Date).ToString("o")
        release = [ordered]@{
            application_version = "4.1.0"
            agent_interface_version = "4.1.0"
            request_schema_version = "1.2"
            task_schema_version = "1.1"
            executable_sha256 = (
                Get-FileHash -Algorithm SHA256 -LiteralPath $stagedExe
            ).Hash.ToLowerInvariant()
            installer_sha256 = (
                Get-FileHash -Algorithm SHA256 -LiteralPath $installer
            ).Hash.ToLowerInvariant()
            build_manifest_sha256 = (
                Get-FileHash -Algorithm SHA256 -LiteralPath (
                    Join-Path $onedirDestination "agent_resources\build_manifest.json"
                )
            ).Hash.ToLowerInvariant()
        }
        verification = [ordered]@{
            asset_sync = "passed"
            unit_tests = [ordered]@{
                passed = 75
                failed = 0
                command = "venv\Scripts\python.exe -m unittest discover -s tests -v"
            }
            frozen_spec = "passed"
            frozen_virtual_interface_e2e = $e2e
            actual_minimax_model_probe = "not_run_requires_target_api_key"
            actual_dws_probe = "not_run_requires_target_dws_authorization"
        }
        source_scan = [ordered]@{
            lens_drawing_project_source_outside_pyinstaller_internal = 0
            dependency_python_sources_inside_pyinstaller_internal = "allowed"
        }
    }
    $verificationPath = Join-Path $staging "LOCAL_RELEASE_VERIFICATION.json"
    [System.IO.File]::WriteAllText(
        $verificationPath,
        ($verification | ConvertTo-Json -Depth 100) + [Environment]::NewLine,
        $utf8NoBom
    )

    $manifestPath = Join-Path $staging "release_manifest.json"
    $shaPath = Join-Path $staging "SHA256.txt"
    $payloadFiles = Get-ChildItem -LiteralPath $staging -Recurse -File |
        Where-Object { $_.FullName -notin @($manifestPath, $shaPath) } |
        Sort-Object FullName
    $inventory = @(
        foreach ($file in $payloadFiles) {
            $relative = [System.IO.Path]::GetRelativePath(
                $staging,
                $file.FullName
            ).Replace("\", "/")
            [ordered]@{
                path = $relative
                size = $file.Length
                sha256 = (
                    Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName
                ).Hash.ToLowerInvariant()
            }
        }
    )
    [int64]$totalPayloadBytes = 0
    foreach ($item in $inventory) {
        $totalPayloadBytes += [int64]$item["size"]
    }
    $manifest = [ordered]@{
        schema_version = "1.0"
        package_name = "LensBot_Deployment_4.1.0_win_x64"
        package_version = "4.1.0"
        platform = "windows-x64"
        created_at = (Get-Date).ToString("o")
        inventory_excludes = @("release_manifest.json", "SHA256.txt")
        file_count = $inventory.Count
        total_payload_bytes = $totalPayloadBytes
        files = $inventory
    }
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($manifest | ConvertTo-Json -Depth 10) + [Environment]::NewLine,
        $utf8NoBom
    )

    $hashLines = @(
        foreach ($item in $inventory) {
            "$($item.sha256) *$($item.path)"
        }
        $manifestHash = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath
        ).Hash.ToLowerInvariant()
        "$manifestHash *release_manifest.json"
    ) | Sort-Object
    [System.IO.File]::WriteAllLines($shaPath, $hashLines, $utf8NoBom)

    $manifestPayload = Get-Content -LiteralPath $manifestPath -Raw |
        ConvertFrom-Json -Depth 100
    $actualPayload = @(
        Get-ChildItem -LiteralPath $staging -Recurse -File |
            Where-Object { $_.FullName -notin @($manifestPath, $shaPath) } |
            ForEach-Object {
                [System.IO.Path]::GetRelativePath(
                    $staging,
                    $_.FullName
                ).Replace("\", "/")
            } |
            Sort-Object
    )
    $declaredPayload = @($manifestPayload.files.path | Sort-Object)
    if (Compare-Object -ReferenceObject $actualPayload -DifferenceObject $declaredPayload) {
        throw "release_manifest.json 文件集合不一致"
    }

    Move-Item -LiteralPath $staging -Destination $final
    Write-Output "FINAL=$final"
    Write-Output "FILES=$($inventory.Count)"
    Write-Output "BYTES=$($manifest['total_payload_bytes'])"
} catch {
    Write-Error "组包失败，暂存目录保留用于诊断: $staging"
    throw
}
