[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$desktopRoot = Join-Path $projectRoot 'apps\desktop'
$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
$env:PATH = "$cargoBin;$env:PATH"

& (Join-Path $PSScriptRoot 'build-worker.ps1')
& (Join-Path $PSScriptRoot 'stage-worker.ps1')

& npm --prefix $desktopRoot ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
& npm --prefix $desktopRoot test -- --run
if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed' }
& cargo test --manifest-path (Join-Path $desktopRoot 'src-tauri\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'Rust tests failed' }

Push-Location $desktopRoot
try {
    & npm run tauri -- build
    if ($LASTEXITCODE -ne 0) { throw "Tauri build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "Installer directory: $(Join-Path $desktopRoot 'src-tauri\target\release\bundle')"
