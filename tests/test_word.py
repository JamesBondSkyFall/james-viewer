"""Word documents: opening, passwords, editing text, Times New Roman, and saving."""

import shutil
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from conftest import FIXTURES, PASSWORD, pump, sha256
from james_viewer.core.docx_engine import read_paragraphs, read_restricted, write_word
from james_viewer.core.sniff import sniff

pytestmark = pytest.mark.skipif(not (FIXTURES / "word-rich.docx").exists(), reason="make the Word test files first")


def _copy(work: Path, name: str) -> Path:
    target = work / name
    shutil.copyfile(FIXTURES / name, target)
    return target


def _page_text(document, position: int) -> str:
    ref = document.ref_at(position)
    page, text = document.workspace.pdfium.text_page(ref.pdf, ref.password, ref.page)
    try:
        return text.get_text_range()
    finally:
        text.close()
        page.close()


def _xml(path: Path, part: str) -> str:
    with zipfile.ZipFile(path) as archive:
        return archive.read(part).decode("utf-8")


# -- reading -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, kind", [
    ("word-rich.docx", ("docx", False)), ("word-rich.doc", ("doc", False)),
    ("word-locked.docx", ("docx", True)), ("word-locked.doc", ("doc", True)),
])
def test_word_files_are_recognised_by_content(work, name, kind):
    found = sniff(_copy(work, name))
    assert (found.format, found.encrypted) == kind and found.family == "word"


def test_a_docx_named_pptx_is_still_word(work):
    target = work / "renamed.pptx"
    shutil.copyfile(FIXTURES / "word-rich.docx", target)
    assert sniff(target).format == "docx"


def test_paragraphs_come_from_body_tables_and_keep_order():
    paragraphs = read_paragraphs(FIXTURES / "word-rich.docx")
    texts = [p.text for p in paragraphs]
    assert texts[0] == "Word fixture 文档标题"
    assert "名称" in texts and "second row" in texts
    assert {p.where for p in paragraphs} >= {"body", "table"}


# -- the real window -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["word-rich.docx", "word-rich.doc"])
def test_word_opens_with_its_pages(ui, work, name):
    view = ui.open(_copy(work, name))
    document = view.document
    assert document.family == "word" and len(document.pages) == 3
    assert "Word fixture" in _page_text(document, 0)
    assert "Page three" in _page_text(document, 2)
    assert not document.dirty


@pytest.mark.parametrize("name", ["word-locked.docx", "word-locked.doc"])
def test_locked_word_needs_the_password(ui, work, name):
    path = _copy(work, name)
    ui.passwords = ["wrong one", PASSWORD]
    view = ui.open(path)
    assert view is not None and ui.password_prompts == 2
    assert "Word fixture" in _page_text(view.document, 0)
    assert sha256(path) == sha256(FIXTURES / name)


def test_editing_a_paragraph_rerenders_and_undoes(ui, work):
    view = ui.open(_copy(work, "word-rich.docx"))
    document = view.document
    view.text_toggle.click()
    pump(0.3)
    panel = view.text_panel
    assert panel.isVisible() and panel.heading.text() == "Page 1"
    assert any(block.name.startswith("Table cell") for block in panel._blocks)
    first = next(i for i, block in enumerate(panel._blocks) if block.text.startswith("Opening paragraph"))
    panel._editors[first].setPlainText("Changed opening 已修改的段落")
    panel.commit()
    ui.settle(document)
    assert "Changed opening" in _page_text(document, 0) and document.dirty
    document.undo()
    ui.settle(document)
    assert "Opening paragraph" in _page_text(document, 0)
    document.redo()
    ui.settle(document)
    assert "Changed opening" in _page_text(document, 0)
    view.text_toggle.click()


def test_text_panel_lists_only_the_paragraphs_on_that_page(ui, work):
    view = ui.open(_copy(work, "word-rich.docx"))
    document = view.document
    first = [block.text for block in document.text_blocks(0)]
    third = [block.text for block in document.text_blocks(2)]
    assert "Second paragraph: 第二段，中文与 English mixed." in first and "名称" in first
    assert third == ["Page three closing paragraph 第三页。"]


def test_long_text_can_move_a_paragraph_to_a_new_page(ui, work):
    view = ui.open(_copy(work, "word-rich.docx"))
    document = view.document
    document.edit_text(0, [(2, "Long paragraph. " * 400)])
    ui.settle(document)
    pump(0.5)
    assert len(document.pages) > 3
    assert any("Page three closing" in _page_text(document, i) for i in range(len(document.pages)))


def test_save_as_docx_keeps_edits_and_the_original(ui, work):
    path = _copy(work, "word-rich.docx")
    view = ui.open(path)
    document = view.document
    document.edit_text(0, [(1, "Saved edit 保存")], latin_font=True)
    ui.settle(document)
    out = work / "saved.docx"
    document.save_copy(out)
    texts = [p.text for p in read_paragraphs(out)]
    assert texts[1] == "Saved edit 保存" and texts[0] == "Word fixture 文档标题"
    assert 'w:ascii="Times New Roman"' in _xml(out, "word/document.xml")
    assert sha256(path) == sha256(FIXTURES / "word-rich.docx")


def test_times_new_roman_everywhere_leaves_chinese_fonts(ui, work):
    view = ui.open(_copy(work, "word-rich.docx"))
    document = view.document
    document.set_font_everywhere(True)
    ui.settle(document)
    out = work / "fonts.docx"
    document.save_copy(out)
    styles = _xml(out, "word/styles.xml")
    assert 'w:hAnsi="Times New Roman"' in styles and "asciiTheme" not in styles
    assert styles.count("eastAsia") == _xml(FIXTURES / "word-rich.docx", "word/styles.xml").count("eastAsia")
    document.undo()
    assert not document.font_everywhere


def test_save_as_pdf_writes_the_current_pages(ui, work):
    view = ui.open(_copy(work, "word-rich.docx"))
    document = view.document
    document.edit_text(0, [(1, "PDF export edit")])
    ui.settle(document)
    out = work / "out.pdf"
    document.save_copy(out)
    import pypdfium2

    pdf = pypdfium2.PdfDocument(str(out))
    assert len(pdf) == len(document.pages) and "PDF export edit" in pdf[0].get_textpage().get_text_range()
    pdf.close()


def test_a_doc_converts_before_editing_and_keeps_the_original(ui, work):
    path = _copy(work, "word-rich.doc")
    view = ui.open(path)
    document = view.document
    ui.answers = [True]
    view.text_toggle.click()
    assert pump(60, lambda: document.format == "docx" and not document.preparing and document.pages)
    document.edit_text(0, [(1, "Edited after converting")])
    ui.settle(document)
    out = work / "converted.docx"
    warnings = document.save_copy(out)
    assert any("converted" in warning for warning in warnings)
    assert read_paragraphs(out)[1].text == "Edited after converting"
    assert sha256(path) == sha256(FIXTURES / "word-rich.doc")


def test_saving_a_locked_docx_keeps_or_removes_the_password_as_asked(ui, work):
    ui.passwords = [PASSWORD]
    view = ui.open(_copy(work, "word-locked.docx"))
    document = view.document
    document.edit_text(0, [(1, "Locked edit")])
    ui.settle(document)
    locked, unlocked = work / "still-locked.docx", work / "unlocked.docx"
    document.save_copy(locked)
    document.save_copy(unlocked, unprotected=True)
    assert sniff(locked).encrypted and not sniff(unlocked).encrypted
    assert read_paragraphs(unlocked)[1].text == "Locked edit"


def test_restricted_editing_is_respected(ui, work):
    source = work / "restricted.docx"
    with zipfile.ZipFile(FIXTURES / "word-rich.docx") as original, zipfile.ZipFile(source, "w") as copy:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "word/settings.xml":
                data = data.replace(b"<w:zoom", b'<w:documentProtection w:edit="readOnly" w:enforcement="1"/><w:zoom', 1)
            copy.writestr(item, data)
    assert read_restricted(source)
    view = ui.open(source)
    assert not view.document.can_edit_text
    view.text_toggle.click()
    pump(0.1)
    assert not view.text_panel.isVisible()
    assert any("restricted" in message for message in ui.messages)


def test_word_pages_cannot_be_rearranged_split_or_merged(ui, work):
    view = ui.open(_copy(work, "word-rich.docx"))
    assert not view.document.can_edit_pages
    window = ui.window
    window.merge_documents([str(_copy(work, "word-rich.docx")), str(_copy(work, "plain.pdf"))])
    assert any("Word" in text for _heading, text in ui.told)


def test_fields_are_never_edited(work):
    out = work / "out.docx"
    write_word(FIXTURES / "word-rich.docx", {}, out)
    assert all(p.editable for p in read_paragraphs(out) if p.text)
