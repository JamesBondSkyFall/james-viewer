"""PDF annotations: highlight, underline, notes, text boxes and ink, made with the real window."""

import shutil
from pathlib import Path

import pikepdf
import pypdfium2
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from conftest import FIXTURES, pump, sha256
from james_viewer.ui import widgets


def _copy(work: Path, name: str = "plain.pdf") -> Path:
    target = work / name
    shutil.copyfile(FIXTURES / name, target)
    return target


def _subtypes(path: Path, index: int = 0) -> list[str]:
    with pikepdf.open(path) as pdf:
        return [str(annot.Subtype) for annot in pdf.pages[index].get("/Annots", [])]


def _page_point(view, x: float, y: float) -> QPoint:
    """Where a point in PDF points shows on screen."""
    canvas = view.canvas
    rect = canvas._page_rect()
    ref = view.document.ref_at(view.document.current)
    scale = rect.width() / ref.width
    return QPoint(round(rect.left() + x * scale), round(rect.top() + (ref.height - y) * scale))


def _pixel(path: Path, x: float, y: float, index: int = 0) -> tuple:
    document = pypdfium2.PdfDocument(str(path))
    image = document[index].render(scale=1).to_pil().convert("RGB")
    height = image.height
    document.close()
    return image.getpixel((int(x), int(height - y)))


@pytest.mark.parametrize("kind, subtype", [("highlight", "/Highlight"), ("underline", "/Underline")])
def test_marking_selected_text(ui, work, kind, subtype):
    path = _copy(work)
    view = ui.open(path)
    document = view.document
    view.canvas.select_all()
    rects = view.canvas.selection_rects()
    assert rects
    view.mark_buttons[kind].click()
    ui.settle(document)
    assert len(document.pages[0].edits) == 1 and not view.canvas.has_selection
    assert "annotated" in document.ref_at(0).pdf  # the page on screen shows it
    out = work / "out.pdf"
    document.save_copy(out)
    assert subtype in _subtypes(out)
    if kind == "highlight":
        left, bottom, right, top = rects[0]
        red, green, blue = _pixel(out, (left + right) / 2, (bottom + top) / 2)
        assert blue < 200 and red > 150, "yellow shows through"
    assert sha256(path) == sha256(FIXTURES / "plain.pdf")


def test_marking_without_a_selection_explains_what_to_do(ui, work):
    view = ui.open(_copy(work))
    view.mark_buttons["highlight"].click()
    assert any("Select some text" in message for message in ui.messages)
    assert not view.document.pages[0].edits


@pytest.mark.parametrize("tool, subtype", [("note", "/Text"), ("text", "/FreeText")])
def test_notes_and_text_boxes_are_placed_with_a_click(ui, work, monkeypatch, tool, subtype):
    monkeypatch.setattr(widgets, "ask_text", lambda *_args: "笔记 A note")
    view = ui.open(_copy(work))
    document = view.document
    view.tool_buttons[tool].click()
    assert view.canvas.tool == tool
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _page_point(view, 300, 400))
    ui.settle(document)
    out = work / "out.pdf"
    document.save_copy(out)
    assert subtype in _subtypes(out)
    with pikepdf.open(out) as pdf:
        annot = [a for a in pdf.pages[0].Annots if str(a.Subtype) == subtype][0]
        assert str(annot.Contents) == "笔记 A note" and "/AP" in annot
        assert abs(float(annot.Rect[0]) - 300) < 3
    view.tool_buttons[tool].click()
    assert view.canvas.tool is None


def test_a_cancelled_note_adds_nothing(ui, work, monkeypatch):
    monkeypatch.setattr(widgets, "ask_text", lambda *_args: None)
    view = ui.open(_copy(work))
    view.tool_buttons["note"].click()
    QTest.mouseClick(view.canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _page_point(view, 300, 400))
    assert not view.document.pages[0].edits
    view.tool_buttons["note"].click()


def test_drawing_with_the_mouse_makes_ink(ui, work):
    view = ui.open(_copy(work))
    document = view.document
    view.tool_buttons["ink"].click()
    viewport = view.canvas.viewport()
    start = _page_point(view, 200, 300)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 12):
        QTest.mouseMove(viewport, start + QPoint(step * 8, (step % 3) * 6))
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(96, 0))
    ui.settle(document)
    out = work / "out.pdf"
    document.save_copy(out)
    assert "/Ink" in _subtypes(out)
    with pikepdf.open(out) as pdf:
        ink = [a for a in pdf.pages[0].Annots if str(a.Subtype) == "/Ink"][0]
        assert len(ink.InkList[0]) >= 8
    view.tool_buttons["ink"].click()


def test_annotations_undo_redo_and_travel_with_their_page(ui, work):
    view = ui.open(_copy(work))
    document = view.document
    document.annotate(1, {"type": "note", "x": 100, "y": 100, "text": "on page two"}, "Add note")
    document.undo()
    assert not document.pages[1].edits
    document.redo()
    assert document.pages[1].edits
    document.move([1], 0)
    ui.settle(document)
    out = work / "out.pdf"
    document.save_copy(out)
    assert "/Text" in _subtypes(out, 0) and "/Text" not in _subtypes(out, 1)


def test_a_duplicated_page_gets_its_own_annotations(ui, work):
    view = ui.open(_copy(work))
    document = view.document
    document.duplicate([0])
    document.annotate(1, {"type": "note", "x": 100, "y": 100, "text": "only the copy"}, "Add note")
    ui.settle(document)
    out = work / "out.pdf"
    document.save_copy(out)
    assert "/Text" not in _subtypes(out, 0) and "/Text" in _subtypes(out, 1)


def test_a_pdf_that_forbids_comments_is_respected(ui, work):
    path = work / "no-comments.pdf"
    with pikepdf.open(FIXTURES / "plain.pdf") as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(owner="owner-secret", user="", allow=pikepdf.Permissions(modify_annotation=False)))
    view = ui.open(path)
    assert not view.document.can_annotate
    assert not view.mark_buttons["highlight"].isEnabled()


def test_rotated_pages_are_declined_clearly(ui, work, monkeypatch):
    monkeypatch.setattr(widgets, "ask_text", lambda *_args: "x")
    path = work / "rotated.pdf"
    with pikepdf.open(FIXTURES / "plain.pdf") as pdf:
        pdf.pages[0].Rotate = 90
        pdf.save(path)
    view = ui.open(path)
    view.document.current = 0
    view.canvas.place_requested.emit("note", 100, 100)
    assert any("rotated" in message for message in ui.messages)
    assert not view.document.pages[0].edits
