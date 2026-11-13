<#
.SYNOPSIS
  Fill and confirm Wonderdraft's native Windows "Export" (or "Save") file dialog without
  touching keyboard focus or the mouse: WM_SETTEXT on the File name edit, BM_CLICK on Save.
.EXAMPLE
  powershell -File tools\wd_savedialog.ps1 -Path C:\out\map.png -Title Export
#>
param(
    [string]$Path = "",
    [switch]$List,
    [string]$Title = "Export",
    [int]$TimeoutMs = 10000
)
$ErrorActionPreference = "Stop"
if (-not ("WdDlg" -as [type])) {
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class WdDlg {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  public static string Txt(IntPtr h) { var s = new StringBuilder(256); GetWindowText(h, s, 256); return s.ToString(); }
}
"@
}
$pids = @(Get-Process Wonderdraft -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
if (-not $pids) { throw "Wonderdraft is not running" }

$deadline = (Get-Date).AddMilliseconds($TimeoutMs)
$dlg = [IntPtr]::Zero
while ($dlg -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline) {
    $script:found = [IntPtr]::Zero
    [WdDlg]::EnumWindows({ param($h, $l)
        $procId = 0; [WdDlg]::GetWindowThreadProcessId($h, [ref]$procId) | Out-Null
        if ($pids -contains $procId -and [WdDlg]::IsWindowVisible($h) -and [WdDlg]::Cls($h) -eq "#32770" -and [WdDlg]::Txt($h) -eq $Title) {
            $script:found = $h; return $false }
        return $true }, [IntPtr]::Zero) | Out-Null
    $dlg = $script:found
    if ($dlg -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 250 }
}
if ($dlg -eq [IntPtr]::Zero) { throw "dialog '$Title' not found within $TimeoutMs ms" }

if ($List) {
    $script:rows = New-Object System.Collections.ArrayList
    [WdDlg]::EnumChildWindows($dlg, { param($h, $l)
        [void]$script:rows.Add(("{0} id={1} text=[{2}]" -f [WdDlg]::Cls($h), [WdDlg]::GetDlgCtrlID($h), [WdDlg]::Txt($h)))
        return $true }, [IntPtr]::Zero) | Out-Null
    $script:rows; return
}
if (-not $Path) { throw "-Path is required" }
# File name edit: in the Vista-style dialog it is the first Edit with control id 1001
# (inside FloatNotifySink > ComboBox). The address bar's Edit has a different id (41477).
$script:edit = [IntPtr]::Zero; $script:save = [IntPtr]::Zero
[WdDlg]::EnumChildWindows($dlg, { param($h, $l)
    $c = [WdDlg]::Cls($h); $id = [WdDlg]::GetDlgCtrlID($h)
    if ($c -eq "Edit" -and $id -eq 1001 -and $script:edit -eq [IntPtr]::Zero) { $script:edit = $h }
    if ($c -eq "Button" -and $id -eq 1) { $script:save = $h }
    return $true }, [IntPtr]::Zero) | Out-Null
if ($script:edit -eq [IntPtr]::Zero) { throw "file name edit not found" }
if ($script:save -eq [IntPtr]::Zero) { throw "Save button (id 1) not found" }

$WM_SETTEXT = 0x000C; $BM_CLICK = 0x00F5
[WdDlg]::SendMessage($script:edit, $WM_SETTEXT, [IntPtr]::Zero, $Path) | Out-Null
Start-Sleep -Milliseconds 200
[WdDlg]::SendMessage($script:save, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
Write-Output "submitted '$Path' to dialog '$Title'"
