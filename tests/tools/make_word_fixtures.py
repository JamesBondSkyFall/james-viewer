"""Create Word test files with a real Word, if one is installed. James Viewer never uses Word.

  make_word_fixtures.py <fixtures folder>

Writes word-rich.docx and word-rich.doc (three pages: headings, mixed formatting, a list, a table,
a picture, a header and page numbers), and the same document locked with the test password.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PASSWORD = "Folio-Test-2026"

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$out = '{out}'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {{
  $doc = $word.Documents.Add()
  $s = $word.Selection
  $s.Style = $doc.Styles.Item(-2)
  $s.TypeText('Word fixture 文档标题')
  $s.TypeParagraph()
  $s.Style = $doc.Styles.Item(-1)
  $s.TypeText('Opening paragraph with ')
  $s.Font.Bold = 1
  $s.TypeText('bold words 粗体')
  $s.Font.Bold = 0
  $s.TypeText(' and plain text after them.')
  $s.TypeParagraph()
  $s.TypeText('Second paragraph: 第二段，中文与 English mixed.')
  $s.TypeParagraph()
  $s.Range.ListFormat.ApplyBulletDefault()
  $s.TypeText('First bullet 第一点')
  $s.TypeParagraph()
  $s.TypeText('Second bullet 第二点')
  $s.TypeParagraph()
  $s.Range.ListFormat.RemoveNumbers()
  $table = $doc.Tables.Add($s.Range, 3, 3)
  $table.Borders.Enable = 1
  $cells = @(@('名称', 'Value', 'Note'), @('甲', '1', 'first row'), @('乙', '2', 'second row'))
  for ($r = 0; $r -lt 3; $r++) {{ for ($c = 0; $c -lt 3; $c++) {{ $table.Cell($r + 1, $c + 1).Range.Text = $cells[$r][$c] }} }}
  $s.EndKey(6) | Out-Null
  $s.TypeParagraph()
  $s.TypeText('Paragraph after the table.')
  $s.TypeParagraph()
  $s.InlineShapes.AddPicture('{picture}') | Out-Null
  $s.TypeParagraph()
  $s.InsertBreak(7)
  $s.Style = $doc.Styles.Item(-3)
  $s.TypeText('Page two heading 第二页')
  $s.TypeParagraph()
  $s.Style = $doc.Styles.Item(-1)
  $s.TypeText('Page two body text. 第二页正文。')
  $s.TypeParagraph()
  $s.InsertBreak(7)
  $s.TypeText('Page three closing paragraph 第三页。')
  $doc.Sections.Item(1).Headers.Item(1).Range.Text = 'Fixture header 页眉'
  $doc.Sections.Item(1).Footers.Item(1).PageNumbers.Add() | Out-Null
  $doc.SaveAs2("$out\word-rich.docx", 16)
  $doc.SaveAs2("$out\word-rich.doc", 0)
  $doc.SaveAs2("$out\word-locked.docx", 16, $false, '{password}')
  $doc.SaveAs2("$out\word-locked.doc", 0, $false, '{password}')
  $doc.Close(0)
}} finally {{ $word.Quit() }}
'done'
"""


def main(out: Path) -> None:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    picture = out / "word-picture.png"
    image = Image.new("RGB", (480, 220), "#e8f2ec")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 460, 200), outline="#3d7a52", width=6)
    draw.ellipse((180, 50, 300, 170), fill="#5c9d72")
    image.save(picture)
    script = SCRIPT.format(out=str(out), picture=str(picture), password=PASSWORD)
    runner = out / "make-word-fixtures.ps1"
    runner.write_text(script, encoding="utf-8-sig")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        runner.unlink(missing_ok=True)
    print(result.stdout.strip()[-400:], result.stderr.strip()[-800:])
    picture.unlink(missing_ok=True)
    for name in ("word-rich.docx", "word-rich.doc", "word-locked.docx", "word-locked.doc"):
        print(name, (out / name).stat().st_size if (out / name).exists() else "MISSING")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
