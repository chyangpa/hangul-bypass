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
    "--size","140,28",
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
Write-Output "Saved (raw): $OutPath"

# 종료 — wt 창 닫기
Stop-Process -Id $wtProc.Id -Force
# hangul-bypass.exe 자식 프로세스도 정리
Get-Process hangul-bypass -ErrorAction SilentlyContinue | Stop-Process -Force

# ── 박스 영역 자동 crop (Python PIL) ─────────────────────
# wt 클라이언트가 박스보다 넓을 때 우측 빈 영역이 남아 시각적으로 어색.
# 박스 좌우 보더(│)와 wt 탭바 위쪽까지 포함하여 균등 마진으로 자른다.
$pyScript = @"
from PIL import Image
img = Image.open(r'$OutPath')
w, h = img.size

def is_bg(p):
    r, g, b = p[:3]
    return max(r, g, b) < 20

# 박스 좌우 │ 픽셀 위치 (박스 본문 영역 y에서 검색)
ys = [h // 3, h // 2 - 20]
lefts, rights = [], []
for y in ys:
    for x in range(w):
        if not is_bg(img.getpixel((x, y))):
            lefts.append(x); break
    for x in range(w - 1, -1, -1):
        if not is_bg(img.getpixel((x, y))):
            rights.append(x); break
box_left = min(lefts)
box_right = max(rights)

# 박스 위쪽 가로선 — top border row 찾기 (회색이 가로로 길게 이어지는 첫 행)
def is_grey(p):
    r, g, b = p[:3]
    return r > 100 and g > 100 and b > 100

top_y = 0
for y in range(h):
    cnt = sum(1 for x in range(0, w, 5) if is_grey(img.getpixel((x, y))))
    if cnt > 50:
        top_y = y; break

# 아래쪽 가로선
bottom_y = h - 1
for y in range(h - 1, 0, -1):
    cnt = sum(1 for x in range(0, w, 5) if is_grey(img.getpixel((x, y))))
    if cnt > 50:
        bottom_y = y; break

# 마진(박스 좌우 균등 + 위쪽은 wt 탭바 살짝 + 아래는 박스 바로 아래 끝까지)
margin = 24
crop_left = max(0, box_left - margin)
crop_right = min(w, box_right + margin + 1)
crop_top = 0  # wt 탭바부터 포함
crop_bottom = min(h, bottom_y + margin)

cropped = img.crop((crop_left, crop_top, crop_right, crop_bottom))
cropped.save(r'$OutPath')
print(f'cropped: {cropped.size[0]}x{cropped.size[1]} (was {w}x{h})')
"@
$tmpPy = [System.IO.Path]::GetTempFileName() + ".py"
$pyScript | Out-File -FilePath $tmpPy -Encoding utf8
python $tmpPy
Remove-Item $tmpPy
