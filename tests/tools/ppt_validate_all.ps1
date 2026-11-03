# Test-only helper: opens every file in a folder in a real PowerPoint, if one is installed.
# Opening raises an error if a package is damaged. James Viewer itself never uses PowerPoint.
param([string]$Folder, [string]$ProtectedName, [string]$Password)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if (Get-Process POWERPNT -ErrorAction SilentlyContinue) { "SKIP: PowerPoint is already open"; exit 3 }
$app = New-Object -ComObject PowerPoint.Application
$bad = 0
try {
  foreach ($file in Get-ChildItem -LiteralPath $Folder -File) {
    $target = $file.FullName
    if ($file.Name -eq $ProtectedName) { $target = "$target::$Password::" }
    try {
      $p = $app.Presentations.Open($target, -1, 0, 0)
      "{0}: OK slides={1} size={2}x{3} designs={4}" -f $file.Name, $p.Slides.Count, $p.PageSetup.SlideWidth, $p.PageSetup.SlideHeight, $p.Designs.Count
      $p.Close()
    } catch { $bad++; "{0}: FAILED {1}" -f $file.Name, $_.Exception.Message }
  }
} finally { $app.Quit() }
"failed: $bad"
