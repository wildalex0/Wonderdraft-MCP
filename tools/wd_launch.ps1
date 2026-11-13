<#
.SYNOPSIS
  Launch Wonderdraft (optionally with a map path argument), wait, and save a full-screen capture.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\wd_launch.ps1 -Map out\rt.wonderdraft_map -Shot out\rt.png
#>
param(
    [string]$Map = "",
    [Parameter(Mandatory = $true)][string]$Shot,
    [int]$Wait = 15,
    [string]$Exe = "C:\Program Files\Wonderdraft\Wonderdraft.exe",
    [switch]$NoLaunch
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing, System.Windows.Forms

if (-not $NoLaunch) {
    if ($Map) {
        $full = (Resolve-Path $Map).Path
        Start-Process -FilePath $Exe -ArgumentList "`"$full`""
    } else {
        Start-Process -FilePath $Exe
    }
    Start-Sleep -Seconds $Wait
}

$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bmp.Size)
$dir = Split-Path -Parent $Shot
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
$bmp.Save($Shot, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $Shot ($($bounds.Width)x$($bounds.Height))"
