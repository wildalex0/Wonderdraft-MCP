<#
.SYNOPSIS
  Launch Wonderdraft (optionally with a map path argument), wait, and save a screen capture.
.DESCRIPTION
  By default the capture is cropped to the Wonderdraft main window (falls back to the
  whole virtual screen if no window is found). -FullScreen captures every monitor.
  -Close closes Wonderdraft after the capture (force-kills if it does not exit in 5 s).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\wd_launch.ps1 -Map out\rt.wonderdraft_map -Shot out\rt.png -Close
#>
param(
    [string]$Map = "",
    [Parameter(Mandatory = $true)][string]$Shot,
    [int]$Wait = 15,
    [string]$Exe = "C:\Program Files\Wonderdraft\Wonderdraft.exe",
    [switch]$NoLaunch,
    [switch]$FullScreen,
    [switch]$Close
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
if (-not ("WdWin" -as [type])) {
    Add-Type @"
using System; using System.Runtime.InteropServices;
public class WdWin {
  public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
}
# Physical pixels everywhere (Godot is DPI-aware; without this, rects and captures disagree).
[WdWin]::SetProcessDPIAware() | Out-Null

if (-not $NoLaunch) {
    if ($Map) {
        $full = (Resolve-Path $Map).Path
        Start-Process -FilePath $Exe -ArgumentList "`"$full`""
    } else {
        Start-Process -FilePath $Exe
    }
    Start-Sleep -Seconds $Wait
}

$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$x = $vs.Left; $y = $vs.Top; $w = $vs.Width; $h = $vs.Height
$proc = Get-Process Wonderdraft -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $FullScreen -and $proc) {
    $r = New-Object WdWin+RECT
    [WdWin]::GetWindowRect($proc.MainWindowHandle, [ref]$r) | Out-Null
    $x = [Math]::Max($r.L, $vs.Left); $y = [Math]::Max($r.T, $vs.Top)
    $w = [Math]::Min($r.R, $vs.Right) - $x; $h = [Math]::Min($r.B, $vs.Bottom) - $y
}

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
if (-not $FullScreen -and $proc) {
    # PrintWindow with PW_RENDERFULLCONTENT (2) captures the window even when it is covered.
    $hdc = $g.GetHdc()
    $ok = [WdWin]::PrintWindow($proc.MainWindowHandle, $hdc, 2)
    $g.ReleaseHdc($hdc)
    if (-not $ok) { $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size) }
} else {
    $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size)
}
$dir = Split-Path -Parent $Shot
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
$bmp.Save($Shot, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $Shot (${w}x${h} at $x,$y)"

if ($Close -and $proc) {
    $proc.CloseMainWindow() | Out-Null
    if (-not $proc.WaitForExit(5000)) { Stop-Process -Id $proc.Id -Force -Confirm:$false }
    Write-Output "closed Wonderdraft"
}
