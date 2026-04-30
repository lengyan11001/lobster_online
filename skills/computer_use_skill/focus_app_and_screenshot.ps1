param(
  [Parameter(Mandatory = $true)]
  [string]$ExePath,

  [string]$ScreenshotPath = "",

  [int]$WaitSeconds = 20
)

$ErrorActionPreference = "Stop"

function New-Result {
  param(
    [bool]$Ok,
    [string]$ErrorMessage = "",
    [int]$ProcessId = 0,
    [string]$Title = "",
    [string]$Screenshot = "",
    [bool]$Started = $false
  )
  [pscustomobject]@{
    ok = $Ok
    error = $ErrorMessage
    pid = $ProcessId
    title = $Title
    screenshot = $Screenshot
    started = $Started
  } | ConvertTo-Json -Compress
}

if (-not (Test-Path -LiteralPath $ExePath)) {
  New-Result -Ok $false -ErrorMessage "exe not found: $ExePath"
  exit 2
}

$exeItem = Get-Item -LiteralPath $ExePath
$processName = [System.IO.Path]::GetFileNameWithoutExtension($exeItem.Name)
$started = $false

function Get-TargetProcess {
  Get-Process -Name $processName -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Sort-Object StartTime -Descending |
    Select-Object -First 1
}

$proc = Get-TargetProcess
if (-not $proc) {
  Start-Process -FilePath $ExePath | Out-Null
  $started = $true
  $deadline = (Get-Date).AddSeconds([Math]::Max(3, $WaitSeconds))
  do {
    Start-Sleep -Milliseconds 500
    $proc = Get-TargetProcess
  } while (-not $proc -and (Get-Date) -lt $deadline)
}

if (-not $proc) {
  New-Result -Ok $false -ErrorMessage "target window not found after launch/wait"
  exit 3
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32Focus {
  public static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
  public static readonly IntPtr HWND_NOTOPMOST = new IntPtr(-2);
  public const UInt32 SWP_NOSIZE = 0x0001;
  public const UInt32 SWP_NOMOVE = 0x0002;
  public const UInt32 SWP_SHOWWINDOW = 0x0040;
  public const byte VK_MENU = 0x12;
  public const UInt32 KEYEVENTF_KEYUP = 0x0002;
  [DllImport("kernel32.dll")]
  public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")]
  public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")]
  public static extern bool BringWindowToTop(IntPtr hWnd);
  [DllImport("user32.dll")]
  public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, UInt32 uFlags);
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")]
  public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
  [DllImport("user32.dll")]
  public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
  [DllImport("user32.dll")]
  public static extern void keybd_event(byte bVk, byte bScan, UInt32 dwFlags, UIntPtr dwExtraInfo);
}
"@

$handle = [IntPtr]$proc.MainWindowHandle

function Set-TargetForeground {
  param([IntPtr]$TargetHandle, [int]$TargetPid)

  [Win32Focus]::ShowWindowAsync($TargetHandle, 9) | Out-Null
  Start-Sleep -Milliseconds 150

  try {
    $shell = New-Object -ComObject WScript.Shell
    $shell.AppActivate($TargetPid) | Out-Null
  } catch {}

  Start-Sleep -Milliseconds 150
  [Win32Focus]::keybd_event([Win32Focus]::VK_MENU, 0, 0, [UIntPtr]::Zero)
  [Win32Focus]::keybd_event([Win32Focus]::VK_MENU, 0, [Win32Focus]::KEYEVENTF_KEYUP, [UIntPtr]::Zero)

  $fg = [Win32Focus]::GetForegroundWindow()
  [uint32]$fgPidForThread = 0
  $fgThread = [Win32Focus]::GetWindowThreadProcessId($fg, [ref]$fgPidForThread)
  [uint32]$targetPidForThread = 0
  $targetThread = [Win32Focus]::GetWindowThreadProcessId($TargetHandle, [ref]$targetPidForThread)
  $currentThread = [Win32Focus]::GetCurrentThreadId()

  if ($fgThread -ne 0) { [Win32Focus]::AttachThreadInput($currentThread, $fgThread, $true) | Out-Null }
  if ($targetThread -ne 0) { [Win32Focus]::AttachThreadInput($currentThread, $targetThread, $true) | Out-Null }
  try {
    [Win32Focus]::BringWindowToTop($TargetHandle) | Out-Null
    [Win32Focus]::SetWindowPos($TargetHandle, [Win32Focus]::HWND_TOPMOST, 0, 0, 0, 0, [Win32Focus]::SWP_NOMOVE -bor [Win32Focus]::SWP_NOSIZE -bor [Win32Focus]::SWP_SHOWWINDOW) | Out-Null
    Start-Sleep -Milliseconds 80
    [Win32Focus]::SetWindowPos($TargetHandle, [Win32Focus]::HWND_NOTOPMOST, 0, 0, 0, 0, [Win32Focus]::SWP_NOMOVE -bor [Win32Focus]::SWP_NOSIZE -bor [Win32Focus]::SWP_SHOWWINDOW) | Out-Null
    [Win32Focus]::SetForegroundWindow($TargetHandle) | Out-Null
  } finally {
    if ($targetThread -ne 0) { [Win32Focus]::AttachThreadInput($currentThread, $targetThread, $false) | Out-Null }
    if ($fgThread -ne 0) { [Win32Focus]::AttachThreadInput($currentThread, $fgThread, $false) | Out-Null }
  }

  Start-Sleep -Milliseconds 600
}

Set-TargetForeground -TargetHandle $handle -TargetPid $proc.Id

$fg = [Win32Focus]::GetForegroundWindow()
[uint32]$fgPid = 0
[Win32Focus]::GetWindowThreadProcessId($fg, [ref]$fgPid) | Out-Null

if ([int]$fgPid -ne [int]$proc.Id) {
  $fgProc = $null
  try { $fgProc = Get-Process -Id ([int]$fgPid) -ErrorAction Stop } catch {}
  $fgTitle = if ($fgProc) { $fgProc.MainWindowTitle } else { "" }
  New-Result -Ok $false -ErrorMessage ("foreground verification failed; foreground_pid={0}; foreground_title={1}" -f $fgPid, $fgTitle) -ProcessId $proc.Id -Title $proc.MainWindowTitle -Started:$started
  exit 4
}

if (-not $ScreenshotPath) {
  $ts = Get-Date -Format "yyyyMMdd_HHmmss"
  $ScreenshotPath = Join-Path (Get-Location) ("computer_use_{0}.png" -f $ts)
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
try {
  $g.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
  $bmp.Save($ScreenshotPath, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
  $g.Dispose()
  $bmp.Dispose()
}

New-Result -Ok $true -ProcessId $proc.Id -Title $proc.MainWindowTitle -Screenshot $ScreenshotPath -Started:$started
