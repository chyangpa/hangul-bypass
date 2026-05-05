param(
    [string]$ExePath = "$PSScriptRoot\..\dist\hangul-bypass.exe",
    [string]$OutPath = "$PSScriptRoot\..\screenshot.png",
    [int]$WaitSeconds = 3
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll", SetLastError=true)]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

# wt 새 창으로 실행 (사이즈 지정)
$args = @(
    "-w","new",
    "--size","130,28",
    "cmd","/k",
    $ExePath
)
Write-Output "Launching: wt $args"
Start-Process wt -ArgumentList $args | Out-Null
Start-Sleep -Seconds $WaitSeconds

# 윈도우 핸들 찾기
$wtProc = Get-Process WindowsTerminal -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like "*hangul-bypass*" } |
    Select-Object -First 1

if (-not $wtProc) {
    Write-Error "WindowsTerminal window with title containing 'hangul-bypass' not found"
    Get-Process WindowsTerminal -ErrorAction SilentlyContinue | Format-Table Id, MainWindowTitle
    exit 1
}

$hwnd = $wtProc.MainWindowHandle
Write-Output "Found hwnd=$hwnd title='$($wtProc.MainWindowTitle)'"

# 윈도우 사이즈 측정
$rect = New-Object Win32+RECT
[Win32]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
Write-Output "Window rect: ${w}x${h}"

# 캡처
$bmp = New-Object System.Drawing.Bitmap $w, $h
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $gfx.GetHdc()
$ok = [Win32]::PrintWindow($hwnd, $hdc, 0x02)  # PW_RENDERFULLCONTENT
$gfx.ReleaseHdc($hdc)
$gfx.Dispose()
Write-Output "PrintWindow result: $ok"

$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "Saved: $OutPath"

# 종료 — wt 창 닫기
Stop-Process -Id $wtProc.Id -Force
# hangul-bypass.exe 자식 프로세스도 정리
Get-Process hangul-bypass -ErrorAction SilentlyContinue | Stop-Process -Force
