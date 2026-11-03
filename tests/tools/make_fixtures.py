"""Generate test files. PowerPoint, when installed, is used only to create genuine
.ppt files and genuinely encrypted Office files. James Viewer does not depend on it."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
from pathlib import Path

import pikepdf
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Inches, Pt

PASSWORD = "Folio-Test-2026"
OWNER_PASSWORD = "Owner-Test-2026"


def make_rich_deck(path: Path, *, wide: bool = True, marker: str = "A") -> None:
    deck = Presentation()
    if wide:
        deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    title = deck.slide_layouts[0]
    content = deck.slide_layouts[1]
    blank = deck.slide_layouts[6]

    slide = deck.slides.add_slide(title)
    slide.shapes.title.text = f"Deck {marker}: 线性回归与数据分析"
    slide.placeholders[1].text = "English and 简体中文 side by side"

    slide = deck.slides.add_slide(content)
    slide.shapes.title.text = f"Bullets {marker}"
    frame = slide.placeholders[1].text_frame
    frame.text = "First bullet 第一点"
    for text, level in (("Second bullet 第二点", 0), ("Nested 嵌套", 1), ("Third 第三点", 0)):
        paragraph = frame.add_paragraph()
        paragraph.text = text
        paragraph.level = level
    run = frame.paragraphs[0].runs[0]
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x2E, 0x7D, 0x4F)
    run.font.name = "Microsoft YaHei"

    slide = deck.slides.add_slide(blank)
    rows, cols = 3, 3
    table = slide.shapes.add_table(rows, cols, Inches(1), Inches(1), Inches(8), Inches(2.4)).table
    for row in range(rows):
        for col in range(cols):
            table.cell(row, col).text = f"单元格 {row + 1}.{col + 1}"

    slide = deck.slides.add_slide(content)
    slide.shapes.title.text = f"Chart {marker}"
    data = CategoryChartData()
    data.categories = ["一月", "February", "三月"]
    data.add_series("Series 1", (4.3, 2.5, 3.5))
    data.add_series("系列 2", (2.4, 4.4, 1.8))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1.8), Inches(8), Inches(4.8), data)

    image = Image.new("RGB", (800, 450), (92, 157, 114))
    painter = ImageDraw.Draw(image)
    for offset in range(0, 800, 40):
        painter.line([(offset, 0), (800 - offset, 450)], fill=(240, 250, 244), width=2)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    slide = deck.slides.add_slide(blank)
    buffer.seek(0)
    slide.shapes.add_picture(buffer, Inches(1), Inches(1), width=Inches(8))
    box = slide.shapes.add_textbox(Inches(1), Inches(5.8), Inches(8), Inches(0.8))
    box.text_frame.text = f"Picture slide {marker} 图片页"

    slide = deck.slides.add_slide(content)
    slide.shapes.title.text = f"Hidden slide {marker}"
    slide._element.set("show", "0")

    slide = deck.slides.add_slide(content)
    slide.shapes.title.text = f"Notes {marker}"
    slide.notes_slide.notes_text_frame.text = "Speaker notes 演讲者备注"

    deck.save(path)


def make_pdf(path: Path, pages: int = 6, marker: str = "P") -> None:
    """A small PDF with an outline and a link, built without extra dependencies."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
    for number in range(1, pages + 1):
        page = pdf.add_blank_page(page_size=(612, 792))
        stream = f"BT /F1 36 Tf 72 680 Td (Page {number} of {marker}) Tj ET".encode()
        page.Contents = pdf.make_stream(stream)
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    with pdf.open_outline() as outline:
        for number in range(pages):
            outline.root.append(pikepdf.OutlineItem(f"{marker} page {number + 1}", number))
    pdf.pages[0].Annots = pdf.make_indirect(
        pikepdf.Array(
            [
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Annot,
                        Subtype=pikepdf.Name.Link,
                        Rect=[72, 600, 300, 640],
                        Border=[0, 0, 0],
                        Dest=pikepdf.Array([pdf.pages[pages - 1].obj, pikepdf.Name.Fit]),
                    )
                )
            ]
        )
    )
    pdf.save(path)


POWERPOINT_SCRIPT = r"""
param([string]$Source, [string]$OutDir, [string]$Password)
$ErrorActionPreference = "Stop"
if (Get-Process POWERPNT -ErrorAction SilentlyContinue) { "SKIP"; exit 3 }
$app = New-Object -ComObject PowerPoint.Application
try {
  $stem = [IO.Path]::GetFileNameWithoutExtension($Source)
  $p = $app.Presentations.Open($Source, -1, 0, 0)
  $p.SaveCopyAs((Join-Path $OutDir "$stem.ppt"), 1)
  $p.Close()
  $p = $app.Presentations.Open($Source, 0, 0, 0)
  $p.Password = $Password
  $p.SaveAs((Join-Path $OutDir "$stem-locked.pptx"), 24)
  $p.SaveAs((Join-Path $OutDir "$stem-locked.ppt"), 1)
  $p.Close()
  "OK"
} finally { $app.Quit() }
"""


def powerpoint_fixtures(source: Path, out: Path, scratch: Path) -> bool:
    script = scratch / "fixtures_ppt.ps1"
    script.write_text(POWERPOINT_SCRIPT, encoding="utf-8")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(source), str(out), PASSWORD],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    print(result.stdout.strip(), result.stderr.strip()[-400:])
    return result.returncode == 0


def main(out: Path, real_pdf: Path | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    make_rich_deck(out / "rich.pptx", marker="A")
    make_rich_deck(out / "second.pptx", marker="B")
    make_rich_deck(out / "standard43.pptx", wide=False, marker="C")
    make_pdf(out / "plain.pdf", marker="P")
    make_pdf(out / "second.pdf", pages=3, marker="Q")

    with pikepdf.open(out / "plain.pdf") as pdf:
        pdf.save(out / "locked-aes256.pdf", encryption=pikepdf.Encryption(user=PASSWORD, owner=OWNER_PASSWORD, R=6))
        pdf.save(out / "locked-rc4.pdf", encryption=pikepdf.Encryption(user=PASSWORD, owner=OWNER_PASSWORD, R=3, aes=False, metadata=False))
        restricted = pikepdf.Permissions(extract=False, modify_annotation=False, modify_assembly=False, modify_form=False, modify_other=False)
        pdf.save(out / "restricted-owner-only.pdf", encryption=pikepdf.Encryption(user="", owner=OWNER_PASSWORD, R=6, allow=restricted))
        pdf.save(out / "restricted-both.pdf", encryption=pikepdf.Encryption(user=PASSWORD, owner=OWNER_PASSWORD, R=6, allow=restricted))

    pristine = (out / "plain.pdf").read_bytes()
    (out / "corrupt-truncated.pdf").write_bytes(pristine[: len(pristine) // 3])
    (out / "corrupt-garbage.pdf").write_bytes(b"this is not a pdf at all" * 40)
    (out / "empty.pdf").write_bytes(b"")
    deck = (out / "rich.pptx").read_bytes()
    (out / "corrupt-truncated.pptx").write_bytes(deck[: len(deck) // 2])
    (out / "corrupt-garbage.pptx").write_bytes(b"\x00\x01garbage" * 400)
    (out / "corrupt-garbage.ppt").write_bytes(b"not an ole file" * 200)
    (out / "text-named.pdf").write_text("plain text only", encoding="utf-8")

    scratch = out / "_scratch"
    scratch.mkdir(exist_ok=True)
    if powerpoint_fixtures((out / "rich.pptx").resolve(), out.resolve(), scratch.resolve()):
        ppt = (out / "rich.ppt").read_bytes()
        (out / "corrupt-truncated.ppt").write_bytes(ppt[: len(ppt) // 3])
    shutil.rmtree(scratch, ignore_errors=True)

    if real_pdf and real_pdf.exists():
        shutil.copyfile(real_pdf, out / "real-chinese.pdf")
    for item in sorted(out.iterdir()):
        print(f"{item.stat().st_size:>10}  {item.name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
