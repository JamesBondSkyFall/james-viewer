"""User-level behaviour, driven through the real window with real key and mouse events."""

import shutil
from pathlib import Path

import pikepdf
import pytest
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QGuiApplication, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from pptx import Presentation

from conftest import FIXTURES, OWNER_PASSWORD, PASSWORD, pump, sha256
from james_viewer.core.sniff import sniff


def _key(widget, key, modifier=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(widget, key, modifier)
    pump(0.05)


def _copy(work: Path, name: str) -> Path:
    target = work / name
    shutil.copyfile(FIXTURES / name, target)
    return target


# -- opening and viewing --------------------------------------------------------------------------


@pytest.mark.parametrize("name, pages", [("plain.pdf", 6), ("real-chinese.pdf", 22), ("rich.pptx", 7), ("rich.ppt", 7)])
def test_opens_each_format_and_shows_a_page(ui, work, name, pages):
    path = _copy(work, name)
    before = sha256(path)
    view = ui.open(path)
    assert view is not None, ui.told
    assert len(view.document.pages) == pages
    assert view.document.ref_at(0) is not None
    assert pump(10, lambda: ui.window.workspace.images.peek_any(view.document.ref_at(0)) is not None)
    assert sha256(path) == before


def test_tabs_keep_several_documents_open(ui, work):
    first = ui.open(_copy(work, "plain.pdf"))
    second = ui.open(_copy(work, "second.pdf"))
    window = ui.window
    assert window.tabs.count() == 2 and window.current_view() is second
    first.go_to(3)
    window.tabs.setCurrentIndex(0)
    pump(0.05)
    assert window.current_view() is first and first.document.current == 3
    window.open_paths([str(second.document.path)])
    pump(0.2)
    assert window.tabs.count() == 2 and window.current_view() is second


def test_keyboard_and_wheel_navigation(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    canvas = view.canvas
    canvas.setFocus()
    _key(canvas, Qt.Key.Key_Right)
    assert view.document.current == 1
    _key(canvas, Qt.Key.Key_PageDown)
    assert view.document.current == 2
    _key(canvas, Qt.Key.Key_Left)
    assert view.document.current == 1
    _key(canvas, Qt.Key.Key_End)
    assert view.document.current == 5
    _key(canvas, Qt.Key.Key_Right)
    assert view.document.current == 5, "stays on the last page"
    _key(canvas, Qt.Key.Key_Home)
    assert view.document.current == 0


def test_thumbnail_click_navigates(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    index = view.rail.model().index(3)
    QTest.mouseClick(view.rail.viewport(), Qt.MouseButton.LeftButton, pos=view.rail.visualRect(index).center())
    pump(0.05)
    assert view.document.current == 3


def test_thumbnails_get_rendered(ui, work):
    view = ui.open(_copy(work, "rich.pptx"))
    assert pump(20, lambda: any(key[2] < 800 and key[0] == view.document.ref_at(0).pdf for key in ui.window.workspace.images._items))


def test_zoom_and_fit(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    canvas = view.canvas
    fit_scale = canvas._scale()
    assert canvas.zoom_text() == "Fit"
    canvas.set_zoom(2.0)
    assert canvas.zoom_text() == "200%" and abs(canvas._scale() - 2.0) < 1e-6
    assert canvas.verticalScrollBar().maximum() > 0
    canvas.zoom_by(1.2)
    assert abs(canvas._scale() - 2.4) < 1e-6
    shortcut = next(s for s in ui.window.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+0"))
    shortcut.activated.emit()
    assert canvas.zoom_text() == "Fit" and abs(canvas._scale() - fit_scale) < 1e-6


def test_text_selection_and_copy(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    canvas = view.canvas
    canvas.select_all()
    assert canvas.selected_text().strip() == "Page 1 of P"
    QGuiApplication.clipboard().clear()
    ui.window._copy()
    assert QGuiApplication.clipboard().text().strip() == "Page 1 of P"


def test_text_copy_is_blocked_when_the_author_forbids_it(ui, work):
    view = ui.open(_copy(work, "restricted-owner-only.pdf"))
    assert view is not None
    view.canvas.select_all()
    assert view.canvas.has_selection
    secret = view.canvas.selected_text().strip()
    QGuiApplication.clipboard().setText("left here by the test")
    pump(0.3)
    ui.window._copy()
    pump(0.2)
    assert QGuiApplication.clipboard().text().strip() != secret
    assert any("does not allow" in message for message in ui.messages)


def test_real_drop_event_opens_files(ui, work):
    window = ui.window
    path = _copy(work, "second.pdf")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDropEvent(QPointF(400, 400), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(event)
    assert pump(10, lambda: window.tabs.count() == 1)
    assert window.current_view().document.path == path


# -- full screen ----------------------------------------------------------------------------------


def _enter_full_screen(window):
    window.activateWindow()
    pump(0.1)
    window.present()
    assert pump(5, lambda: window.presenter is not None and window.presenter.isFullScreen())
    pump(0.4)
    return window.presenter


@pytest.mark.parametrize("name", ["plain.pdf", "rich.pptx"])
def test_full_screen_shows_only_the_page(ui, work, name):
    view = ui.open(_copy(work, name))
    window = ui.window
    presenter = _enter_full_screen(window)
    screen = window.screen().geometry()
    assert presenter.geometry() == screen, "covers the whole monitor, including the taskbar"
    assert presenter.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not presenter.findChildren(type(view.canvas)), "no document chrome inside"
    assert QGuiApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor, "hidden even if the mouse never moves"

    page_w, page_h = view.document.page_size(presenter.position)
    rect = presenter.page_rect()
    assert abs(rect.width() / rect.height() - page_w / page_h) < 0.01, "aspect ratio preserved"
    assert rect.left() >= -0.5 and rect.top() >= -0.5 and rect.right() <= screen.width() + 0.5 and rect.bottom() <= screen.height() + 0.5, "not cropped"

    image = presenter.grab().toImage()
    ratio = image.width() / presenter.width()
    corner = image.pixelColor(1, 1)
    assert pump(5, lambda: window.workspace.images.peek_any(view.document.ref_at(0)) is not None)
    if rect.left() > 2 or rect.top() > 2:
        assert corner.black() == 255 or corner.lightness() == 0, "letterbox is plain black"
    center = presenter.grab().toImage().pixelColor(int(image.width() / 2), int(rect.top() * ratio) + 2)
    assert center.lightness() > 200, "page is painted"
    _key(presenter, Qt.Key.Key_Escape)
    assert pump(3, lambda: window.presenter is None)


def test_full_screen_keys(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    window = ui.window
    presenter = _enter_full_screen(window)
    _key(presenter, Qt.Key.Key_Right)
    _key(presenter, Qt.Key.Key_Right)
    assert presenter.position == 2
    _key(presenter, Qt.Key.Key_Left)
    assert presenter.position == 1
    _key(presenter, Qt.Key.Key_F11)
    assert pump(3, lambda: window.presenter is None)
    assert view.document.current == 1, "the main view follows where the presentation ended"


def test_full_screen_skips_hidden_slides(ui, work):
    view = ui.open(_copy(work, "rich.pptx"))
    assert view.document.pages[5].hidden
    view.go_to(4)
    presenter = _enter_full_screen(ui.window)
    _key(presenter, Qt.Key.Key_Right)
    assert presenter.position == 6
    _key(presenter, Qt.Key.Key_Escape)
    pump(0.3)


def test_ppt_hidden_slide_is_detected(ui, work):
    view = ui.open(_copy(work, "rich.ppt"))
    assert [page.hidden for page in view.document.pages].count(True) == 1


def test_cursor_hides_again_after_moving(ui, work):
    ui.open(_copy(work, "plain.pdf"))
    presenter = _enter_full_screen(ui.window)
    hider = presenter.cursor_hider
    assert hider.hidden
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(50, 50), QPointF(50, 50), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(presenter, move)
    pump(0.05)
    assert hider.hidden, "a mouse-move event without real movement must not reveal the pointer"
    hider._last_position = None  # as if the mouse had really moved
    QApplication.sendEvent(presenter, move)
    pump(0.1)
    assert not hider.hidden and QGuiApplication.overrideCursor() is None
    assert pump(3, lambda: hider.hidden), "hides again once the mouse rests"
    _key(presenter, Qt.Key.Key_Escape)
    pump(0.3)
    assert QGuiApplication.overrideCursor() is None, "the normal pointer returns after full screen"


# -- PDF editing ----------------------------------------------------------------------------------


def _pdf_labels(path, password=None):
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path), password=password)
    labels = []
    for index in range(len(document)):
        page = document[index]
        text = page.get_textpage()
        labels.append(text.get_text_range().strip())
        text.close()
        page.close()
    document.close()
    return labels


def _save(ui, document, out, **options):
    ui.window.save_to(document, out, **options)
    assert pump(120, lambda: out.exists() or ui.told), ui.told
    pump(0.3)


def test_pdf_edit_undo_redo_and_save_as(ui, work):
    source = _copy(work, "plain.pdf")
    before = sha256(source)
    view = ui.open(source)
    document = view.document
    view.set_mode("arrange")

    document.delete([0])
    assert len(document.pages) == 5
    document.duplicate([0])
    assert len(document.pages) == 6
    document.move([5], 0)
    document.undo()
    document.undo()
    assert len(document.pages) == 5
    document.redo()
    assert len(document.pages) == 6
    document.move([5], 0)
    document.add_blank_pdf_page(1)
    assert document.dirty and len(document.pages) == 7
    assert document.undo_label == "Add blank page"

    out = work / "edited.pdf"
    _save(ui, document, out)
    assert _pdf_labels(out) == ["Page 6 of P", "", "Page 2 of P", "Page 2 of P", "Page 3 of P", "Page 4 of P", "Page 5 of P"]
    assert not document.dirty
    assert sha256(source) == before


def test_real_keyboard_shortcuts_for_pages(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    window = ui.window
    window.set_nav("arrange")
    pump(0.1)
    view.grid.setFocus()
    view.grid.setCurrentIndex(view.grid.model().index(0))
    view.duplicate_selected()
    assert len(view.document.pages) == 7
    _key(view.grid, Qt.Key.Key_Delete)
    assert len(view.document.pages) == 6
    window.set_nav("view")


def test_drag_reorder_in_the_grid(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    ui.window.set_nav("arrange")
    pump(0.3)
    grid = view.grid
    grid.setCurrentIndex(grid.model().index(0))
    target = grid.visualRect(grid.model().index(2))
    from PySide6.QtGui import QDragMoveEvent

    from james_viewer.ui.pagelist import MIME

    mime = QMimeData()
    mime.setData(MIME, b"pages")
    position = QPointF(target.right() - 4, target.center().y())
    drop = QDropEvent(position, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    original_source = QDropEvent.source
    QDropEvent.source = lambda _self: grid
    try:
        grid.dropEvent(drop)
    finally:
        QDropEvent.source = original_source
    assert [page.index for page in view.document.pages][:3] == [1, 2, 0]
    ui.window.set_nav("view")
    assert QDragMoveEvent is not None


def test_cannot_delete_every_page(ui, work):
    view = ui.open(_copy(work, "second.pdf"))
    view.set_mode("arrange")
    view.grid.selectAll()
    view.delete_selected()
    assert len(view.document.pages) == 3
    assert any("at least one" in message for message in ui.messages)


def test_copy_paste_pages_between_tabs(ui, work):
    first = ui.open(_copy(work, "plain.pdf"))
    second = ui.open(_copy(work, "second.pdf"))
    window = ui.window
    window.tabs.setCurrentIndex(0)
    pump(0.05)
    first.set_mode("arrange")
    first.grid.setCurrentIndex(first.grid.model().index(5))
    first.copy_pages()
    window.tabs.setCurrentIndex(1)
    pump(0.05)
    second.set_mode("arrange")
    second.grid.setCurrentIndex(second.grid.model().index(0))
    second.paste_pages()
    assert len(second.document.pages) == 4
    out = work / "pasted.pdf"
    _save(ui, second.document, out)
    assert _pdf_labels(out) == ["Page 1 of Q", "Page 6 of P", "Page 2 of Q", "Page 3 of Q"]


def test_family_mixing_is_refused(ui, work):
    pdf = ui.open(_copy(work, "plain.pdf"))
    deck = ui.open(_copy(work, "rich.pptx"))
    ui.window.tabs.setCurrentIndex(0)
    pump(0.05)
    pdf.copy_pages()
    ui.window.tabs.setCurrentIndex(1)
    pump(0.05)
    deck.paste_pages()
    assert len(deck.document.pages) == 7
    assert any("cannot be mixed" in message for message in ui.messages)


# -- PowerPoint editing ---------------------------------------------------------------------------


def test_pptx_edit_text_rerenders_and_saves(ui, work):
    source = _copy(work, "rich.pptx")
    before = sha256(source)
    view = ui.open(source)
    document = view.document
    old_ref = document.ref_at(0)
    blocks = document.text_blocks(0)
    document.edit_text(0, [(blocks[0].index, "已编辑的标题 Edited title")])
    ui.settle(document)
    new_ref = document.ref_at(0)
    assert new_ref != old_ref, "the preview was re-rendered"
    import pypdfium2

    pdf = pypdfium2.PdfDocument(new_ref.pdf)
    text = pdf[new_ref.page].get_textpage().get_text_range()
    assert "Edited title" in text and "已编辑的标题" in text
    document.undo()
    assert document.ref_at(0) == old_ref, "undo restores the earlier render at once"
    document.redo()
    out = work / "edited.pptx"
    _save(ui, document, out)
    assert Presentation(out).slides[0].shapes.title.text == "已编辑的标题 Edited title"
    assert sha256(source) == before


def test_text_panel_applies_edits(ui, work):
    view = ui.open(_copy(work, "rich.pptx"))
    view.set_mode("view")
    view.text_toggle.setChecked(True)
    view._toggle_text_panel(True)
    pump(0.1)
    panel = view.text_panel
    assert panel.isVisible() and panel._editors
    panel._editors[0].setPlainText("From the panel")
    panel.commit()
    assert view.document.pages[0].edits
    assert view.document.text_blocks(0)[0].text == "From the panel"
    view.text_toggle.setChecked(False)
    view._toggle_text_panel(False)


def test_pptx_structure_edits_save_correctly(ui, work):
    view = ui.open(_copy(work, "rich.pptx"))
    document = view.document
    document.move([3], 0)
    document.delete([1])
    document.duplicate([0])
    document.add_slide(1)
    ui.settle(document)
    assert all(document.ref_at(i) is not None for i in range(len(document.pages)))
    out = work / "structure.pptx"
    _save(ui, document, out)
    deck = Presentation(out)
    assert len(deck.slides) == 8
    titles = [slide.shapes.title.text if slide.shapes.title is not None else "" for slide in deck.slides]
    assert titles[0] == "Chart A" and titles[2] == "Chart A" and titles[1] == ""


def test_pasted_slide_from_another_deck(ui, work):
    first = ui.open(_copy(work, "rich.pptx"))
    second = ui.open(_copy(work, "second.pptx"))
    ui.window.tabs.setCurrentIndex(0)
    pump(0.05)
    first.set_mode("arrange")
    first.grid.setCurrentIndex(first.grid.model().index(3))
    first.copy_pages()
    ui.window.tabs.setCurrentIndex(1)
    pump(0.05)
    second.set_mode("arrange")
    second.grid.setCurrentIndex(second.grid.model().index(0))
    second.paste_pages()
    ui.settle(second.document)
    assert second.document.ref_at(1) is not None
    out = work / "pasted.pptx"
    _save(ui, second.document, out)
    deck = Presentation(out)
    assert len(deck.slides) == 8 and deck.slides[1].shapes.title.text == "Chart A"
    assert any(shape.has_chart for shape in deck.slides[1].shapes)


def test_ppt_edit_converts_and_keeps_original(ui, work):
    source = _copy(work, "rich.ppt")
    before = sha256(source)
    view = ui.open(source)
    document = view.document
    ui.answers = [True]
    view.delete_selected()
    assert "Convert to PPTX for editing?" in ui.asked
    assert pump(120, lambda: document.format == "pptx" and not document.preparing), ui.told
    assert len(document.pages) == 7
    view.delete_selected()
    assert len(document.pages) == 6
    out = work / "converted.pptx"
    _save(ui, document, out)
    assert sniff(out).format == "pptx" and len(Presentation(out).slides) == 6
    assert sha256(source) == before


def test_ppt_save_as_same_format_is_byte_identical(ui, work):
    source = _copy(work, "rich.ppt")
    view = ui.open(source)
    out = work / "copy.ppt"
    _save(ui, view.document, out)
    assert sha256(out) == sha256(source)


# -- split and merge ------------------------------------------------------------------------------


def _choose_folder(monkeypatch, folder):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))


def _choose_save(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))


def test_split_pdf_by_ranges(ui, work, monkeypatch):
    source = _copy(work, "plain.pdf")
    before = sha256(source)
    ui.open(source)
    out = work / "parts"
    out.mkdir()
    _choose_folder(monkeypatch, out)
    ui.window.split_document([[0, 1], [4]], True)
    assert pump(30, lambda: len(list(out.iterdir())) == 2)
    pump(0.3)
    assert _pdf_labels(out / "plain (1-2).pdf") == ["Page 1 of P", "Page 2 of P"]
    assert _pdf_labels(out / "plain (5).pdf") == ["Page 5 of P"]
    assert sha256(source) == before


def test_split_pdf_into_one_file(ui, work, monkeypatch):
    ui.open(_copy(work, "plain.pdf"))
    out = work / "parts"
    out.mkdir()
    _choose_folder(monkeypatch, out)
    ui.window.split_document([[0], [2, 3]], False)
    assert pump(30, lambda: len(list(out.iterdir())) == 1)
    pump(0.3)
    assert len(_pdf_labels(next(out.iterdir()))) == 3


def test_split_never_overwrites(ui, work, monkeypatch):
    ui.open(_copy(work, "plain.pdf"))
    out = work / "parts"
    out.mkdir()
    existing = out / "plain (1).pdf"
    existing.write_bytes(b"keep me")
    _choose_folder(monkeypatch, out)
    ui.window.split_document([[0]], True)
    assert pump(30, lambda: (out / "plain (1) (2).pdf").exists())
    assert existing.read_bytes() == b"keep me"


@pytest.mark.parametrize("name", ["rich.pptx", "rich.ppt"])
def test_split_powerpoint(ui, work, monkeypatch, name):
    source = _copy(work, name)
    before = sha256(source)
    ui.open(source)
    out = work / "parts"
    out.mkdir()
    _choose_folder(monkeypatch, out)
    ui.window.split_document([[0, 1], [3]], True)
    assert pump(120, lambda: len(list(out.iterdir())) == 2), ui.told
    pump(0.4)
    assert len(Presentation(out / "rich (1-2).pptx").slides) == 2
    assert len(Presentation(out / "rich (4).pptx").slides) == 1
    assert sha256(source) == before


def test_merge_pdfs_through_the_window(ui, work, monkeypatch):
    first, second = _copy(work, "plain.pdf"), _copy(work, "second.pdf")
    hashes = sha256(first), sha256(second)
    out = work / "merged.pdf"
    _choose_save(monkeypatch, out)
    ui.window.merge_documents([str(first), str(second)])
    assert pump(30, lambda: "Created merged.pdf" in ui.asked), ui.told
    assert len(_pdf_labels(out)) == 9
    assert (sha256(first), sha256(second)) == hashes


def test_merge_ppt_and_pptx_into_pptx(ui, work, monkeypatch):
    first, second = _copy(work, "rich.pptx"), _copy(work, "rich.ppt")
    hashes = sha256(first), sha256(second)
    out = work / "merged.pptx"
    _choose_save(monkeypatch, out)
    ui.window.merge_documents([str(first), str(second)])
    assert pump(180, lambda: "Created merged.pptx" in ui.asked), ui.told
    assert len(Presentation(out).slides) == 14
    assert (sha256(first), sha256(second)) == hashes


def test_merge_refuses_mixed_families(ui, work, monkeypatch):
    first, second = _copy(work, "plain.pdf"), _copy(work, "rich.pptx")
    ui.window.merge_documents([str(first), str(second)])
    assert any("cannot be merged together" in text for _heading, text in ui.told)


def test_merge_asks_for_passwords(ui, work, monkeypatch):
    first, second = _copy(work, "plain.pdf"), _copy(work, "locked-aes256.pdf")
    out = work / "merged.pdf"
    _choose_save(monkeypatch, out)
    ui.passwords = [PASSWORD]
    ui.window.merge_documents([str(first), str(second)])
    assert pump(30, lambda: "Created merged.pdf" in ui.asked), ui.told
    assert ui.password_prompts == 1
    assert len(_pdf_labels(out)) == 12


def test_merged_file_cannot_replace_an_input(ui, work, monkeypatch):
    first, second = _copy(work, "plain.pdf"), _copy(work, "second.pdf")
    _choose_save(monkeypatch, first)
    before = sha256(first)
    ui.window.merge_documents([str(first), str(second)])
    assert any("cannot replace" in text for _heading, text in ui.told)
    assert sha256(first) == before


# -- originals ------------------------------------------------------------------------------------


def test_saving_over_the_original_needs_explicit_consent(ui, work):
    source = _copy(work, "plain.pdf")
    before = sha256(source)
    view = ui.open(source)
    view.document.delete([0])
    ui.answers = [False]
    ui.window.save_to(view.document, source)
    pump(0.5)
    assert ui.asked == ["Replace the original file?"]
    assert sha256(source) == before
    ui.answers = [True]
    ui.window.save_to(view.document, source)
    assert pump(30, lambda: sha256(source) != before)
    pump(0.3)
    assert len(_pdf_labels(source)) == 5


def test_save_after_save_uses_the_edited_content(ui, work):
    view = ui.open(_copy(work, "plain.pdf"))
    document = view.document
    document.delete([0])
    first = work / "first.pdf"
    _save(ui, document, first)
    second = work / "second-save.pdf"
    _save(ui, document, second)
    assert len(_pdf_labels(second)) == 5


# -- passwords ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["locked-aes256.pdf", "locked-rc4.pdf", "rich-locked.pptx", "rich-locked.ppt"])
def test_password_flow(ui, work, name):
    path = _copy(work, name)
    before = sha256(path)
    ui.passwords = ["wrong-one", PASSWORD]
    view = ui.open(path)
    assert ui.password_prompts == 2, "asked, rejected the wrong password, asked again"
    assert view is not None, ui.told
    assert view.document.protected and view.document.pages
    assert view.document.ref_at(0) is not None
    assert sha256(path) == before


@pytest.mark.parametrize("name", ["locked-aes256.pdf", "rich-locked.pptx", "rich-locked.ppt"])
def test_cancelling_the_password_opens_nothing(ui, work, name):
    path = _copy(work, name)
    view = ui.open(path, timeout=10)
    assert view is None and ui.password_prompts == 1


@pytest.mark.parametrize("name, password", [("locked-aes256.pdf", OWNER_PASSWORD), ("rich-locked.pptx", PASSWORD), ("rich-locked.ppt", PASSWORD)])
def test_save_unprotected_copy(ui, work, name, password):
    source = _copy(work, name)
    before = sha256(source)
    ui.passwords = [password]
    view = ui.open(source)
    assert view is not None, ui.told
    suffix = source.suffix
    out = work / f"open{suffix}"
    _save(ui, view.document, out, unprotected=True)
    kind = sniff(out)
    assert kind.encrypted is False
    assert sha256(source) == before
    ui.close_all()
    reopened = ui.open(out)
    assert reopened is not None and len(reopened.document.pages) == len(view.document.pages)


def test_unprotected_copy_refused_without_owner_password(ui, work):
    source = _copy(work, "restricted-both.pdf")
    ui.passwords = [PASSWORD]
    view = ui.open(source)
    assert view is not None
    document = view.document
    assert not document.can_edit_pages and not document.can_copy_text
    ui.window.set_nav("protection")
    pump(0.05)
    assert not ui.window.protection.unprotect_button.isEnabled()
    assert ui.window.protection.owner_button.isVisible()
    out = work / "x.pdf"
    ui.window.save_to(document, out, unprotected=True)
    assert pump(10, lambda: bool(ui.told))
    assert not out.exists() and "owner password" in ui.told[-1][1]
    ui.window.set_nav("view")


def test_restricted_pdf_blocks_page_changes_and_splitting(ui, work, monkeypatch):
    view = ui.open(_copy(work, "restricted-owner-only.pdf"))
    view.delete_selected()
    assert len(view.document.pages) == 6
    assert any("does not allow" in message for message in ui.messages)
    ui.window.split_document([[0]], True)
    assert any(heading == "Splitting is not allowed" for heading, _text in ui.told)


def test_edited_protected_pptx_stays_protected(ui, work):
    ui.passwords = [PASSWORD]
    view = ui.open(_copy(work, "rich-locked.pptx"))
    view.document.delete([0])
    out = work / "still-locked.pptx"
    _save(ui, view.document, out)
    assert sniff(out).encrypted is True


def test_protected_content_stays_out_of_the_persistent_cache(ui, work):
    from james_viewer.core import paths

    before = {entry.name for entry in paths.cache_dir().iterdir()}
    ui.passwords = [PASSWORD]
    view = ui.open(_copy(work, "rich-locked.pptx"))
    assert view is not None
    assert {entry.name for entry in paths.cache_dir().iterdir()} == before


# -- errors and reporting -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["corrupt-garbage.pdf", "empty.pdf", "text-named.pdf", "corrupt-truncated.pptx", "corrupt-garbage.pptx", "corrupt-garbage.ppt"])
def test_damaged_file_gives_a_message_not_a_crash(ui, work, name):
    view = ui.open(_copy(work, name), timeout=30)
    assert view is None
    assert ui.told and name in ui.told[-1][0]
    assert ui.window.isVisible()


@pytest.mark.parametrize("name", ["corrupt-truncated.pdf", "corrupt-truncated.ppt"])
def test_truncated_file_is_recovered_or_reported(ui, work, name):
    view = ui.open(_copy(work, name), timeout=120)
    if view is None:
        assert ui.told
    else:
        pump(1)
    assert ui.window.isVisible()


def test_missing_file_is_reported(ui, work):
    view = ui.open(work / "does-not-exist.pdf", timeout=10)
    assert view is None and "could not be found" in ui.told[-1][1]


def test_compatibility_notes_are_shown(ui, work):
    from james_viewer.core.inspect import inspect_ppt

    view = ui.open(_copy(work, "rich.pptx"))
    assert any("Chart" in note.title for note in view.document.notes)
    assert inspect_ppt()[0].important
    ui.window.set_nav("protection")
    pump(0.05)
    labels = [item.widget().text() for item in (ui.window.protection.notes.itemAt(i) for i in range(ui.window.protection.notes.count())) if item.widget()]
    assert any("Chart" in text for text in labels)
    ui.window.set_nav("view")


def test_pdf_reopens_from_cache_without_the_office_engine(ui, work):
    source = _copy(work, "second.pptx")
    ui.open(source)
    ui.close_all()
    worker = ui.window.workspace.office._worker
    calls = []
    original = worker.call
    worker.call = lambda *a, **k: calls.append(a) or original(*a, **k)
    try:
        view = ui.open(source)
        assert view.document.ref_at(0) is not None
        assert calls == [], "a second open of the same file is served from the cache"
    finally:
        worker.call = original


def test_pikepdf_is_available_for_checks():
    assert pikepdf.__version__
