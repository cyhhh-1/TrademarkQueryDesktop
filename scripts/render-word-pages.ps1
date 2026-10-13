[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,
    [string]$PagePrefix = 'page'
)

$ErrorActionPreference = 'Stop'
$inputFullPath = [System.IO.Path]::GetFullPath($InputPath)
$outputFullPath = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputFullPath -Force | Out-Null
Get-ChildItem -LiteralPath $outputFullPath -File -Filter "$PagePrefix-*.emf" |
    Remove-Item -Force

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $document = $word.Documents.Open($inputFullPath, $false, $true)
    try {
        $pageCount = $document.ComputeStatistics(2)
        for ($pageNumber = 1; $pageNumber -le $pageCount; $pageNumber++) {
            $start = $document.GoTo(1, 1, $pageNumber).Start
            if ($pageNumber -lt $pageCount) {
                $end = $document.GoTo(1, 1, $pageNumber + 1).Start - 1
            } else {
                $end = $document.Content.End - 1
            }
            $range = $document.Range($start, $end)
            $bits = $range.EnhMetaFileBits
            $outputPath = Join-Path $outputFullPath ("{0}-{1}.emf" -f $PagePrefix, $pageNumber)
            [System.IO.File]::WriteAllBytes($outputPath, $bits)
        }
        Write-Output $pageCount
    } finally {
        $document.Close($false)
    }
} finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) | Out-Null
}
