"""`James Viewer.exe --self-check`: try every bundled part on this PC and write a plain report.

Builds its own small test files in a temporary folder. It opens none of the user's documents.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__

MACHINES = {0x8664: "x64", 0xAA64: "ARM64"}
PASSWORD = "self-check"


def _machine_lines() -> list[str]:
    process, native = wt.USHORT(), wt.USHORT()
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.IsWow64Process2.argtypes = [wt.HANDLE, ctypes.POINTER(wt.USHORT), ctypes.POINTER(wt.USHORT)]
    kernel.IsWow64Process2(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native))
    return [
        f"Windows: {platform.platform()}",
        f"PC processor: {MACHINES.get(native.value, hex(native.value))}",
        f"App code: {platform.machine()}, Python {platform.python_version()}",
    ]


def _dark_pixels(data: bytes, width: int, height: int, stride: int, channels: int) -> int:
    image = QImage(data, width, height, stride, QImage.Format.Format_RGB32 if channels == 4 else QImage.Format.Format_BGR888)
    return sum(1 for y in range(0, height, 4) for x in range(0, width, 4) if QColor(image.pixel(x, y)).lightness() < 100)


def _check_drawing(_work: Path) -> str:
    image = QImage(200, 60, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setPen(Qt.GlobalColor.black)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "James Viewer 中文")
    painter.end()
    dark = sum(1 for y in range(60) for x in range(200) if QColor(image.pixel(x, y)).lightness() < 128)
    assert dark > 50, "text did not draw"
    return f"{len(QFontDatabase.families())} fonts, platform {QApplication.platformName()}"


def _make_pdf(path: Path, pages: int = 3) -> None:
    import pikepdf

    with pikepdf.new() as pdf:
        for _ in range(pages):
            pdf.add_blank_page(page_size=(400, 400))
            pdf.pages[-1].Contents = pdf.make_stream(b"0 g 50 50 300 300 re f")
        pdf.save(path)


def _check_pdf(work: Path) -> str:
    from .core.pdf_engine import PdfiumDocuments

    path = work / "check.pdf"
    _make_pdf(path)
    documents = PdfiumDocuments()
    try:
        data, width, height, stride, channels = documents.render(str(path), None, 0, 400, 400)
        assert _dark_pixels(data, width, height, stride, channels) > 1000, "the page rendered blank"
    finally:
        documents.close_all()
    return f"rendered {width}x{height}"


def _check_pdf_editing(work: Path) -> str:
    import pikepdf

    path, locked = work / "edit.pdf", work / "locked.pdf"
    _make_pdf(path, 5)
    with pikepdf.open(path) as pdf:
        del pdf.pages[1]
        pdf.save(locked, encryption=pikepdf.Encryption(owner=PASSWORD, user=PASSWORD))
    with pikepdf.open(locked, password=PASSWORD) as pdf:
        assert len(pdf.pages) == 4
    return "deleted a page, saved with a password, reopened"


def _check_powerpoint_password(work: Path) -> str:
    from cryptography import __version__ as version

    from .core.security import decrypt_office, encrypt_pptx

    plain = _sample_deck()
    locked, opened = work / "locked.pptx", work / "opened.pptx"
    encrypt_pptx(plain, PASSWORD, locked)
    decrypt_office(locked, PASSWORD, opened)
    assert opened.read_bytes() == plain.read_bytes(), "the decrypted file differs"
    return f"encrypted and decrypted a deck, cryptography {version}"


def _check_markdown(work: Path) -> str:
    from .core.markdown_engine import parse_markdown

    path = work / "check.md"
    path.write_text("# Title\n\n**中文** with $x^2$ and\n\n```python\nprint(1)\n```\n\n| a |\n|---|\n| b |\n", encoding="utf-8")
    page = parse_markdown(path)
    formulas = [asset for asset in page.assets if asset.kind == "math"]
    assert len(page.headings) == 1 and "<table" in page.template, "the document did not parse"
    assert formulas and formulas[0].blob and not formulas[0].error, "the formula did not draw"
    return f"parsed text, a table, code and {len(formulas)} formula"


def _sample_deck() -> Path:
    from .core import paths

    return paths.app_dir() / "licenses" / "self-check.pptx" if getattr(sys, "frozen", False) else paths.app_dir() / "tests" / "fixtures" / "rich.pptx"


def _check_office(work: Path) -> str:
    from .core.office_engine import OfficeEngine
    from .core.pdf_engine import PdfiumDocuments

    engine = OfficeEngine()
    assert engine.available, "the office folder is missing"
    started = time.perf_counter()
    output = work / "deck.pdf"
    try:
        engine.to_pdf(_sample_deck(), output)
    finally:
        engine.close()
    documents = PdfiumDocuments()
    try:
        data, width, height, stride, channels = documents.render(str(output), None, 0, 800, 600)
        assert _dark_pixels(data, width, height, stride, channels) > 20, "the slide rendered blank"
    finally:
        documents.close_all()
    return f"PowerPoint slide rendered in {time.perf_counter() - started:.1f} s"


CHECKS = (
    ("Drawing text and fonts", _check_drawing),
    ("PDF viewing", _check_pdf),
    ("PDF editing and passwords", _check_pdf_editing),
    ("PowerPoint passwords", _check_powerpoint_password),
    ("Markdown and formulas", _check_markdown),
    ("PowerPoint office engine", _check_office),
)


def run() -> int:
    app = QApplication(sys.argv[:1])
    lines = [f"{APP_NAME} {__version__} self-check", time.strftime("%Y-%m-%d %H:%M:%S"), *_machine_lines(), ""]
    failed = 0
    with tempfile.TemporaryDirectory(prefix="james-viewer-check-") as folder:
        for name, check in CHECKS:
            started = time.perf_counter()
            try:
                detail = check(Path(folder))
            except Exception:
                failed += 1
                lines += [f"FAIL  {name}", traceback.format_exc()]
            else:
                lines.append(f"PASS  {name}: {detail} ({time.perf_counter() - started:.1f} s)")
    lines += ["", f"{len(CHECKS) - failed} of {len(CHECKS)} passed."]
    report = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)) / f"{APP_NAME} self-check.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if "--quiet" not in sys.argv:
        QMessageBox.information(None, f"{APP_NAME} self-check", f"{len(CHECKS) - failed} of {len(CHECKS)} checks passed.\n\nThe report is on your Desktop:\n{report.name}")
    app.quit()
    return 1 if failed else 0
