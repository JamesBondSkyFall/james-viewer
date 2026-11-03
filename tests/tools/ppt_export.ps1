# Test-only helper: exports reference images from a real PowerPoint, if installed, so
# James Viewer's rendering can be compared against them. James Viewer itself never uses PowerPoint.
param([string]$Source, [string]$OutDir, [int]$Width = 1600, [int]$Height = 900)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if (Get-Process POWERPNT -ErrorAction SilentlyContinue) { "SKIP: PowerPoint is already open"; exit 3 }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$app = New-Object -ComObject PowerPoint.Application
try {
  $p = $app.Presentations.Open($Source, -1, 0, 0)
  $count = $p.Slides.Count
  for ($i = 1; $i -le $count; $i++) {
    $p.Slides.Item($i).Export((Join-Path $OutDir ("slide-{0:D3}.png" -f $i)), "PNG", $Width, $Height)
  }
  $p.Close()
  "exported $count slides"
} finally { $app.Quit() }
