<#
.SYNOPSIS
  Spike C: export a map to PNG through the real Wonderdraft app, end to end.
  Launch with the map path → wait for the window title → Ctrl+E (SendInput) → Enter to confirm the
  Godot "Export Options" dialog → fill the native Windows save dialog via WM_SETTEXT/BM_CLICK →
  poll for the output file → close Wonderdraft.
#>
param(
    [Parameter(Mandatory = $true)][string]$Map,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$TimeoutSec = 120,
    [string]$Exe = "C:\Program Files\Wonderdraft\Wonderdraft.exe"
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mapFull = (Resolve-Path $Map).Path
$outFull = [System.IO.Path]::GetFullPath($Out)
if (Test-Path $outFull) { throw "output exists: $outFull" }
if (Get-Process Wonderdraft -ErrorAction SilentlyContinue) { throw "Wonderdraft is already running; close it first" }
$sw = [Diagnostics.Stopwatch]::StartNew()
$stem = [System.IO.Path]::GetFileNameWithoutExtension($mapFull)
Start-Process -FilePath $Exe -ArgumentList "`"$mapFull`""
# wait until the main window title shows the map name (map loaded)
$proc = $null
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    $proc = Get-Process Wonderdraft -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "$stem*" } | Select-Object -First 1
    if ($proc) { break }; Start-Sleep -Milliseconds 500
}
if (-not $proc) { throw "map window did not appear" }
$tLoaded = $sw.Elapsed.TotalSeconds
Start-Sleep -Seconds 3   # let the canvas finish its first render
try {
    & powershell -ExecutionPolicy Bypass -File "$here\wd_gui.ps1" -Steps "key:ctrl+e;wait:1500;key:enter" | Out-Null
    & powershell -ExecutionPolicy Bypass -File "$here\wd_savedialog.ps1" -Path $outFull -TimeoutMs 15000 | Out-Null
    $last = -1
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (Test-Path $outFull) {
            $len = (Get-Item $outFull).Length
            if ($len -gt 0 -and $len -eq $last) { break }   # size stable across two polls
            $last = $len
        }
        Start-Sleep -Milliseconds 700
    }
    if (-not (Test-Path $outFull)) { throw "export file did not appear" }
} finally {
    $p = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
    if ($p) { $p.CloseMainWindow() | Out-Null; if (-not $p.WaitForExit(5000)) { Stop-Process -Id $p.Id -Force -Confirm:$false } }
}
Write-Output ("exported {0} ({1} bytes); loaded {2:N1}s, total {3:N1}s" -f $outFull, (Get-Item $outFull).Length, $tLoaded, $sw.Elapsed.TotalSeconds)
