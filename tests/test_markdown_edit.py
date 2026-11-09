"""Editing Markdown on the formatted page: click a block, change its Markdown, apply, undo, save."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtTest import QTest

from conftest import pump, sha256
from test_markdown import REAL_FILE

SOURCE = """# Title 标题

First paragraph with **bold** and $x^2$ inside.

## Section 第二节

- one 一
- two 二

| a | b |
|---|---|
| 1 | 2 |

```python
print("code")
```

Last paragraph 最后一段.
"""


def _notes(work: Path, text: str = SOURCE, encoding: str = "utf-8", newline: str = "\n") -> Path:
    path = work / "edit-me.md"
    path.write_bytes(text.replace("\n", newline).encode(encoding))
    return path


def _ready(ui, path: Path):
    view = ui.open(path)
    reader = view.canvas
    assert pump(5, lambda: reader._rendered_width > 0 and not reader._timer.isActive())
    pump(0.2)
    return view, reader


def _point_of(reader, words: str) -> QPoint:
    found = reader.browser.document().find(words)
    assert not found.isNull(), words
    rect = reader.browser.cursorRect(found)
    return rect.center()


def _click(reader, words: str) -> None:
    QTest.mouseClick(reader.browser.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _point_of(reader, words))
    pump(0.1)


def test_clicking_a_block_in_edit_mode_shows_its_markdown(ui, work):
    view, reader = _ready(ui, _notes(work))
    _click(reader, "First paragraph")
    assert reader._editor is None, "nothing opens outside edit mode"
    view.edit_toggle.click()
    _click(reader, "First paragraph")
    assert reader._editor is not None
    assert reader._editor.toPlainText() == "First paragraph with **bold** and $x^2$ inside."
    QTest.keyClick(reader._editor, Qt.Key.Key_Escape)
    assert reader._editor is None and not view.document.dirty
    view.edit_toggle.click()


@pytest.mark.parametrize("words, expected", [
    ("Title", "# Title 标题"),
    ("two", "- one 一\n- two 二"),
    ("print", '```python\nprint("code")\n```'),
    ("Last paragraph", "Last paragraph 最后一段."),
])
def test_each_kind_of_block_opens_its_own_lines(ui, work, words, expected):
    view, reader = _ready(ui, _notes(work))
    view.edit_toggle.click()
    _click(reader, words)
    assert reader._editor.toPlainText() == expected
    reader.close_editor(False)
    view.edit_toggle.click()


def test_ctrl_enter_applies_and_the_page_updates(ui, work):
    view, reader = _ready(ui, _notes(work))
    view.edit_toggle.click()
    _click(reader, "Last paragraph")
    reader._editor.setPlainText("Changed **ending** 改了")
    QTest.keyClick(reader._editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    pump(0.3)
    document = view.document
    assert "Changed **ending** 改了" in document.text and "Last paragraph" not in document.text
    assert "First paragraph with **bold**" in document.text, "other blocks are untouched"
    assert not reader.browser.document().find("Changed ending").isNull()
    assert document.dirty and view.save_in_place.isEnabled()
    document.undo()
    assert "Last paragraph 最后一段." in document.text and not document.dirty
    document.redo()
    assert "Changed **ending**" in document.text
    view.edit_toggle.click()


def test_clicking_away_applies_the_edit(ui, work):
    view, reader = _ready(ui, _notes(work))
    view.edit_toggle.click()
    _click(reader, "Last paragraph")
    reader._editor.setPlainText("Applied by clicking away")
    _click(reader, "Title")
    pump(0.2)
    assert "Applied by clicking away" in view.document.text
    reader.close_editor(False)
    view.edit_toggle.click()


def test_editing_a_heading_updates_the_outline(ui, work):
    view, reader = _ready(ui, _notes(work))
    view.edit_toggle.click()
    _click(reader, "Section")
    reader._editor.setPlainText("## Renamed section 新名字")
    reader.close_editor(True)
    pump(0.2)
    titles = [view.outline.items.item(i).text().strip() for i in range(view.outline.items.count())]
    assert "Renamed section 新名字" in titles
    view.edit_toggle.click()


def test_save_keeps_the_encoding_and_line_endings_and_asks_once(ui, work):
    path = _notes(work, encoding="gb18030", newline="\r\n")
    view, reader = _ready(ui, path)
    view.document.replace_lines(16, 17, "Saved 已保存")
    ui.answers = [True]
    ui.window.save()
    assert pump(5, lambda: not view.document.dirty)
    data = path.read_bytes()
    assert "Saved 已保存".encode("gb18030") in data and b"\r\n" in data and b"\n\n" not in data.replace(b"\r\n", b"")
    view.document.replace_lines(0, 1, "# New title")
    asked = len(ui.asked)
    ui.window.save()
    assert pump(5, lambda: not view.document.dirty)
    assert len(ui.asked) == asked, "replacing the original was confirmed once already"
    assert path.read_bytes().decode("gb18030").startswith("# New title")


def test_declining_to_replace_leaves_the_file_alone(ui, work):
    path = _notes(work)
    before = sha256(path)
    view, reader = _ready(ui, path)
    view.document.replace_lines(16, 17, "Not saved")
    ui.answers = [False]
    ui.window.save()
    pump(0.5)
    assert sha256(path) == before and view.document.dirty


def test_save_as_writes_a_copy(ui, work):
    path = _notes(work)
    before = sha256(path)
    view, reader = _ready(ui, path)
    view.document.replace_lines(16, 17, "Only in the copy")
    out = work / "copy.md"
    view.document.save_copy(out)
    assert "Only in the copy" in out.read_text(encoding="utf-8") and sha256(path) == before


def test_the_real_note_maps_every_block_to_its_lines(ui):
    if not REAL_FILE.exists():
        pytest.skip("the real note is not on this PC")
    before = sha256(REAL_FILE)
    view, reader = _ready(ui, REAL_FILE)
    reader._index_headings()
    page = view.document.page
    lines = page.text.split("\n")
    checked = 0
    for position, number in reader._block_marks[::9]:
        cursor = reader.browser.textCursor()
        cursor.setPosition(position)
        reader.browser.setTextCursor(cursor)
        reader.browser.ensureCursorVisible()
        pump(0.05)
        point = reader.browser.cursorRect(cursor).center() + QPoint(2, 0)
        view.edit_toggle.setChecked(True)
        assert reader.open_editor_at(point)
        start, end = page.blocks[number]
        assert reader._editor.toPlainText() == "\n".join(lines[start:end]).rstrip("\n").rstrip()
        reader.close_editor(False)
        checked += 1
    view.edit_toggle.setChecked(False)
    assert checked >= 10 and not view.document.dirty
    assert sha256(REAL_FILE) == before
