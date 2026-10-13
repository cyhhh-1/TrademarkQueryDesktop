[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:PATH = "$(Join-Path $env:USERPROFILE '.cargo\bin');$env:PATH"
foreach ($tool in @('py', 'npm', 'cargo', 'rustc')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "Missing required tool: $tool. See docs/回家继续开发.md"
    }
}
Push-Location $projectRoot
try {
    & py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required.' }
    if (-not (Test-Path 'worker\.venv\Scripts\python.exe')) {
        & py -3.11 -m venv worker\.venv
        if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
    }
    & worker\.venv\Scripts\python.exe -m pip install -e 'worker[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
    & npm --prefix apps\desktop ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    # Ensure the existing build scripts use this machine's new environment.
    $previousPython = $env:TRADEMARK_WORKER_PYTHON
    try {
        $env:TRADEMARK_WORKER_PYTHON = Join-Path $projectRoot 'worker\.venv\Scripts\python.exe'
        & (Join-Path $PSScriptRoot 'build-worker.ps1')
        & (Join-Path $PSScriptRoot 'stage-worker.ps1')
    } finally {
        $env:TRADEMARK_WORKER_PYTHON = $previousPython
    }
    Write-Host 'Ready. Run the tests in docs/回家继续开发.md, then npm run tauri -- dev in apps/desktop.'
} finally {
    Pop-Location
}
