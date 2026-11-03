param([string]$InDir = "", [string]$OutDir = "", [int]$MaxWait = 40, [int]$ZoomW = 3840, [int]$ZoomH = 2400)
# EXB -> high-res PNG: open in CAXA, resize window beyond screen size, PrintWindow capture.
Add-Type -AssemblyName System.Drawing
$sig = @'
using System;
using System.Runtime.InteropServices;
public class W32 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
'@
if (-not ("W32" -as [type])) { Add-Type -TypeDefinition $sig }
$W = [W32]

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $InDir)  { $InDir  = Join-Path $here "IN" }
if (-not $OutDir) { $OutDir = Join-Path $here "OUT" }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$exe = $null
foreach ($r in @("C:\Program Files\CAXA", "C:\Program Files (x86)\CAXA", "D:\CAXA", "E:\CAXA")) {
  if (Test-Path $r) {
    $hit = Get-ChildItem $r -Recurse -Filter "CDRAFT_M.exe" -ErrorAction SilentlyContinue |
           Sort-Object FullName -Descending | Select-Object -First 1
    if ($hit) { $exe = $hit.FullName; break }
  }
}
if (-not $exe) { Write-Output "FATAL: CAXA CDRAFT_M.exe not found"; exit 2 }

$files = Get-ChildItem $InDir -Filter *.exb -ErrorAction SilentlyContinue
if (-not $files) { Write-Output "FATAL: no .exb in $InDir"; exit 3 }

$n = 0; $fail = 0
foreach ($f in $files) {
  $png = Join-Path $OutDir ($f.BaseName + ".png")
  $n++
  Write-Output ("[{0}] {1}" -f $n, $f.Name)
  $p = Start-Process -FilePath $exe -ArgumentList ('"' + $f.FullName + '"') -PassThru
  $h = [IntPtr]::Zero; $deadline = (Get-Date).AddSeconds($MaxWait)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $p.Refresh()
    if ($p.HasExited) { break }
    if ($p.MainWindowHandle -ne [IntPtr]::Zero) { $h = $p.MainWindowHandle }
    if ($h -ne [IntPtr]::Zero -and $p.MainWindowTitle -match [regex]::Escape($f.Name)) { break }
  }
  if ($h -eq [IntPtr]::Zero) { Write-Output "  FAIL: no window"; $fail++; continue }

  # resize window beyond the screen, then PrintWindow the whole client area
  $W::ShowWindow($h, 1) | Out-Null                # restore (normal) first
  Start-Sleep -Milliseconds 500
  $W::SetWindowPos($h, [IntPtr]::Zero, 0, 0, $ZoomW, $ZoomH, 0x0040) | Out-Null
  $W::SetForegroundWindow($h) | Out-Null
  Start-Sleep -Milliseconds 3000                  # render at new size

  $r = New-Object W32+RECT
  $W::GetWindowRect($h, [ref]$r) | Out-Null
  $w = $r.R - $r.L; $ht = $r.B - $r.T
  if ($w -le 100 -or $ht -le 100) { Write-Output "  FAIL: bad rect"; $fail++ }
  else {
    $bmp = New-Object System.Drawing.Bitmap $w, $ht
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    $ok = $W::PrintWindow($h, $hdc, 2)            # 2 = PW_RENDERFULLCONTENT
    $g.ReleaseHdc($hdc); $g.Dispose()
    if ($ok) { $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png); Write-Output ("  OK -> {0} ({1}x{2}) printwindow" -f (Split-Path $png -Leaf), $w, $ht) }
    else {
      $g2 = [System.Drawing.Graphics]::FromImage($bmp)
      $g2.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $ht))
      $g2.Dispose()
      $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
      Write-Output ("  OK -> {0} ({1}x{2}) copyfromscreen" -f (Split-Path $png -Leaf), $w, $ht)
    }
    $bmp.Dispose()
  }
  try { Stop-Process -Id $p.Id -Force -ErrorAction Stop; Start-Sleep -Milliseconds 1200 } catch {}
}
Write-Output ("DONE: {0} ok, {1} fail" -f ($n - $fail), $fail)
exit 0
