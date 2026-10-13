[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$source = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'worker\dist\trademark-worker'))
$target = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'apps\desktop\src-tauri\resources\worker'))
$projectPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar

if (-not $target.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean a directory outside the project: $target"
}
if ($target -eq $projectRoot) {
    throw 'Refusing to use the project root as a resource target'
}
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "Worker onedir not found; run build-worker.ps1 first: $source"
}

if (Test-Path -LiteralPath $target) {
    Remove-Item -LiteralPath $target -Recurse -Force
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force

$executable = Join-Path $target 'trademark-worker.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Staged Worker executable not found: $executable"
}
$version = & $executable --version
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '0.1.29') {
    throw "Staged Worker version check failed: $version"
}

Write-Host "Worker resources staged: $target"
