# Test-only helper: opens files in a real PowerPoint, if one is installed, to prove they
# load without a repair error. James Viewer itself never uses PowerPoint.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Files)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if (Get-Process POWERPNT -ErrorAction SilentlyContinue) { "SKIP: PowerPoint is already open"; exit 3 }
$app = New-Object -ComObject PowerPoint.Application
try {
  foreach ($f in $Files) {
    try {
      # ReadOnly, not Untitled, no window. A package needing repair raises an error here.
      $p = $app.Presentations.Open($f, -1, 0, 0)
      "{0}: OK slides={1} size={2}x{3} designs={4}" -f (Split-Path $f -Leaf), $p.Slides.Count, $p.PageSetup.SlideWidth, $p.PageSetup.SlideHeight, $p.Designs.Count
      $p.Close()
    } catch { "{0}: FAILED {1}" -f (Split-Path $f -Leaf), $_.Exception.Message }
  }
} finally { $app.Quit() }
