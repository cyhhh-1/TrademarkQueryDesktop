[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$workerRoot = Join-Path $projectRoot 'worker'
$python = if ($env:TRADEMARK_WORKER_PYTHON) {
    $env:TRADEMARK_WORKER_PYTHON
} else {
    Join-Path $workerRoot '.venv\Scripts\python.exe'
}
$stagedWorker = Join-Path $projectRoot 'apps\desktop\src-tauri\resources\worker\trademark-worker.exe'

if (-not (Test-Path -LiteralPath $stagedWorker -PathType Leaf)) {
    throw "Staged Worker not found: $stagedWorker"
}

$version = & $stagedWorker --version
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '0.1.30') {
    throw "Staged Worker version is invalid: $version"
}
$handshake = '{"type":"shutdown","protocol":1}' | & $stagedWorker
if ($LASTEXITCODE -ne 0) { throw 'Staged Worker handshake failed' }
$hello = $handshake | Select-Object -First 1 | ConvertFrom-Json
if ($hello.type -ne 'hello') { throw 'Staged Worker did not output hello' }

Push-Location $workerRoot
try {
    & $python -m pytest tests\test_offline_e2e.py tests\test_worker_commands.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline end-to-end tests failed' }
} finally {
    Pop-Location
}

Write-Host 'Release resource smoke tests passed'
