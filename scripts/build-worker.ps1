[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$workerRoot = Join-Path $projectRoot 'worker'
$python = if ($env:TRADEMARK_WORKER_PYTHON) {
    [System.IO.Path]::GetFullPath($env:TRADEMARK_WORKER_PYTHON)
} elseif (Test-Path -LiteralPath (Join-Path $workerRoot '.venv\Scripts\python.exe') -PathType Leaf) {
    Join-Path $workerRoot '.venv\Scripts\python.exe'
} else {
    Join-Path $workerRoot '.python311\python.exe'
}
$spec = Join-Path $workerRoot 'trademark-worker.spec'
$executable = Join-Path $workerRoot 'dist\trademark-worker\trademark-worker.exe'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Worker virtual environment not found: $python"
}
if (-not (Test-Path -LiteralPath $spec -PathType Leaf)) {
    throw "PyInstaller spec not found: $spec"
}

Push-Location $workerRoot
try {
    & $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Worker executable not found: $executable"
}

$version = & $executable --version
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '0.1.30') {
    throw "Worker version check failed: $version"
}

$playwright = & $executable --playwright-smoke
if ($LASTEXITCODE -ne 0 -or $playwright.Trim() -ne 'chromium') {
    throw "Frozen Playwright startup check failed: $playwright"
}

$ocr = & $executable --ocr-smoke
if ($LASTEXITCODE -ne 0 -or $ocr.Trim() -ne 'rapidocr') {
    throw "Frozen RapidOCR startup check failed: $ocr"
}

Write-Host "Worker build completed: $executable"
