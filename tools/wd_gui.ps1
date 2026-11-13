<#
.SYNOPSIS
  Drive the running Wonderdraft window with synthetic input (SendInput), then optionally capture it.
.DESCRIPTION
  Steps are a list like: "click:331,63", "dclick:x,y", "key:ctrl+e", "type:C:\out\map.png",
  "wait:1500". Coordinates are window-relative physical pixels (DPI-aware).
  Keys use SendInput with scan codes (Godot ignores WScript SendKeys).
.EXAMPLE
  powershell -File tools\wd_gui.ps1 -Steps "click:331,63;wait:1500" -Shot out\after.png
#>
param(
    [string]$Steps = "",   # steps separated by ';'
    [string]$Shot = ""
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
if (-not ("WdIn" -as [type])) {
Add-Type @"
using System; using System.Runtime.InteropServices;
public class WdIn {
  public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] public struct UNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public UNION u; }
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] inputs, int size);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);
  public static void Mouse(uint flags) { var i = new INPUT[1]; i[0].type = 0; i[0].u.mi.dwFlags = flags; SendInput(1, i, Marshal.SizeOf(typeof(INPUT))); }
  public static void Key(ushort vk, bool up) {
    var i = new INPUT[1]; i[0].type = 1; i[0].u.ki.wVk = vk; i[0].u.ki.wScan = (ushort)MapVirtualKey(vk, 0);
    i[0].u.ki.dwFlags = up ? 2u : 0u; SendInput(1, i, Marshal.SizeOf(typeof(INPUT)));
  }
  public static void Unicode(char c) {
    var i = new INPUT[2];
    i[0].type = 1; i[0].u.ki.wScan = c; i[0].u.ki.dwFlags = 4;
    i[1].type = 1; i[1].u.ki.wScan = c; i[1].u.ki.dwFlags = 4 | 2;
    SendInput(2, i, Marshal.SizeOf(typeof(INPUT)));
  }
}
"@
}
[WdIn]::SetProcessDPIAware() | Out-Null
$proc = Get-Process Wonderdraft -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $proc) { throw "Wonderdraft is not running" }
$h = $proc.MainWindowHandle
$r = New-Object WdIn+RECT; [WdIn]::GetWindowRect($h, [ref]$r) | Out-Null
[WdIn]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 300

$vk = @{ ctrl = 0x11; shift = 0x10; alt = 0x12; enter = 0x0D; esc = 0x1B; tab = 0x09; backspace = 0x08;
         del = 0x2E; home = 0x24; end = 0x23; up = 0x26; down = 0x28; left = 0x25; right = 0x27; space = 0x20 }
function Send-Combo([string]$combo) {
    $parts = $combo.ToLower().Split("+")
    $codes = foreach ($p in $parts) { if ($vk.ContainsKey($p)) { $vk[$p] } elseif ($p.Length -eq 1) { [int][char]$p.ToUpper() } else { throw "unknown key $p" } }
    foreach ($c in $codes) { [WdIn]::Key([uint16]$c, $false); Start-Sleep -Milliseconds 40 }
    [array]::Reverse($codes)
    foreach ($c in $codes) { [WdIn]::Key([uint16]$c, $true); Start-Sleep -Milliseconds 40 }
}
function Click-At([int]$x, [int]$y, [int]$n) {
    [WdIn]::SetCursorPos($r.L + $x, $r.T + $y) | Out-Null; Start-Sleep -Milliseconds 120
    for ($k = 0; $k -lt $n; $k++) { [WdIn]::Mouse(2); Start-Sleep -Milliseconds 60; [WdIn]::Mouse(4); Start-Sleep -Milliseconds 90 }
}
foreach ($s in ($Steps.Split(";") | Where-Object { $_ -ne "" })) {
    $kind, $arg = $s.Split(":", 2)
    switch ($kind) {
        "click"  { $x, $y = $arg.Split(","); Click-At $x $y 1 }
        "dclick" { $x, $y = $arg.Split(","); Click-At $x $y 2 }
        "key"    { Send-Combo $arg }
        "type"   { foreach ($ch in $arg.ToCharArray()) { [WdIn]::Unicode($ch); Start-Sleep -Milliseconds 15 } }
        "wait"   { Start-Sleep -Milliseconds ([int]$arg) }
        default  { throw "unknown step $s" }
    }
    Write-Output "did $s"
}
if ($Shot) {
    $w = $r.R - $r.L; $hh = $r.B - $r.T
    $bmp = New-Object System.Drawing.Bitmap $w, $hh
    $g = [System.Drawing.Graphics]::FromImage($bmp); $hdc = $g.GetHdc()
    [WdIn]::PrintWindow($h, $hdc, 2) | Out-Null; $g.ReleaseHdc($hdc)
    $bmp.Save($Shot, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
    Write-Output "saved $Shot (${w}x${hh})"
}
