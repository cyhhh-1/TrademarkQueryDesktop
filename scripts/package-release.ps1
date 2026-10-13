[CmdletBinding()]
param([switch]$IncludeManual)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$version = "0.1.29"
$releaseDir = Join-Path $projectRoot "release"
$installerDir = Join-Path $projectRoot "apps\desktop\src-tauri\target\release\bundle\nsis"
$installerSource = Get-ChildItem -LiteralPath $installerDir -File -Filter "*_${version}_x64-setup.exe" | Select-Object -First 1 -ExpandProperty FullName
if (-not $installerSource) {
    throw "No version $version installer found in $installerDir"
}
$installerTarget = Join-Path $releaseDir (Split-Path -Leaf $installerSource)
$manualDocx = Join-Path $releaseDir ("商标查询助手安装与使用说明_{0}.docx" -f $version)
$manualPdf = Join-Path $releaseDir ("商标查询助手安装与使用说明_{0}.pdf" -f $version)
$checksumFile = Join-Path $releaseDir "SHA256.txt"
$bundleBase = ([System.IO.Path]::GetFileNameWithoutExtension($installerSource)) -replace "_${version}_x64-setup$", ""
$bundlePath = Join-Path $releaseDir ("{0}_{1}_release.zip" -f $bundleBase, $version)

$requiredPaths = @($installerSource)
if ($IncludeManual) {
    $requiredPaths += @($manualDocx, $manualPdf)
}
foreach ($requiredPath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Missing release input: $requiredPath"
    }
}

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
Copy-Item -LiteralPath $installerSource -Destination $installerTarget -Force

$releaseFiles = @($installerTarget)
if ($IncludeManual) {
    $releaseFiles += @($manualDocx, $manualPdf)
}
$checksumLines = foreach ($file in $releaseFiles) {
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $file)"
}
Set-Content -LiteralPath $checksumFile -Value $checksumLines -Encoding UTF8

$stageName = "release-stage-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
$stageDir = Join-Path (Join-Path $projectRoot "worker\build") $stageName
New-Item -ItemType Directory -Path $stageDir | Out-Null

foreach ($file in @($releaseFiles + $checksumFile)) {
    Copy-Item -LiteralPath $file -Destination $stageDir
}

if (Test-Path -LiteralPath $bundlePath) {
    throw "Release bundle already exists: $bundlePath"
}
Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $bundlePath -CompressionLevel Optimal

Write-Output "Release bundle: $bundlePath"
Write-Output "Stage directory: $stageDir"
Get-ChildItem -LiteralPath $releaseDir -File |
    Sort-Object Name |
    Select-Object Name, Length, LastWriteTime
