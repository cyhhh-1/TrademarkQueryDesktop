[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class WindowCaptureNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int command);
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
}
'@

[WindowCaptureNative]::SetProcessDPIAware() | Out-Null
$process = Get-Process -Id $ProcessId
$handle = $process.MainWindowHandle
if ($handle -eq [IntPtr]::Zero) { throw 'Process has no main window' }
[WindowCaptureNative]::ShowWindow($handle, 3) | Out-Null
[WindowCaptureNative]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 600
$rect = New-Object WindowCaptureNative+RECT
if (-not [WindowCaptureNative]::GetWindowRect($handle, [ref]$rect)) {
    throw 'GetWindowRect failed'
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) { throw 'Window bounds are empty' }

Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $directory = Split-Path -Parent $OutputPath
    if ($directory) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
    $bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
